---
name: qa-ui
description: >-
  QA engineer role for DNS Debug Web UI — acceptance, regression, data correctness,
  live/historical/compare modes, charts, tables, API↔UI consistency, read-only safety.
---

# DNS Debug — QA Engineer (Web UI)

Use this skill when validating DNS Debug Web UI functionality, data correctness, observability UX, regression risks, or producing QA deliverables for dashboard changes.

## When to use

- Acceptance testing before shipping UI changes
- Regression testing after filters, charts, historical mode, or compare mode changes
- Verifying API ↔ UI data consistency (KPIs, percentiles, deltas)
- Validating live / historical / compare mode behavior
- Checking empty, loading, error, stale, and retention-exceeded states
- Security regression: read-only UI, auth on UI JSON routes
- Producing test checklists, bug reports, or UX validation notes

## Artifacts to read first

1. [`AGENT.md`](../../../AGENT.md) — Web UI section, AI roles, dashboard modes
2. [`.ai/skills/dns-debug/debugging-checklist.md`](../dns-debug/debugging-checklist.md) — §8–9 UI walkthrough, QA acceptance
3. [`.ai/skills/dns-debug/metrics-reference.md`](../dns-debug/metrics-reference.md) — Prometheus + UI panel mapping
4. [`app/ui/aggregator.py`](../../../app/ui/aggregator.py) — panel data sources
5. [`app/ui/filters.py`](../../../app/ui/filters.py) — filter params, envelope contract
6. [`app/ui/compare.py`](../../../app/ui/compare.py) — compare delta logic
7. [`app/snapshot_store.py`](../../../app/snapshot_store.py) — snapshot persistence (file or PostgreSQL)
8. [`app/db/`](../../../app/db/) — PostgreSQL historical persistence and 7-day retention cleanup
8. [`docs/SECURITY.md`](../../../docs/SECURITY.md) — roles, UI auth

## Live / historical / compare verification

### Live mode

- [ ] Header shows **Live** badge; `view_mode=live` in API envelope
- [ ] Sticky sub-nav: Status | Diagnostics | Drilldown scrolls to `zone-status`, `zone-diagnostics`, `zone-drilldown`
- [ ] `global_status.level` and signals visible in L1 within 5s of load
- [ ] Auto-refresh **toggle** (default ON) polls every `DNS_DEBUG_UI_REFRESH_SECONDS`; OFF stops polling
- [ ] KPI cards show ▲/▼ trend vs previous poll; click scrolls to target panel
- [ ] Live window presets (15m/1h) filter event buffer via `from`/`to`
- [ ] Quick search + status filter apply client-side to records/rankings
- [ ] `last_update` advances on each poll
- [ ] `data_source` is `live_memory`
- [ ] Active test data updates while `POST /tests` run is in progress; running-test info banner
- [ ] Filters (test_id, resolve_mode, query_type) apply to all panels
- [ ] `GET /api/ui/events` returns recent events for drilldown modal

### Historical mode

- [ ] Header shows **Historical** badge; auto-refresh **stopped**
- [ ] Manual refresh button works
- [ ] Snapshot selector lists completed runs (`GET /api/ui/snapshots`)
- [ ] Time range (`from`, `to`) filters in-memory events or snapshot data
- [ ] `data_source` is `snapshot` or `event_buffer` — never ambiguous
- [ ] Selected time range visible in header / filter chips
- [ ] Stale banner when `warnings` contains `event_buffer_truncated`
- [ ] Retention banner when snapshot list is pruned (`snapshot_retention_at_limit` file mode) or PostgreSQL 7-day window (`retention.db_enabled`, `retention.db_retention_days`)
- [ ] `outside_retention_window` and `db_unavailable` warnings render correct copy
- [ ] Missing historical data explained (empty state message), not silent blank panels

### Compare mode

