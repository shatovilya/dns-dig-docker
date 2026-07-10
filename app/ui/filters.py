from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Query

from config import Settings, get_settings
from stats_store import QueryAttempt, TestState

ViewMode = Literal["live", "historical", "compare"]


@dataclass
class UIFilters:
    test_id: str | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None
    resolve_mode: str | None = None
    query_type: str | None = None
    view_mode: ViewMode = "live"
    snapshot_id: str | None = None
    baseline_from_ts: datetime | None = None
    baseline_to_ts: datetime | None = None
    compare_from_ts: datetime | None = None
    compare_to_ts: datetime | None = None
    baseline_snapshot_id: str | None = None
    compare_snapshot_id: str | None = None

    def applied(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "from": self.from_ts.isoformat() if self.from_ts else None,
            "to": self.to_ts.isoformat() if self.to_ts else None,
            "resolve_mode": self.resolve_mode,
            "query_type": self.query_type,
            "view_mode": self.view_mode,
            "snapshot_id": self.snapshot_id,
            "baseline_from": self.baseline_from_ts.isoformat() if self.baseline_from_ts else None,
            "baseline_to": self.baseline_to_ts.isoformat() if self.baseline_to_ts else None,
            "compare_from": self.compare_from_ts.isoformat() if self.compare_from_ts else None,
            "compare_to": self.compare_to_ts.isoformat() if self.compare_to_ts else None,
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "compare_snapshot_id": self.compare_snapshot_id,
        }


@dataclass
class CompareFilters:
    test_id: str | None = None
    resolve_mode: str | None = None
    query_type: str | None = None
    baseline_from_ts: datetime | None = None
    baseline_to_ts: datetime | None = None
    compare_from_ts: datetime | None = None
    compare_to_ts: datetime | None = None
    baseline_snapshot_id: str | None = None
    compare_snapshot_id: str | None = None
    baseline_test_id: str | None = None
    compare_test_id: str | None = None
    baseline_resolve_mode: str | None = None
    compare_resolve_mode: str | None = None

    def baseline_ui_filters(self) -> UIFilters:
        test_id = self.baseline_test_id or self.test_id
        resolve_mode = self.baseline_resolve_mode or self.resolve_mode
        if self.baseline_snapshot_id:
            return UIFilters(
                test_id=test_id,
                resolve_mode=resolve_mode,
                query_type=self.query_type,
                view_mode="historical",
                snapshot_id=self.baseline_snapshot_id,
            )
        return UIFilters(
            test_id=test_id,
            from_ts=self.baseline_from_ts,
            to_ts=self.baseline_to_ts,
            resolve_mode=resolve_mode,
            query_type=self.query_type,
            view_mode="historical",
        )

    def comparison_ui_filters(self) -> UIFilters:
        test_id = self.compare_test_id or self.test_id
        resolve_mode = self.compare_resolve_mode or self.resolve_mode
        if self.compare_snapshot_id:
            return UIFilters(
                test_id=test_id,
                resolve_mode=resolve_mode,
                query_type=self.query_type,
                view_mode="historical",
                snapshot_id=self.compare_snapshot_id,
            )
        return UIFilters(
            test_id=test_id,
            from_ts=self.compare_from_ts,
            to_ts=self.compare_to_ts,
            resolve_mode=resolve_mode,
            query_type=self.query_type,
            view_mode="historical",
        )

    def applied(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "resolve_mode": self.resolve_mode,
            "query_type": self.query_type,
            "baseline_from": self.baseline_from_ts.isoformat() if self.baseline_from_ts else None,
            "baseline_to": self.baseline_to_ts.isoformat() if self.baseline_to_ts else None,
            "compare_from": self.compare_from_ts.isoformat() if self.compare_from_ts else None,
            "compare_to": self.compare_to_ts.isoformat() if self.compare_to_ts else None,
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "compare_snapshot_id": self.compare_snapshot_id,
            "baseline_test_id": self.baseline_test_id,
            "compare_test_id": self.compare_test_id,
            "baseline_resolve_mode": self.baseline_resolve_mode,
            "compare_resolve_mode": self.compare_resolve_mode,
        }


def _parse_iso_datetime(value: str | None) -> datetime | None:
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


def _parse_view_mode(value: str | None) -> ViewMode:
    if value in ("live", "historical", "compare"):
        return value  # type: ignore[return-value]
    return "live"


def parse_ui_filters(
    test_id: str | None = Query(default=None),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    resolve_mode: str | None = Query(default=None),
    query_type: str | None = Query(default=None),
    view_mode: str | None = Query(default=None),
    snapshot_id: str | None = Query(default=None),
) -> UIFilters:
    return UIFilters(
        test_id=test_id.strip() if test_id else None,
        from_ts=_parse_iso_datetime(from_),
        to_ts=_parse_iso_datetime(to),
        resolve_mode=resolve_mode.strip() if resolve_mode else None,
        query_type=query_type.upper() if query_type else None,
        view_mode=_parse_view_mode(view_mode),
        snapshot_id=snapshot_id.strip() if snapshot_id else None,
    )


