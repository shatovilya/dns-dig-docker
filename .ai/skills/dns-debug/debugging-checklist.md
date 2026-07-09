# DNS Debug — Operational Checklist

Step-by-step workflow for diagnosing DNS behavior inside the Docker container. Assumes core service on `http://localhost:8080`. Web UI steps apply when `DNS_DEBUG_UI_ENABLED=true` (default base path `/dns-debug`).

- Bundling React/Vue or breaking core API when UI is disabled
- Presenting UI cache cards as confirmed Docker DNS cache hits
- Weakening `API_AUTH_ENABLED` defaults or disabling write rate limits without explicit request
- New endpoints without security classification

## 0. API authentication (when `API_AUTH_ENABLED=true`)

```bash
# Bearer token
curl -s -H "Authorization: Bearer <token>" http://localhost:8080/tests

# API key
curl -s -H "X-API-Key: <key>" http://localhost:8080/summary
```

Local dev without auth: `API_AUTH_ENABLED=false` in `.env` (explicit only).

Production: configure `API_STATIC_CREDENTIALS_JSON` — see [docs/SECURITY.md](../../docs/SECURITY.md).

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
| `options` | Raw resolver options (`edns0`, etc.) — feeds EDNS analytics in UI |

Force refresh after container restart:

```bash
curl -s "http://localhost:8080/resolver?refresh=true" | jq
```

## 2. Reproduce under load

Start a manual test (requires `AUTONOMOUS_MODE=false`):

```bash
curl -s -X POST http://localhost:8080/tests \
  -H "Authorization: Bearer <operator-token>" \
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

```bash
curl -s http://localhost:8080/tests/<test_id> | jq '.counters.by_resolve_mode'
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

```bash
curl -s http://localhost:8080/tests/<test_id> | jq '.summary.noise_counts, .summary.noisy_query_ratio'
curl -s http://localhost:8080/metrics | grep dns_debug_noisy_queries_total
```

Remember: `search_suffix_*` types come from **diagnostic probes**, not primary lookups.

## 5. Latency, errors, and diagnosis

```bash
curl -s http://localhost:8080/tests/<test_id> | jq '{
  success_rate: .summary.success_rate,
  error_rate: .summary.error_rate,
  nxdomains: .summary.nxdomains,
  avg_latency_ms: .summary.avg_latency_ms,
  p95_latency_ms: .summary.p95_latency_ms,
  possible_cache_hit_ratio: .summary.possible_cache_hit_ratio
}'

curl -s http://localhost:8080/tests/<test_id>/diagnosis | jq
```

Errors use `dns_debug_queries_total` with `outcome` label — there is no separate errors counter.

## 6. Prometheus /metrics

```bash
curl -s http://localhost:8080/health | jq
curl -s http://localhost:8080/summary | jq
curl -s http://localhost:8080/metrics | grep dns_debug
```

See [metrics-reference.md](metrics-reference.md) for PromQL examples and UI mapping.

## 7. MTR network path (optional)

When `MTR_ENABLED=true` or for on-demand runs:

```bash
curl -s http://localhost:8080/health | jq '.mtr_enabled, .mtr_service_name'
curl -s http://localhost:8080/mtr | jq '.status, .parsed_hops'
curl -s -X POST "http://localhost:8080/mtr?count=5"
curl -s http://localhost:8080/mtr/runs | jq 'length'
curl -s http://localhost:8080/metrics | grep dns_debug_mtr
```

Requires `mtr-tiny` in the image and `cap_add: NET_RAW`. MTR resolves the target via container `/etc/resolv.conf`.

## 8. Web UI walkthrough (`DNS_DEBUG_UI_ENABLED=true`)

Enable in `.env`:

```bash
DNS_DEBUG_UI_ENABLED=true
DNS_DEBUG_UI_BASE_PATH=/dns-debug
DNS_DEBUG_UI_READONLY=true
DNS_DEBUG_UI_REFRESH_SECONDS=5
```

Open dashboard:

```
http://localhost:8080/dns-debug/
```

### Global controls

- **Theme toggle** — dark/light; persisted in `localStorage`
- **Filters** — test_id, time range, resolve_mode, query_type (apply across sections)
- **Auto-refresh** — polls JSON API every `DNS_DEBUG_UI_REFRESH_SECONDS`

### What to check in each section

