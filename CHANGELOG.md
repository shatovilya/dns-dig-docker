# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0-dev]: https://github.com/shatovilya/dns-dig-docker/releases/tag/v0.2.0-dev
[0.1.0]: https://github.com/shatovilya/dns-dig-docker/releases/tag/v0.1.0
