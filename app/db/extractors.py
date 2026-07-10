import json
from datetime import datetime, timezone
from typing import Any


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def extract_run_aggregate(panels: dict[str, Any]) -> dict[str, Any]:
    overview = panels.get("overview") or {}
    dns_latency = panels.get("dns_latency") or {}
    kpi = overview.get("kpi_extras") or {}
    return {
        "total_queries": overview.get("total_queries"),
        "success_rate": overview.get("success_ratio"),
        "error_rate": kpi.get("error_rate"),
        "nxdomain_rate": kpi.get("nxdomain_rate"),
        "noisy_ratio": kpi.get("noisy_ratio"),
        "cache_hit_ratio": kpi.get("cache_hit_ratio"),
        "latency_p50_ms": kpi.get("p50_ms") or dns_latency.get("p50"),
        "latency_p95_ms": kpi.get("p95_ms") or dns_latency.get("p95"),
        "latency_p99_ms": kpi.get("p99_ms") or dns_latency.get("p99"),
        "sample_count": dns_latency.get("sample_count"),
    }


def extract_test_run(payload: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    started = _parse_ts(payload.get("started_at"))
    finished = _parse_ts(payload.get("finished_at"))
    duration_ms = None
    if started and finished:
        duration_ms = (finished - started).total_seconds() * 1000.0

    mode = None
    if isinstance(summary, dict):
        config = summary.get("config") or {}
        modes = config.get("resolve_modes") or config.get("resolve_modes_list")
        if modes:
            mode = ",".join(modes) if isinstance(modes, list) else str(modes)

    status = None
    if isinstance(summary, dict):
        status = summary.get("status")

    return {
        "snapshot_id": payload["snapshot_id"],
        "test_id": payload.get("test_id", ""),
        "test_name": payload.get("test_name", ""),
        "status": status,
        "mode": mode,
        "started_at": started,
        "finished_at": finished,
        "duration_ms": duration_ms,
        "metadata": {"summary_keys": list(summary.keys()) if isinstance(summary, dict) else []},
    }


def extract_resolver_aggregates(panels: dict[str, Any]) -> list[dict[str, Any]]:
    rankings = panels.get("rankings") or {}
    errors = panels.get("errors") or {}
    cache = panels.get("cache") or {}
    by_mode_cache = (cache.get("by_resolve_mode") or {}) if isinstance(cache, dict) else {}
    rows: list[dict[str, Any]] = []

    for item in rankings.get("resolvers") or []:
        resolver = item.get("key")
        if not resolver:
            continue
        cache_note = by_mode_cache.get(resolver) or {}
        rows.append(
            {
                "resolver": resolver,
                "availability": round(1.0 - float(item.get("error_rate") or 0), 4),
                "latency_p50_ms": item.get("avg_latency_ms"),
                "latency_p95_ms": item.get("avg_latency_ms"),
                "error_count": item.get("errors"),
                "cache_efficiency": None,
                "edns_counters": {},
            }
        )

    for resolver, count in (errors.get("by_resolver") or {}).items():
        if any(r["resolver"] == resolver for r in rows):
            continue
        rows.append(
            {
                "resolver": resolver,
                "availability": None,
                "latency_p50_ms": None,
                "latency_p95_ms": None,
                "error_count": count,
                "cache_efficiency": None,
                "edns_counters": {},
            }
        )
    return rows


def extract_domain_aggregates(panels: dict[str, Any]) -> list[dict[str, Any]]:
    rankings = panels.get("rankings") or {}
    records = panels.get("records") or {}
    garbage = panels.get("garbage") or {}
    noisy_domains = garbage.get("top_noisy_domains") or {}
    rows: list[dict[str, Any]] = []

    for item in rankings.get("domains") or []:
        fqdn = item.get("key")
        if not fqdn:
            continue
        noisy_markers = {}
        if fqdn in noisy_domains:
            noisy_markers["noisy_count"] = noisy_domains[fqdn]
        rows.append(
            {
                "fqdn": fqdn,
                "latency_p50_ms": item.get("avg_latency_ms"),
                "latency_p95_ms": item.get("avg_latency_ms"),
                "error_count": item.get("errors"),
                "response_class": None,
                "noisy_markers": noisy_markers,
            }
        )

    for rec in records.get("records") or []:
        fqdn = rec.get("fqdn")
        if not fqdn or any(r["fqdn"] == fqdn for r in rows):
            continue
        rows.append(
            {
                "fqdn": fqdn,
                "latency_p50_ms": rec.get("avg_latency_ms"),
                "latency_p95_ms": rec.get("avg_latency_ms"),
                "error_count": rec.get("errors"),
                "response_class": rec.get("status"),
                "noisy_markers": {},
            }
        )
    return rows


def extract_error_aggregates(panels: dict[str, Any]) -> list[dict[str, Any]]:
    errors = panels.get("errors") or {}
    rows: list[dict[str, Any]] = []
    for error_type, count in (errors.get("by_error_class") or {}).items():
        rows.append({"error_type": error_type, "count": count, "resolver": None, "domain": None, "bucket_at": None})
    matrix = errors.get("resolver_error_matrix") or {}
    for resolver, classes in matrix.items():
        for error_type, count in classes.items():
            rows.append(
                {
                    "error_type": error_type,
                    "count": count,
                    "resolver": resolver,
                    "domain": None,
                    "bucket_at": None,
                }
            )
    for domain, count in (errors.get("by_domain") or {}).items():
        rows.append(
            {
                "error_type": "domain_error",
                "count": count,
                "resolver": None,
                "domain": domain,
                "bucket_at": None,
            }
        )
    return rows


def extract_edns_aggregates(panels: dict[str, Any]) -> list[dict[str, Any]]:
    edns = panels.get("edns") or {}
    rows: list[dict[str, Any]] = []
    for level in edns.get("levels") or []:
        rows.append(
            {
                "edns_level": level.get("level"),
                "query_count": level.get("queries"),
                "error_count": level.get("errors"),
            }
        )
    return rows


def extract_chart_buckets(panels: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    dns_latency = panels.get("dns_latency") or {}
    for bucket in dns_latency.get("time_buckets") or []:
        bucket_at = _parse_ts(bucket.get("bucket_start") or bucket.get("timestamp"))
        if bucket_at:
            rows.append({"panel": "dns_latency", "bucket_at": bucket_at, "metrics": bucket})

    load = panels.get("load") or {}
    for bucket in load.get("time_series") or []:
        bucket_at = _parse_ts(bucket.get("bucket_start") or bucket.get("timestamp"))
        if bucket_at:
            rows.append({"panel": "load", "bucket_at": bucket_at, "metrics": bucket})
    return rows


def payload_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, default=str).encode("utf-8"))