- [ ] Header shows **Compare** badge; auto-refresh **stopped**
- [ ] Baseline and comparison ranges/snapshots explicitly labeled; optional per-side `test_id` and `resolve_mode`
- [ ] `GET /api/ui/compare` deltas match manual curl cross-check for overview, latency, errors, garbage, cache, load, rankings, `resolver_error_matrix`
- [ ] Delta KPIs: absolute + percent change for errors, success_ratio, p50/p95/p99, garbage_ratio, cache hit_ratio, load error_rate/QPS
- [ ] Division by zero → `null` delta with explanation, not misleading 0%
- [ ] Improvement vs regression uses consistent color semantics (green = better for errors/latency)
- [ ] Compare charts show dual series or side-by-side with legend

## Charts and tables

- [ ] p50/p95/p99 in UI match `/api/ui/dns-latency` response (± rounding)
- [ ] Chart axes and legend readable in dark and light theme
- [ ] No overlapping labels at laptop width (≥1024px) and mobile (≤768px)
- [ ] Records drilldown table: sort by fqdn, errors, avg_latency_ms
- [ ] Records filter respects global filters
- [ ] MTR hop table highlights loss ≥ 5%
- [ ] Cache section shows heuristic disclaimer — never "cache hits" without qualifier

## Responsive audit checklist (Stage 3)

Test all **changed** UI surfaces at these viewport widths (browser devtools or real devices). Core triage flow = load dashboard → read `global_status` → apply filter → open Error analysis or Records drilldown.

| Width | Breakpoint | Pass criteria |
|-------|------------|---------------|
| **1920px** | Large desktop | Full 3-tier layout; KPI row and charts readable without horizontal scroll |
| **1440px** | Laptop | Sub-nav, filter bar, and KPI trends intact |
| **1366px** | Common laptop | Same as 1440; no clipped mode badge or refresh toggle |
| **1280px** | Small laptop | Charts resize or stack; filter chips wrap without hiding primary actions |
| **1024px** | Laptop minimum | **P1 blocker** if sticky sub-nav, filters, or KPI row unusable |
| **768px** | Tablet | Mode switcher and global filters usable; panels stack vertically |
| **390px** | Mobile (large) | Status zone and primary KPIs readable; tables horizontally scrollable |
| **375px** | Mobile (small) | Same as 390; no overflow hiding health badge or mode badge |

Per width checklist:

- [ ] Header: mode badge, data source, last update visible
- [ ] Sticky sub-nav: Status | Diagnostics | Drilldown clickable and scrolls correctly
- [ ] Global filter bar: all controls reachable (wrap, scroll, or collapse acceptable)
- [ ] KPI row: cards not truncated without tooltip or scroll
- [ ] Changed charts: axes and legend readable
- [ ] Records/rankings tables: horizontal scroll if needed; sort controls reachable
- [ ] Compare mode: baseline/comparison pickers usable at width
- [ ] Theme toggle works; chart colors update

**P1 blocker:** Laptop breakage at 1024–1440px where core triage flow is blocked (overflow hiding controls, unusable filters, charts with zero readable labels).

## Visual regression review

After layout, chart, or CSS changes:

- [ ] Screenshot or record baseline for Live, Historical, and Compare at 1440px and 1024px
- [ ] Dark and light theme: no illegible text or chart lines
- [ ] Panel spacing consistent with 3-tier IA (no accidental margin collapse between zones)
- [ ] Severity colors (`--ok`, `--warn`, `--crit`) unchanged semantics unless intentional
- [ ] Cache disclaimer tooltip and retention banners still visible after restyle
- [ ] No regression in compare delta colors (green = improvement for errors/latency)

## Localization (i18n) review

For any UI change that adds or modifies user-visible text:

- [ ] New keys added to **both** `app/ui/static/i18n/en.json` and `ru.json`
- [ ] Template uses `data-i18n` / `data-i18n-title` / `data-i18n-placeholder` (not hardcoded English)
- [ ] `dashboard.js` uses `t("namespace.key")` — no inline English literals for labels
- [ ] EN and RU visual parity at 1440px and 1024px (no clipped header controls)
- [ ] Historical + Compare screens fully localized (not Live-only)
- [ ] Glossary consistency: same RU term for Live/Historical/Compare (e.g. «Снимок», «Сравнение»)
- [ ] Run `tests/test_ui_i18n.py` — key parity and template key coverage

