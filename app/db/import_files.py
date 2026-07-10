import json
import logging
from pathlib import Path

from config import get_settings
from db.connection import get_db_pool
from db.repository import persist_snapshot

logger = logging.getLogger(__name__)


async def import_file_snapshots() -> int:
    """Import existing JSON snapshots into PostgreSQL (idempotent)."""
    settings = get_settings()
    if not settings.dns_debug_db_enabled or not settings.dns_debug_db_import_files_on_startup:
        return 0
    if get_db_pool() is None:
        return 0

    directory = Path(settings.snapshot_dir)
    if not directory.is_dir():
        return 0

    imported = 0
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping invalid snapshot file %s: %s", path, exc)
            continue
        snapshot_id = data.get("snapshot_id")
        if not snapshot_id:
            continue
        await persist_snapshot(data)
        imported += 1

    if imported:
        logger.info(
            "Imported file snapshots into PostgreSQL",
            extra={"event": "db_import_snapshots", "extra": {"count": imported}},
        )
    return imported
