import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from config import Settings
from snapshot_store import FileSnapshotStore, get_snapshot_store, reset_snapshot_store


@pytest.fixture
def snapshot_dir(tmp_path):
    return tmp_path / "snapshots"


@pytest.fixture
def store(snapshot_dir):
    settings = Settings(snapshot_dir=str(snapshot_dir), snapshot_retention_count=3, dns_debug_db_enabled=False)
    return FileSnapshotStore(settings)


@pytest.mark.asyncio
async def test_save_and_get(store):
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "snapshot_id": "t1_20250101T120000Z",
        "test_id": "t1",
        "created_at": now,
        "panels": {"overview": {"total_queries": 5}},
    }
    await store.save("t1_20250101T120000Z", payload)
    loaded = await store.get("t1_20250101T120000Z")
    assert loaded is not None
    assert loaded["test_id"] == "t1"
    assert loaded["panels"]["overview"]["total_queries"] == 5


@pytest.mark.asyncio
async def test_list_snapshots_ordered(store):
    now = datetime.now(timezone.utc).isoformat()
    for i in range(2):
        sid = f"t{i}_2025010{i}T120000Z"
        await store.save(
            sid,
            {"snapshot_id": sid, "test_id": f"t{i}", "test_name": f"Test {i}", "created_at": now},
        )
    metas = await store.list_snapshots()
    assert len(metas) == 2
    assert metas[0].test_id in ("t0", "t1")


@pytest.mark.asyncio
async def test_list_snapshots_includes_time_range_and_size(store):
    now = datetime.now(timezone.utc)
    started = now - timedelta(hours=1)
    payload = {
        "snapshot_id": "t1_20250101T120000Z",
        "test_id": "t1",
        "test_name": "Test 1",
        "created_at": now.isoformat(),
        "started_at": started.isoformat(),
        "finished_at": now.isoformat(),
    }
    await store.save("t1_20250101T120000Z", payload)
    meta = (await store.list_snapshots())[0]
    d = meta.to_dict()
    assert d["time_range"]["from"] == started.isoformat()
    assert d["time_range"]["to"] == now.isoformat()
    assert d["file_size_bytes"] > 0


@pytest.mark.asyncio
async def test_prune_retention(store, snapshot_dir):
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        sid = f"t_{i}"
        path = snapshot_dir / f"{sid}.json"
        path.write_text(
            json.dumps({"snapshot_id": sid, "test_id": sid, "test_name": sid, "created_at": now})
        )
    removed = await store.prune()
    assert removed == 2
    assert len(await store.list_snapshots()) == 3


@pytest.mark.asyncio
async def test_prune_removes_aged_files(store, snapshot_dir):
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    old_ts = "2020-01-01T12:00:00+00:00"
    path = snapshot_dir / "old.json"
    path.write_text(
        json.dumps(
            {
                "snapshot_id": "old",
                "test_id": "old",
                "test_name": "old",
                "created_at": old_ts,
            }
        )
    )
    removed = await store.prune()
    assert removed >= 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get("nonexistent") is None


def test_get_snapshot_store_file_backend(monkeypatch):
    reset_snapshot_store()
    monkeypatch.setenv("DNS_DEBUG_DB_ENABLED", "false")
    from config import get_settings

    get_settings.cache_clear()
    store = get_snapshot_store()
    assert store.__class__.__name__ == "FileSnapshotStore"
    reset_snapshot_store()
    get_settings.cache_clear()
