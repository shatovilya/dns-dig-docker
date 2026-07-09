# DNS Debug — Claude Code Guide

## What this repo is

A FastAPI DNS debug / stress / observability toolkit that runs **inside a Docker container** and measures DNS behavior through the embedded resolver (`127.0.0.11`). It load-tests names under configurable RPS, compares resolve modes, classifies noisy queries, exposes Prometheus metrics, runs optional MTR TCP path diagnostics, and can serve an **optional** built-in Web UI with charts and tables for visual analysis.

The Web UI is a **feature-flag-driven optional observability layer** for developers, SREs, QA engineers, and analysts — not a required part of the core DNS engine.

## Hard constraints

- No `/etc/resolv.conf` changes
- No `dns:` / `dns_search:` in docker-compose
- No `network_mode: host`
- No sidecar DNS resolvers
- No fake cache claims — `dns_debug_possible_cached_response_total` is a latency heuristic
- Preserve stable core API paths and Prometheus metric names
- Do not break UI JSON contracts for `/api/ui/*` endpoints

## Key modules

```
app/config.py            → settings (autonomous, MTR, UI)
app/resolver_snapshot.py → read resolv.conf
app/dns_runner.py        → run tests, resolve, search probes
app/stats_store.py       → aggregate attempts
app/ndots_analytics.py   → ndots/search analytics + diagnosis
app/mtr_runner.py        → MTR subprocess runs
app/mtr_store.py         → MTR run history
app/api.py               → REST API + UI JSON routes (when enabled)
app/metrics.py           → Prometheus
app/ui/                  → optional dashboard (templates, static, aggregators)
```

## Resolution modes

| Label | Description |
|-------|-------------|
| `system` | Normal resolver with search domains |
| `absolute_fqdn` | Trailing dot, bypasses search |
| `ndots:N` | Programmatic ndots override (from `ndots_values`) |

Search suffix probes are **diagnostic** — they measure overhead, not primary app traffic.

## API

**Core** (port 8080): `/health`, `/resolver`, `/tests`, `/tests/{id}`, `/tests/{id}/diagnosis`, `/summary`, `/metrics`, `/mtr`, `/mtr/runs`

**Web UI** (when `DNS_DEBUG_UI_ENABLED=true`):

| Variable | Default |
|----------|---------|
| `DNS_DEBUG_UI_ENABLED` | `false` |
| `DNS_DEBUG_UI_PORT` | `8088` |
| `DNS_DEBUG_UI_BIND` | `0.0.0.0` |
| `DNS_DEBUG_UI_BASE_PATH` | `/dns-debug` |
| `DNS_DEBUG_UI_READONLY` | `true` |
| `DNS_DEBUG_UI_REFRESH_SECONDS` | `5` |

- Dashboard: `http://localhost:8080/dns-debug/` (default `DNS_DEBUG_UI_BASE_PATH`)
- JSON: `/dns-debug/api/ui/overview`, `/dns-debug/api/ui/dns-latency`, `/edns`, `/errors`, `/garbage`, `/cache`, `/records`, `/load`, `/mtr`, `/rankings`

Set `DNS_DEBUG_UI_ENABLED=false` to run core-only (no UI routes).

## Mandatory invariants

- DNS engine works without UI
- Prometheus metrics always available
- EDNS analytics, garbage accounting, cache heuristic, per-resolver breakdown documented and observable
- MTR observability documented (optional at runtime)
- Background async test execution

## AI assistant rules

1. **Understand project domain** — DNS debugging and observability inside Docker, not generic web dev.
2. **Check DNS and cache invariants first** before any infrastructure or resolver change.
3. **Do not break metrics contract** — use exact `dns_debug_*` names from `app/metrics.py`; update `metrics-reference.md` on changes.
4. **Do not break UI JSON contracts** — additive fields only unless explicitly requested.
5. **Do not remove MTR observability** from docs or aggregators.
6. **Do not remove EDNS detail** or per-resolver breakdown.
7. **Update AI docs synchronously** — `AGENT.md`, `SKILL.md`, `debugging-checklist.md`, `metrics-reference.md`, this file.
8. **Prefer minimal, safe, backward-compatible changes.**
9. **Document trade-offs explicitly** when choosing between simplicity and observability richness.
10. **When in doubt, preserve observability richness** — do not strip dashboard sections or metric labels.
11. **Web UI is optional** — `DNS_DEBUG_UI_ENABLED` feature flag; core must run with UI disabled.
12. **UI audience** — developer, SRE, QA, analyst; readonly by default.

## Safe changes

- Diagnosis thresholds, new noise types, summary fields from existing data
- Optional Web UI sections and read-only JSON aggregators
- Documentation and tests
- Logging improvements
- New env vars documented in all relevant AI files

## Risky changes

- Infrastructure (compose networking, DNS overrides, sidecars)
- Renaming core metrics or API routes
- Modifying resolv.conf or resolver configuration at the OS level
- Misrepresenting cache heuristics as real cache introspection
- Heavy SPA frameworks or mutating UI when `DNS_DEBUG_UI_READONLY=true`
- Removing EDNS, garbage, cache, per-resolver, or MTR observability

## Development

```bash
cp .env.example .env
# Optional: DNS_DEBUG_UI_ENABLED=true
docker compose up -d --build
curl http://localhost:8080/health
# If UI enabled:
# open http://localhost:8080/dns-debug/
```

## Related documentation

| File | Purpose |
|------|---------|
| `AGENT.md` | Full engineering brief, architecture, env vars, UI spec |
| `.ai/skills/dns-debug/SKILL.md` | DNS/MTR/UI analysis skill and change checklist |
| `.ai/skills/dns-debug/debugging-checklist.md` | Operational and UI troubleshooting workflow |
| `.ai/skills/dns-debug/metrics-reference.md` | Prometheus + conceptual metrics + UI mapping |
| `.cursor/rules/dns-debug-project.mdc` | Cursor always-on project rules |
