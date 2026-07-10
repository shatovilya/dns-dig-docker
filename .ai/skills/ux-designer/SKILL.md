---
name: ux-designer
description: >-
  UX designer role for DNS Debug Web UI — information architecture, dashboard usability,
  live/historical/compare modes, state design, chart hierarchy, engineering workflows.
---

# DNS Debug — UX Designer (Web UI)

Use this skill when improving DNS Debug dashboard information architecture, usability, visual design, workflows, or state design for the optional Web UI observability layer.

## When to use

- Designing or restructuring dashboard sections (overview, diagnostics, drilldown)
- Adding or changing live, historical, or compare modes
- Improving filters, KPI hierarchy, chart readability
- Designing loading, empty, error, stale, and retention states
- Incident triage and historical analysis workflows
- Writing microcopy, interaction notes, or UX rationale for UI changes

## How to analyze current UI

1. Open `GET {DNS_DEBUG_UI_BASE_PATH}/` (default `/dns-debug/`)
2. Map the **10 panels** (Overview → Rankings) and header controls
3. Trace **global filters**: test_id, resolve_mode, query_type, time range, view mode
4. Note **cognitive load**: how many decisions before answering "is DNS degraded?"
5. Read [`app/ui/templates/dashboard.html`](../../../app/ui/templates/dashboard.html) and [`dashboard.js`](../../../app/ui/static/js/dashboard.js) for interaction gaps
6. Cross-check [`AGENT.md`](../../../AGENT.md) UI spec for intended vs actual behavior

## Observability dashboard patterns

### Information architecture (3-tier)

| Zone | DOM ID | Purpose | Content |
|------|--------|---------|---------|
| **L1 Status** | `zone-status` | 30-second health scan | `global_status` rollup, resolver context, KPI row with live poll trends |
| **L2 Diagnostics** | `zone-diagnostics` | Latency, EDNS, errors, garbage, cache, load, MTR | Charts + KPIs by severity; error matrix; top noisy domains |
| **L3 Drilldown** | `zone-drilldown` | Per-record investigation | Sortable/filterable FQDN table, rankings, events/diagnosis modals |

Sticky sub-nav under header: **Status | Diagnostics | Drilldown** (scroll-to-section).

Legacy mental model still applies:

| Zone | Purpose | Content |
|------|---------|---------|
| **Overview** | Health at a glance | Global status, KPI trends, mode badge |
| **Path** | Non-DNS issues | MTR hops, verdict, timeline (within Diagnostics tier) |

### KPI hierarchy (Live)

1. **Critical** — `global_status.level`, error rate, MTR degraded count
2. **Performance** — p50/p95/p99 with ▲/▼ vs previous poll
3. **Diagnostic** — NXDOMAIN rate, garbage ratio, cache heuristic (with disclaimer tooltip)
4. **Context** — total queries, data source badge, last update

Click KPI → scroll to target panel + apply context (errors → panel-errors, cache → panel-cache).

### Engineering workflows

| Workflow | Entry | Path |
|----------|-------|------|
| Fast incident review | Overview → Error analysis → Record drilldown | Live mode, filter failing test |
| Historical analysis | Historical mode → snapshot or time range → Latency + Load | Compare periods |
| Resolver comparison | Filter resolve_mode or Compare mode | system vs absolute_fqdn |
| Domain investigation | Rankings → Records drilldown | Sort by error_rate |
| MTR diagnostics | MTR panel → hop table | Verdict card first |
| Compare-period investigation | Compare mode → baseline vs comparison KPI deltas | Overview delta row |

## Historical data views

### Rules

- **Mode must be explicit** — badge: Live | Historical | Compare (never implicit)
- **Time range always visible** — in header and filter chips (`from` – `to` or snapshot label)
- **Data source labeled** — `live_memory`, `event_buffer`, or `snapshot`
- **Auto-refresh off** in historical/compare; manual refresh button visible
- **Retention honest** — explain `EVENT_BUFFER_SIZE` truncation; PostgreSQL **7-day** window (`DNS_DEBUG_DB_RETENTION_DAYS`) or file snapshot count limit
- **Snapshot selector** — list by test name, completed time, test_id

### Microcopy examples

| State | Copy |
|-------|------|
| No snapshots | "No saved runs yet. Complete a DNS test to create a historical snapshot." |
| Buffer truncated | "Showing last N events only. Older queries are not in memory." |
| Retention pruned | File: "Older snapshots were removed (retention limit)." PG: "Historical data retained for N days in local PostgreSQL." Outside window: data not available. |
| Empty time range | "No queries in the selected time range. Widen the range or pick another test." |

## Compare mode

### Layout