def parse_compare_filters(
    test_id: str | None = Query(default=None),
    resolve_mode: str | None = Query(default=None),
    query_type: str | None = Query(default=None),
    baseline_from: str | None = Query(default=None),
    baseline_to: str | None = Query(default=None),
    compare_from: str | None = Query(default=None),
    compare_to: str | None = Query(default=None),
    baseline_snapshot_id: str | None = Query(default=None),
    compare_snapshot_id: str | None = Query(default=None),
    baseline_test_id: str | None = Query(default=None),
    compare_test_id: str | None = Query(default=None),
    baseline_resolve_mode: str | None = Query(default=None),
    compare_resolve_mode: str | None = Query(default=None),
) -> CompareFilters:
    return CompareFilters(
        test_id=test_id.strip() if test_id else None,
        resolve_mode=resolve_mode.strip() if resolve_mode else None,
        query_type=query_type.upper() if query_type else None,
        baseline_from_ts=_parse_iso_datetime(baseline_from),
        baseline_to_ts=_parse_iso_datetime(baseline_to),
        compare_from_ts=_parse_iso_datetime(compare_from),
        compare_to_ts=_parse_iso_datetime(compare_to),
        baseline_snapshot_id=baseline_snapshot_id.strip() if baseline_snapshot_id else None,
        compare_snapshot_id=compare_snapshot_id.strip() if compare_snapshot_id else None,
        baseline_test_id=baseline_test_id.strip() if baseline_test_id else None,
        compare_test_id=compare_test_id.strip() if compare_test_id else None,
        baseline_resolve_mode=baseline_resolve_mode.strip() if baseline_resolve_mode else None,
        compare_resolve_mode=compare_resolve_mode.strip() if compare_resolve_mode else None,
    )


def select_tests(tests: list[TestState], filters: UIFilters) -> list[TestState]:
    if filters.test_id:
        return [t for t in tests if t.test_id == filters.test_id]
    return tests


def filter_attempts(tests: list[TestState], filters: UIFilters) -> list[QueryAttempt]:
    attempts: list[QueryAttempt] = []
    for test in tests:
        for event in test.events:
            if not isinstance(event, QueryAttempt):
                continue
            if filters.from_ts and event.timestamp < filters.from_ts:
                continue
            if filters.to_ts and event.timestamp > filters.to_ts:
                continue
            if filters.resolve_mode and event.resolve_mode != filters.resolve_mode:
                continue
            if filters.query_type and event.query_type.upper() != filters.query_type:
                continue
            attempts.append(event)
    return attempts


def collect_warnings(tests: list[TestState], settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    warnings: list[str] = []
    for test in tests:
        if len(test.events) >= settings.event_buffer_size:
            warnings.append("event_buffer_truncated")
            break
    snapshot_store_count = 0
    if settings.snapshot_enabled:
        try:
            from snapshot_store import get_snapshot_store

            snapshot_store_count = len(get_snapshot_store().list_snapshots())
            if snapshot_store_count >= settings.snapshot_retention_count:
                warnings.append("snapshot_retention_at_limit")
        except OSError:
            pass
    return list(dict.fromkeys(warnings))


def resolve_data_source(filters: UIFilters) -> str:
    if filters.snapshot_id:
        return "snapshot"
    if filters.view_mode == "historical":
        return "event_buffer"
    return "live_memory"


def envelope(filters: UIFilters, settings: Settings | None = None, **payload: Any) -> dict[str, Any]:
    settings = settings or get_settings()
    warnings = payload.pop("warnings", None)
    is_stale = payload.pop("is_stale", False)
    if warnings is None:
        warnings = []

    time_range = {
        "from": filters.from_ts.isoformat() if filters.from_ts else None,
        "to": filters.to_ts.isoformat() if filters.to_ts else None,
    }
    if filters.snapshot_id:
        time_range["snapshot_id"] = filters.snapshot_id

    snapshot_count = 0
    if settings.snapshot_enabled:
        try:
            from snapshot_store import get_snapshot_store

            snapshot_count = len(get_snapshot_store().list_snapshots())
        except OSError:
            snapshot_count = 0

    return {
        "last_update": datetime.now(timezone.utc).isoformat(),
        "test_id": filters.test_id,
        "view_mode": filters.view_mode,
        "data_source": resolve_data_source(filters),
        "time_range": time_range,
        "filters_applied": filters.applied(),
        "retention": {
            "event_buffer_size": settings.event_buffer_size,
            "snapshot_count": snapshot_count,
            "snapshot_retention_count": settings.snapshot_retention_count,
            "mtr_max_history": settings.mtr_max_history,
        },
        "warnings": warnings,
        "is_stale": is_stale,
        **payload,
    }
