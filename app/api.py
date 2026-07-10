import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from config import get_settings
from dns_runner import cancel_test, start_test_background
from models import (
    DiagnosisResponse,
    GlobalSummaryResponse,
    HealthResponse,
    MtrHopResponse,
    MtrRunResponse,
    MtrStatusResponse,
    RecordStatsResponse,
    ResolveMode,
    TestCreateRequest,
    TestDetailResponse,
    TestListItem,
    TestStatus,
    TestSummaryResponse,
)
from mtr_runner import MtrAlreadyRunningError, is_mtr_running, trigger_mtr_now
from mtr_store import MtrRunResult, get_mtr_store
from ndots_analytics import build_diagnosis, build_test_analytics
from resolver_snapshot import capture, get_snapshot
from security.abuse import check_dns_run_allowed, validate_run_duration
from security.audit import audit_event
from security.auth import RequireOperator, RequireReadOnly, check_metrics_access
from stats_store import get_stats_store

router = APIRouter()


def _mtr_run_to_response(run: MtrRunResult) -> MtrRunResponse:
    return MtrRunResponse(
        run_id=run.run_id,
        service_name=run.service_name,
        port=run.port,
        count=run.count,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=round(run.duration_ms, 3) if run.duration_ms is not None else None,
        exit_code=run.exit_code,
        raw_report=run.raw_report,
        stderr=run.stderr,
        status=run.status,  # type: ignore[arg-type]
        parsed_hops=[
            MtrHopResponse(
                hop=h.hop,
                host=h.host,
                loss_pct=h.loss_pct,
                sent=h.sent,
                last_ms=h.last_ms,
                avg_ms=h.avg_ms,
                best_ms=h.best_ms,
                worst_ms=h.worst_ms,
                stdev_ms=h.stdev_ms,
            )
            for h in run.parsed_hops
        ],
        triggered_by=run.triggered_by,
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        autonomous_mode=settings.autonomous_mode,
        autonomous_test_id=settings.autonomous_test_id if settings.autonomous_mode else None,
        mtr_enabled=settings.mtr_enabled,
        mtr_service_name=settings.mtr_service_name or None,
    )


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


