# DNS Debug — Prometheus Metrics Reference

All **implementation** metrics are defined in `app/metrics.py` and exposed at `GET /metrics`. Prefix: `dns_debug_`.

**There is no `dns_debug_errors_total`.** Errors, timeouts, and NXDOMAIN are counted via `dns_debug_queries_total` with the `outcome` label.

This document covers:

1. **Conceptual metrics** — target observability model for UI, alerting, and future implementation
2. **Implementation metrics** — current `dns_debug_*` names in code
3. **Conceptual → implementation mapping**
4. **UI panel mapping**

---

## Label conventions

| Label | Values / usage |
|-------|----------------|
| `resolver` | Resolve path / nameserver context |
| `server` | DNS server or upstream identifier |
| `domain` | Parent domain grouping |
| `fqdn` | Fully qualified name under test |
| `record_type` | `A`, `AAAA`, `CNAME`, `TXT`, `MX`, `NS`, `SRV` |
| `edns_level` | `edns0`, `edns1`, `edns2`, `edns3`, `edns4`, `edns5` |
| `error_type` | `timeout`, `nxdomain`, `servfail`, `refused`, `truncated`, `malformed`, `unexpected_rcode` |
| `run_id` | Test or background run identifier (`test_id`) |
| `density` | QPS bucket or load tier |
| `hop` | MTR hop number |
| `target` | MTR destination host:port |
| `resolve_mode` | `system`, `absolute_fqdn`, `ndots:N` |
| `query_type` | `A`, `AAAA`, … |
| `outcome` | `success`, `error`, `nxdomain`, `timeout` |
| `noise_type` | Six `NoiseType` enum values |
| `status` (MTR) | `completed`, `failed`, `timeout` |

---

## Conceptual metrics catalog

These names describe the **target observability contract** for dashboards, alerts, and UI aggregators. Map to `dns_debug_*` implementation metrics where noted.

### Query volume and outcomes

| Metric | Type | Labels | Meaning | Alerting hints |
|--------|------|--------|---------|----------------|
| `dns_queries_total` | Counter | `resolver`, `server`, `fqdn`, `record_type`, `edns_level`, `run_id`, `outcome` | Total DNS queries | Rate drop may indicate runner stall |
| `dns_query_duration_seconds` | Histogram | `resolver`, `record_type`, `edns_level`, `run_id` | Query latency | p95 > SLO threshold |
| `dns_query_errors_total` | Counter | `resolver`, `domain`, `error_type`, `run_id` | Failed queries by class | Spike in `timeout` or `servfail` |
| `dns_query_success_total` | Counter | `resolver`, `record_type`, `run_id` | Successful queries | Use with errors for success rate |

**Implementation mapping:**

| Conceptual | Implementation |
|------------|----------------|
| `dns_queries_total` | `dns_debug_queries_total` (`test_id`, `resolve_mode`, `query_type`, `outcome`) |
| `dns_query_duration_seconds` | `dns_debug_query_latency_seconds` |
| `dns_query_errors_total` | `dns_debug_queries_total{outcome=~"error\|timeout"}` (+ nxdomain if counted as error) |
| `dns_query_success_total` | `dns_debug_queries_total{outcome="success"}` |

### Garbage / noise

| Metric | Type | Labels | Meaning | Alerting hints |
|--------|------|--------|---------|----------------|
| `dns_garbage_queries_total` | Counter | `domain`, `fqdn`, `error_type`, `run_id` | Noisy / garbage queries | Ratio > 30% of total queries |

**Implementation:** `dns_debug_noisy_queries_total` (`test_id`, `noise_type`)

### Cache (heuristic)

| Metric | Type | Labels | Meaning | Alerting hints |
|--------|------|--------|---------|----------------|
| `dns_cache_hits_total` | Counter | `resolver`, `fqdn`, `run_id` | Heuristic cache-like fast repeats | **Not** real cache — trend only |
| `dns_cache_misses_total` | Counter | `resolver`, `fqdn`, `run_id` | First-query or slow repeat | Derived from attempts − hits |
| `dns_cache_hit_ratio` | Gauge | `resolver`, `run_id` | hits / (hits + misses) | Do not use as hard SLO |

