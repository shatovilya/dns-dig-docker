# DNS Debug — Prometheus Metrics Reference

All metrics are defined in `app/metrics.py` and exposed at `GET /metrics`. Prefix: `dns_debug_`.

**There is no `dns_debug_errors_total`.** Errors, timeouts, and NXDOMAIN are counted via `dns_debug_queries_total` with the `outcome` label.

## Startup / global gauges

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_configured_nameservers` | Gauge | — | Count of nameservers in `/etc/resolv.conf` | Set once at startup via `init_from_snapshot` |
| `dns_debug_search_domains_count` | Gauge | — | Count of search domains | Same |
| `dns_debug_configured_ndots` | Gauge | — | Parsed `ndots` from resolv.conf options | Omitted if not configured |
| `dns_debug_worst_case_resolve_budget_ms` | Gauge | — | Theoretical worst-case resolve time (ms) from search × attempts × timeout × query types | Upper bound estimate, not observed latency |
| `dns_debug_active_tests` | Gauge | — | Currently running DNS debug tests | Incremented per background runner |

## Per-test gauges

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_test_progress` | Gauge | `test_id` | Ratio elapsed / duration (0–1) | 0 for continuous autonomous tests |
| `dns_debug_query_amplification_ratio` | Gauge | `test_id` | Total queries / estimated primary lookups | Set at test end via `set_test_analytics` |
| `dns_debug_search_suffix_nxdomain_ratio` | Gauge | `test_id` | Fraction of queries that are search suffix NXDOMAIN probes | Includes diagnostic probes |

## Per-test counters

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_queries_total` | Counter | `test_id`, `resolve_mode`, `query_type`, `outcome` | Total DNS queries executed | `outcome`: `success`, `error`, `nxdomain`, `timeout` |
| `dns_debug_noisy_queries_total` | Counter | `test_id`, `noise_type` | Noisy queries detected | See `models.NoiseType` for label values |
| `dns_debug_possible_cached_response_total` | Counter | `test_id` | Queries matching cache-hit **heuristic** | **Not** real Docker DNS cache; see below |

## Per-test histograms

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_query_latency_seconds` | Histogram | `test_id`, `resolve_mode`, `query_type` | DNS query latency | **Not** `query_duration_seconds`; values in seconds |
| `dns_debug_repeat_query_latency_delta_ms` | Histogram | `test_id` | First minus repeat latency (ms) for same key | **Heuristic only** — paired with cache counter |
| `dns_debug_fqdn_latency_delta_ms` | Histogram | `test_id`, `record` | System minus absolute_fqdn latency per record (ms) | Only observed when delta > 0 |

## MTR gauges and counters

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_mtr_last_run_timestamp` | Gauge | — | Unix timestamp of last completed MTR run | Set on each run completion |
| `dns_debug_mtr_last_exit_code` | Gauge | — | Exit code of last MTR run | `-1` if unavailable (timeout/kill) |
| `dns_debug_mtr_runs_total` | Counter | `status` | MTR runs completed | `status`: `completed`, `failed`, `timeout` |

## Common queries

```promql
# Error rate by test
sum(rate(dns_debug_queries_total{outcome=~"error|timeout"}[5m])) by (test_id)
  / sum(rate(dns_debug_queries_total[5m])) by (test_id)

# P95 latency by resolve mode
histogram_quantile(0.95,
  sum(rate(dns_debug_query_latency_seconds_bucket[5m])) by (le, resolve_mode))

# Noise breakdown
sum(rate(dns_debug_noisy_queries_total[5m])) by (test_id, noise_type)

# Amplification
dns_debug_query_amplification_ratio
```

## Cache metrics disclaimer

`dns_debug_possible_cached_response_total` and `dns_debug_repeat_query_latency_delta_ms` are based on a **repeat-query latency heuristic**:

- First query to a (effective_name, query_type, resolve_mode) key records baseline latency
- Subsequent queries that are faster than `cache_latency_threshold_ms` and below `cache_latency_ratio × first_latency` increment the counter

This does **not** read Docker embedded DNS cache state. Use as a weak signal only. Never document or present these as confirmed cache hits.

## Label conventions

| Label | Values |
|-------|--------|
| `resolve_mode` | `system`, `absolute_fqdn`, `ndots:4`, `ndots:5`, … |
| `query_type` | `A`, `AAAA` |
| `outcome` | `success`, `error`, `nxdomain`, `timeout` |
| `noise_type` | `search_suffix_query`, `search_suffix_nxdomain`, `duplicate_query`, `empty_answer`, `aaaa_noise`, `eventual_fqdn_success` |

## Recording functions

| Function | Metrics updated |
|----------|-----------------|
| `record_query` | `queries_total`, `query_latency_seconds` |
| `record_noisy` | `noisy_queries_total` |
| `record_possible_cache` | `possible_cached_response_total`, `repeat_query_latency_delta_ms` |
| `set_test_analytics` | `query_amplification_ratio`, `search_suffix_nxdomain_ratio`, `fqdn_latency_delta_ms` |
| `set_test_progress` | `test_progress` |
| `init_from_snapshot` | `configured_nameservers`, `search_domains_count`, `configured_ndots`, `worst_case_resolve_budget_ms` |
| `set_active_tests` | `active_tests` |
| `record_mtr_run` | `mtr_last_run_timestamp`, `mtr_last_exit_code`, `mtr_runs_total` |