- Two labeled pickers: **Baseline** and **Comparison** (time range or snapshot each)
- Overview row: delta KPIs with ↑/↓ and color (green = improvement for errors/latency)
- Charts: dual-line overlay or grouped bars with legend "Baseline" / "Comparison"

### Delta presentation rules

- Show absolute delta and percent change when baseline ≠ 0
- Show "—" or "N/A" when baseline is zero — never fake 0% improvement
- Label whether lower or higher is better per metric (errors: lower is better)
- Do not use red/green without text labels (accessibility)

## Cognitive load reduction

- **Progressive disclosure** — Overview first; drilldown on demand
- **Global filters once** — apply to all panels; show active filter chips
- **One primary action per mode** — Live: watch; Historical: pick snapshot; Compare: pick two periods
- **Consistent panel order** — match incident triage priority (errors before rankings)
- **Limit chart count per row** — max 2 side-by-side on laptop; stack on mobile

## Localization (i18n)

### Language switcher

- Placement: header actions group, before Theme — compact `EN | RU`
- Active language: filled accent style; `aria-pressed` on buttons
- Do not hide switcher when `DNS_DEBUG_UI_I18N_ENABLED=true`

### RU microcopy guidelines

- Short technical Russian; avoid literary phrasing
- Keep canonical identifiers in Latin: `NXDOMAIN`, `MTR`, `p50`, `system`, `absolute_fqdn`
- Prefer: «Онлайн», «История», «Сравнение», «Снимок», «Эвристика кэша»
- Long labels: allow wrap in KPI `.label` and filter `<span>` — test at 1024–1440px

### Layout impact checklist (RU)

- [ ] Header: mode badge + lang switcher + refresh visible at 1280px
- [ ] Compare pickers readable without horizontal scroll at 1024px
- [ ] Chart legends reflow to bottom at 768px (existing behavior)

## Chart and table readability

- Chart.js: sufficient padding, `maintainAspectRatio: false` with min-height
- Axis units in labels ("ms", "%", "queries")
- Max 6–8 legend entries; aggregate "other" if needed
- Records table: zebra rows, status badges, sort indicators
- Severity colors: `--ok`, `--warn`, `--crit`, `--info` from CSS variables
- Theme toggle must update chart colors (`refreshChartsTheme`)

## State design

| State | Visual | Copy / behavior |
|-------|--------|-----------------|
| Loading | Per-panel skeleton or spinner | Disable filter submit briefly |
| Empty | Centered message in panel | Explain why + suggested action |
| Partial | Amber warning chip in header | List `warnings` from API |
| Degraded | Health badge warn/crit | Link to Error analysis |
| Stale | Amber banner | Event buffer / snapshot age |
| Retention exceeded | Info banner | Suggest new test |
| No EDNS data | Note from API (`edns-note`) | edns0-only instrumentation message |
| No MTR data | Verdict card + empty table | "MTR not enabled" or "No runs yet" |
| Error | Red banner + retry | Console log detail; health badge error |

## Deliverables

When proposing UI changes, produce:

1. **IA proposal** — what lives in overview vs diagnostics vs drilldown
2. **Dashboard structure** — panel order and grouping
3. **Section priority** — P0 panels for incident triage
4. **Filter strategy** — global vs per-panel; chip behavior
5. **Interaction notes** — mode switch, refresh, sort, chip clear
6. **Microcopy** — empty/loading/stale/retention strings
7. **Compare mode guidance** — picker layout, delta format
8. **Historical mode UX rules** — snapshot vs time-range precedence

## UX rationale template

```markdown
## Problem
[What engineer cannot do or understand today]

## Proposal
[Concrete UI change]

## Rationale
[Why this reduces cognitive load / supports workflow]

## States covered
[loading, empty, error, stale, ...]

## Docs to sync
[ux-designer SKILL, qa-ui SKILL, AGENT.md, debugging-checklist.md]
```

## Quality bar

- every screen has a clear primary purpose
- live vs historical vs compare clearly distinguishable
- filters are visible and understandable
- time range is always explicit
- charts are readable without guesswork
- states are explained, not silent
- drilldown is discoverable
- dense data remains scannable
- visual hierarchy supports engineering workflows

## Anti-patterns

- Decorative icons or charts without diagnostic value
- Hidden view mode (user cannot tell live vs historical)
- "Updated" timestamp without data source
- Silent empty panels (no explanation)
- Compare deltas without baseline/comparison labels
- Implying full query history when only event buffer or snapshot exists
- Removing cache heuristic disclaimer to simplify UI
- Heavy SPA framework without explicit request
- Per-panel conflicting filters that disagree with global chips

## Pre-release responsibilities (Stage 2 — UX review)

After implementation, before QA release readiness, the UX designer runs the **pre-release UX audit** and files a deliverable. This is mandatory for any visual, layout, chart, filter, or state change.