| Section | Look for |
|---------|----------|
| **1. Overview** | Health status, active vs completed tests, error count, success/failed ratio, last update timestamp |
| **2. DNS latency** | Line chart over time; p50/p95/p99; spikes by resolver and query type |
| **3. EDNS analytics** | edns0–edns5 query/error counts, avg latency, error rate — correlate with `/resolver` options |
| **4. Error analysis** | Error rate vs QPS; heatmap resolver × error class (timeout, nxdomain, servfail, refused, truncated, malformed, unexpected_rcode) |
| **5. Garbage / noisy** | Six noise types; top noisy domains; search/internal suffix noise; useful vs garbage ratio |
| **6. Cache behavior** | Heuristic hit/miss — **not real cache**; effectiveness by resolver; repeat-query correlation |
| **7. DNS record drilldown** | Per-FQDN table with status badges; filter/sort failing records |
| **8. Load / density** | errors/latency/success vs qps; saturation; burst test panel |
| **9. MTR diagnostics** | Hop table, loss %, latency stats, problem hops, verdict card (local/upstream/destination/unstable/packet_loss) |
| **10. Rankings** | Worst resolvers, domains, query types, MTR targets |

When `DNS_DEBUG_UI_READONLY=true`, use core API for `POST /tests` and `POST /mtr` — the UI is view-only.

## 9. UI JSON API (curl examples)

Base path default: `/dns-debug`. All endpoints accept optional query params: `test_id`, `from`, `to`, `resolve_mode`, `query_type`.

```bash
BASE=http://localhost:8080/dns-debug/api/ui

curl -s "$BASE/overview" | jq
curl -s "$BASE/dns-latency?test_id=<test_id>" | jq '.p50, .p95, .p99'
curl -s "$BASE/edns" | jq
curl -s "$BASE/errors" | jq '.by_resolver, .by_error_class'
curl -s "$BASE/garbage" | jq '.noise_counts, .useful_vs_garbage_ratio'
curl -s "$BASE/cache" | jq '.hit_ratio, .disclaimer'
curl -s "$BASE/records?test_id=<test_id>" | jq '.records[:5]'
curl -s "$BASE/load" | jq
curl -s "$BASE/mtr" | jq '.verdict, .hops'
curl -s "$BASE/rankings" | jq
```

Returns **404** or empty stubs when `DNS_DEBUG_UI_ENABLED=false`.

## 10. Symptom → signal → action

| Symptom | Signal | Action |
|---------|--------|--------|
| Slow in-cluster DNS | High `system` latency vs `absolute_fqdn` in UI latency section | Use trailing dot; reduce search list; check ndots |
| Many extra queries | High `dns_debug_noisy_queries_total` / garbage section | Review search probes; fix short names |
| Timeouts under load | Error analysis heatmap + load section saturation | Lower RPS; increase timeout; check upstream resolver |
| AAAA overhead | `aaaa_noise` in garbage section | Drop redundant AAAA lookups |
| Path issues to external service | MTR section verdict + hop loss | Investigate upstream/network; not DNS resolver config |
| Cache card looks good | `possible_cached_response_total` rising | Treat as weak heuristic only — verify with repeat-query latency chart |

---

## DNS symptoms checklist

| Symptom | Check | Likely cause |
|---------|-------|--------------|
| Intermittent resolution failures | Error analysis by domain | Upstream resolver or record TTL issue |
| All queries fail | `/resolver` nameservers | Container DNS misconfiguration (not this service) |
| Only short names fail | Compare system vs absolute_fqdn | Search list / ndots misconfiguration |
| Cluster-local names slow | Garbage section search suffix noise | Use FQDN with trailing dot in apps |
| External names slow | MTR + DNS latency by resolver | Network path or upstream DNS |

## Cache symptoms checklist

| Symptom | Check | Interpretation |
|---------|-------|----------------|
| First query slow, repeats fast | `possible_cache_hit_ratio`, cache UI card | Heuristic signal — may be cache or warm path |
| Cache ratio high but errors also high | Error + cache sections together | Unrelated issues — don't attribute errors to cache |
| No cache signal at all | `dns_debug_possible_cached_response_total` flat | Normal for unique names every query |
| Stale answers suspected | Not detectable by this service | Requires application-level TTL tracking |

**Reminder:** Cache metrics are latency heuristics, not Docker DNS cache introspection.

## Suffix / noise symptoms checklist

- [ ] `dns_debug_search_suffix_nxdomain_ratio` > 0.1
- [ ] Top noisy domains include `.svc.cluster.local` suffix variants
- [ ] `search_suffix_query` dominates noise counts
- [ ] `dns_debug_query_amplification_ratio` > 2
- [ ] UI Garbage section shows high useful vs garbage ratio skew

**Action:** Prefer `absolute_fqdn` in apps, increase ndots, or shorten search list at orchestration level.

## NXDOMAIN storm checklist

