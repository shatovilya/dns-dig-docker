# DNS Debug — DNS diagnostics inside a Docker container

**Current release:** [v0.5.2](docs/releases/0.5.2.md) — see [CHANGELOG.md](CHANGELOG.md) and [docs/releases/](docs/releases/README.md) for release history and playbook.

A service for observing DNS behavior **from inside a container** through the standard Docker DNS (`127.0.0.11`). It does not change anything on the host and does not replace the resolver.

Helps you understand: which extra queries are generated, how many errors and NXDOMAIN responses occur, what the latency looks like, and how normal resolution differs from absolute FQDN (with a trailing dot).

## Features

- Snapshot of the container DNS environment from `/etc/resolv.conf` (`GET /resolver`)
- Background DNS load tests (RPS, concurrency, duration)
- Compare resolve modes: `system` (as in the app), `absolute_fqdn` (no search domains), and overridden `ndots` (e.g. 4 and 5)
- Classification of "noisy" queries: search suffix, duplicates, redundant AAAA
- Indirect cache hints from latency (heuristic, not Docker DNS internals)
- Prometheus metrics at `http://localhost:8080/metrics`
- **MTR diagnostics** for the TCP path to a target service (`GET/POST /mtr`) — complements DNS, does not change the resolver

## Constraints

The project intentionally **does not use**:

- `dns:` and `dns_search:` in `docker-compose.yml`
- `network_mode: host`
- Sidecar resolvers (unbound, bind, dnsmasq)
- Modifications to `/etc/resolv.conf` in the container

Only the standard Docker bridge network and the built-in embedded DNS.

## Quick start

```bash
cp .env.example .env
# Edit .env — set your domains (see "Operating modes")
docker compose up -d --build
```

Verify:

```bash
curl -s http://localhost:8080/health
curl -s http://localhost:8080/resolver
```

`docker compose up` starts **PostgreSQL** (internal service) and the app with `DNS_DEBUG_DB_ENABLED=true` by default. Historical UI data is stored in PostgreSQL with a **7-day retention** policy (configurable via `DNS_DEBUG_DB_RETENTION_DAYS`). Cleanup runs at startup and every hour (`DNS_DEBUG_DB_CLEANUP_INTERVAL_SECONDS`).

Default container resource limits (`deploy.resources.limits`):

| Service | Memory | CPU |
|---------|--------|-----|
| `dns-debugger` | 512M | 1.0 |
| `postgres` | 256M | 0.25 |
| `prometheus` (monitoring profile) | 256M | 0.25 |

Raise limits in `docker-compose.yml` if autonomous mode or high RPS needs more headroom.

To run without PostgreSQL (file-only snapshots, backward compatible):

```env
DNS_DEBUG_DB_ENABLED=false
```

### PostgreSQL historical persistence

| Setting | Default | Purpose |
|---------|---------|---------|
| `DNS_DEBUG_DB_ENABLED` | `true` in compose | Use PostgreSQL for historical snapshots and aggregates |
| `DNS_DEBUG_DB_RETENTION_DAYS` | `7` | **Day-based rotation:** delete persisted data older than N days (1–365) |
| `DNS_DEBUG_DB_CLEANUP_ENABLED` | `true` | Automatic retention cleanup |
| `DNS_DEBUG_DB_CLEANUP_INTERVAL_SECONDS` | `3600` | Periodic cleanup interval |
| `DNS_DEBUG_DB_IMPORT_FILES_ON_STARTUP` | `true` | Import existing JSON snapshots from `SNAPSHOT_DIR` into PG |

Postgres is **not** published to the host by default (internal Docker network only). Data volume: `postgres_data`.

Retention deletes `historical_snapshots` older than the window (child aggregate tables cascade). Orphan MTR rows without snapshots are also pruned. Live DNS tests, Prometheus metrics, and in-memory event buffers are unaffected.

See [AGENT.md](AGENT.md) for full env var list and schema overview.

### Optional Web UI

Enable in `.env`:

```env
DNS_DEBUG_UI_ENABLED=true
DNS_DEBUG_UI_BASE_PATH=/dns-debug
API_AUTH_ENABLED=false
```

Restart and open the dashboard:

```
http://localhost:8080/dns-debug/
```