**Implementation:** `dns_debug_possible_cached_response_total`, `dns_debug_repeat_query_latency_delta_ms`

### Run lifecycle

| Metric | Type | Labels | Meaning | Alerting hints |
|--------|------|--------|---------|----------------|
| `dns_active_runs` | Gauge | — | Currently running tests | Stuck > 0 after expected duration |
| `dns_completed_runs_total` | Counter | `run_id`, `outcome` | Finished test runs | — |
| `dns_run_failures_total` | Counter | `run_id`, `error_type` | Runs ending in error state | Any sustained increase |

**Implementation:** `dns_debug_active_tests`, test status from `stats_store` (no dedicated completed counter yet)

### Record checks

| Metric | Type | Labels | Meaning | Alerting hints |
|--------|------|--------|---------|----------------|
| `dns_record_check_total` | Counter | `fqdn`, `record_type`, `run_id` | Per-record validation attempts | — |
| `dns_record_check_failures_total` | Counter | `fqdn`, `record_type`, `error_type`, `run_id` | Per-record failures | Top failing FQDN in UI drilldown |

**Implementation:** Derived from `stats_store` per-record counters and `dns_debug_queries_total` grouped by record

### EDNS

| Metric | Type | Labels | Meaning | Alerting hints |
|--------|------|--------|---------|----------------|
| `dns_edns_queries_total` | Counter | `edns_level`, `resolver`, `run_id` | Queries per EDNS level | — |
| `dns_edns_errors_total` | Counter | `edns_level`, `error_type`, `run_id` | EDNS-level errors | Spike at one `edns_level` |

**Implementation:** Aggregated from attempt metadata + `dns_debug_queries_total`; resolver `options` from startup gauges

### Resolver health and load

| Metric | Type | Labels | Meaning | Alerting hints |
|--------|------|--------|---------|----------------|
| `dns_resolver_health` | Gauge | `resolver`, `server` | 1=healthy, 0=degraded | Sustained 0 |
| `dns_qps_current` | Gauge | `run_id`, `density` | Current query rate | Compare to configured RPS |
| `dns_qps_bucket` | Histogram | `density`, `run_id` | QPS distribution over time | Saturation analysis |

**Implementation:** QPS derived from `rate(dns_debug_queries_total)`; health from error rate per `resolve_mode`

### MTR

| Metric | Type | Labels | Meaning | Alerting hints |
|--------|------|--------|---------|----------------|
| `dns_mtr_runs_total` | Counter | `target`, `status` | MTR runs completed | `failed` or `timeout` spike |
| `dns_mtr_hop_latency_ms` | Histogram | `target`, `hop` | Per-hop latency | Mid-path p95 spike |
| `dns_mtr_packet_loss_ratio` | Gauge | `target`, `hop` | Loss percentage per hop | Any hop > 5% |
| `dns_mtr_path_changes_total` | Counter | `target` | Hop count changes between runs | Unstable routing |

**Implementation:** `dns_debug_mtr_runs_total`, `dns_debug_mtr_last_run_timestamp`, `dns_debug_mtr_last_exit_code`; hop data from `mtr_store`

---

## Implementation metrics (current code)

### Startup / global gauges

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_configured_nameservers` | Gauge | — | Count of nameservers in `/etc/resolv.conf` | Set once at startup via `init_from_snapshot` |
| `dns_debug_search_domains_count` | Gauge | — | Count of search domains | Same |
| `dns_debug_configured_ndots` | Gauge | — | Parsed `ndots` from resolv.conf options | Omitted if not configured |
| `dns_debug_worst_case_resolve_budget_ms` | Gauge | — | Theoretical worst-case resolve time (ms) | Upper bound estimate, not observed latency |
| `dns_debug_active_tests` | Gauge | — | Currently running DNS debug tests | Maps to conceptual `dns_active_runs` |

### Per-test gauges

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_test_progress` | Gauge | `test_id` | Ratio elapsed / duration (0–1) | 0 for continuous autonomous tests |
| `dns_debug_query_amplification_ratio` | Gauge | `test_id` | Total queries / estimated primary lookups | Set at test end via `set_test_analytics` |
| `dns_debug_search_suffix_nxdomain_ratio` | Gauge | `test_id` | Fraction of queries that are search suffix NXDOMAIN probes | Includes diagnostic probes |