### Pre-release UX audit checklist

Audit all **changed** panels and global chrome. Mark pass/fail per block; file P0/P1 for blockers.

#### 1. Overview

- [ ] `global_status` level and signals readable within 5 s of load
- [ ] KPI hierarchy clear: critical → performance → diagnostic → context
- [ ] Live poll trends (▲/▼) distinguishable; click-to-scroll targets correct panel
- [ ] Mode badge (Live / Historical / Compare) unambiguous
- [ ] Data source and time range visible in header or filter chips

#### 2. Layout

- [ ] 3-tier IA intact: Status → Diagnostics → Drilldown
- [ ] Sticky sub-nav scrolls to correct `zone-*` sections
- [ ] Panel order matches incident triage priority (errors before rankings)
- [ ] Max 2 charts per row on laptop; stacks cleanly on tablet/mobile
- [ ] No horizontal overflow or clipped primary actions at 1024–1440px

#### 3. Charts

- [ ] Axes labeled with units (ms, %, queries)
- [ ] Legend ≤ 8 entries; "other" aggregation when needed
- [ ] Dark and light theme: `refreshChartsTheme` updates colors
- [ ] Compare mode: dual series or grouped bars with Baseline / Comparison labels
- [ ] No unreadable overlapping labels at laptop width

#### 4. Filters

- [ ] Global filters (test_id, resolve_mode, query_type, time range, view mode) visible
- [ ] Active filter chips shown; clear action works
- [ ] One primary action per mode (Live: watch; Historical: pick snapshot; Compare: pick two periods)
- [ ] Filters do not conflict between global bar and per-panel controls

#### 5. States

- [ ] Loading: skeleton or spinner per changed panel
- [ ] Empty: centered copy with suggested action (not silent blank)
- [ ] Error: banner + retry; health badge reflects degradation
- [ ] Stale / retention: banners honest (`event_buffer_truncated`, `snapshot_retention_at_limit`)
- [ ] No EDNS / no MTR: explained empty states with API note or env hint
- [ ] Cache section retains heuristic disclaimer

#### 6. Responsive

- [ ] 1920px — full layout; no wasted dead zones breaking scan path
- [ ] 1440px / 1366px / 1280px — laptop triage flow intact (sub-nav, filters, KPI row)
- [ ] 1024px — charts stack or resize; no clipped controls (P1 if core flow blocked)
- [ ] 768px — tablet: mode switcher and filter bar usable
- [ ] 390px / 375px — mobile: primary KPIs and status zone readable; tables scroll horizontally

#### 7. Accessibility

- [ ] Compare deltas use text labels, not color alone (green/red + ↑/↓ + metric name)
- [ ] Focus order logical through header controls and filter bar
- [ ] Severity badges distinguishable without color-only encoding
- [ ] Tooltips/disclaimers available for cache heuristic and retention limits

### UX audit deliverable template

```markdown
## UX audit — [feature/change name]

**Date:** YYYY-MM-DD
**View modes tested:** live | historical | compare
**Widths:** 1920, 1440, 1366, 1280, 1024, 768, 390, 375

### Summary
[One paragraph: ship / ship with fixes / block]

### Checklist results
| Block | Pass | Notes |
|-------|------|-------|
| Overview | ✓/✗ | |
| Layout | ✓/✗ | |
| Charts | ✓/✗ | |
| Filters | ✓/✗ | |
| States | ✓/✗ | |
| Responsive | ✓/✗ | |
| Accessibility | ✓/✗ | |

### Findings
| ID | Severity | Block | Description | Suggested fix |
|----|----------|-------|-------------|---------------|
| UX-1 | P1 | Layout | ... | ... |

### Sign-off
- [ ] No open P0/P1 UX defects — ready for QA Stage 3
- [ ] Docs to sync: ux-designer SKILL, qa-ui SKILL, AGENT.md, debugging-checklist.md
```

### Additional UX designer responsibilities

- Run Stage 2 audit after implementer Stage 1 self-check, before QA Stage 3
- File UX audit deliverable in PR/MR description or linked issue
- Re-review after Stage 4 fix pass when layout, charts, or states changed materially
- Coordinate with QA on responsive P1 criteria (laptop 1024–1440px, tablet 768px)

## Sibling skills

- DNS/MTR/metrics → [dns-debug SKILL](../dns-debug/SKILL.md)
- Acceptance / regression / data correctness → [qa-ui SKILL](../qa-ui/SKILL.md)
- **Workflow for large UI changes:** this skill (design) → implement → Stage 1 self-check → **this skill (Stage 2 audit)** → qa-ui (Stage 3) → fix pass → release readiness → sync all AI docs