## State coverage review

For every **changed** panel, verify all applicable states (not only happy path):

| State | Check |
|-------|-------|
| Loading | Skeleton/spinner; filters briefly disabled if expected |
| Empty | Explained copy + suggested action |
| Partial | `warnings` chip in header when API returns partial data |
| Error | Red banner; retry or refresh path |
| Stale | Amber banner for old event buffer or snapshot age |
| Retention at limit | Info banner with `snapshot_retention_at_limit`; honest copy |
| No EDNS | `edns-note` or panel note visible |
| No MTR | Verdict card + "MTR not enabled" or "No runs yet" |
| Running test | Info banner when test in progress |

- [ ] No silent blank panels on any changed section
- [ ] Historical empty ≠ Compare empty ≠ Live empty — copy matches mode
- [ ] State transitions: switching live → historical mid-poll does not flash live data without badge change

## Release readiness checklist (Stage 3 / Stage 5)

Complete before marking UI work release-ready:

### Functional

- [ ] All deliverables in **Acceptance checklist** and **Regression checklist** pass
- [ ] Live / historical / compare behaviors verified (sections above)
- [ ] API ↔ UI KPI cross-check for one active test
- [ ] Read-only enforced when `DNS_DEBUG_UI_READONLY=true`
- [ ] Responsive audit passed at all 8 widths (no open P1 laptop/tablet blockers)
- [ ] State coverage review passed on changed panels
- [ ] Visual regression review passed (or deltas documented)

### Process

- [ ] Stage 1 self-check completed by implementer (`dns-debug` skill)
- [ ] Stage 2 UX audit deliverable filed (`ux-designer` skill)
- [ ] Stage 4 fix pass closed all P0/P1 findings (or explicitly deferred with approval)
- [ ] AI docs synced: this skill, UX skill, `AGENT.md`, `debugging-checklist.md`, rules, `CLAUDE.md`, `CURSOR.md`
- [ ] `CHANGELOG.md` updated for the release
- [ ] `docs/releases/X.Y.Z.md` created with full release notes (UX, responsive, localization, workflow, docs)
- [ ] Version in `app/main.py` matches CHANGELOG entry

### Release blockers (do not ship)

- [ ] No open P0 bugs
- [ ] No open P1 bugs (including responsive laptop breakage and misleading observability)
- [ ] No skipped UX audit or QA release readiness pass
- [ ] No missing release documentation (`CHANGELOG.md` or `docs/releases/X.Y.Z.md`)

## API ↔ UI consistency

Cross-check workflow:

```bash
BASE=http://localhost:8080/dns-debug/api/ui
# Overview KPIs
curl -s "$BASE/overview?test_id=autonomous" | jq '{total: .total_queries, errors: .error_count, success_ratio}'
curl -s http://localhost:8080/summary | jq '.aggregate_summary | {total_queries, error_rate, success_rate}'
```

- [ ] Overview `total_queries` matches filtered attempt count
- [ ] Error counts align with `/api/ui/errors` `total_errors`
- [ ] Garbage `useful_vs_garbage_ratio` sums match attempt classification
- [ ] Rankings order matches manual sort of `/api/ui/records`
- [ ] MTR timeline length ≤ `MTR_MAX_HISTORY`

## Retention / snapshots / runs history

| Source | Limit | QA check |
|--------|-------|----------|
| `EVENT_BUFFER_SIZE` | Per-test deque | UI warns when buffer full |
| `SNAPSHOT_RETENTION_COUNT` | Persisted snapshots | Oldest pruned; list reflects retention |
| `MTR_MAX_HISTORY` | MTR runs in memory | Timeline capped; empty state if none |

- [ ] Completing a test creates snapshot when `SNAPSHOT_ENABLED=true`
- [ ] Historical snapshot data matches source aggregates at save time
- [ ] Stale data marked via `is_stale` and `warnings` in envelope

## Read-only safety

- [ ] With `DNS_DEBUG_UI_READONLY=true`: no POST/DELETE forms or buttons in dashboard HTML/JS
- [ ] Test control only via core API (`POST /tests`, `DELETE /tests/{id}`)
- [ ] UI JSON routes require read-only role when `API_AUTH_ENABLED=true`
- [ ] No new write endpoints exposed under `/api/ui/*`

