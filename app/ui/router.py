from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import Settings
from security.auth import RequireReadOnly
from security.ip_allowlist import check_ip_allowed
from ui.aggregator import UIAggregator
from ui.compare import build_compare_response
from ui.filters import CompareFilters, UIFilters, parse_compare_filters, parse_ui_filters
from ui.i18n import build_i18n_context, load_locale_messages

_UI_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_UI_DIR / "templates"))


def build_ui_router(settings: Settings) -> APIRouter:
    base = settings.dns_debug_ui_base_path.rstrip("/") or "/dns-debug"
    router = APIRouter(prefix=base, tags=["ui"])
    aggregator = UIAggregator(settings)

    async def ui_access(request: Request, _principal: RequireReadOnly) -> None:
        if settings.dns_debug_ui_ip_allowlist:
            check_ip_allowed(request, settings, settings.dns_debug_ui_ip_allowlist)

    UiAccess = Annotated[None, Depends(ui_access)]

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request, _access: UiAccess) -> HTMLResponse:
        query_lang = request.query_params.get("lang")
        i18n_ctx = build_i18n_context(settings, query_lang=query_lang)
        en_messages = load_locale_messages("en")
        return _TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            {
                "base_path": base,
                "refresh_seconds": settings.dns_debug_ui_refresh_seconds,
                "readonly": settings.dns_debug_ui_readonly,
                "title": i18n_ctx["pageTitle"],
                "i18n": i18n_ctx,
                "i18n_en_messages": en_messages,
            },
        )

    @router.get("/api/ui/overview")
    async def ui_overview(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.overview(filters)

    @router.get("/api/ui/dns-latency")
    async def ui_dns_latency(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.dns_latency(filters)

    @router.get("/api/ui/edns")
    async def ui_edns(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.edns(filters)

    @router.get("/api/ui/errors")
    async def ui_errors(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.errors(filters)

    @router.get("/api/ui/garbage")
    async def ui_garbage(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.garbage(filters)

    @router.get("/api/ui/cache")
    async def ui_cache(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.cache(filters)

    @router.get("/api/ui/records")
    async def ui_records(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.records(filters)

    @router.get("/api/ui/load")
    async def ui_load(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.load(filters)

    @router.get("/api/ui/mtr")
    async def ui_mtr(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.mtr(filters)

    @router.get("/api/ui/rankings")
    async def ui_rankings(filters: Annotated[UIFilters, Depends(parse_ui_filters)], _access: UiAccess):
        return await aggregator.rankings(filters)

    @router.get("/api/ui/events")
    async def ui_events(
        filters: Annotated[UIFilters, Depends(parse_ui_filters)],
        _access: UiAccess,
        record: str | None = None,
        limit: int = 50,
    ):
        return await aggregator.events(filters, record=record, limit=limit)

    @router.get("/api/ui/snapshots")
    async def ui_snapshots(_access: UiAccess):
        from snapshot_store import get_snapshot_store

        store = get_snapshot_store()
        metas = await store.list_snapshots()
        retention: dict[str, Any] = {
            "retention_count": settings.snapshot_retention_count,
            "enabled": settings.snapshot_enabled,
        }
        if settings.dns_debug_db_enabled:
            from ui.filters import _retention_window_from

            retention["db_enabled"] = True
            retention["db_retention_days"] = settings.dns_debug_db_retention_days
            retention["retention_window_from"] = _retention_window_from(settings)
        return {
            "snapshots": [m.to_dict() for m in metas],
            **retention,
        }

    @router.get("/api/ui/snapshots/{snapshot_id}")
    async def ui_snapshot_detail(snapshot_id: str, _access: UiAccess):
        from snapshot_store import get_snapshot_store

        data = await get_snapshot_store().get(snapshot_id)
        if not data:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Snapshot not found")
        return data

    @router.get("/api/ui/compare")
    async def ui_compare(
        filters: Annotated[CompareFilters, Depends(parse_compare_filters)],
        _access: UiAccess,
    ):
        baseline_filters = filters.baseline_ui_filters()
        comparison_filters = filters.comparison_ui_filters()
        baseline_overview = await aggregator.overview(baseline_filters)
        comparison_overview = await aggregator.overview(comparison_filters)
        baseline_latency = await aggregator.dns_latency(baseline_filters)
        comparison_latency = await aggregator.dns_latency(comparison_filters)
        baseline_garbage = await aggregator.garbage(baseline_filters)
        comparison_garbage = await aggregator.garbage(comparison_filters)
        baseline_errors = await aggregator.errors(baseline_filters)
        comparison_errors = await aggregator.errors(comparison_filters)
        baseline_cache = await aggregator.cache(baseline_filters)
        comparison_cache = await aggregator.cache(comparison_filters)
        baseline_load = await aggregator.load(baseline_filters)
        comparison_load = await aggregator.load(comparison_filters)
        baseline_rankings = await aggregator.rankings(baseline_filters)
        comparison_rankings = await aggregator.rankings(comparison_filters)
        return build_compare_response(
            baseline_overview,
            comparison_overview,
            baseline_latency,
            comparison_latency,
            baseline_garbage,
            comparison_garbage,
            baseline_errors,
            comparison_errors,
            baseline_filters.applied(),
            comparison_filters.applied(),
            baseline_cache,
            comparison_cache,
            baseline_load,
            comparison_load,
            baseline_rankings,
            comparison_rankings,
        )

    return router


def ui_static_directory() -> Path:
    return _UI_DIR / "static"


def mount_ui(app, settings: Settings) -> None:
    """Mount Web UI router and static assets when enabled."""
    if not settings.dns_debug_ui_enabled:
        return

    base = settings.dns_debug_ui_base_path.rstrip("/") or "/dns-debug"
    static_dir = ui_static_directory()
    if static_dir.is_dir():
        app.mount(
            f"{base}/static",
            StaticFiles(directory=str(static_dir)),
            name="ui-static",
        )
    app.include_router(build_ui_router(settings))
