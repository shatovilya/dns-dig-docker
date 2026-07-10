"""Shared historical data retention helpers (days-based rotation)."""

from datetime import datetime, timedelta, timezone

from config import Settings, get_settings


def retention_days(settings: Settings | None = None) -> int:
    """Configured retention window in days (minimum 1)."""
    settings = settings or get_settings()
    return max(1, settings.dns_debug_db_retention_days)


def retention_cutoff(settings: Settings | None = None) -> datetime:
    """UTC cutoff: data older than this timestamp is outside retention."""
    return datetime.now(timezone.utc) - timedelta(days=retention_days(settings))


def parse_snapshot_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_within_retention(ts: datetime | None, settings: Settings | None = None) -> bool:
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= retention_cutoff(settings)