## Regression risks

- [ ] UI JSON contracts backward compatible (additive fields only)
- [ ] `DNS_DEBUG_UI_ENABLED=false` → no UI routes, core API unaffected
- [ ] Existing `/api/ui/overview` etc. work without new query params (default `view_mode=live`)
- [ ] Prometheus metrics flow unchanged by UI-only changes
- [ ] Security classification covers new endpoints as `PROTECTED_READ`

## Bug report format

```markdown
## Summary
One-line description

## Environment
- DNS_DEBUG_UI_ENABLED=true
- view_mode: live | historical | compare
- Filters: test_id=..., from=..., to=...

## Steps to reproduce
1. ...

## Expected
...

## Actual
...

## Evidence
- Screenshot / curl response / browser console
- API envelope: view_mode, data_source, warnings

## Severity
P0–P3 (see matrix below)
```

## Severity matrix

| Level | Criteria | Example |
|-------|----------|---------|
| P0 | Data wrong; security broken; core flow blocked | Compare delta inverted; UI allows DELETE |
| P1 | Major feature broken; misleading observability; responsive laptop breakage | Historical shows live data; cache shown as real hits; core triage flow broken at 1024–1440px |
| P2 | Partial breakage; workaround exists | One chart empty; sort broken on one column |
| P3 | Cosmetic; minor UX friction | Legend overlap on mobile; typo in empty state |

## Deliverables

### Test checklist (smoke)

- [ ] Dashboard loads at `{BASE_PATH}/`
- [ ] All 10 panels render with live data
- [ ] Theme toggle persists
- [ ] Filters apply globally
- [ ] Mode toggle: live → historical → compare

### Acceptance checklist

- [ ] All quality bar items pass (below)
- [ ] Live/historical/compare behaviors verified
- [ ] API ↔ UI KPI cross-check for one active test
- [ ] Read-only enforced
- [ ] AI docs synced (QA skill, UX skill, AGENT.md)

### Regression checklist

- [ ] Core API paths unchanged
- [ ] `/metrics` names unchanged
- [ ] UI disabled mode still works
- [ ] Auth roles unchanged for existing endpoints
- [ ] Cache disclaimer present

### Exploratory scenarios

- Switch modes mid-refresh; verify no race / stale live data in historical
- Select time range with zero events; verify explained empty state
- Compare identical periods; deltas should be zero
- Compare periods with zero baseline; verify null deltas
- Complete test → verify snapshot appears → historical load
- Exceed `EVENT_BUFFER_SIZE` with high RPS; verify truncation warning
- MTR disabled; MTR panel shows explained empty state
- EDNS note visible when only edns0 has data

### Edge cases

- Empty test list
- Test cancelled mid-run
- `from` > `to` (invalid range)
- Snapshot deleted from disk while UI lists it
- Very long FQDN in records table
- Concurrent filter changes during load

## Quality bar

- no ambiguous state labels
- no silent empty states
- no unexplained missing historical data
- no inaccessible critical actions
- no misleading comparison deltas
- no chart with unreadable axes/legend
- no hidden active filters
- no broken mobile/laptop layout for core flows

## Anti-patterns

- Treating `possible_cache_hit_ratio` or cache UI cards as confirmed Docker DNS cache hits
- Shipping compare mode without server-side delta tests
- Silent truncation when event buffer is full
- Testing only live mode and skipping historical/compare
- UI task marked complete without completing pre-release workflow Stages 1–3
- Assuming `debugging-checklist.md` time-range UI exists without verifying implementation

## Sibling skills

- DNS/MTR/metrics logic → [dns-debug SKILL](../dns-debug/SKILL.md)
- UX structure, states, microcopy → [ux-designer SKILL](../ux-designer/SKILL.md)
- Complex dashboard change: UX design → implement → Stage 1 self-check → UX Stage 2 audit → **this skill Stage 3** → fix pass → release readiness → release documentation → sync docs
