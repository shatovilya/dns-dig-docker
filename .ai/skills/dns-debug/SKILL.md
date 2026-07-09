# DNS Debug Skill

Use this skill when working on DNS resolution logic, load-test execution, ndots/search analytics, Prometheus metrics, or AI documentation for the dns-dig project.

## When to use

- Debugging search domain or ndots-related latency
- Adding or changing noise classification
- Extending diagnosis signals or summary fields
- Adding or documenting Prometheus metrics
- Reviewing changes that touch `dns_runner`, `stats_store`, `ndots_analytics`, or `mtr_runner`

## Project context

FastAPI DNS debug service inside a Docker container. Uses embedded DNS `127.0.0.11` via standard bridge networking. Reads `/etc/resolv.conf` but never modifies it.

**Hard constraints:** no resolv.conf changes, no `dns:`/`dns_search:` in compose, no host network, no sidecar DNS, no fake cache claims.

## Sibling references

- [debugging-checklist.md](debugging-checklist.md) — step-by-step operational flow with curl examples
- [metrics-reference.md](metrics-reference.md) — exact Prometheus metric names and caveats

## Resolution modes

| Label | Source | Notes |
|-------|--------|-------|
| `system` | `resolve_modes` | Application-like resolver with search |
| `absolute_fqdn` | `resolve_modes` | Trailing dot, no search |
| `ndots:N` | `ndots_values` | Programmatic ndots override per value N |

Search probes (`_run_search_probes`) are **diagnostic** — they measure search suffix overhead and are not primary app queries.

## Extension points

### `app/dns_runner.py`

| Function | Purpose |
|----------|---------|
| `expand_work_items` | Cartesian product of records, query types, resolve specs |
| `_resolve` | Single DNS query via dnspython; handles ndots override, search flag, probe mode |
| `_classify_noise` | Maps attempt to `NoiseType` (6 types) |
| `_run_search_probes` | Diagnostic search suffix queries per search domain |
| `_check_cache` | Heuristic repeat-query latency comparison |
| `_execute_query` | Primary query + optional search probes |

### `app/stats_store.py`

| Function | Purpose |
|----------|---------|
| `record_attempt` | Persist `QueryAttempt`, update per-record and per-mode counters |
| `build_summary` | Aggregate `TestSummaryResponse` including noise counts and ndots analytics hook |

### `app/ndots_analytics.py`

| Function | Purpose |
|----------|---------|
| `build_test_analytics` | Compute `NdotsSearchAnalytics` from test state and resolver snapshot |
| `build_diagnosis` | Produce `DiagnosisResponse` with signals, severity, recommendations |

### `app/mtr_runner.py`

| Function | Purpose |
|----------|---------|
| `build_mtr_command` | argv for `mtr -rzbw HOST --tcp -P PORT -c N` (no shell) |
| `run_mtr` | Subprocess exec, parse report, update store and metrics |
| `parse_mtr_report` | Line-parser for tabular `-r` hop output |
| `start_mtr_background` / `cancel_mtr` | Periodic runner lifecycle |
| `trigger_mtr_now` | On-demand run via API (mutex via `asyncio.Lock`) |

### `app/mtr_store.py`

| Function | Purpose |
|----------|---------|
| `create_run` / `finalize_run` | Track running and completed MTR results |
| `get_latest` / `list_runs` | API read paths |

### `app/metrics.py`

| Function | Purpose |
|----------|---------|
| `record_query` | Increment `dns_debug_queries_total`, observe `dns_debug_query_latency_seconds` |
| `record_noisy` | Increment `dns_debug_noisy_queries_total` |
| `record_possible_cache` | Increment `dns_debug_possible_cached_response_total`, observe delta histogram |
| `set_test_analytics` | Set amplification, search NXDOMAIN ratio, FQDN latency deltas |
| `init_from_snapshot` | Set startup gauges from resolver snapshot |
| `set_test_progress` | Update per-test progress gauge |
| `record_mtr_run` | Update MTR gauges and counter |

## Change checklist

Before merging DNS-related changes:

1. **Constraints** — no resolv.conf edits, no compose DNS overrides, no host network, no sidecar
2. **Metric names** — match `metrics-reference.md` exactly; no separate errors counter
3. **Resolve labels** — use `system`, `absolute_fqdn`, `ndots:N` consistently
4. **Search probes** — keep `is_search_probe` separate from primary queries; exclude probes from cache heuristic
5. **Cache honesty** — document heuristic nature; never imply Docker internal cache access
6. **API stability** — existing paths unchanged unless explicitly requested
7. **Noise types** — align with `models.NoiseType` enum values
8. **Docs** — update `AGENT.md`, this skill, or `metrics-reference.md` if behavior changes

## Safe modifications

- New diagnosis thresholds via `config.py` env vars
- Additional noise types (enum + metrics label + classification logic)
- New summary or diagnosis signals derived from existing attempt data
- Histogram bucket tuning (preserve metric names)

## Risky modifications

- Changing how `absolute_fqdn` appends trailing dot
- Altering search probe triggering conditions without updating analytics
- Renaming `outcome` label values on `dns_debug_queries_total`
- Infrastructure changes that bypass `127.0.0.11`
