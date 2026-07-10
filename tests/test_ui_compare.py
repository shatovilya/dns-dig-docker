import pytest

from ui.compare import build_compare_response, compare_kpis, compute_delta, compare_cache, compare_load


def test_compare_cache_delta():
    baseline = {"hit_ratio": 0.1, "possible_cache_hits": 5}
    comparison = {"hit_ratio": 0.15, "possible_cache_hits": 7}
    deltas = compare_cache(baseline, comparison)
    assert deltas["hit_ratio"]["absolute"] == pytest.approx(0.05, abs=0.001)


def test_compare_load_delta():
    baseline = {"error_rate": 0.05, "actual_qps": 10}
    comparison = {"error_rate": 0.03, "actual_qps": 12}
    deltas = compare_load(baseline, comparison)
    assert deltas["error_rate"]["absolute"] == pytest.approx(-0.02, abs=0.001)
    assert deltas["actual_qps"]["absolute"] == 2.0


def test_compute_delta_normal():
    result = compute_delta(100.0, 120.0)
    assert result["absolute"] == 20.0
    assert result["percent"] == 20.0
    assert result["note"] is None


def test_compute_delta_zero_baseline():
    result = compute_delta(0.0, 50.0)
    assert result["absolute"] == 50.0
    assert result["percent"] is None
    assert result["note"] == "baseline is zero"


def test_compute_delta_missing():
    result = compute_delta(None, 10.0)
    assert result["absolute"] is None
    assert result["note"] == "missing value"


def test_compare_kpis():
    baseline = {"total_queries": 100, "error_count": 5, "success_ratio": 0.95}
    comparison = {"total_queries": 120, "error_count": 8, "success_ratio": 0.93}
    deltas = compare_kpis(baseline, comparison)
    assert deltas["error_count"]["absolute"] == 3.0
    assert deltas["success_ratio"]["absolute"] == pytest.approx(-0.02, abs=0.001)


def test_build_compare_response_structure():
    baseline_ov = {"total_queries": 10, "error_count": 1, "success_ratio": 0.9, "failed_ratio": 0.1, "nxdomain_count": 0}
    comparison_ov = {"total_queries": 10, "error_count": 2, "success_ratio": 0.8, "failed_ratio": 0.2, "nxdomain_count": 0}
    baseline_lat = {"p50": 10, "p95": 20, "p99": 30, "sample_count": 10, "time_buckets": []}
    comparison_lat = {"p50": 12, "p95": 22, "p99": 32, "sample_count": 10, "time_buckets": []}
    baseline_garbage = {"useful_vs_garbage_ratio": {"garbage_ratio": 0.1}, "query_amplification_ratio": 1.5}
    comparison_garbage = {"useful_vs_garbage_ratio": {"garbage_ratio": 0.2}, "query_amplification_ratio": 2.0}
    baseline_errors = {
        "total_errors": 3,
        "error_qps": 0.5,
        "by_error_class": {"timeout": 2, "nxdomain": 1},
        "resolver_error_matrix": {"system": {"timeout": 2}},
    }
    comparison_errors = {
        "total_errors": 5,
        "error_qps": 0.8,
        "by_error_class": {"timeout": 3, "nxdomain": 2},
        "resolver_error_matrix": {"system": {"timeout": 3, "nxdomain": 1}},
    }
    baseline_cache = {"hit_ratio": 0.1, "possible_cache_hits": 5, "repeat_query_keys": 2}
    comparison_cache = {"hit_ratio": 0.2, "possible_cache_hits": 8, "repeat_query_keys": 3}
    baseline_load = {"actual_qps": 10, "error_rate": 0.05, "saturation_ratio": 0.8, "success_rate": 0.95}
    comparison_load = {"actual_qps": 12, "error_rate": 0.08, "saturation_ratio": 0.9, "success_rate": 0.92}
    baseline_rankings = {"domains": [{"key": "a.example", "error_rate": 0.1}]}
    comparison_rankings = {"domains": [{"key": "a.example", "error_rate": 0.2}]}
    result = build_compare_response(
        baseline_ov,
        comparison_ov,
        baseline_lat,
        comparison_lat,
        baseline_garbage,
        comparison_garbage,
        baseline_errors,
        comparison_errors,
        {"from": "a"},
        {"from": "b"},
        baseline_cache,
        comparison_cache,
        baseline_load,
        comparison_load,
        baseline_rankings,
        comparison_rankings,
    )
    assert result["view_mode"] == "compare"
    assert "deltas" in result
    assert result["deltas"]["overview"]["error_count"]["absolute"] == 1.0
    assert result["baseline"]["errors"]["total_errors"] == 3
    assert result["comparison"]["errors"]["total_errors"] == 5
    assert result["deltas"]["errors"]["total_errors"]["absolute"] == 2.0
    assert result["deltas"]["cache"]["hit_ratio"]["absolute"] == pytest.approx(0.1, abs=0.001)
    assert result["deltas"]["load"]["error_rate"]["absolute"] == pytest.approx(0.03, abs=0.001)
    assert "resolver_error_matrix" in result["deltas"]
    assert "time_series_overlay" in result