### Per-test counters

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_queries_total` | Counter | `test_id`, `resolve_mode`, `query_type`, `outcome` | Total DNS queries executed | `outcome`: `success`, `error`, `nxdomain`, `timeout` |
| `dns_debug_noisy_queries_total` | Counter | `test_id`, `noise_type` | Noisy queries detected | Maps to `dns_garbage_queries_total` |
| `dns_debug_possible_cached_response_total` | Counter | `test_id` | Queries matching cache-hit **heuristic** | Maps to `dns_cache_hits_total` — **not** real cache |

### Per-test histograms

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_query_latency_seconds` | Histogram | `test_id`, `resolve_mode`, `query_type` | DNS query latency | Maps to `dns_query_duration_seconds`; values in seconds |
| `dns_debug_repeat_query_latency_delta_ms` | Histogram | `test_id` | First minus repeat latency (ms) | Heuristic only |
| `dns_debug_fqdn_latency_delta_ms` | Histogram | `test_id`, `record` | System minus absolute_fqdn latency per record (ms) | Only observed when delta > 0 |

### MTR gauges and counters

| Metric | Type | Labels | Meaning | Caveats |
|--------|------|--------|---------|---------|
| `dns_debug_mtr_last_run_timestamp` | Gauge | — | Unix timestamp of last completed MTR run | Set on each run completion |
| `dns_debug_mtr_last_exit_code` | Gauge | — | Exit code of last MTR run | `-1` if unavailable (timeout/kill) |
| `dns_debug_mtr_runs_total` | Counter | `status` | MTR runs completed | Maps to `dns_mtr_runs_total`; `status`: `completed`, `failed`, `timeout` |

---

## Common PromQL queries

```promql
# Error rate by test
sum(rate(dns_debug_queries_total{outcome=~"error|timeout"}[5m])) by (test_id)
  / sum(rate(dns_debug_queries_total[5m])) by (test_id)

# P95 latency by resolve mode
histogram_quantile(0.95,
  sum(rate(dns_debug_query_latency_seconds_bucket[5m])) by (le, resolve_mode))

# P50 / P99 latency
histogram_quantile(0.50, sum(rate(dns_debug_query_latency_seconds_bucket[5m])) by (le))
histogram_quantile(0.99, sum(rate(dns_debug_query_latency_seconds_bucket[5m])) by (le))

# Noise breakdown
sum(rate(dns_debug_noisy_queries_total[5m])) by (test_id, noise_type)

# Amplification
dns_debug_query_amplification_ratio

# Conceptual success rate (using implementation metrics)
sum(rate(dns_debug_queries_total{outcome="success"}[5m])) by (test_id)
  / sum(rate(dns_debug_queries_total[5m])) by (test_id)

# MTR run rate
sum(rate(dns_debug_mtr_runs_total[1h])) by (status)

# Active runs
dns_debug_active_tests
```

### Alerting examples

```yaml
# High DNS error rate
- alert: DnsDebugHighErrorRate
  expr: |
    sum(rate(dns_debug_queries_total{outcome=~"error|timeout"}[5m])) by (test_id)
    / sum(rate(dns_debug_queries_total[5m])) by (test_id) > 0.05
  for: 5m

# High P95 latency
- alert: DnsDebugHighLatencyP95
  expr: |
    histogram_quantile(0.95,
      sum(rate(dns_debug_query_latency_seconds_bucket[5m])) by (le)) > 0.5
  for: 5m

# Query amplification
- alert: DnsDebugHighAmplification
  expr: dns_debug_query_amplification_ratio > 3
  for: 10m

# MTR failure
- alert: DnsDebugMtrFailed
  expr: dns_debug_mtr_last_exit_code != 0
  for: 1m
```

