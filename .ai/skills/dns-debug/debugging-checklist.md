# DNS Debug — Operational Checklist

Step-by-step workflow for diagnosing DNS behavior inside the Docker container. Assumes service on `http://localhost:8080`.

## 1. Inspect resolver environment

Capture the container's DNS configuration before running tests.

```bash
curl -s http://localhost:8080/resolver | jq
```

Verify:

| Field | What to check |
|-------|---------------|
| `nameservers` | Expect `127.0.0.11` in standard Docker bridge setup |
| `search` | Non-empty search list explains extra suffix queries |
| `ndots` | Threshold for search-first vs FQDN-first behavior |
| `timeout_seconds` | Per-attempt timeout (glibc default 5 if unset) |
| `attempts` | Retry count (glibc default 2 if unset) |
| `options` | Raw resolver options (`edns0`, etc.) |

Force refresh after container restart:

```bash
curl -s "http://localhost:8080/resolver?refresh=true" | jq
```

## 2. Reproduce under load

Start a manual test (requires `AUTONOMOUS_MODE=false`):

```bash
curl -s -X POST http://localhost:8080/tests \
  -H "Content-Type: application/json" \
  -d '{
    "test_name": "repro",
    "records": ["kubernetes.default.svc.cluster.local", "api.example.com"],
    "query_types": ["A", "AAAA"],
    "resolve_modes": ["system", "absolute_fqdn"],
    "ndots_values": [4, 5],
    "rps": 10,
    "concurrency": 5,
    "duration_seconds": 30,
    "timeout_seconds": 2
  }'
```

Note the returned `test_id`. Poll status:

```bash
curl -s http://localhost:8080/tests/<test_id> | jq '.status, .progress, .summary'
```

## 3. Compare resolve modes

Each test runs all combinations from `records × resolve_modes × ndots_values × query_types`.

| Mode | Metric label | Interpretation |
|------|--------------|----------------|
| System | `resolve_mode="system"` | Normal app behavior with search |
| Absolute FQDN | `resolve_mode="absolute_fqdn"` | Baseline without search domains |
| ndots override | `resolve_mode="ndots:4"`, `ndots:5`, … | Threshold sensitivity |

Compare in test detail:

```bash
curl -s http://localhost:8080/tests/<test_id> | jq '.counters.by_resolve_mode'
```

Or in Prometheus:

```bash
curl -s http://localhost:8080/metrics | grep 'dns_debug_queries_total{.*resolve_mode'
```

**Key signal:** if `absolute_fqdn` has far fewer queries and lower latency than `system`, search domains are adding overhead.

## 4. Analyze noise types

Six noise types from `models.NoiseType`:

| `noise_type` | Meaning |
|--------------|---------|
| `search_suffix_query` | Diagnostic probe to `record.search_domain` |
| `search_suffix_nxdomain` | Search suffix probe returned NXDOMAIN |
| `duplicate_query` | Same (record, type, mode) within 2 s window |
| `empty_answer` | SUCCESS with zero answers |
| `aaaa_noise` | AAAA after A already succeeded for same record |
| `eventual_fqdn_success` | Search mode failed but absolute_fqdn succeeded |

Check noise counts:

```bash
curl -s http://localhost:8080/tests/<test_id> | jq '.summary.noise_counts, .summary.noisy_query_ratio'
```

Prometheus:

```bash
curl -s http://localhost:8080/metrics | grep dns_debug_noisy_queries_total
```

Remember: `search_suffix_*` types come from **diagnostic probes**, not primary lookups.

## 5. Latency and errors

Summary fields on `GET /tests/{id}`:

```bash
curl -s http://localhost:8080/tests/<test_id> | jq '{
  success_rate: .summary.success_rate,
  error_rate: .summary.error_rate,
  nxdomains: .summary.nxdomains,
  avg_latency_ms: .summary.avg_latency_ms,
  p95_latency_ms: .summary.p95_latency_ms,
  possible_cache_hit_ratio: .summary.possible_cache_hit_ratio
}'
```

ndots/search analytics block:

```bash
curl -s http://localhost:8080/tests/<test_id> | jq '.summary.ndots_search_analytics'
```

| Analytics field | Meaning |
|-----------------|---------|
| `query_amplification_ratio` | Total queries vs estimated primary lookups |
| `search_suffix_nxdomain_ratio` | Share of queries that are search NXDOMAIN probes |
| `avg_fqdn_latency_savings_ms` | Average system − absolute_fqdn latency |
| `worst_case_resolve_budget_ms` | Theoretical max delay (search × attempts × timeout × types) |
| `dual_stack_overhead_ratio` | AAAA share of queries |
| `per_record` | Per-name dot count, search-first flag, mode latencies |

Errors are tracked via `dns_debug_queries_total` with `outcome` label (`success`, `error`, `nxdomain`, `timeout`) — there is no separate errors counter.

## 6. Automated diagnosis

```bash
curl -s http://localhost:8080/tests/<test_id>/diagnosis | jq
```

Review:

- `signals` — triggered indicators (FQDN faster, high search NXDOMAIN, timeouts in search modes)
- `severity` — `low` / `medium` / `high`
- `likely_ndots_search_issue` — boolean verdict
- `recommendations` — actionable fixes (trailing dot, reduce ndots, drop redundant AAAA)
- `analytics` — full `NdotsSearchAnalytics`

## 7. Global summary and health

```bash
curl -s http://localhost:8080/health | jq
curl -s http://localhost:8080/summary | jq
curl -s http://localhost:8080/metrics | grep dns_debug
```

## 8. MTR network path (optional)

When `MTR_ENABLED=true` or for on-demand runs via API:

```bash
# health shows mtr_enabled / mtr_service_name
curl -s http://localhost:8080/health | jq '.mtr_enabled, .mtr_service_name'

# latest MTR report (404 until first run completes)
curl -s http://localhost:8080/mtr | jq '.status, .parsed_hops'

# trigger on-demand (202); optional overrides
curl -s -X POST "http://localhost:8080/mtr?count=5"

# history of completed runs
curl -s http://localhost:8080/mtr/runs | jq 'length'

# MTR metrics
curl -s http://localhost:8080/metrics | grep dns_debug_mtr
```

Requires `mtr-tiny` in the image and `cap_add: NET_RAW` in compose. MTR resolves the target hostname via container `/etc/resolv.conf` — same DNS path as application traffic.

## Autonomous mode notes

When `AUTONOMOUS_MODE=true`:

- `POST /tests` and `DELETE /tests/{id}` return **403**
- Default `test_id` is `autonomous`
- Use `curl http://localhost:8080/tests/autonomous` and `/tests/autonomous/diagnosis`

## Cache heuristic disclaimer

`possible_cache_hit_ratio` and `dns_debug_possible_cached_response_total` indicate repeat queries were **faster than the first** — this is a latency heuristic, not visibility into Docker DNS cache internals.
