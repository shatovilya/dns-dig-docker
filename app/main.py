import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from api import router
from config import Settings, get_settings
from dns_runner import build_autonomous_config, cancel_all_tests, start_test_background
from mtr_runner import cancel_mtr, start_mtr_background
from resolver_snapshot import capture, set_snapshot
from stats_store import get_stats_store
import metrics
from ndots_analytics import effective_attempts, effective_ndots, effective_timeout_seconds, worst_case_resolve_budget_ms
from utils import setup_logging

logger = logging.getLogger(__name__)


async def _prepare_autonomous_dns(settings: Settings) -> dict[str, Any]:
    if not settings.autonomous_records:
        logger.error(
            "AUTONOMOUS_MODE is enabled but AUTONOMOUS_RECORDS is empty",
            extra={"event": "autonomous_startup_failed"},
        )
        sys.exit(1)

    config = build_autonomous_config(settings)
    store = get_stats_store()
    await store.create_test(settings.autonomous_test_id, "autonomous", config)
    return config


async def _start_background_runners(settings: Settings) -> None:
    """Prepare and start DNS + MTR background runners without serializing their execution."""
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
    settings = get_settings()
    setup_logging(settings.log_level)

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
            },
        },
    )

    await _start_background_runners(settings)

    yield

    logger.info("Shutting down, cancelling active tests", extra={"event": "shutdown"})
    await asyncio.gather(
        cancel_all_tests(timeout=settings.shutdown_timeout_seconds),
        cancel_mtr(timeout=settings.shutdown_timeout_seconds),
    )
    logger.info("Shutdown complete", extra={"event": "shutdown_complete"})


app = FastAPI(title="DNS Debugger", version="0.1.0", lifespan=lifespan)
app.include_router(router)
