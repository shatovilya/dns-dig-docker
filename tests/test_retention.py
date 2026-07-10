from datetime import datetime, timedelta, timezone

import pytest

from config import Settings
from retention import is_within_retention, retention_cutoff, retention_days


def test_retention_days_minimum_one():
    settings = Settings.model_construct(dns_debug_db_retention_days=0)
    assert retention_days(settings) == 1


def test_retention_days_validator_rejects_out_of_range():
    with pytest.raises(ValueError):
        Settings(dns_debug_db_retention_days=0)
    with pytest.raises(ValueError):
        Settings(dns_debug_db_retention_days=400)


def test_retention_cutoff_uses_configured_days():
    settings = Settings(dns_debug_db_retention_days=14)
    cutoff = retention_cutoff(settings)
    expected = datetime.now(timezone.utc) - timedelta(days=14)
    assert abs((cutoff - expected).total_seconds()) < 2


def test_is_within_retention():
    settings = Settings(dns_debug_db_retention_days=7)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    old = datetime.now(timezone.utc) - timedelta(days=30)
    assert is_within_retention(recent, settings) is True
    assert is_within_retention(old, settings) is False
