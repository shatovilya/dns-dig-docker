import json
from pathlib import Path

import pytest

from config import Settings
from snapshot_store import SnapshotStore


@pytest.fixture
def snapshot_dir(tmp_path):
    return tmp_path / "snapshots"


@pytest.fixture
def store(snapshot_dir):
    settings = Settings(snapshot_dir=str(snapshot_dir), snapshot_retention_count=3)
    return SnapshotStore(settings)


def test_save_and_get(store):
    payload = {"snapshot_id": "t1_20250101T120000Z", "test_id": "t1", "panels": {"overview": {"total_queries": 5}}}
    store.save("t1_20250101T120000Z", payload)
    loaded = store.get("t1_20250101T120000Z")
    assert loaded is not None
    assert loaded["test_id"] == "t1"
    assert loaded["panels"]["overview"]["total_queries"] == 5


def test_list_snapshots_ordered(store):
    for i in range(2):
        sid = f"t{i}_2025010{i}T120000Z"
        store.save(sid, {"snapshot_id": sid, "test_id": f"t{i}", "test_name": f"Test {i}", "created_at": f"2025-01-0{i}"})
    metas = store.list_snapshots()
    assert len(metas) == 2
    assert metas[0].test_id in ("t0", "t1")


def test_list_snapshots_includes_time_range_and_size(store):
    payload = {
        "snapshot_id": "t1_20250101T120000Z",
        "test_id": "t1",
        "test_name": "Test 1",
        "created_at": "2025-01-01T12:00:00+00:00",
        "started_at": "2025-01-01T11:00:00+00:00",
        "finished_at": "2025-01-01T12:00:00+00:00",
    }
    store.save("t1_20250101T120000Z", payload)
    meta = store.list_snapshots()[0]
    d = meta.to_dict()
    assert d["time_range"]["from"] == "2025-01-01T11:00:00+00:00"
    assert d["time_range"]["to"] == "2025-01-01T12:00:00+00:00"
    assert d["file_size_bytes"] > 0


def test_prune_retention(store, snapshot_dir):
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        sid = f"t_{i}"
        path = snapshot_dir / f"{sid}.json"
        path.write_text(json.dumps({"snapshot_id": sid, "test_id": sid, "test_name": sid, "created_at": str(i)}))
    removed = store.prune()
    assert removed == 2
    assert len(store.list_snapshots()) == 3


def test_get_missing_returns_none(store):
    assert store.get("nonexistent") is None
