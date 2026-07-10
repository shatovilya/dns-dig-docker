from prometheus_client import Counter, Gauge, Histogram

# Startup / global gauges
configured_nameservers = Gauge(
    "dns_debug_configured_nameservers",
    "Number of nameservers in /etc/resolv.conf",
)
search_domains_count = Gauge(
    "dns_debug_search_domains_count",
    "Number of search domains in /etc/resolv.conf",
)
configured_ndots = Gauge(
    "dns_debug_configured_ndots",
    "ndots value from /etc/resolv.conf (unset if not configured)",
)
worst_case_resolve_budget_ms = Gauge(
    "dns_debug_worst_case_resolve_budget_ms",
    "Worst-case resolve time budget from resolv.conf (ms)",
)
active_tests = Gauge(
    "dns_debug_active_tests",
    "Number of currently running DNS debug tests",
)

# Per-test gauges
test_progress = Gauge(
    "dns_debug_test_progress",
    "Test progress ratio (elapsed / duration_seconds)",
    ["test_id"],
)

# Per-test counters
queries_total = Counter(
    "dns_debug_queries_total",
    "Total DNS queries executed",
    ["test_id", "resolve_mode", "query_type", "outcome"],
)
noisy_queries_total = Counter(
    "dns_debug_noisy_queries_total",
    "Total noisy DNS queries detected",
    ["test_id", "noise_type"],
)
possible_cached_response_total = Counter(
    "dns_debug_possible_cached_response_total",
    "Queries that look like possible cache hits (heuristic)",
    ["test_id"],
)

# Per-test histograms
query_latency_seconds = Histogram(
    "dns_debug_query_latency_seconds",
    "DNS query latency in seconds",
    ["test_id", "resolve_mode", "query_type"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
repeat_query_latency_delta_ms = Histogram(
    "dns_debug_repeat_query_latency_delta_ms",
    "Latency delta between first and repeat query for same key (ms)",
    ["test_id"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000),
)
fqdn_latency_delta_ms = Histogram(
    "dns_debug_fqdn_latency_delta_ms",
    "Latency delta system minus absolute_fqdn per record (ms)",
    ["test_id", "record"],
    buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
)
query_amplification_ratio = Gauge(
    "dns_debug_query_amplification_ratio",
    "Ratio of total queries to estimated primary lookups",
    ["test_id"],
)
search_suffix_nxdomain_ratio = Gauge(
    "dns_debug_search_suffix_nxdomain_ratio",
    "Ratio of search suffix NXDOMAIN probes to total queries",
    ["test_id"],
)

# MTR diagnostics
mtr_last_run_timestamp = Gauge(
    "dns_debug_mtr_last_run_timestamp",
    "Unix timestamp of the last completed MTR run",
)
mtr_last_exit_code = Gauge(
    "dns_debug_mtr_last_exit_code",
    "Exit code of the last completed MTR run (-1 if unavailable)",
)
mtr_runs_total = Counter(
    "dns_debug_mtr_runs_total",
    "Total MTR runs completed",
    ["status"],
)

# PostgreSQL persistence observability
db_write_total = Counter(
    "dns_debug_db_write_total",
    "Successful PostgreSQL persistence writes",
    ["entity"],
)
db_write_errors_total = Counter(
    "dns_debug_db_write_errors_total",
    "Failed PostgreSQL persistence writes",
    ["entity", "error_class"],
)
db_cleanup_runs_total = Counter(
    "dns_debug_db_cleanup_runs_total",
    "Retention cleanup runs",
    ["status"],
)
db_cleanup_deleted_rows_total = Counter(
    "dns_debug_db_cleanup_deleted_rows_total",
    "Rows deleted by retention cleanup",
    ["table"],
)


def init_from_snapshot(
    nameserver_count: int,
    search_count: int,
    ndots: int | None = None,
    budget_ms: float | None = None,
) -> None:
    configured_nameservers.set(nameserver_count)
    search_domains_count.set(search_count)
    if ndots is not None:
        configured_ndots.set(ndots)
    if budget_ms is not None:
        worst_case_resolve_budget_ms.set(budget_ms)


def set_active_tests(count: int) -> None:
    active_tests.set(count)


def record_query(
    test_id: str,
    resolve_mode: str,
    query_type: str,
    outcome: str,
    latency_ms: float,
) -> None:
    queries_total.labels(
        test_id=test_id,
        resolve_mode=resolve_mode,
        query_type=query_type,
        outcome=outcome,
    ).inc()
    query_latency_seconds.labels(
        test_id=test_id,
        resolve_mode=resolve_mode,
        query_type=query_type,
    ).observe(latency_ms / 1000.0)


def record_noisy(test_id: str, noise_type: str) -> None:
    noisy_queries_total.labels(test_id=test_id, noise_type=noise_type).inc()


def record_possible_cache(test_id: str, delta_ms: float) -> None:
    possible_cached_response_total.labels(test_id=test_id).inc()
    repeat_query_latency_delta_ms.labels(test_id=test_id).observe(delta_ms)


def set_test_progress(test_id: str, progress: float) -> None:
    test_progress.labels(test_id=test_id).set(progress)


def set_test_analytics(
    test_id: str,
    amplification: float,
    search_nxdomain_ratio: float,
    per_record_fqdn_deltas: dict[str, float],
) -> None:
    query_amplification_ratio.labels(test_id=test_id).set(amplification)
    search_suffix_nxdomain_ratio.labels(test_id=test_id).set(search_nxdomain_ratio)
    for record, delta in per_record_fqdn_deltas.items():
        if delta > 0:
            fqdn_latency_delta_ms.labels(test_id=test_id, record=record).observe(delta)


def clear_test_metrics(test_id: str) -> None:
    """Best-effort cleanup; Prometheus client does not support label removal."""
    test_progress.labels(test_id=test_id).set(0)


def record_mtr_run(status: str, exit_code: int | None, finished_timestamp: float) -> None:
    mtr_last_run_timestamp.set(finished_timestamp)
    mtr_last_exit_code.set(exit_code if exit_code is not None else -1)
    mtr_runs_total.labels(status=status).inc()


def record_db_write(entity: str) -> None:
    db_write_total.labels(entity=entity).inc()


def record_db_write_error(entity: str, error_class: str) -> None:
    db_write_errors_total.labels(entity=entity, error_class=error_class).inc()


def record_db_cleanup(status: str) -> None:
    db_cleanup_runs_total.labels(status=status).inc()


def record_db_cleanup_deleted(table: str, count: int) -> None:
    if count > 0:
        db_cleanup_deleted_rows_total.labels(table=table).inc(count)
