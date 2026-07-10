import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Settings, get_settings

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


class SnapshotStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._dir = Path(settings.snapshot_dir)

    def _ensure_dir(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def make_snapshot_id(self, test_id: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{test_id}_{ts}"

    def save(self, snapshot_id: str, payload: dict[str, Any]) -> Path:
        directory = self._ensure_dir()
        path = directory / _snapshot_filename(snapshot_id)
        path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
        self.prune()
        return path

    def get(self, snapshot_id: str) -> dict[str, Any] | None:
        path = self._dir / _snapshot_filename(snapshot_id)
        if not path.is_file():
            # fallback: scan for matching snapshot_id inside files
            for candidate in self._dir.glob("*.json"):
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if data.get("snapshot_id") == snapshot_id:
                    return data
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read snapshot %s: %s", snapshot_id, exc)
            return None

    def list_snapshots(self) -> list[SnapshotMeta]:
        directory = self._ensure_dir()
        metas: list[SnapshotMeta] = []
        for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
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

    def prune(self) -> int:
        metas = self.list_snapshots()
        limit = self.settings.snapshot_retention_count
        removed = 0
        for meta in metas[limit:]:
            try:
                Path(meta.file_path).unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                logger.warning("Failed to prune snapshot %s: %s", meta.snapshot_id, exc)
        return removed


_store: SnapshotStore | None = None


def get_snapshot_store() -> SnapshotStore:
    global _store
    if _store is None:
        _store = SnapshotStore(get_settings())
    return _store


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
    snapshot_store.save(snapshot_id, payload)
    logger.info("Saved UI snapshot %s for test %s", snapshot_id, test_id)
    return snapshot_id
