from datetime import datetime, timezone

from config import Settings
from ui.aggregator import derive_ui_health
from ui.filters import UIFilters, envelope, parse_ui_filters, resolve_data_source, CompareFilters


def test_derive_ui_health_ok():
    settings = Settings(diagnosis_error_rate_threshold=0.05)
    result = derive_ui_health(
        settings,
        total_queries=100,
        error_count=1,
        success_ratio=0.99,
        p95_ms=20,
        garbage_ratio=0.02,
        error_qps=0.1,
        mtr_verdict="ok",
        mtr_enabled=True,
    )
    assert result["level"] == "ok"


def test_derive_ui_health_critical_mtr():
    settings = Settings()
    result = derive_ui_health(
        settings,
        total_queries=100,
        error_count=5,
        success_ratio=0.95,
        p95_ms=30,
        garbage_ratio=0.05,
        error_qps=1.0,
        mtr_verdict="packet_loss_suspected",
        mtr_enabled=True,
    )
    assert result["level"] == "critical"


def test_compare_filters_resolve_mode_dims():
    cf = CompareFilters(
        baseline_resolve_mode="system",
        compare_resolve_mode="absolute_fqdn",
        baseline_test_id="t1",
        compare_test_id="t2",
    )
    assert cf.baseline_ui_filters().resolve_mode == "system"
    assert cf.comparison_ui_filters().resolve_mode == "absolute_fqdn"
    assert cf.baseline_ui_filters().test_id == "t1"
    assert cf.comparison_ui_filters().test_id == "t2"


def test_envelope_includes_view_mode_and_retention():
    filters = UIFilters(test_id="t1", view_mode="historical")
    settings = Settings(event_buffer_size=500, snapshot_retention_count=10, mtr_max_history=5)
    result = envelope(filters, settings, total_queries=1)
    assert result["view_mode"] == "historical"
    assert result["data_source"] == "event_buffer"
    assert result["retention"]["event_buffer_size"] == 500
    assert "time_range" in result
    assert "warnings" in result


def test_resolve_data_source_snapshot():
    filters = UIFilters(snapshot_id="snap1", view_mode="historical")
    assert resolve_data_source(filters) == "snapshot"


def test_resolve_data_source_live():
    filters = UIFilters(view_mode="live")
    assert resolve_data_source(filters) == "live_memory"


def test_applied_includes_view_mode():
    filters = UIFilters(view_mode="compare", snapshot_id="s1")
    applied = filters.applied()
    assert applied["view_mode"] == "compare"
    assert applied["snapshot_id"] == "s1"
