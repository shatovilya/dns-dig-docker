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
app/db/                  → PostgreSQL persistence (snapshots, aggregates, 7-day retention)
app/snapshot_store.py    → snapshot store (file or PostgreSQL backend)
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
- JSON: `/dns-debug/api/ui/overview`, `/dns-latency`, `/edns`, `/errors`, `/garbage`, `/cache`, `/records`, `/load`, `/mtr`, `/rankings`, `/events`, `/snapshots`, `/compare`
- View modes: **Live** (auto-refresh toggle, KPI trends), **Historical** (PostgreSQL snapshots, **7-day retention**), **Compare** (full panel server-side deltas)
- Dashboard IA: 3-tier zones (`zone-status`, `zone-diagnostics`, `zone-drilldown`) with sticky sub-nav

Set `DNS_DEBUG_UI_ENABLED=false` to run core-only (no UI routes).

## Mandatory invariants

- DNS engine works without UI
- Prometheus metrics always available
- EDNS analytics, garbage accounting, cache heuristic, per-resolver breakdown documented and observable
- MTR observability documented (optional at runtime)
- Background async test execution

## AI role selection

Select a role by task type. UI/historical/compare changes without QA and UX review are **incomplete**.

| Role | Skill | When to apply |
|------|-------|---------------|
| DNS engineer | `.ai/skills/dns-debug/SKILL.md` | DNS logic, metrics, MTR, core API, noise/diagnosis |
| QA engineer | `.ai/skills/qa-ui/SKILL.md` | Acceptance, regression, data correctness, live/historical/compare validation |
| UX designer | `.ai/skills/ux-designer/SKILL.md` | Dashboard IA, states, filters, chart hierarchy, microcopy |

**Large UI change workflow:** UX designer → implement → Stage 1 self-check → UX audit → QA validate → fix pass → release readiness → sync docs.

**Pre-release UX workflow (5 stages):** Self-check → UX review → QA review → Fix pass → Release readiness. UI changes without Stages 1–3 are **incomplete**. See `AGENT.md` → Pre-release UX workflow.

**Release blockers:** P0 data/security issues; P1 misleading observability; P1 responsive laptop breakage (1024–1440px); P1 tablet layout breakage (768px); missing state coverage; skipped UX audit or QA pass; stale AI docs.

**Incomplete task definition:** shipping UI/UX/historical/compare changes without updating the QA skill, UX skill, and relevant AI docs (`AGENT.md`, `debugging-checklist.md`, `CLAUDE.md`, `CURSOR.md`, rules) or without completing the pre-release workflow.

**Release documentation:** UI/UX/i18n/workflow/docs changes must ship with release artifacts (`CHANGELOG.md` + `docs/releases/X.Y.Z.md`). Do not stop at code-only changes. Package user-visible and process-visible changes into release form per [`docs/releases/README.md`](docs/releases/README.md). Stage 5 pre-release ≠ release complete without release doc.

### Doc sync triggers

| Change type | Update |
|-------------|--------|
| Charts, filters, view modes, historical/compare | `qa-ui` + `ux-designer` skills, `debugging-checklist.md`, `AGENT.md` |
| New UI JSON fields or envelope | `AGENT.md`, `debugging-checklist.md`, `qa-ui` skill |
| Dashboard IA or state design | `ux-designer` skill, `AGENT.md` UI section |
| Metrics added/renamed | `metrics-reference.md`, `dns-debug` skill |
| Persistence / retention / PostgreSQL | `dns-debug` skill, `AGENT.md`, `debugging-checklist.md`, `metrics-reference.md`, rules |
| UI/UX/i18n/workflow release | `CHANGELOG.md`, `docs/releases/X.Y.Z.md`, version in `app/main.py`, AI docs |

See `CURSOR.md` for Cursor-specific role routing and the full mandatory sync table.

## AI assistant rules

1. **Understand project domain** — DNS debugging and observability inside Docker, not generic web dev.
2. **Check DNS and cache invariants first** before any infrastructure or resolver change.
3. **Do not break metrics contract** — use exact `dns_debug_*` names from `app/metrics.py`; update `metrics-reference.md` on changes.
4. **Do not break UI JSON contracts** — additive fields only unless explicitly requested.
5. **Do not remove MTR observability** from docs or aggregators.
6. **Do not remove EDNS detail** or per-resolver breakdown.
7. **Update AI docs synchronously** — `AGENT.md`, `SKILL.md`, `debugging-checklist.md`, `metrics-reference.md`, `qa-ui`/`ux-designer` skills when UI changes, this file, `CURSOR.md`.
8. **Prefer minimal, safe, backward-compatible changes.**
9. **Document trade-offs explicitly** when choosing between simplicity and observability richness.
10. **When in doubt, preserve observability richness** — do not strip dashboard sections or metric labels.
11. **Web UI is optional** — `DNS_DEBUG_UI_ENABLED` feature flag; core must run with UI disabled.
12. **UI audience** — developer, SRE, QA, analyst; readonly by default.
13. **Select AI role by task** — DNS → `dns-debug` skill; UI acceptance/regression → `qa-ui`; UX/IA/states → `ux-designer`.
14. **UI tasks need QA/UX review** — historical/compare/filter/chart changes require both skills updated and validation; complete pre-release workflow Stages 1–3 minimum.
15. **Sync on dashboard changes** — update `qa-ui/SKILL.md`, `ux-designer/SKILL.md`, `AGENT.md`, `debugging-checklist.md`, rules, this file, `CURSOR.md`.
16. **Pre-release workflow** — UI changes require 5-stage workflow (self-check → UX audit → QA release readiness → fix pass → release sign-off); see `AGENT.md`.
17. **Release documentation** — treat `CHANGELOG.md` + `docs/releases/X.Y.Z.md` as mandatory deliverable; do not stop at code changes only; see `AGENT.md` → Release documentation.
18. **PostgreSQL retention** — default **7-day** historical retention (`DNS_DEBUG_DB_RETENTION_DAYS`); do not bypass cleanup or break local-postgres dev model without docs sync.
19. **Storage changes** — schema/config/persistence changes require `AGENT.md`, skills, rules, and release doc updates.
20. **UI localization** — treat EN+RU strings as part of UI completeness; update `en.json` + `ru.json` with every user-facing change; verify RU layout, not only EN.
21. **Missing RU strings** — incomplete work; never show raw translation keys in the dashboard.

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
# Optional: DNS_DEBUG_HOST_PORT=18080  # host port in docker compose
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
| `.ai/skills/qa-ui/SKILL.md` | QA engineer — UI acceptance and regression |
| `.ai/skills/ux-designer/SKILL.md` | UX designer — dashboard IA and states |
| `.ai/skills/dns-debug/debugging-checklist.md` | Operational and UI troubleshooting workflow |
| `CURSOR.md` | Cursor role routing and skill paths |
| `.ai/skills/dns-debug/metrics-reference.md` | Prometheus + conceptual metrics + UI mapping |
| `.cursor/rules/dns-debug-project.mdc` | Cursor always-on project rules |
| `.cursor/rules/qa-ux-gates.mdc` | QA/UX enforcement gates for UI work |
