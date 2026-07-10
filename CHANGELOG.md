# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-07-10

Full release notes: [docs/releases/0.5.0.md](docs/releases/0.5.0.md)

### Added

- Web UI **English/Russian localization** (`DNS_DEBUG_UI_I18N_*` env vars)
- Client i18n layer: `app/ui/static/js/i18n.js`, `app/ui/static/i18n/en.json`, `ru.json`
- Language switcher (EN | RU) in dashboard header; preference stored in `localStorage`
- Locale-aware formatting for numbers and percents via `Intl`
- Additive `params` on `global_status.signals` for client-side translation

### Changed

- FastAPI app version → 0.5.0
- Dashboard template and JS use `data-i18n` / `t()` — no hardcoded user-facing strings
- CSS adjustments for long Russian labels (KPI cards, filter bar, header actions)

### Localization

- Supported languages: `en`, `ru` (extensible JSON namespace structure)
- Default: `en`; fallback chain: active locale → English

## [0.4.0] - 2026-07-10

Full release notes: [docs/releases/0.4.0.md](docs/releases/0.4.0.md)

### Added

- Local **PostgreSQL** persistence for historical UI data (`DNS_DEBUG_DB_*` env vars)
- **7-day retention policy** with startup + periodic automatic cleanup
- Normalized aggregate tables: test runs, DNS/resolver/domain/error/EDNS aggregates, MTR history, chart buckets
- `postgres` service in `docker-compose.yml` (internal network, persistent volume, healthcheck)
- Prometheus metrics: `dns_debug_db_write_total`, `dns_debug_db_write_errors_total`, `dns_debug_db_cleanup_*`
- Optional JSON snapshot import into PostgreSQL on startup
- UI retention messaging: `db_enabled`, `db_retention_days`, `outside_retention_window`, `db_unavailable` warnings

### Changed

- FastAPI app version → 0.4.0
- Historical/compare snapshot reads use PostgreSQL when `DNS_DEBUG_DB_ENABLED=true` (file fallback when false)
- UI envelope adds additive `retention.db_*` and `storage_backend` fields

### Docs / process

- README, AGENT, CLAUDE, CURSOR, rules, and skills synced for PostgreSQL retention invariant

## [0.3.0] - 2026-07-10

Full release notes: [docs/releases/0.3.0.md](docs/releases/0.3.0.md)

### Added

- Optional Web UI dashboard (`DNS_DEBUG_UI_ENABLED`) with live, historical, and compare modes
- UI JSON API (`/dns-debug/api/ui/*`): overview, latency, EDNS, errors, garbage, cache, records, load, MTR, rankings, events, snapshots, compare
- Snapshot persistence for historical mode (`SNAPSHOT_*` env vars, docker volume)
- Pre-release UX workflow (5 stages), `qa-ui` and `ux-designer` agent skills
- API security layer (auth, rate limit, audit, `/live`, `/ready`)
- Release documentation infrastructure (`docs/releases/`)

### UX improvements

- 3-tier information architecture: Status → Diagnostics → Drilldown with sticky sub-nav scroll-spy
- Live mode: auto-refresh toggle, KPI ▲/▼ trends, live window presets (15m/1h)
- Historical mode: snapshot picker, retention/truncation honesty banners
- Compare mode: server-side deltas via `/api/ui/compare`, delta KPI row
- Dark/light theme toggle, global filters with active chips, events and diagnosis modals

### Changed

- FastAPI app version → 0.3.0
- `docker-compose.yml`: snapshot volume mount (`./data/snapshots:/app/data/snapshots`)

### Fixed

- Web UI chart lifecycle (destroy/recreate on mode switch)
- Responsive layout and sticky header overlap at laptop widths (1024–1440px)
- Chart legend reflow at tablet widths (768px)

### QA / UX workflow

- 5-stage pre-release workflow for visual/behavioral UI changes (self-check → UX audit → QA → fix pass → release readiness)
- Release blockers: P0/P1 data/security, misleading observability, laptop/tablet layout breakage, missing state coverage

### Localization

- None — UI remains English-only (`lang="en"`); Russian/i18n not in scope for this release

## [0.2.0-dev] - 2026-07-09

### Added

- API security layer (`app/security/`): Bearer/API-key auth, roles, rate limiting, IP allowlist, audit logging, request ID
- Health probes: `GET /live`, `GET /ready`
- Security guide: `docs/SECURITY.md`
- Security tests: `tests/test_security.py`
- Expanded AI documentation: Web UI spec, security model, metrics→UI mapping

### Changed

- FastAPI app version → 0.2.0
- Protected endpoints use role-based auth when `API_AUTH_ENABLED=true` (default `false` for local dev)
- `.env.example` extended with security/UI env vars

## [0.1.0] - 2026-07-09

### Added

- First public release of DNS Debug — a FastAPI service for DNS diagnostics inside Docker via the embedded resolver (`127.0.0.11`)
- Snapshot of container DNS environment from `/etc/resolv.conf` (`GET /resolver`)
- Background DNS load tests with configurable RPS, concurrency, and duration
- Resolve mode comparison: `system`, `absolute_fqdn`, and programmatic `ndots:N` overrides
- Classification of noisy queries: search suffix, duplicates, redundant AAAA, and related noise types
- Indirect cache hints from latency (heuristic, not Docker DNS internals)
- Prometheus metrics at `GET /metrics`
- MTR TCP path diagnostics (`GET/POST /mtr`, `GET /mtr/runs`)
- Autonomous mode for continuous monitoring on startup
- Auto-diagnosis API (`GET /tests/{id}/diagnosis`) for ndots/search domain analysis
- Optional Prometheus sidecar via `docker compose --profile monitoring up`
- AI project guidance files (`AGENT.md`, `.ai/skills/`, `.cursor/rules/`, `CLAUDE.md`)

[0.3.0]: https://github.com/shatovilya/dns-dig-docker/releases/tag/v0.3.0
[0.2.0-dev]: https://github.com/shatovilya/dns-dig-docker/releases/tag/v0.2.0-dev
[0.1.0]: https://github.com/shatovilya/dns-dig-docker/releases/tag/v0.1.0