- [ ] Spike in `dns_debug_queries_total{outcome="nxdomain"}`
- [ ] Error heatmap shows nxdomain column hot
- [ ] Correlates with specific domain in Record drilldown
- [ ] Search suffix probes returning NXDOMAIN (`search_suffix_nxdomain`)

```bash
curl -s http://localhost:8080/metrics | grep 'outcome="nxdomain"'
```

**Action:** Fix record names; verify search domains aren't generating false NXDOMAIN probes.

## Timeout analysis checklist

- [ ] `dns_debug_queries_total{outcome="timeout"}` rising
- [ ] Timeouts correlate with high QPS in Load section
- [ ] `timeout_seconds` in test config vs `resolver.timeout_seconds`
- [ ] Worst-case budget: `dns_debug_worst_case_resolve_budget_ms`

```promql
sum(rate(dns_debug_queries_total{outcome="timeout"}[5m])) by (test_id, resolve_mode)
```

**Action:** Lower RPS, increase per-query timeout, check upstream resolver health.

## EDNS incompatibility checklist

- [ ] UI EDNS section shows errors concentrated at one level (edns0–edns5)
- [ ] `/resolver` options missing `edns0`
- [ ] Errors spike after enabling EDNS in upstream
- [ ] `servfail` or `truncated` in error class matrix

**Action:** Test with different upstream; document EDNS level in incident report.

## Resolver-specific failures checklist

- [ ] Errors isolated to one `resolve_mode` label
- [ ] Per-resolver ranking shows one resolver degraded
- [ ] `absolute_fqdn` works but `system` fails → search-related
- [ ] All modes fail → embedded DNS or upstream issue

## Load-related degradation checklist

- [ ] Error rate rises with QPS (Load section)
- [ ] Latency p99 spikes before error rate (saturation curve)
- [ ] Success rate drops at burst RPS
- [ ] `dns_debug_active_tests` > 0 during overlap

```bash
# Run stepped RPS test and compare UI Load section
curl -s -X POST http://localhost:8080/tests -H "Content-Type: application/json" \
  -d '{"records":["example.com"],"rps":50,"concurrency":20,"duration_seconds":60,...}'
```

## MTR path degradation checklist

- [ ] `dns_debug_mtr_last_exit_code` != 0
- [ ] Hop with loss > 5% highlighted in UI
- [ ] Verdict: `unstable_path` or `packet_loss_suspected`
- [ ] Latency stdev high on mid-path hops
- [ ] Multiple MTR runs show inconsistent hop count (`dns_mtr_path_changes_total` when implemented)

```bash
curl -s http://localhost:8080/mtr/runs | jq '.[].parsed_hops[] | select(.loss_percent > 5)'
```

## UI troubleshooting checklist

| Problem | Check | Fix |
|---------|-------|-----|
| Dashboard 404 | `DNS_DEBUG_UI_ENABLED` in container env | Set `true`, restart |
| JSON API 404 | Base path mismatch | Verify `DNS_DEBUG_UI_BASE_PATH` |
| Empty charts | No active/completed tests | Start test via `POST /tests` |
| Stale data | `last_update` timestamp | Check `DNS_DEBUG_UI_REFRESH_SECONDS`; hard refresh |
| Cannot start test from UI | `DNS_DEBUG_UI_READONLY=true` | Use core API `POST /tests` |
| Theme not persisting | Browser localStorage | Expected — not server-side |
| Charts unreadable | Theme toggle | Switch dark/light mode |

## Prometheus scraping checklist

- [ ] Target: `http://<container>:8080/metrics`
- [ ] Scrape interval: 15–30 s for DNS tests; 60 s for idle
- [ ] Key alerts: error rate, p95 latency, amplification ratio, MTR exit code
- [ ] Label cardinality: watch `test_id` and `record` label growth
- [ ] No separate `errors_total` — use `dns_debug_queries_total{outcome=~"error|timeout|nxdomain"}`
- [ ] Cache metrics: use for trends only, not SLO gates

Example scrape config snippet:

```yaml
- job_name: dns-debug
  scrape_interval: 15s
  static_configs:
    - targets: ['dns-debug:8080']
  metrics_path: /metrics
```

## Autonomous mode notes

When `AUTONOMOUS_MODE=true`:

- `POST /tests` and `DELETE /tests/{id}` return **403**
- Default `test_id` is `autonomous`
- UI filters: use `test_id=autonomous`

```bash
curl -s http://localhost:8080/tests/autonomous | jq
curl -s http://localhost:8080/tests/autonomous/diagnosis | jq
```

## Cache heuristic disclaimer

`possible_cache_hit_ratio`, UI cache cards, and `dns_debug_possible_cached_response_total` indicate repeat queries were **faster than the first** — a latency heuristic, not visibility into Docker DNS cache internals.
