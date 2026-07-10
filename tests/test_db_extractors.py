from db.extractors import (
    extract_chart_buckets,
    extract_domain_aggregates,
    extract_edns_aggregates,
    extract_error_aggregates,
    extract_resolver_aggregates,
    extract_run_aggregate,
    extract_test_run,
)


def _sample_panels() -> dict:
    return {
        "overview": {
            "total_queries": 100,
            "success_ratio": 0.95,
            "kpi_extras": {
                "error_rate": 0.05,
                "nxdomain_rate": 0.01,
                "noisy_ratio": 0.02,
                "cache_hit_ratio": 0.1,
                "p50_ms": 10.0,
                "p95_ms": 50.0,
                "p99_ms": 80.0,
            },
        },
        "dns_latency": {"sample_count": 90, "p50": 10.0, "p95": 50.0, "p99": 80.0, "time_buckets": []},
        "rankings": {
            "resolvers": [{"key": "system", "errors": 2, "error_rate": 0.02, "avg_latency_ms": 12.0}],
            "domains": [{"key": "example.com", "errors": 1, "error_rate": 0.01, "avg_latency_ms": 11.0}],
        },
        "errors": {
            "by_error_class": {"timeout": 3},
            "by_resolver": {"system": 3},
            "by_domain": {"example.com": 1},
            "resolver_error_matrix": {"system": {"timeout": 3}},
        },
        "edns": {"levels": [{"level": "edns0", "queries": 100, "errors": 5}]},
        "records": {"records": [{"fqdn": "svc.local", "errors": 0, "avg_latency_ms": 9.0, "status": "success"}]},
        "garbage": {"top_noisy_domains": {"noise.example": 4}},
        "load": {"time_series": []},
    }


def test_extract_run_aggregate():
    agg = extract_run_aggregate(_sample_panels())
    assert agg["total_queries"] == 100
    assert agg["success_rate"] == 0.95
    assert agg["latency_p95_ms"] == 50.0
    assert agg["sample_count"] == 90


def test_extract_test_run():
    payload = {
        "snapshot_id": "t1_20250101T120000Z",
        "test_id": "t1",
        "test_name": "Test",
        "started_at": "2025-01-01T10:00:00+00:00",
        "finished_at": "2025-01-01T10:01:00+00:00",
    }
    summary = {"status": "completed", "config": {"resolve_modes": ["system", "absolute_fqdn"]}}
    run = extract_test_run(payload, summary)
    assert run["test_id"] == "t1"
    assert run["status"] == "completed"
    assert run["mode"] == "system,absolute_fqdn"
    assert run["duration_ms"] == 60000.0


def test_extract_resolver_and_domain_aggregates():
    panels = _sample_panels()
    resolvers = extract_resolver_aggregates(panels)
    domains = extract_domain_aggregates(panels)
    assert any(r["resolver"] == "system" for r in resolvers)
    assert any(d["fqdn"] == "example.com" for d in domains)
    assert any(d["fqdn"] == "svc.local" for d in domains)


def test_extract_error_and_edns_aggregates():
    panels = _sample_panels()
    errors = extract_error_aggregates(panels)
    edns = extract_edns_aggregates(panels)
    assert any(e["error_type"] == "timeout" for e in errors)
    assert edns[0]["edns_level"] == "edns0"
    assert edns[0]["query_count"] == 100


def test_extract_chart_buckets_parses_timestamps():
    panels = {
        "dns_latency": {
            "time_buckets": [{"bucket_start": "2025-01-01T10:00:00+00:00", "count": 5}],
        },
        "load": {
            "time_series": [{"timestamp": "2025-01-01T10:00:10+00:00", "qps": 1.2}],
        },
    }
    buckets = extract_chart_buckets(panels)
    assert len(buckets) == 2
    assert buckets[0]["panel"] == "dns_latency"
    assert buckets[1]["panel"] == "load"
