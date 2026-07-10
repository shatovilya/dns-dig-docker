import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import metrics
from config import Settings, get_settings
from db.connection import get_db_pool
from db.extractors import (
    extract_chart_buckets,
    extract_domain_aggregates,
    extract_edns_aggregates,
    extract_error_aggregates,
    extract_resolver_aggregates,
    extract_run_aggregate,
    extract_test_run,
    payload_size_bytes,
)
from retention import retention_cutoff

logger = logging.getLogger(__name__)


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


async def persist_snapshot(payload: dict[str, Any]) -> None:
    pool = get_db_pool()
    if pool is None:
        return

    snapshot_id = payload["snapshot_id"]
    panels = payload.get("panels") or {}
    summary = payload.get("summary") or {}
    created_at = _parse_ts(payload.get("created_at")) or datetime.now(timezone.utc)
    started_at = _parse_ts(payload.get("started_at"))
    finished_at = _parse_ts(payload.get("finished_at"))
    size = payload_size_bytes(payload)

    run_agg = extract_run_aggregate(panels)
    test_run = extract_test_run(payload, summary if isinstance(summary, dict) else {})
    resolver_rows = extract_resolver_aggregates(panels)
    domain_rows = extract_domain_aggregates(panels)
    error_rows = extract_error_aggregates(panels)
    edns_rows = extract_edns_aggregates(panels)
    chart_rows = extract_chart_buckets(panels)

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO historical_snapshots (
                        snapshot_id, test_id, test_name, created_at, started_at, finished_at,
                        summary, panels, payload_size_bytes
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9)
                    ON CONFLICT (snapshot_id) DO UPDATE SET
                        test_id = EXCLUDED.test_id,
                        test_name = EXCLUDED.test_name,
                        created_at = EXCLUDED.created_at,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        summary = EXCLUDED.summary,
                        panels = EXCLUDED.panels,
                        payload_size_bytes = EXCLUDED.payload_size_bytes
                    """,
                    snapshot_id,
                    payload.get("test_id", ""),
                    payload.get("test_name", ""),
                    created_at,
                    started_at,
                    finished_at,
                    _json(summary),
                    _json(panels),
                    size,
                )

                await conn.execute(
                    """
                    INSERT INTO test_runs (
                        snapshot_id, test_id, test_name, status, mode,
                        started_at, finished_at, duration_ms, metadata
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                    ON CONFLICT (snapshot_id) DO UPDATE SET
                        test_id = EXCLUDED.test_id,
                        test_name = EXCLUDED.test_name,
                        status = EXCLUDED.status,
                        mode = EXCLUDED.mode,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        duration_ms = EXCLUDED.duration_ms,
                        metadata = EXCLUDED.metadata
                    """,
                    test_run["snapshot_id"],
                    test_run["test_id"],
                    test_run["test_name"],
                    test_run["status"],
                    test_run["mode"],
                    test_run["started_at"],
                    test_run["finished_at"],
                    test_run["duration_ms"],
                    _json(test_run["metadata"]),
                )

                await conn.execute(
                    """
                    INSERT INTO run_aggregates (
                        snapshot_id, total_queries, success_rate, error_rate, nxdomain_rate,
                        noisy_ratio, cache_hit_ratio, latency_p50_ms, latency_p95_ms,
                        latency_p99_ms, sample_count
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (snapshot_id) DO UPDATE SET
                        total_queries = EXCLUDED.total_queries,
                        success_rate = EXCLUDED.success_rate,
                        error_rate = EXCLUDED.error_rate,
                        nxdomain_rate = EXCLUDED.nxdomain_rate,
                        noisy_ratio = EXCLUDED.noisy_ratio,
                        cache_hit_ratio = EXCLUDED.cache_hit_ratio,
                        latency_p50_ms = EXCLUDED.latency_p50_ms,
                        latency_p95_ms = EXCLUDED.latency_p95_ms,
                        latency_p99_ms = EXCLUDED.latency_p99_ms,
                        sample_count = EXCLUDED.sample_count
                    """,
                    snapshot_id,
                    run_agg.get("total_queries"),
                    run_agg.get("success_rate"),
                    run_agg.get("error_rate"),
                    run_agg.get("nxdomain_rate"),
                    run_agg.get("noisy_ratio"),
                    run_agg.get("cache_hit_ratio"),
                    run_agg.get("latency_p50_ms"),
                    run_agg.get("latency_p95_ms"),
                    run_agg.get("latency_p99_ms"),
                    run_agg.get("sample_count"),
                )

                await _replace_child_rows(conn, snapshot_id, resolver_rows, domain_rows, error_rows, edns_rows, chart_rows)

        metrics.record_db_write("snapshot")
        logger.debug("Persisted snapshot %s to PostgreSQL", snapshot_id)
    except Exception as exc:
        metrics.record_db_write_error("snapshot", type(exc).__name__)
        logger.warning("Failed to persist snapshot %s: %s", snapshot_id, exc)


async def _replace_child_rows(
    conn: Any,
    snapshot_id: str,
    resolver_rows: list[dict[str, Any]],
    domain_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
    edns_rows: list[dict[str, Any]],
    chart_rows: list[dict[str, Any]],
) -> None:
    for table in (
        "resolver_aggregates",
        "domain_aggregates",
        "error_aggregates",
        "edns_aggregates",
        "chart_buckets",
    ):
        await conn.execute(f"DELETE FROM {table} WHERE snapshot_id = $1", snapshot_id)

    for row in resolver_rows:
        await conn.execute(
            """
            INSERT INTO resolver_aggregates (
                snapshot_id, resolver, availability, latency_p50_ms, latency_p95_ms,
                error_count, cache_efficiency, edns_counters
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            snapshot_id,
            row["resolver"],
            row.get("availability"),
            row.get("latency_p50_ms"),
            row.get("latency_p95_ms"),
            row.get("error_count"),
            row.get("cache_efficiency"),
            _json(row.get("edns_counters") or {}),
        )

    for row in domain_rows:
        await conn.execute(
            """
            INSERT INTO domain_aggregates (
                snapshot_id, fqdn, latency_p50_ms, latency_p95_ms, error_count,
                response_class, noisy_markers
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            snapshot_id,
            row["fqdn"],
            row.get("latency_p50_ms"),
            row.get("latency_p95_ms"),
            row.get("error_count"),
            row.get("response_class"),
            _json(row.get("noisy_markers") or {}),
        )

    for row in error_rows:
        await conn.execute(
            """
            INSERT INTO error_aggregates (
                snapshot_id, error_type, count, resolver, domain, bucket_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            snapshot_id,
            row["error_type"],
            row["count"],
            row.get("resolver"),
            row.get("domain"),
            row.get("bucket_at"),
        )

    for row in edns_rows:
        if not row.get("edns_level"):
            continue
        await conn.execute(
            """
            INSERT INTO edns_aggregates (snapshot_id, edns_level, query_count, error_count)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (snapshot_id, edns_level) DO UPDATE SET
                query_count = EXCLUDED.query_count,
                error_count = EXCLUDED.error_count
            """,
            snapshot_id,
            row["edns_level"],
            row.get("query_count"),
            row.get("error_count"),
        )

    for row in chart_rows:
        await conn.execute(
            """
            INSERT INTO chart_buckets (snapshot_id, panel, bucket_at, metrics)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            snapshot_id,
            row["panel"],
            row["bucket_at"],
            _json(row["metrics"]),
        )


async def persist_mtr_run(run: Any, *, snapshot_id: str | None = None, test_id: str | None = None) -> None:
    pool = get_db_pool()
    if pool is None:
        return

    hops = [h.to_dict() for h in run.parsed_hops]
    loss_values = [h.loss_pct for h in run.parsed_hops]
    packet_loss = max(loss_values) if loss_values else 0.0
    degraded = run.status not in ("completed",) or packet_loss >= 5.0
    target = f"{run.service_name}:{run.port}"

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mtr_runs (
                    run_id, snapshot_id, test_id, target, started_at, finished_at,
                    packet_loss_summary, degraded, hops_snapshot, status, raw_report
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11)
                ON CONFLICT (run_id) DO UPDATE SET
                    snapshot_id = EXCLUDED.snapshot_id,
                    finished_at = EXCLUDED.finished_at,
                    packet_loss_summary = EXCLUDED.packet_loss_summary,
                    degraded = EXCLUDED.degraded,
                    hops_snapshot = EXCLUDED.hops_snapshot,
                    status = EXCLUDED.status,
                    raw_report = EXCLUDED.raw_report
                """,
                run.run_id,
                snapshot_id,
                test_id,
                target,
                run.started_at,
                run.finished_at,
                packet_loss,
                degraded,
                _json(hops),
                run.status,
                run.raw_report,
            )
        metrics.record_db_write("mtr")
    except Exception as exc:
        metrics.record_db_write_error("mtr", type(exc).__name__)
        logger.warning("Failed to persist MTR run %s: %s", run.run_id, exc)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)