---

## Cache metrics disclaimer

`dns_debug_possible_cached_response_total`, `dns_cache_hits_total` (conceptual), and `dns_debug_repeat_query_latency_delta_ms` are based on a **repeat-query latency heuristic**:

- First query to a (effective_name, query_type, resolve_mode) key records baseline latency
- Subsequent queries that are faster than `cache_latency_threshold_ms` and below `cache_latency_ratio × first_latency` increment the counter

This does **not** read Docker embedded DNS cache state. Use as a weak signal only. Never document or present these as confirmed cache hits — including in the Web UI cache section.

---

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
| `record_db_write` | `db_write_total` |
| `record_db_write_error` | `db_write_errors_total` |
| `record_db_cleanup` | `db_cleanup_runs_total` |
| `record_db_cleanup_deleted` | `db_cleanup_deleted_rows_total` |

---

## PostgreSQL persistence metrics (v0.4.0+)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `dns_debug_db_write_total` | Counter | `entity` | Successful DB writes (`snapshot`, `mtr`) |
| `dns_debug_db_write_errors_total` | Counter | `entity`, `error_class` | Failed DB writes |
| `dns_debug_db_cleanup_runs_total` | Counter | `status` | Retention cleanup runs |
| `dns_debug_db_cleanup_deleted_rows_total` | Counter | `table` | Rows deleted per cleanup |

---

## Metrics → UI mapping

When `DNS_DEBUG_UI_ENABLED=true`, the dashboard sections consume these metrics (directly or via `ui_aggregator`):

| UI section | Endpoint | Primary metrics / sources |
|------------|----------|---------------------------|
| **1. Overview** | `/api/ui/overview` | `dns_debug_active_tests`, `dns_active_runs`, `dns_debug_queries_total` (outcome breakdown), `stats_store` summaries, `/health` |
| **2. DNS latency** | `/api/ui/dns-latency` | `dns_debug_query_latency_seconds`, `dns_query_duration_seconds` (p50/p95/p99), breakdown by `resolve_mode`, `query_type`, `edns_level` |
| **3. EDNS analytics** | `/api/ui/edns` | `dns_edns_queries_total`, `dns_edns_errors_total`, per-EDNS from attempt metadata + `dns_debug_queries_total` |
| **4. Error analysis** | `/api/ui/errors` | `dns_debug_queries_total{outcome=~"error\|timeout\|nxdomain"}`, `dns_query_errors_total`, QPS from test config + attempt rate |
| **5. Garbage / noisy** | `/api/ui/garbage` | `dns_debug_noisy_queries_total`, `dns_garbage_queries_total`, `dns_debug_query_amplification_ratio`, `dns_debug_search_suffix_nxdomain_ratio` |
| **6. Cache behavior** | `/api/ui/cache` | `dns_debug_possible_cached_response_total`, `dns_cache_hits_total`, `dns_cache_hit_ratio`, `dns_debug_repeat_query_latency_delta_ms` — **with disclaimer** |
| **7. DNS record drilldown** | `/api/ui/records` | `dns_record_check_total`, `dns_record_check_failures_total`, `stats_store` per-record attempts, `dns_debug_fqdn_latency_delta_ms` |
| **8. Load / density** | `/api/ui/load` | `dns_qps_current`, `dns_qps_bucket`, `dns_debug_queries_total` rate vs configured RPS; latency histograms at different load windows |
| **9. MTR diagnostics** | `/api/ui/mtr` | `dns_debug_mtr_*`, `dns_mtr_hop_latency_ms`, `dns_mtr_packet_loss_ratio`, `mtr_store` hop data, verdict |
| **10. Rankings** | `/api/ui/rankings` | Aggregated error rate and latency by resolver, domain, `query_type`, MTR `target` |

UI JSON endpoints mirror this mapping under `{DNS_DEBUG_UI_BASE_PATH}/api/ui/`.