JSON API: `http://localhost:8080/dns-debug/api/ui/overview`

**View modes:** Live (auto-refresh toggle, KPI trends, 3-tier IA), Historical (PostgreSQL-backed snapshots with **7-day retention**, or file snapshots when DB disabled), Compare (full panel deltas via `/api/ui/compare`). **Languages:** English and Russian — header switcher `EN | RU` (`DNS_DEBUG_UI_I18N_*`). See [AGENT.md](AGENT.md) and AI skills [qa-ui](.ai/skills/qa-ui/SKILL.md) / [ux-designer](.ai/skills/ux-designer/SKILL.md). **Pre-release UX workflow** (5 stages before shipping UI changes): [AGENT.md → Pre-release UX workflow](AGENT.md#pre-release-ux-workflow), [debugging-checklist.md §10](.ai/skills/dns-debug/debugging-checklist.md).

External service port: **8080** by default; set `DNS_DEBUG_HOST_PORT` in `.env` to publish a different host port (container stays on `8080`).

## MTR diagnostics

Periodic or on-demand TCP MTR from the same container (bridge + embedded DNS). MTR resolves `MTR_SERVICE_NAME` via the container's `/etc/resolv.conf` — project DNS invariants are preserved.

In `.env`:

```env
MTR_ENABLED=true
MTR_SERVICE_NAME=api.example.com
MTR_SERVICE_PORT=443
MTR_COUNT=20
MTR_INTERVAL_SECONDS=300
```

`MTR_ENABLED=true` starts a background loop on startup. MTR requires `cap_add: NET_RAW` in `docker-compose.yml` (already added).

```bash
# latest run (404 if none yet)
curl -s http://localhost:8080/mtr

# completed run history
curl -s http://localhost:8080/mtr/runs

# manual run (202 + run_id); optional query parameters
curl -s -X POST "http://localhost:8080/mtr"
curl -s -X POST "http://localhost:8080/mtr?service_name=other.example.com&port=443&count=10"
```

Command: `mtr -rzbw HOST --tcp -P PORT -c N` (no shell). Parallel runs are blocked by a mutex — if busy, `POST /mtr` returns **409**.

Metrics: `dns_debug_mtr_last_run_timestamp`, `dns_debug_mtr_last_exit_code`, `dns_debug_mtr_runs_total`.

## Operating modes

### Mode A — autonomous (recommended for continuous monitoring)

The test starts automatically on container startup and runs continuously until stopped.

In `.env`:

```env
AUTONOMOUS_MODE=true
AUTONOMOUS_RECORDS=["your-service.example.com","db.internal"]
AUTONOMOUS_RPS=5
AUTONOMOUS_CONCURRENCY=3
AUTONOMOUS_QUERY_TYPES=["A","AAAA"]
AUTONOMOUS_RESOLVE_MODES=["system","absolute_fqdn"]
AUTONOMOUS_NDOTS_VALUES=[4,5]
```

`AUTONOMOUS_RECORDS` — JSON array or comma-separated list: `host1.com,host2.com`

`AUTONOMOUS_NDOTS_VALUES` — additional modes `ndots:4`, `ndots:5`, etc. (see "ndots testing"). Empty list `[]` — only `resolve_modes` without ndots override.

```bash
docker compose up -d --build
```

Real-time monitoring:

```bash
# autonomous test status (default test_id is "autonomous")
curl -s http://localhost:8080/tests/autonomous

# metrics
curl -s http://localhost:8080/metrics | grep dns_debug

# summary
curl -s http://localhost:8080/summary
```

In autonomous mode:

- `POST /tests` returns **403** (manual tests disabled)
- `DELETE /tests/{id}` returns **403** (stop via `docker compose down`)
- metrics have label `test_id="autonomous"`

### Mode B — manual (one-off diagnostics)

Set `AUTONOMOUS_MODE=false` in `.env`, then start a test via the API:

```bash
curl -s -X POST http://localhost:8080/tests \
  -H "Content-Type: application/json" \
  -d '{
    "test_name": "smoke-test",
    "records": ["example.com"],
    "query_types": ["A"],
    "resolve_modes": ["system", "absolute_fqdn"],
    "ndots_values": [4, 5],
    "rps": 2,
    "concurrency": 2,
    "duration_seconds": 5,
    "timeout_seconds": 2
  }'
```

Response: `{"test_id":"...","status":"started"}`

Test status:

```bash
curl -s http://localhost:8080/tests
curl -s http://localhost:8080/tests/<test_id>
```

Stop:

```bash
curl -s -X DELETE http://localhost:8080/tests/<test_id>
```

Or with defaults from `.env` (`DEFAULT_*` fields):

```bash
curl -s -X POST http://localhost:8080/tests
```

## Where to look

| Task | Command |
|------|---------|
| Container DNS environment | `curl http://localhost:8080/resolver` |
| Live test status | `curl http://localhost:8080/tests/autonomous` or `/tests/<test_id>` |
| Summary across all tests | `curl http://localhost:8080/summary` |
| Prometheus metrics | `curl http://localhost:8080/metrics \| grep dns_debug` |

### Prometheus UI (optional)

```bash
docker compose --profile monitoring up -d
```

UI: http://localhost:9091

Example metrics: `dns_debug_queries_total`, `dns_debug_noisy_queries_total`, `dns_debug_possible_cached_response_total`.

## How to read results

### `/resolver`

| Field | Meaning |
|-------|---------|
| `nameservers` | DNS servers from `/etc/resolv.conf` (in Docker usually `127.0.0.11`) |
| `search` | Search domains — suffixes appended to relative names |
| `options` | Resolver options (e.g. `ndots:5`, `edns0`) |
| `ndots` | Parsed `ndots` value from `options` (or `null` if unset) |
| `timeout_seconds` | Per-attempt timeout from `options timeout:N` (glibc default: 5) |
| `attempts` | Number of attempts from `options attempts:N` (glibc default: 2) |
| `raw` | Full contents of `/etc/resolv.conf` |

If `search` is non-empty, a query for `myservice` (no trailing dot) may trigger attempts like `myservice.<search_domain>`. The `absolute_fqdn` mode (`myservice.cluster.local.`) lets you compare the volume of "extra" queries.

### Noisy queries (`noise_type`)

| Type | When counted as noise |
|------|----------------------|
| `search_suffix_query` | Explicit check of a name with search suffix |
| `search_suffix_nxdomain` | Search suffix probe returned NXDOMAIN |
| `duplicate_query` | Repeat of the same query within a short interval (2 s) |
| `empty_answer` | Success but 0 answers |
| `aaaa_noise` | AAAA query when A already resolved successfully |
| `eventual_fqdn_success` | search mode (`system`, `ndots:*`) failed but `absolute_fqdn` for the same name succeeded |

### ndots testing

The `ndots` option in `/etc/resolv.conf` sets a threshold: names with **fewer** than `ndots` dots try search domains first; with **greater than or equal** — FQDN first.

The service **does not modify** `/etc/resolv.conf`, but can programmatically override `ndots` in dnspython to compare scenarios. Set via `NDOTS_VALUES` (or `ndots_values` in the API) — for each value a `ndots:N` mode is created alongside `system` and `absolute_fqdn`.

Example `.env`:

```env
AUTONOMOUS_RESOLVE_MODES=["system","absolute_fqdn"]
AUTONOMOUS_NDOTS_VALUES=[4,5]
DEFAULT_NDOTS_VALUES=[4,5]
```

| Name | Dots | ndots=4 | ndots=5 |
|------|------|---------|---------|
| `myservice` | 0 | search first | search first |
| `kubernetes.default.svc.cluster.local` | 4 | **FQDN first** | search first |

Compare `by_resolve_mode` in `GET /tests/{id}` and metrics `dns_debug_queries_total{resolve_mode="ndots:4"}` vs `ndots:5`. Differences in NXDOMAIN, latency, and `noisy_query_ratio` show the impact of the ndots threshold.

### ndots/search diagnosis (Docker Compose)

Analytics based on the [ndots/search checklist](https://fastfox.pro/blog/tutorials/k8s-dns-ndots-search-latency/) for scenarios with long names, frequent queries, and Docker DNS degradation.

**Automatic verdict:**

```bash
curl -s http://localhost:8080/tests/autonomous/diagnosis | jq
```

Response includes:
- `signals` — triggered indicators (FQDN faster, search NXDOMAIN, timeouts only in search modes, etc.)
- `severity` — `low` / `medium` / `high`
- `likely_ndots_search_issue` — probable ndots/search problem
- `recommendations` — what to do (trailing dot, lower ndots, drop redundant AAAA)
- `analytics` — full analytics

**Aggregate in summary** (`GET /tests/{id}` → `summary.ndots_search_analytics`):

| Field | Meaning |
|-------|---------|
| `query_amplification_ratio` | How many times actual QPS exceeds "needed" lookups |
| `search_suffix_nxdomain_ratio` | Share of extra NXDOMAIN on search suffix |
| `avg_fqdn_latency_savings_ms` | Average gain of `absolute_fqdn` over `system` |
| `worst_case_resolve_budget_ms` | Theoretical max delay before bare FQDN (search × attempts × timeout × A/AAAA) |
| `dual_stack_overhead_ratio` | Share of AAAA queries |
| `per_record` | Per-name profile: `dot_count`, `search_first`, `fqdn_latency_delta_ms`, `ndots_latency_deltas` |

**Example for a long name under load:**

```bash
curl -s -X POST http://localhost:8080/tests -H "Content-Type: application/json" -d '{
  "records": ["kubernetes.default.svc.cluster.local", "api.example.com"],
  "query_types": ["A", "AAAA"],
  "resolve_modes": ["system", "absolute_fqdn"],
  "ndots_values": [4, 5],
  "rps": 20,
  "concurrency": 10,
  "duration_seconds": 30,
  "timeout_seconds": 2
}'
curl -s http://localhost:8080/tests/<test_id>/diagnosis | jq '.signals, .recommendations'
```

**Analytics Prometheus metrics:**
- `dns_debug_fqdn_latency_delta_ms` — delta system vs absolute_fqdn per record
- `dns_debug_query_amplification_ratio` — amplification per test
- `dns_debug_search_suffix_nxdomain_ratio`
- `dns_debug_worst_case_resolve_budget_ms` — from resolv.conf at startup

### `possible_cached_response_total`

This is **not** a count of the real Docker DNS cache. Heuristic: if a repeat query to the same name is noticeably faster than the first — possible cache hit. Use as a signal, not as a precise fact.

### Practical takeaways

- Difference between `ndots:4` and `ndots:5` for names at the threshold — check whether extra search queries are generated
- High `fqdn_wins` or `eventual_fqdn_success` — use trailing-dot FQDN in app configs
- Rising `p95_latency_ms` and `error_rate` with higher RPS — resolver or upstream overloaded
- High `noisy_query_ratio` — extra queries from search domains, duplicates, or dual-stack AAAA

## Environment variables

Copy `.env.example` to `.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTONOMOUS_MODE` | `false` | Auto-start continuous test |
| `AUTONOMOUS_RECORDS` | `[]` | Domains for autonomous mode (required when `true`) |
| `AUTONOMOUS_RPS` | `10` | Queries per second |
| `AUTONOMOUS_CONCURRENCY` | `5` | Concurrent queries |
| `AUTONOMOUS_QUERY_TYPES` | `A,AAAA` | DNS query types |
| `AUTONOMOUS_RESOLVE_MODES` | `system,absolute_fqdn` | Resolve modes |
| `AUTONOMOUS_NDOTS_VALUES` | `[]` | Extra `ndots:N` modes (e.g. `[4,5]`) |
| `DEFAULT_NDOTS_VALUES` | `[]` | Default `ndots_values` for `POST /tests` |
| `DEFAULT_RPS` | `10` | Defaults for manual `POST /tests` |
| `DEFAULT_CONCURRENCY` | `5` | |
| `DEFAULT_DURATION_SECONDS` | `60` | |
| `DEFAULT_RECORDS` | see `.env.example` | |
| `DIAGNOSIS_FQDN_LATENCY_DELTA_MS` | `50` | Threshold for system vs absolute_fqdn delta (ms) |
| `DIAGNOSIS_SEARCH_NXDOMAIN_RATIO` | `0.1` | Threshold for search_suffix NXDOMAIN ratio |
| `DIAGNOSIS_ERROR_RATE_THRESHOLD` | `0.05` | error_rate threshold for degradation signal |
| `DIAGNOSIS_AMPLIFICATION_RATIO` | `2.0` | Query amplification threshold |
| `METRICS_ENABLED` | `true` | `/metrics` endpoint |
| `MTR_ENABLED` | `false` | Periodic MTR on startup |
| `MTR_SERVICE_NAME` | `""` | Hostname for mtr (required when `MTR_ENABLED=true`) |
| `MTR_SERVICE_PORT` | `443` | TCP port (`-P`) |
| `MTR_COUNT` | `20` | Cycles (`-c`) |
| `MTR_INTERVAL_SECONDS` | `300` | Background run interval |
| `MTR_TIMEOUT_SECONDS` | `120` | Subprocess timeout |
| `MTR_MAX_HISTORY` | `10` | Number of runs kept in memory |
| `DNS_DEBUG_UI_ENABLED` | `false` | Enable built-in Web UI at `/dns-debug/` |
| `DNS_DEBUG_UI_READONLY` | `true` | View-only dashboard (no POST/DELETE from browser) |
| `DNS_DEBUG_UI_REFRESH_SECONDS` | `5` | Client polling interval for UI panels |
| `DNS_DEBUG_UI_I18N_ENABLED` | `true` | Enable EN/RU UI localization |
| `DNS_DEBUG_UI_DEFAULT_LANG` | `en` | Default UI language |
| `DNS_DEBUG_UI_SUPPORTED_LANGS` | `en,ru` | Supported UI languages |
| `DNS_DEBUG_UI_LOCALE_STORAGE_ENABLED` | `true` | Persist language in browser |

## API security

The service supports optional API hardening via environment variables. Default: `API_AUTH_ENABLED=false` (backward compatible). Production: set `API_AUTH_ENABLED=true` and configure credentials.

See [docs/SECURITY.md](docs/SECURITY.md) for roles, Prometheus protection, rate limits, and migration notes.

```bash
# With auth enabled
curl -s -H "Authorization: Bearer <token>" http://localhost:8080/tests
```

## AI project files

For AI agents (Cursor, Claude Code, etc.) the repository includes local guidance files — they describe architecture and project constraints without changing code:

| File | Purpose |
|------|---------|
| `AGENT.md` | Main engineering brief: constraints, architecture, resolution model, AI roles, pre-release UX workflow |
| `CURSOR.md` | Cursor-specific role routing and doc sync |
| `CLAUDE.md` | Short repo guide for Claude Code |
| `.ai/skills/dns-debug/SKILL.md` | Skill for DNS logic, analytics, and metrics |
| `.ai/skills/qa-ui/SKILL.md` | QA engineer — UI acceptance and regression |
| `.ai/skills/ux-designer/SKILL.md` | UX designer — dashboard IA and states |
| `.ai/skills/dns-debug/debugging-checklist.md` | Operational checklist with curl examples; §9 QA acceptance; §10 pre-release UX workflow |
| `.ai/skills/dns-debug/metrics-reference.md` | Prometheus metrics reference (exact names from `app/metrics.py`) |
| `.cursor/rules/dns-debug-project.mdc` | Cursor rule with project invariants |
| `.cursor/rules/qa-ux-gates.mdc` | QA/UX enforcement gates for UI work |

## Project structure

```
app/                  # FastAPI application
app/ui/               # Optional Web UI (templates, static, aggregators)
prometheus/           # Prometheus config (monitoring profile)
docker-compose.yml
.env.example
```

## API (quick reference)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service status |
| GET | `/live` | Liveness probe |
| GET | `/ready` | Readiness probe |
| GET | `/resolver` | Snapshot of `/etc/resolv.conf` |
| POST | `/tests` | Start test (unavailable in autonomous mode) |
| GET | `/tests` | List tests |
| GET | `/tests/{id}` | Test details |
| GET | `/tests/{id}/diagnosis` | ndots/search auto-diagnosis |
| DELETE | `/tests/{id}` | Stop test |
| GET | `/summary` | Global summary |
| GET | `/metrics` | Prometheus metrics |
| GET | `/mtr` | Latest MTR run |
| GET | `/mtr/runs` | MTR run history |
| POST | `/mtr` | On-demand MTR (202) |
