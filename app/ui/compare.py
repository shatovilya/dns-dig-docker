from typing import Any


def compute_delta(baseline: float | None, comparison: float | None) -> dict[str, Any]:
    """Absolute and percent change from baseline to comparison."""
    if baseline is None or comparison is None:
        return {
            "baseline": baseline,
            "comparison": comparison,
            "absolute": None,
            "percent": None,
            "note": "missing value",
        }
    absolute = round(comparison - baseline, 6)
    if baseline == 0:
        return {
            "baseline": baseline,
            "comparison": comparison,
            "absolute": absolute,
            "percent": None,
            "note": "baseline is zero",
        }
    percent = round((absolute / baseline) * 100.0, 4)
    return {
        "baseline": baseline,
        "comparison": comparison,
        "absolute": absolute,
        "percent": percent,
        "note": None,
    }


def compare_kpis(baseline: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    """Compare overview-style KPI payloads."""
    fields = [
        ("total_queries", "total_queries"),
        ("error_count", "error_count"),
        ("success_ratio", "success_ratio"),
        ("failed_ratio", "failed_ratio"),
        ("nxdomain_count", "nxdomain_count"),
    ]
    deltas: dict[str, Any] = {}
    for key, bkey in fields:
        deltas[key] = compute_delta(
            _as_float(baseline.get(bkey)),
            _as_float(comparison.get(bkey)),
        )
    return deltas


def compare_latency(baseline: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for key in ("p50", "p95", "p99"):
        deltas[key] = compute_delta(
            _as_float(baseline.get(key)),
            _as_float(comparison.get(key)),
        )
    deltas["sample_count"] = compute_delta(
        _as_float(baseline.get("sample_count")),
        _as_float(comparison.get("sample_count")),
    )
    return deltas


def compare_errors(baseline: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    deltas: dict[str, Any] = {
        "total_errors": compute_delta(
            _as_float(baseline.get("total_errors")),
            _as_float(comparison.get("total_errors")),
        ),
        "error_qps": compute_delta(
            _as_float(baseline.get("error_qps")),
            _as_float(comparison.get("error_qps")),
        ),
    }
    base_classes = baseline.get("by_error_class") or {}
    comp_classes = comparison.get("by_error_class") or {}
    class_deltas: dict[str, Any] = {}
    for label in sorted(set(base_classes) | set(comp_classes)):
        class_deltas[label] = compute_delta(
            _as_float(base_classes.get(label)),
            _as_float(comp_classes.get(label)),
        )
    deltas["by_error_class"] = class_deltas
    return deltas


def compare_garbage(baseline: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    base_ratio = (baseline.get("useful_vs_garbage_ratio") or {}).get("garbage_ratio")
    comp_ratio = (comparison.get("useful_vs_garbage_ratio") or {}).get("garbage_ratio")
    return {
        "garbage_ratio": compute_delta(_as_float(base_ratio), _as_float(comp_ratio)),
        "query_amplification_ratio": compute_delta(
            _as_float(baseline.get("query_amplification_ratio")),
            _as_float(comparison.get("query_amplification_ratio")),
        ),
    }


def compare_cache(baseline: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    return {
        "hit_ratio": compute_delta(
            _as_float(baseline.get("hit_ratio")),
            _as_float(comparison.get("hit_ratio")),
        ),
        "possible_cache_hits": compute_delta(
            _as_float(baseline.get("possible_cache_hits")),
            _as_float(comparison.get("possible_cache_hits")),
        ),
        "repeat_query_keys": compute_delta(
            _as_float(baseline.get("repeat_query_keys")),
            _as_float(comparison.get("repeat_query_keys")),
        ),
    }


def compare_load(baseline: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    return {
        "actual_qps": compute_delta(
            _as_float(baseline.get("actual_qps")),
            _as_float(comparison.get("actual_qps")),
        ),
        "error_rate": compute_delta(
            _as_float(baseline.get("error_rate")),
            _as_float(comparison.get("error_rate")),
        ),
        "saturation_ratio": compute_delta(
            _as_float(baseline.get("saturation_ratio")),
            _as_float(comparison.get("saturation_ratio")),
        ),
        "success_rate": compute_delta(
            _as_float(baseline.get("success_rate")),
            _as_float(comparison.get("success_rate")),
        ),
    }


def compare_rankings(baseline: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    def _top_error_rate(panel: dict[str, Any], key: str) -> float | None:
        items = panel.get(key) or []
        if not items:
            return None
        return _as_float(items[0].get("error_rate"))

    return {
        "domains_top_error_rate": compute_delta(
            _top_error_rate(baseline, "domains"),
            _top_error_rate(comparison, "domains"),
        ),
        "resolvers_top_error_rate": compute_delta(
            _top_error_rate(baseline, "resolvers"),
            _top_error_rate(comparison, "resolvers"),
        ),
    }


def compare_error_matrix(
    baseline: dict[str, Any], comparison: dict[str, Any]
) -> dict[str, Any]:
    base_matrix = baseline.get("resolver_error_matrix") or {}
    comp_matrix = comparison.get("resolver_error_matrix") or {}
    resolvers = sorted(set(base_matrix) | set(comp_matrix))
    deltas: dict[str, Any] = {}
    for resolver in resolvers:
        base_row = base_matrix.get(resolver) or {}
        comp_row = comp_matrix.get(resolver) or {}
        classes = sorted(set(base_row) | set(comp_row))
        deltas[resolver] = {
            cls: compute_delta(_as_float(base_row.get(cls)), _as_float(comp_row.get(cls)))
            for cls in classes
        }
    return deltas


def build_compare_response(
    baseline_overview: dict[str, Any],
    comparison_overview: dict[str, Any],
    baseline_latency: dict[str, Any],
    comparison_latency: dict[str, Any],
    baseline_garbage: dict[str, Any],
    comparison_garbage: dict[str, Any],
    baseline_errors: dict[str, Any],
    comparison_errors: dict[str, Any],
    baseline_filters: dict[str, Any],
    comparison_filters: dict[str, Any],
    baseline_cache: dict[str, Any] | None = None,
    comparison_cache: dict[str, Any] | None = None,
    baseline_load: dict[str, Any] | None = None,
    comparison_load: dict[str, Any] | None = None,
    baseline_rankings: dict[str, Any] | None = None,
    comparison_rankings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_cache = baseline_cache or {}
    comparison_cache = comparison_cache or {}
    baseline_load = baseline_load or {}
    comparison_load = comparison_load or {}
    baseline_rankings = baseline_rankings or {}
    comparison_rankings = comparison_rankings or {}
    return {
        "view_mode": "compare",
        "baseline": {
            "filters_applied": baseline_filters,
            "overview": baseline_overview,
            "dns_latency": baseline_latency,
            "garbage": baseline_garbage,
            "errors": baseline_errors,
            "cache": baseline_cache,
            "load": baseline_load,
            "rankings": baseline_rankings,
        },
        "comparison": {
            "filters_applied": comparison_filters,
            "overview": comparison_overview,
            "dns_latency": comparison_latency,
            "garbage": comparison_garbage,
            "errors": comparison_errors,
            "cache": comparison_cache,
            "load": comparison_load,
            "rankings": comparison_rankings,
        },
        "deltas": {
            "overview": compare_kpis(baseline_overview, comparison_overview),
            "dns_latency": compare_latency(baseline_latency, comparison_latency),
            "garbage": compare_garbage(baseline_garbage, comparison_garbage),
            "errors": compare_errors(baseline_errors, comparison_errors),
            "cache": compare_cache(baseline_cache, comparison_cache),
            "load": compare_load(baseline_load, comparison_load),
            "rankings": compare_rankings(baseline_rankings, comparison_rankings),
            "resolver_error_matrix": compare_error_matrix(baseline_errors, comparison_errors),
        },
        "time_series_overlay": {
            "baseline": baseline_latency.get("time_buckets") or [],
            "comparison": comparison_latency.get("time_buckets") or [],
        },
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