@router.get("/resolver")
async def resolver(
    request: Request,
    _principal: RequireReadOnly,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    snap = capture() if refresh else get_snapshot(refresh=False)
    return {
        "nameservers": snap.nameservers,
        "search": snap.search,
        "options": snap.options,
        "ndots": snap.ndots,
        "timeout_seconds": snap.timeout_seconds,
        "attempts": snap.attempts,
        "raw": snap.raw,
        "captured_at": snap.captured_at.isoformat(),
    }


@router.post("/tests", status_code=201)
async def create_test(
    request: Request,
    principal: RequireOperator,
    body: TestCreateRequest | None = None,
) -> dict[str, str]:
    settings = get_settings()

    if settings.autonomous_mode:
        raise HTTPException(403, "Autonomous mode enabled; manual tests disabled")

    if body is None:
        req = TestCreateRequest(
            records=settings.default_records,
            resolve_modes=[ResolveMode(m) for m in settings.default_resolve_modes],
            ndots_values=settings.default_ndots_values,
            rps=settings.default_rps,
            concurrency=settings.default_concurrency,
            duration_seconds=settings.default_duration_seconds,
            timeout_seconds=settings.default_timeout_seconds,
        )
    else:
        req = body

    if req.rps > settings.max_rps:
        raise HTTPException(400, f"rps exceeds max_rps ({settings.max_rps})")
    if req.concurrency > settings.max_concurrency:
        raise HTTPException(400, f"concurrency exceeds max_concurrency ({settings.max_concurrency})")
    if req.duration_seconds > settings.max_duration_seconds:
        raise HTTPException(400, f"duration_seconds exceeds max ({settings.max_duration_seconds})")
    if len(req.records) > min(settings.max_records, settings.api_max_records_per_run):
        cap = min(settings.max_records, settings.api_max_records_per_run)
        raise HTTPException(400, f"records exceeds limit ({cap})")

    allowed_qt = {t.upper() for t in settings.api_allowed_query_types}
    for qt in req.query_types:
        if qt.upper() not in allowed_qt:
            raise HTTPException(400, f"query type {qt} not allowed")

    validate_run_duration(req.duration_seconds)
    await check_dns_run_allowed(request, principal)

    test_id = str(uuid.uuid4())
    config = {
        "test_name": req.test_name,
        "records": req.records,
        "query_types": req.query_types,
        "resolve_modes": [m.value for m in req.resolve_modes],
        "ndots_values": req.ndots_values,
        "rps": req.rps,
        "concurrency": req.concurrency,
        "duration_seconds": req.duration_seconds,
        "timeout_seconds": req.timeout_seconds,
        "cache_latency_threshold_ms": settings.cache_latency_threshold_ms,
        "cache_latency_ratio": settings.cache_latency_ratio,
        "triggered_by": principal.credential_id,
    }

    store = get_stats_store()
    await store.create_test(test_id, req.test_name, config)
    start_test_background(test_id, config)

    audit_event(
        "dns_test_started",
        request,
        principal=principal,
        extra={"test_id": test_id, "records_count": len(req.records)},
    )

    return {"test_id": test_id, "status": TestStatus.PENDING.value}


@router.get("/tests", response_model=list[TestListItem])
async def list_tests(_principal: RequireReadOnly) -> list[TestListItem]:
    store = get_stats_store()
    tests = await store.list_tests()
    return [
        TestListItem(
            test_id=t.test_id,
            test_name=t.test_name,
            status=t.status,
            started_at=t.started_at,
        )
        for t in tests
    ]


@router.get("/tests/{test_id}", response_model=TestDetailResponse)
async def get_test(test_id: str, _principal: RequireReadOnly) -> TestDetailResponse:
    store = get_stats_store()
    test = await store.get_test(test_id)
    if not test:
        raise HTTPException(404, "test not found")

    summary = test.summary or store.build_summary(test)
    analytics = summary.ndots_search_analytics
    profile_by_record = {}
    if analytics:
        profile_by_record = {p.record: p for p in analytics.per_record}

    per_record = [
        RecordStatsResponse(
            record=r.record,
            total_queries=r.total_queries,
            a_queries=r.a_queries,
            aaaa_queries=r.aaaa_queries,
            errors=r.errors,
            nxdomains=r.nxdomains,
            noisy_system_resolves=r.noisy_system_resolves,
            fqdn_wins=r.fqdn_wins,
            avg_latency_ms=round(r.avg_latency_ms, 3),
            dot_count=profile_by_record[r.record].dot_count if r.record in profile_by_record else 0,
            search_first_at_configured_ndots=(
                profile_by_record[r.record].search_first_at_configured_ndots
                if r.record in profile_by_record
                else False
            ),
            fqdn_latency_delta_ms=(
                profile_by_record[r.record].fqdn_latency_delta_ms
                if r.record in profile_by_record
                else 0.0
            ),
        )
        for r in test.per_record.values()
    ]
    recent = [e.to_dict() for e in list(test.events)[-20:]]

    return TestDetailResponse(
        test_id=test.test_id,
        test_name=test.test_name,
        status=test.status,
        config=test.config,
        started_at=test.started_at,
        finished_at=test.finished_at,
        progress=test.progress,
        counters={
            "total": test.counters.total,
            "success": test.counters.success,
            "error": test.counters.error,
            "nxdomain": test.counters.nxdomain,
            "timeout": test.counters.timeout,
            "noisy": test.counters.noisy,
            "possible_cache_hits": test.counters.possible_cache_hits,
            "by_query_type": test.counters.by_query_type,
            "by_resolve_mode": test.counters.by_resolve_mode,
            "by_outcome": test.counters.by_outcome,
        },
        per_record=per_record,
        summary=summary,
        recent_events=recent,
    )


@router.delete("/tests/{test_id}")
async def delete_test(
    request: Request,
    test_id: str,
    principal: RequireOperator,
) -> dict[str, str]:
    settings = get_settings()
    if settings.autonomous_mode:
        raise HTTPException(403, "Autonomous mode enabled; stop the container to end testing")

    store = get_stats_store()
    test = await store.get_test(test_id)
    if not test:
        raise HTTPException(404, "test not found")
    if test.status != TestStatus.RUNNING:
        raise HTTPException(409, f"test is not running (status={test.status.value})")
    cancelled = await cancel_test(test_id)
    if not cancelled:
        raise HTTPException(409, "could not cancel test")
    audit_event("dns_test_cancelled", request, principal=principal, extra={"test_id": test_id})
    return {"test_id": test_id, "status": "cancelling"}


@router.get("/tests/{test_id}/diagnosis", response_model=DiagnosisResponse)
async def get_test_diagnosis(test_id: str, _principal: RequireReadOnly) -> DiagnosisResponse:
    store = get_stats_store()
    test = await store.get_test(test_id)
    if not test:
        raise HTTPException(404, "test not found")

    snapshot = get_snapshot()
    analytics = build_test_analytics(test, snapshot)
    return build_diagnosis(test, analytics, snapshot)


@router.get("/summary", response_model=GlobalSummaryResponse)
async def global_summary(_principal: RequireReadOnly) -> GlobalSummaryResponse:
    store = get_stats_store()
    data = await store.get_global_summary()
    tests = await store.list_tests()
    return GlobalSummaryResponse(
        total_tests=data["total_tests"],
        active_tests=data["active_tests"],
        completed_tests=data["completed_tests"],
        total_queries=data["total_queries"],
        aggregate_summary=data["aggregate_summary"],
        tests=[
            TestListItem(
                test_id=t.test_id,
                test_name=t.test_name,
                status=t.status,
                started_at=t.started_at,
            )
            for t in tests
        ],
    )


@router.get("/metrics")
async def prometheus_metrics(request: Request) -> PlainTextResponse:
    check_metrics_access(request)
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/mtr", response_model=MtrRunResponse)
async def get_mtr_latest(_principal: RequireReadOnly) -> MtrRunResponse:
    store = get_mtr_store()
    latest = await store.get_latest()
    if not latest:
        raise HTTPException(404, "no MTR runs yet")
    return _mtr_run_to_response(latest)


@router.get("/mtr/runs", response_model=list[MtrRunResponse])
async def list_mtr_runs(_principal: RequireReadOnly) -> list[MtrRunResponse]:
    store = get_mtr_store()
    runs = await store.list_runs()
    return [_mtr_run_to_response(r) for r in runs]


@router.post("/mtr", status_code=202, response_model=MtrStatusResponse)
async def trigger_mtr(
    request: Request,
    principal: RequireOperator,
    service_name: str | None = Query(default=None),
    port: int | None = Query(default=None, ge=1, le=65535),
    count: int | None = Query(default=None, ge=1),
) -> MtrStatusResponse:
    settings = get_settings()
    name = (service_name or settings.mtr_service_name).strip()
    if not name:
        raise HTTPException(400, "service_name is required (set MTR_SERVICE_NAME or pass query param)")
    resolved_port = port if port is not None else settings.mtr_service_port
    resolved_count = count if count is not None else settings.mtr_count

    if is_mtr_running():
        raise HTTPException(409, "an MTR run is already in progress")

    try:
        run_id = await trigger_mtr_now(name, resolved_port, resolved_count)
    except MtrAlreadyRunningError:
        raise HTTPException(409, "an MTR run is already in progress")

    audit_event(
        "mtr_triggered",
        request,
        principal=principal,
        extra={"run_id": run_id, "service_name": name, "port": resolved_port},
    )
    return MtrStatusResponse(run_id=run_id, status="running")
