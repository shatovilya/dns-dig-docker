import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from config import Settings, get_settings
from retention import is_within_retention, parse_snapshot_timestamp, retention_cutoff, retention_days

logger = logging.getLogger(__name__)


@dataclass
class SnapshotMeta:
    snapshot_id: str
    test_id: str
    test_name: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    file_path: str
    file_size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "test_id": self.test_id,
            "test_name": self.test_name,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "time_range": {
                "from": self.started_at,
                "to": self.finished_at,
            },
            "file_size_bytes": self.file_size_bytes,
        }


def _snapshot_filename(snapshot_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in snapshot_id)
    return f"{safe}.json"


class SnapshotStoreProtocol(Protocol):
    def make_snapshot_id(self, test_id: str) -> str: ...

    async def save(self, snapshot_id: str, payload: dict[str, Any]) -> str: ...

    async def get(self, snapshot_id: str) -> dict[str, Any] | None: ...

    async def list_snapshots(self) -> list[SnapshotMeta]: ...

    async def prune(self) -> int: ...


class FileSnapshotStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._dir = Path(settings.snapshot_dir)

    def _ensure_dir(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def make_snapshot_id(self, test_id: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{test_id}_{ts}"

    async def save(self, snapshot_id: str, payload: dict[str, Any]) -> str:
        directory = self._ensure_dir()
        path = directory / _snapshot_filename(snapshot_id)
        path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
        await self.prune()
        return str(path)

    async def get(self, snapshot_id: str) -> dict[str, Any] | None:
        path = self._dir / _snapshot_filename(snapshot_id)
        data: dict[str, Any] | None = None
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read snapshot %s: %s", snapshot_id, exc)
                return None
        else:
            for candidate in self._dir.glob("*.json"):
                try:
                    candidate_data = json.loads(candidate.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if candidate_data.get("snapshot_id") == snapshot_id:
                    data = candidate_data
                    break
        if not data:
            return None
        created = parse_snapshot_timestamp(data.get("created_at"))
        if not is_within_retention(created, self.settings):
            return None
        return data

    async def list_snapshots(self) -> list[SnapshotMeta]:
        directory = self._ensure_dir()
        metas: list[SnapshotMeta] = []
        for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            created = parse_snapshot_timestamp(data.get("created_at"))
            if not is_within_retention(created, self.settings):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            metas.append(
                SnapshotMeta(
                    snapshot_id=data.get("snapshot_id", path.stem),
                    test_id=data.get("test_id", ""),
                    test_name=data.get("test_name", ""),
                    created_at=data.get("created_at", ""),
                    started_at=data.get("started_at"),
                    finished_at=data.get("finished_at"),
                    file_path=str(path),
                    file_size_bytes=size,
                )
            )
        return metas

    async def prune(self) -> int:
        directory = self._ensure_dir()
        cutoff = retention_cutoff(self.settings)
        removed = 0

        # Day-based rotation via DNS_DEBUG_DB_RETENTION_DAYS
        for path in directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            created = parse_snapshot_timestamp(data.get("created_at"))
            if created is not None and created < cutoff:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError as exc:
                    logger.warning("Failed to prune aged snapshot %s: %s", path, exc)

        # Secondary count cap (SNAPSHOT_RETENTION_COUNT)
        metas = await self.list_snapshots()
        limit = self.settings.snapshot_retention_count
        for meta in metas[limit:]:
            try:
                Path(meta.file_path).unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                logger.warning("Failed to prune snapshot %s: %s", meta.snapshot_id, exc)
        if removed:
            logger.info(
                "File snapshot retention prune completed",
                extra={
                    "event": "file_snapshot_prune",
                    "extra": {"removed": removed, "retention_days": retention_days(self.settings)},
                },
            )
        return removed


class PostgresSnapshotStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def make_snapshot_id(self, test_id: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{test_id}_{ts}"

    async def save(self, snapshot_id: str, payload: dict[str, Any]) -> str:
        from db.repository import persist_snapshot

        await persist_snapshot(payload)
        return f"postgres:{snapshot_id}"

    async def get(self, snapshot_id: str) -> dict[str, Any] | None:
        from db.connection import get_db_pool

        pool = get_db_pool()
        if pool is None:
            return None

        cutoff = retention_cutoff(self.settings)
        row = await pool.fetchrow(
            """
            SELECT snapshot_id, test_id, test_name, created_at, started_at, finished_at,
                   summary, panels
            FROM historical_snapshots
            WHERE snapshot_id = $1 AND created_at >= $2
            """,
            snapshot_id,
            cutoff,
        )
        if not row:
            return None
        return _row_to_payload(row)

    async def list_snapshots(self) -> list[SnapshotMeta]:
        from db.connection import get_db_pool

        pool = get_db_pool()
        if pool is None:
            return []

        cutoff = retention_cutoff(self.settings)
        rows = await pool.fetch(
            """
            SELECT snapshot_id, test_id, test_name, created_at, started_at, finished_at,
                   payload_size_bytes
            FROM historical_snapshots
            WHERE created_at >= $1
            ORDER BY created_at DESC
            """,
            cutoff,
        )
        return [
            SnapshotMeta(
                snapshot_id=row["snapshot_id"],
                test_id=row["test_id"],
                test_name=row["test_name"],
                created_at=_iso(row["created_at"]),
                started_at=_iso(row["started_at"]) if row["started_at"] else None,
                finished_at=_iso(row["finished_at"]) if row["finished_at"] else None,
                file_path="postgres",
                file_size_bytes=row["payload_size_bytes"] or 0,
            )
            for row in rows
        ]

    async def prune(self) -> int:
        # Time-based retention handled by cleanup job.
        return 0


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _row_to_payload(row: Any) -> dict[str, Any]:
    summary = row["summary"]
    panels = row["panels"]
    if isinstance(summary, str):
        summary = json.loads(summary)
    if isinstance(panels, str):
        panels = json.loads(panels)
    return {
        "snapshot_id": row["snapshot_id"],
        "test_id": row["test_id"],
        "test_name": row["test_name"],
        "created_at": _iso(row["created_at"]),
        "started_at": _iso(row["started_at"]) if row["started_at"] else None,
        "finished_at": _iso(row["finished_at"]) if row["finished_at"] else None,
        "summary": summary,
        "panels": panels,
    }


# Backward-compatible alias for tests
SnapshotStore = FileSnapshotStore

_store: SnapshotStoreProtocol | None = None


def get_snapshot_store() -> SnapshotStoreProtocol:
    global _store
    if _store is None:
        settings = get_settings()
        if settings.dns_debug_db_enabled:
            _store = PostgresSnapshotStore(settings)
        else:
            _store = FileSnapshotStore(settings)
    return _store


def reset_snapshot_store() -> None:
    """Reset singleton (tests)."""
    global _store
    _store = None


async def save_test_snapshot(test_id: str) -> str | None:
    """Build UI panel payloads for a completed test and persist a snapshot."""
    settings = get_settings()
    if not settings.snapshot_enabled:
        return None

    from stats_store import get_stats_store
    from ui.aggregator import UIAggregator
    from ui.filters import UIFilters

    store = get_stats_store()
    test = await store.get_test(test_id)
    if not test:
        return None

    snapshot_store = get_snapshot_store()
    snapshot_id = snapshot_store.make_snapshot_id(test_id)
    filters = UIFilters(test_id=test_id, view_mode="historical")
    aggregator = UIAggregator(settings)

    summary = test.summary or store.build_summary(test)
    panels = {
        "overview": await aggregator.overview(filters),
        "dns_latency": await aggregator.dns_latency(filters),
        "edns": await aggregator.edns(filters),
        "errors": await aggregator.errors(filters),
        "garbage": await aggregator.garbage(filters),
        "cache": await aggregator.cache(filters),
        "records": await aggregator.records(filters),
        "load": await aggregator.load(filters),
        "mtr": await aggregator.mtr(filters),
        "rankings": await aggregator.rankings(filters),
    }

    payload = {
        "snapshot_id": snapshot_id,
        "test_id": test.test_id,
        "test_name": test.test_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": test.started_at.isoformat() if test.started_at else None,
        "finished_at": test.finished_at.isoformat() if test.finished_at else None,
        "summary": summary.model_dump() if hasattr(summary, "model_dump") else summary,
        "panels": panels,
    }
    await snapshot_store.save(snapshot_id, payload)
    logger.info("Saved UI snapshot %s for test %s", snapshot_id, test_id)
    return snapshot_id
