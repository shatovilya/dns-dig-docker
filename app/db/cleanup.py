import logging
from dataclasses import dataclass, field
from typing import Any

import metrics
from config import Settings, get_settings
from db.connection import get_db_pool
from retention import retention_cutoff, retention_days

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    deleted_snapshots: int = 0
    deleted_mtr_orphans: int = 0
    tables: dict[str, int] = field(default_factory=dict)


def _parse_delete_count(result: str) -> int:
    parts = result.split()
    if len(parts) == 2 and parts[0] == "DELETE":
        try:
            return int(parts[1])
        except ValueError:
            return 0
    return 0


async def run_retention_cleanup(settings: Settings | None = None) -> CleanupResult:
    pool = get_db_pool()
    settings = settings or get_settings()
    result = CleanupResult()
    if pool is None or not settings.dns_debug_db_cleanup_enabled:
        return result

    cutoff = retention_cutoff(settings)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                mtr_result = await conn.execute(
                    """
                    DELETE FROM mtr_runs
                    WHERE started_at < $1 AND snapshot_id IS NULL
                    """,
                    cutoff,
                )
                result.deleted_mtr_orphans = _parse_delete_count(mtr_result)

                snap_result = await conn.execute(
                    "DELETE FROM historical_snapshots WHERE created_at < $1",
                    cutoff,
                )
                result.deleted_snapshots = _parse_delete_count(snap_result)

        metrics.record_db_cleanup("success")
        if result.deleted_snapshots or result.deleted_mtr_orphans:
            metrics.record_db_cleanup_deleted("historical_snapshots", result.deleted_snapshots)
            metrics.record_db_cleanup_deleted("mtr_runs", result.deleted_mtr_orphans)
            logger.info(
                "Retention cleanup completed",
                extra={
                    "event": "db_cleanup",
                    "extra": {
                        "retention_days": retention_days(settings),
                        "cutoff": cutoff.isoformat(),
                        "deleted_snapshots": result.deleted_snapshots,
                        "deleted_mtr_orphans": result.deleted_mtr_orphans,
                    },
                },
            )
    except Exception as exc:
        metrics.record_db_cleanup("error")
        logger.warning("Retention cleanup failed: %s", exc)
    return result


async def periodic_cleanup_loop() -> None:
    import asyncio

    settings = get_settings()
    interval = max(60, settings.dns_debug_db_cleanup_interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval)
            if settings.dns_debug_db_enabled and settings.dns_debug_db_cleanup_enabled:
                await run_retention_cleanup(settings)
            elif settings.snapshot_enabled and not settings.dns_debug_db_enabled:
                from snapshot_store import FileSnapshotStore, get_snapshot_store

                store = get_snapshot_store()
                if isinstance(store, FileSnapshotStore):
                    await store.prune()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Periodic retention cleanup error: %s", exc)


async def run_file_retention_cleanup(settings: Settings | None = None) -> int:
    """Prune file snapshots older than DNS_DEBUG_DB_RETENTION_DAYS."""
    settings = settings or get_settings()
    if settings.dns_debug_db_enabled or not settings.snapshot_enabled:
        return 0
    from snapshot_store import FileSnapshotStore, get_snapshot_store

    store = get_snapshot_store()
    if isinstance(store, FileSnapshotStore):
        return await store.prune()
    return 0
