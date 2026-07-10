import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import router
from config import Settings, get_settings
from dns_runner import build_autonomous_config, cancel_all_tests, start_test_background
from mtr_runner import cancel_mtr, start_mtr_background
from resolver_snapshot import capture, set_snapshot
from stats_store import get_stats_store
import metrics
from ndots_analytics import effective_attempts, effective_ndots, effective_timeout_seconds, worst_case_resolve_budget_ms
from security.errors import register_exception_handlers
from security.headers import SecurityHeadersMiddleware
from security.middleware import SecurityMiddleware
from security.rate_limit import RateLimitMiddleware
from security.request_id import RequestIdMiddleware
from ui.router import mount_ui
from utils import setup_logging

logger = logging.getLogger(__name__)


async def _prepare_autonomous_dns(settings: Settings) -> dict[str, Any]:
    if not settings.autonomous_records:
        logger.error(
            "AUTONOMOUS_MODE is enabled but AUTONOMOUS_RECORDS is empty",
            extra={"event": "autonomous_startup_failed"},
        )
        import sys

        sys.exit(1)

    config = build_autonomous_config(settings)
    store = get_stats_store()
    await store.create_test(settings.autonomous_test_id, "autonomous", config)
    return config


async def _start_background_runners(settings: Settings) -> None:
    """Prepare and start DNS + MTR background runners without serializing their execution."""
    import asyncio

    autonomous_config: dict[str, Any] | None = None
    mtr_error: str | None = None

    prep_tasks: list[asyncio.Task] = []
    if settings.autonomous_mode:
        prep_tasks.append(asyncio.create_task(_prepare_autonomous_dns(settings)))

    if settings.mtr_enabled:
        prep_tasks.append(asyncio.create_task(asyncio.to_thread(settings.validate_mtr_startup)))

    if prep_tasks:
        results = await asyncio.gather(*prep_tasks)
        if settings.autonomous_mode:
            autonomous_config = results[0]
        if settings.mtr_enabled:
            mtr_error = results[-1] if settings.autonomous_mode else results[0]

    if mtr_error:
        logger.error(
            mtr_error,
            extra={"event": "mtr_startup_failed"},
        )

    if settings.autonomous_mode and autonomous_config is not None:
        start_test_background(settings.autonomous_test_id, autonomous_config)
        logger.info(
            "Autonomous DNS test started",
            extra={
                "event": "autonomous_started",
                "extra": {
                    "test_id": settings.autonomous_test_id,
                    "records": settings.autonomous_records,
                    "rps": settings.autonomous_rps,
                    "concurrency": settings.autonomous_concurrency,
                },
            },
        )

    if settings.mtr_enabled and not mtr_error:
        start_mtr_background(settings)
        logger.info(
            "Periodic MTR started",
            extra={
                "event": "mtr_started",
                "extra": {
                    "service_name": settings.mtr_service_name,
                    "port": settings.mtr_service_port,
                    "count": settings.mtr_count,
                    "interval_seconds": settings.mtr_interval_seconds,
                },
            },
        )

    if settings.autonomous_mode or (settings.mtr_enabled and not mtr_error):
        await asyncio.sleep(0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    settings = get_settings()
    setup_logging(settings.log_level)

    cleanup_task: asyncio.Task | None = None

    snapshot = capture()
    set_snapshot(snapshot)
    query_types_count = max(1, len(settings.default_query_types))
    budget = worst_case_resolve_budget_ms(
        len(snapshot.search),
        effective_attempts(snapshot),
        effective_timeout_seconds(snapshot),
        query_types_count,
    )
    metrics.init_from_snapshot(
        len(snapshot.nameservers),
        len(snapshot.search),
        snapshot.ndots,
        budget,
    )
    metrics.set_active_tests(0)

    logger.info(
        "DNS debugger started",
        extra={
            "event": "startup",
            "extra": {
                "nameservers": snapshot.nameservers,
                "search": snapshot.search,
                "options": snapshot.options,
                "ndots": snapshot.ndots,
                "autonomous_mode": settings.autonomous_mode,
                "api_auth_enabled": settings.api_auth_enabled,
                "ui_enabled": settings.dns_debug_ui_enabled,
                "ui_base_path": settings.dns_debug_ui_base_path if settings.dns_debug_ui_enabled else None,
                "ui_readonly": settings.dns_debug_ui_readonly,
            },
        },
    )

    if settings.dns_debug_ui_enabled:
        logger.info(
            "Web UI enabled",
            extra={
                "event": "ui_startup",
                "extra": {
                    "base_path": settings.dns_debug_ui_base_path,
                    "readonly": settings.dns_debug_ui_readonly,
                    "refresh_seconds": settings.dns_debug_ui_refresh_seconds,
                },
            },
        )

    if settings.dns_debug_db_enabled:
        from db import (
            import_file_snapshots,
            init_db_pool,
            periodic_cleanup_loop,
            run_migrations,
            run_retention_cleanup,
        )

        await init_db_pool(settings)
        await run_migrations()
        await import_file_snapshots()
        await run_retention_cleanup(settings)
        if settings.dns_debug_db_cleanup_enabled:
            cleanup_task = asyncio.create_task(periodic_cleanup_loop())
        logger.info(
            "PostgreSQL persistence enabled",
            extra={
                "event": "db_startup",
                "extra": {
                    "retention_days": settings.dns_debug_db_retention_days,
                    "cleanup_interval_seconds": settings.dns_debug_db_cleanup_interval_seconds,
                },
            },
        )
    elif settings.snapshot_enabled and settings.dns_debug_db_cleanup_enabled:
        from db.cleanup import periodic_cleanup_loop, run_file_retention_cleanup

        await run_file_retention_cleanup(settings)
        cleanup_task = asyncio.create_task(periodic_cleanup_loop())
        logger.info(
            "File snapshot retention enabled",
            extra={
                "event": "file_retention_startup",
                "extra": {"retention_days": settings.dns_debug_db_retention_days},
            },
        )

    await _start_background_runners(settings)

    yield

    logger.info("Shutting down, cancelling active tests", extra={"event": "shutdown"})

    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    await asyncio.gather(
        cancel_all_tests(timeout=settings.shutdown_timeout_seconds),
        cancel_mtr(timeout=settings.shutdown_timeout_seconds),
    )

    if settings.dns_debug_db_enabled:
        from db import close_db_pool

        await close_db_pool()

    logger.info("Shutdown complete", extra={"event": "shutdown_complete"})


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="DNS Debugger", version="0.5.1", lifespan=lifespan)
    register_exception_handlers(app)

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(RequestIdMiddleware)

    if settings.api_cors_enabled and settings.api_cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.api_cors_allow_origins,
            allow_credentials=settings.api_cors_allow_credentials,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "X-API-Key", "X-Request-ID", "Content-Type"],
        )

    app.include_router(router)
    mount_ui(app, settings)
    return app


app = create_app()
