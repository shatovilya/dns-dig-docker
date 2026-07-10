from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from config import Settings
from models import NoiseType, QueryOutcome, TestStatus
from mtr_store import MtrRunResult, get_mtr_store
from resolver_snapshot import get_snapshot
from stats_store import QueryAttempt, TestState, get_stats_store
from ui.filters import UIFilters, collect_warnings_async, envelope, filter_attempts, select_tests
from utils import percentile, safe_ratio

CACHE_DISCLAIMER = (
    "Heuristic only — based on repeat-query latency deltas. "
    "This does NOT represent real Docker embedded DNS cache hits or misses."
)

# UI health rollup thresholds (overridable via DIAGNOSIS_* env vars on Settings)
_GARBAGE_RATIO_DEGRADED = 0.15
_LATENCY_P95_DEGRADED_MS = 100.0
_ERROR_STORM_QPS = 5.0


def derive_ui_health(
    settings: Settings,
    *,
    total_queries: int,
    error_count: int,
    success_ratio: float,
    p95_ms: float,
    garbage_ratio: float,
    error_qps: float,
    mtr_verdict: str,
    mtr_enabled: bool,
) -> dict[str, Any]:
    """Derive global_status rollup for Live overview (additive JSON)."""
    signals: list[dict[str, Any]] = []
    level = "ok"

    error_rate = 1.0 - success_ratio if total_queries else 0.0
    threshold = settings.diagnosis_error_rate_threshold

    def _signal(code: str, message: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": code, "message": message}
        if params:
            payload["params"] = params
        return payload

    if mtr_enabled and mtr_verdict in (
        "packet_loss_suspected",
        "destination_issue",
        "upstream_issue",
        "local_issue",
    ):
        level = "critical"
        signals.append(_signal("mtr_packet_loss", f"MTR verdict: {mtr_verdict}", {"mtr_verdict": mtr_verdict}))
    elif error_qps >= _ERROR_STORM_QPS and error_count > 0:
        level = "critical"
        signals.append(
            _signal(
                "error_storm",
                f"Error QPS {error_qps:.1f} exceeds storm threshold",
                {"error_qps": round(error_qps, 1)},
            )
        )

    if level != "critical":
        if error_rate >= threshold * 2:
            level = "critical"
            signals.append(
                _signal(
                    "high_error_rate",
                    f"Error rate {error_rate:.1%} is critically elevated",
                    {"error_rate": error_rate},
                )
            )
        elif error_rate >= threshold:
            level = "degraded"
            signals.append(
                _signal(
                    "elevated_errors",
                    f"Error rate {error_rate:.1%} above threshold {threshold:.1%}",
                    {"error_rate": error_rate, "threshold": threshold},
                )
            )

    if p95_ms >= _LATENCY_P95_DEGRADED_MS and level == "ok":
        level = "degraded"
        signals.append(
            _signal(
                "latency_p95_high",
                f"p95 latency {p95_ms:.0f} ms elevated",
                {"p95_ms": round(p95_ms)},
            )
        )

    if garbage_ratio >= _GARBAGE_RATIO_DEGRADED and level == "ok":
        level = "degraded"
        signals.append(
            _signal(
                "noisy_ratio_high",
                f"Garbage ratio {garbage_ratio:.1%} above {_GARBAGE_RATIO_DEGRADED:.0%}",
                {"garbage_ratio": garbage_ratio, "threshold": _GARBAGE_RATIO_DEGRADED},
            )
        )

    if mtr_enabled and mtr_verdict == "unstable_path" and level == "ok":
        level = "degraded"
        signals.append(_signal("mtr_unstable", "MTR reports unstable path"))

    if not signals and total_queries == 0:
        signals.append(_signal("no_data", "No queries in selected scope"))

    return {"level": level, "signals": signals}

EDNS_NOTE = (
    "dns_runner does not instrument EDNS levels yet; all queries are counted under edns0."
)

NOISE_TYPES = [nt.value for nt in NoiseType]


def _error_class(attempt: QueryAttempt) -> str:
    if attempt.outcome == QueryOutcome.TIMEOUT:
        return "timeout"
    if attempt.outcome == QueryOutcome.NXDOMAIN:
        return "nxdomain"
    if attempt.outcome == QueryOutcome.ERROR:
        msg = (attempt.error_message or "").lower()
        for label in ("servfail", "refused", "truncated", "malformed"):
            if label in msg:
                return label
        return "error"
    return "success"


def _latency_percentiles(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    return {
        "p50": round(percentile(samples, 50), 3),
        "p95": round(percentile(samples, 95), 3),
        "p99": round(percentile(samples, 99), 3),
    }


def _time_buckets(attempts: list[QueryAttempt], bucket_seconds: int = 10) -> list[dict[str, Any]]:
    if not attempts:
        return []
    by_bucket: dict[int, list[QueryAttempt]] = defaultdict(list)
    for a in attempts:
        if a.is_search_probe:
            continue
        ts = int(a.timestamp.timestamp())
        bucket = (ts // bucket_seconds) * bucket_seconds
        by_bucket[bucket].append(a)

    buckets: list[dict[str, Any]] = []
    for bucket_ts in sorted(by_bucket):
        items = by_bucket[bucket_ts]
        latencies = [a.latency_ms for a in items]
        errors = sum(1 for a in items if a.outcome != QueryOutcome.SUCCESS)
        buckets.append(
            {
                "timestamp": datetime.fromtimestamp(bucket_ts, tz=timezone.utc).isoformat(),
                "count": len(items),
                "error_rate": round(safe_ratio(errors, len(items)), 4),
                **_latency_percentiles(latencies),
            }
        )
    return buckets


def _aggregate_noise(tests: list[TestState]) -> dict[str, int]:
    counts: dict[str, int] = {nt: 0 for nt in NOISE_TYPES}
    for test in tests:
        for nt, count in test.noise_counts.items():
            counts[nt] = counts.get(nt, 0) + count
    return counts


def _configured_rps(tests: list[TestState]) -> float:
    total = 0.0
    for test in tests:
        total += float(test.config.get("rps", 0))
    return total


def _mtr_verdict(run: MtrRunResult | None) -> str:
    if not run or not run.parsed_hops:
        return "unknown"
    hops = run.parsed_hops
    if not hops:
        return "unknown"

    max_loss = max(h.loss_pct for h in hops)
    if max_loss >= 5.0:
        if hops[-1].loss_pct >= 5.0:
            return "destination_issue"
        mid = hops[1:-1] if len(hops) > 2 else []
        if mid and any(h.loss_pct >= 5.0 for h in mid):
            return "upstream_issue"
        if hops[0].loss_pct >= 5.0 or (len(hops) > 1 and hops[1].loss_pct >= 5.0):
            return "local_issue"
        return "packet_loss_suspected"

    stdevs = [h.stdev_ms for h in hops if h.stdev_ms is not None]
    if stdevs and max(stdevs) > 50:
        return "unstable_path"

    return "ok"


class UIAggregator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _panel_key(self, name: str) -> str:
        return name.replace("-", "_")

    async def _load_snapshot_panel_async(self, filters: UIFilters, panel: str) -> dict[str, Any] | None:
        if not filters.snapshot_id:
            return None
        from snapshot_store import get_snapshot_store

        data = await get_snapshot_store().get(filters.snapshot_id)
        if not data:
            return None
        panels = data.get("panels") or {}
        return panels.get(panel)

    async def _meta(self, tests: list[TestState], filters: UIFilters) -> dict[str, Any]:
        from ui.filters import get_snapshot_count

        warnings = await collect_warnings_async(tests, self.settings, filters)
        is_stale = "event_buffer_truncated" in warnings or filters.view_mode == "historical"
        if filters.snapshot_id:
            is_stale = False
        snapshot_count = await get_snapshot_count(self.settings)
        return {"warnings": warnings, "is_stale": is_stale, "snapshot_count": snapshot_count}

    async def _context(self, filters: UIFilters) -> tuple[list[TestState], list[QueryAttempt]]:
        store = get_stats_store()
        tests = select_tests(await store.list_tests(), filters)
        attempts = filter_attempts(tests, filters)
        return tests, attempts

    async def overview(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "overview")
        if cached:
            return cached

        store = get_stats_store()
        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        global_summary = await store.get_global_summary()
        snapshot = get_snapshot()

        total = len(attempts)
        success = sum(1 for a in attempts if a.outcome == QueryOutcome.SUCCESS)
        errors = sum(
            1 for a in attempts if a.outcome in (QueryOutcome.ERROR, QueryOutcome.TIMEOUT)
        )
        nx = sum(1 for a in attempts if a.outcome == QueryOutcome.NXDOMAIN)

        active = sum(1 for t in tests if t.status == TestStatus.RUNNING)
        completed = sum(1 for t in tests if t.status == TestStatus.COMPLETED)

        primary = [a for a in attempts if not a.is_search_probe]
        latencies = [a.latency_ms for a in primary]
        p95 = _latency_percentiles(latencies)["p95"]
        noise_counts = _aggregate_noise(tests)
        garbage = sum(1 for a in attempts if a.is_noisy or a.is_search_probe)
        useful = sum(1 for a in attempts if not a.is_noisy and not a.is_search_probe)
        garbage_ratio = safe_ratio(garbage, useful + garbage)

        duration_s = 1.0
        for test in tests:
            if test.started_at:
                end = test.finished_at or datetime.now(timezone.utc)
                duration_s = max(duration_s, (end - test.started_at).total_seconds())
        error_qps = len(
            [a for a in primary if a.outcome != QueryOutcome.SUCCESS]
        ) / duration_s

        mtr_store = get_mtr_store()
        latest_mtr = await mtr_store.get_latest()
        mtr_verdict = _mtr_verdict(latest_mtr)

        cache_hits = sum(t.counters.possible_cache_hits for t in tests)
        cache_total = sum(t.counters.total for t in tests)
        mtr_degraded = 0
        if self.settings.mtr_enabled:
            runs = await mtr_store.list_runs()
            mtr_degraded = sum(1 for r in runs if _mtr_verdict(r) not in ("ok", "unknown"))

        global_status = derive_ui_health(
            self.settings,
            total_queries=total,
            error_count=errors,
            success_ratio=round(safe_ratio(success, total), 4),
            p95_ms=p95,
            garbage_ratio=garbage_ratio,
            error_qps=round(error_qps, 3),
            mtr_verdict=mtr_verdict,
            mtr_enabled=self.settings.mtr_enabled,
        )

        return envelope(
            filters,
            self.settings,
            **meta,
            health={
                "status": "ok",
                "autonomous_mode": self.settings.autonomous_mode,
                "mtr_enabled": self.settings.mtr_enabled,
                "ui_readonly": self.settings.dns_debug_ui_readonly,
            },
            resolver={
                "nameservers": snapshot.nameservers,
                "search_domains_count": len(snapshot.search),
                "ndots": snapshot.ndots,
            },
            active_tests=active if filters.test_id else global_summary["active_tests"],
            completed_tests=completed if filters.test_id else global_summary["completed_tests"],
            total_queries=total,
            success_count=success,
            error_count=errors,
            nxdomain_count=nx,
            success_ratio=round(safe_ratio(success, total), 4),
            failed_ratio=round(safe_ratio(errors + nx, total), 4),
            tests=[
                {
                    "test_id": t.test_id,
                    "test_name": t.test_name,
                    "status": t.status.value,
                    "progress": round(t.progress, 3),
                }
                for t in tests
            ],
            global_status=global_status,
            kpi_extras={
                "p50_ms": _latency_percentiles(latencies)["p50"],
                "p95_ms": p95,
                "p99_ms": _latency_percentiles(latencies)["p99"],
                "error_rate": round(safe_ratio(errors, total), 4),
                "nxdomain_rate": round(safe_ratio(nx, total), 4),
                "noisy_ratio": round(garbage_ratio, 4),
                "cache_hit_ratio": round(safe_ratio(cache_hits, cache_total), 4),
                "cache_disclaimer": CACHE_DISCLAIMER,
                "mtr_degraded_count": mtr_degraded,
            },
        )

    async def events(
        self,
        filters: UIFilters,
        *,
        record: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Recent query events from in-memory test buffer (no persistence)."""
        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        if record:
            attempts = [a for a in attempts if a.record == record]
        attempts = sorted(attempts, key=lambda a: a.timestamp, reverse=True)[: max(1, min(limit, 200))]
        rows = [
            {
                "timestamp": a.timestamp.isoformat(),
                "record": a.record,
                "query_type": a.query_type,
                "resolve_mode": a.resolve_mode,
                "outcome": a.outcome.value,
                "latency_ms": round(a.latency_ms, 3),
                "is_noisy": a.is_noisy,
                "is_search_probe": a.is_search_probe,
                "error_message": a.error_message,
            }
            for a in attempts
        ]
        return envelope(filters, self.settings, **meta, events=rows, limit=limit, record_filter=record)

    async def dns_latency(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "dns_latency")
        if cached:
            return cached

        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        primary = [a for a in attempts if not a.is_search_probe]
        latencies = [a.latency_ms for a in primary]

        by_mode: dict[str, list[float]] = defaultdict(list)
        by_qtype: dict[str, list[float]] = defaultdict(list)
        for a in primary:
            by_mode[a.resolve_mode].append(a.latency_ms)
            by_qtype[a.query_type].append(a.latency_ms)

        return envelope(
            filters,
            self.settings,
            **meta,
            sample_count=len(latencies),
            **_latency_percentiles(latencies),
            time_buckets=_time_buckets(primary),
            by_resolve_mode={
                mode: {"count": len(vals), **_latency_percentiles(vals)}
                for mode, vals in sorted(by_mode.items())
            },
            by_query_type={
                qt: {"count": len(vals), **_latency_percentiles(vals)}
                for qt, vals in sorted(by_qtype.items())
            },
        )

    async def edns(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "edns")
        if cached:
            return cached

        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        primary = [a for a in attempts if not a.is_search_probe]
        total = len(primary)
        errors = sum(1 for a in primary if a.outcome != QueryOutcome.SUCCESS)
        avg_latency = round(safe_ratio(sum(a.latency_ms for a in primary), total), 3)

        levels: list[dict[str, Any]] = []
        for level in range(6):
            label = f"edns{level}"
            if level == 0:
                levels.append(
                    {
                        "level": label,
                        "queries": total,
                        "errors": errors,
                        "avg_latency_ms": avg_latency,
                        "error_rate": round(safe_ratio(errors, total), 4),
                    }
                )
            else:
                levels.append(
                    {
                        "level": label,
                        "queries": 0,
                        "errors": 0,
                        "avg_latency_ms": 0.0,
                        "error_rate": 0.0,
                    }
                )

        return envelope(filters, self.settings, **meta, levels=levels, note=EDNS_NOTE)

    async def errors(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "errors")
        if cached:
            return cached

        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        error_attempts = [
            a
            for a in attempts
            if a.outcome in (QueryOutcome.ERROR, QueryOutcome.TIMEOUT, QueryOutcome.NXDOMAIN)
            and not a.is_search_probe
        ]

        by_resolver: dict[str, int] = defaultdict(int)
        by_domain: dict[str, int] = defaultdict(int)
        by_qtype: dict[str, int] = defaultdict(int)
        by_class: dict[str, int] = defaultdict(int)
        matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for a in error_attempts:
            cls = _error_class(a)
            by_resolver[a.resolve_mode] += 1
            by_domain[a.record] += 1
            by_qtype[a.query_type] += 1
            by_class[cls] += 1
            matrix[a.resolve_mode][cls] += 1

        duration_s = 1.0
        if tests:
            for test in tests:
                if test.started_at:
                    end = test.finished_at or datetime.now(timezone.utc)
                    duration_s = max(duration_s, (end - test.started_at).total_seconds())

        return envelope(
            filters,
            self.settings,
            **meta,
            total_errors=len(error_attempts),
            error_qps=round(len(error_attempts) / duration_s, 3),
            configured_rps=_configured_rps(tests),
            by_resolver=dict(by_resolver),
            by_domain=dict(sorted(by_domain.items(), key=lambda x: -x[1])[:20]),
            by_query_type=dict(by_qtype),
            by_error_class=dict(by_class),
            resolver_error_matrix={k: dict(v) for k, v in matrix.items()},
        )

    async def garbage(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "garbage")
        if cached:
            return cached

        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        noise_counts = _aggregate_noise(tests)
        total_noise = sum(noise_counts.values())
        useful = sum(1 for a in attempts if not a.is_noisy and not a.is_search_probe)
        garbage = sum(1 for a in attempts if a.is_noisy or a.is_search_probe)

        noisy_domains: dict[str, int] = defaultdict(int)
        for a in attempts:
            if a.is_noisy or a.is_search_probe:
                noisy_domains[a.record] += 1

        amplification = 0.0
        for test in tests:
            summary = test.summary or get_stats_store().build_summary(test)
            if summary.ndots_search_analytics:
                amplification = max(
                    amplification, summary.ndots_search_analytics.query_amplification_ratio
                )

        return envelope(
            filters,
            self.settings,
            **meta,
            noise_counts=noise_counts,
            total_noisy=total_noise,
            top_noisy_domains=dict(sorted(noisy_domains.items(), key=lambda x: -x[1])[:15]),
            useful_queries=useful,
            garbage_queries=garbage,
            useful_vs_garbage_ratio={
                "useful": useful,
                "garbage": garbage,
                "garbage_ratio": round(safe_ratio(garbage, useful + garbage), 4),
            },
            query_amplification_ratio=round(amplification, 3),
        )

    async def cache(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "cache")
        if cached:
            return cached

        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        hits = sum(t.counters.possible_cache_hits for t in tests)
        total = sum(t.counters.total for t in tests)

        by_mode_total: dict[str, int] = defaultdict(int)
        for test in tests:
            for mode, count in test.counters.by_resolve_mode.items():
                by_mode_total[mode] += count
        for a in attempts:
            if a.is_search_probe:
                continue
            by_mode_total[a.resolve_mode] = by_mode_total.get(a.resolve_mode, 0)

        repeat_keys: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        for a in attempts:
            if a.is_search_probe:
                continue
            key = (a.record, a.query_type, a.resolve_mode)
            repeat_keys[key].append(a.latency_ms)

        repeat_count = sum(1 for vals in repeat_keys.values() if len(vals) > 1)

        threshold = self.settings.cache_latency_threshold_ms
        ratio_threshold = self.settings.cache_latency_ratio

        return envelope(
            filters,
            self.settings,
            **meta,
            disclaimer=CACHE_DISCLAIMER,
            possible_cache_hits=hits,
            total_queries=total,
            hit_ratio=round(safe_ratio(hits, total), 4),
            repeat_query_keys=repeat_count,
            heuristic_config={
                "cache_latency_threshold_ms": threshold,
                "cache_latency_ratio": ratio_threshold,
            },
            by_resolve_mode={
                mode: {
                    "queries": by_mode_total.get(mode, 0),
                    "effectiveness_note": "heuristic — not real cache per resolver",
                }
                for mode in sorted(by_mode_total)
            },
        )

    async def records(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "records")
        if cached:
            return cached

        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        by_record: dict[str, list[QueryAttempt]] = defaultdict(list)
        for a in attempts:
            if a.is_search_probe:
                continue
            by_record[a.record].append(a)

        rows: list[dict[str, Any]] = []
        for record, items in sorted(by_record.items()):
            last = max(items, key=lambda x: x.timestamp)
            errors = sum(1 for a in items if a.outcome != QueryOutcome.SUCCESS)
            rows.append(
                {
                    "fqdn": record,
                    "query_type": last.query_type,
                    "resolve_mode": last.resolve_mode,
                    "status": last.outcome.value,
                    "queries": len(items),
                    "errors": errors,
                    "avg_latency_ms": round(safe_ratio(sum(a.latency_ms for a in items), len(items)), 3),
                    "last_error": last.error_message,
                    "edns_level": "edns0",
                    "retries": 0,
                }
            )

        per_record_stats = []
        for test in tests:
            for rec in test.per_record.values():
                per_record_stats.append(
                    {
                        "record": rec.record,
                        "total_queries": rec.total_queries,
                        "errors": rec.errors,
                        "nxdomains": rec.nxdomains,
                        "fqdn_wins": rec.fqdn_wins,
                        "avg_latency_ms": round(rec.avg_latency_ms, 3),
                    }
                )

        return envelope(filters, self.settings, **meta, records=rows, per_record_summary=per_record_stats)

    async def load(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "load")
        if cached:
            return cached

        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        primary = [a for a in attempts if not a.is_search_probe]
        total = len(primary)
        success = sum(1 for a in primary if a.outcome == QueryOutcome.SUCCESS)
        errors = sum(1 for a in primary if a.outcome != QueryOutcome.SUCCESS)

        duration_s = 1.0
        for test in tests:
            if test.started_at:
                end = test.finished_at or datetime.now(timezone.utc)
                duration_s = max(duration_s, (end - test.started_at).total_seconds())

        actual_qps = round(total / duration_s, 3)
        configured = _configured_rps(tests)
        latencies = [a.latency_ms for a in primary]

        windows = _time_buckets(primary, bucket_seconds=10)
        saturation = round(safe_ratio(actual_qps, configured), 3) if configured else 0.0

        return envelope(
            filters,
            self.settings,
            **meta,
            configured_rps=configured,
            actual_qps=actual_qps,
            saturation_ratio=saturation,
            success_rate=round(safe_ratio(success, total), 4),
            error_rate=round(safe_ratio(errors, total), 4),
            avg_latency_ms=round(safe_ratio(sum(latencies), len(latencies)), 3),
            **_latency_percentiles(latencies),
            time_series=windows,
            burst_panel={
                "concurrency": sum(int(t.config.get("concurrency", 0)) for t in tests),
                "active_tests": sum(1 for t in tests if t.status == TestStatus.RUNNING),
            },
        )

    async def mtr(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "mtr")
        if cached:
            return cached

        tests, _ = await self._context(filters)
        meta = await self._meta(tests, filters)
        if len(await get_mtr_store().list_runs()) >= self.settings.mtr_max_history:
            meta["warnings"] = list(dict.fromkeys([*meta["warnings"], "mtr_history_at_limit"]))
        store = get_mtr_store()
        latest = await store.get_latest()
        runs = await store.list_runs()
        verdict = _mtr_verdict(latest)
        truncated = max(0, len(runs) - self.settings.mtr_max_history)

        def _run_payload(run: MtrRunResult) -> dict[str, Any]:
            hops = [h.to_dict() for h in run.parsed_hops]
            problem_hops = [h for h in run.parsed_hops if h.loss_pct >= 5.0]
            return {
                "run_id": run.run_id,
                "service_name": run.service_name,
                "port": run.port,
                "status": run.status,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "exit_code": run.exit_code,
                "hops": hops,
                "problem_hops": [h.hop for h in problem_hops],
                "verdict": _mtr_verdict(run),
            }

        return envelope(
            filters,
            self.settings,
            **meta,
            mtr_enabled=self.settings.mtr_enabled,
            latest=_run_payload(latest) if latest else None,
            verdict=verdict,
            hops=latest.parsed_hops[0].to_dict() if latest and latest.parsed_hops else None,
            timeline=[_run_payload(r) for r in runs[: self.settings.mtr_max_history]],
            truncated_runs=truncated,
            truncated_reason=(
                f"Only the latest {self.settings.mtr_max_history} MTR runs are retained"
                if truncated
                else None
            ),
            targets=list({r.service_name for r in runs if r.service_name}),
        )

    async def rankings(self, filters: UIFilters) -> dict[str, Any]:
        cached = await self._load_snapshot_panel_async(filters, "rankings")
        if cached:
            return cached

        tests, attempts = await self._context(filters)
        meta = await self._meta(tests, filters)
        primary = [a for a in attempts if not a.is_search_probe]

        def _rank(key_fn, limit: int = 10) -> list[dict[str, Any]]:
            groups: dict[str, list[QueryAttempt]] = defaultdict(list)
            for a in primary:
                groups[key_fn(a)].append(a)
            ranked: list[dict[str, Any]] = []
            for key, items in groups.items():
                errors = sum(1 for a in items if a.outcome != QueryOutcome.SUCCESS)
                ranked.append(
                    {
                        "key": key,
                        "queries": len(items),
                        "errors": errors,
                        "error_rate": round(safe_ratio(errors, len(items)), 4),
                        "avg_latency_ms": round(
                            safe_ratio(sum(a.latency_ms for a in items), len(items)), 3
                        ),
                    }
                )
            ranked.sort(key=lambda x: (-x["error_rate"], -x["avg_latency_ms"]))
            return ranked[:limit]

        mtr_store = get_mtr_store()
        mtr_runs = await mtr_store.list_runs()
        mtr_rankings = []
        for run in mtr_runs:
            loss = max((h.loss_pct for h in run.parsed_hops), default=0.0)
            avg_ms = next((h.avg_ms for h in reversed(run.parsed_hops) if h.avg_ms), 0.0) or 0.0
            mtr_rankings.append(
                {
                    "target": f"{run.service_name}:{run.port}",
                    "loss_pct": loss,
                    "avg_latency_ms": avg_ms,
                    "verdict": _mtr_verdict(run),
                }
            )
        mtr_rankings.sort(key=lambda x: -x["loss_pct"])

        return envelope(
            filters,
            self.settings,
            **meta,
            resolvers=_rank(lambda a: a.resolve_mode),
            domains=_rank(lambda a: a.record),
            query_types=_rank(lambda a: a.query_type),
            mtr_targets=mtr_rankings[:10],
        )
