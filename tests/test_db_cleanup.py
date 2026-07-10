from datetime import datetime, timedelta, timezone

import pytest

from config import Settings
from db.cleanup import _parse_delete_count, run_retention_cleanup
from retention import retention_cutoff


def test_parse_delete_count():
    assert _parse_delete_count("DELETE 5") == 5
    assert _parse_delete_count("DELETE 0") == 0
    assert _parse_delete_count("UPDATE 1") == 0


def test_retention_cutoff_default_seven_days():
    settings = Settings(dns_debug_db_retention_days=7)
    cutoff = retention_cutoff(settings)
    expected = datetime.now(timezone.utc) - timedelta(days=7)
    assert abs((cutoff - expected).total_seconds()) < 2


@pytest.mark.asyncio
async def test_run_retention_cleanup_no_pool():
    settings = Settings(dns_debug_db_enabled=False)
    result = await run_retention_cleanup(settings)
    assert result.deleted_snapshots == 0
    assert result.deleted_mtr_orphans == 0
