# DNS Debug — Security Guide

## Threat model (summary)

| Risk | Mitigation |
|------|------------|
| Unauthenticated API abuse | `API_AUTH_ENABLED`, roles, rate limits |
| DNS/MTR resource exhaustion | concurrency caps, duration limits, expensive rate limits |
| Open metrics / UI data leak | Prometheus IP/auth; UI auth when enabled |
| Token leakage via logs | audit logs use credential id only, never full secrets |
| Forwarded header spoofing | `TRUST_PROXY_ENABLED` + `TRUST_PROXY_IPS` |
| Open DNS stress proxy | operator role for writes, abuse limits |

## Endpoint classification

| Class | Endpoints | Min role (when auth on) |
|-------|-----------|-------------------------|
| Public | `GET /health`, `/live`, `/ready` | — |
| Metrics | `GET /metrics` | IP allowlist / bearer / internal network |
| Protected read | `/resolver`, `/tests`, `/summary`, `/mtr`, UI JSON | read-only |
| Protected write | `DELETE /tests/{id}` | operator |
| Expensive | `POST /tests`, `POST /mtr` | operator |

## Roles

- **read-only** — read status, results, UI JSON
- **operator** — start/cancel tests, trigger MTR
- **admin** — future dangerous/debug endpoints

Configure via `API_STATIC_CREDENTIALS_JSON`:

```json
[
  {"id": "reader", "secret": "your-read-token", "role": "read-only"},
  {"id": "operator", "secret": "your-op-token", "role": "operator"}
]
```

Or legacy lists: `API_BEARER_TOKENS`, `API_KEYS` (operator), `API_ADMIN_KEYS`.

## Authentication headers

```bash
# Bearer
curl -H "Authorization: Bearer your-token" http://localhost:8080/tests

# API key
curl -H "X-API-Key: your-key" http://localhost:8080/tests
```

## Production-like defaults (recommended)

```env
API_AUTH_ENABLED=true
API_RATE_LIMIT_ENABLED=true
API_EXPOSE_ERROR_DETAILS=false
MTR_AUTH_REQUIRED=true
PROMETHEUS_TRUST_INTERNAL_NETWORKS=true
DNS_DEBUG_UI_AUTH_ENABLED=true   # when UI enabled
```

## Local development

Explicit opt-out only:

```env
API_AUTH_ENABLED=false
API_RATE_LIMIT_ENABLED=false
PROMETHEUS_TRUST_INTERNAL_NETWORKS=false
```

Never commit real secrets.

## Prometheus scrape

Default: `/metrics` open on trusted internal networks (`PROMETHEUS_TRUST_INTERNAL_NETWORKS=true` allows RFC1918/loopback).

For auth-protected scrape:

```env
PROMETHEUS_AUTH_ENABLED=true
PROMETHEUS_BEARER_TOKEN=scrape-secret
```

Prometheus `scrape_config`:

```yaml
authorization:
  credentials: scrape-secret
```

Or use `PROMETHEUS_IP_ALLOWLIST=172.16.0.0/12` for docker bridge only.

## Migration from unauthenticated API

**Before:** all endpoints anonymous.

**After (production):** set `API_AUTH_ENABLED=true` and configure credentials. Existing curl/scripts need `Authorization` header or set `API_AUTH_ENABLED=false` for local dev only.

Core API paths and Prometheus metric names are unchanged.

## Module layout

```
app/security/
  principal.py      # Role, Principal
  auth.py           # credentials, dependencies
  classification.py # endpoint → protection class
  middleware.py     # auth gate, body size
  rate_limit.py     # per-IP/token limits
  ip_allowlist.py   # CIDR checks
  headers.py        # security headers
  audit.py          # structured audit logs
  request_id.py     # X-Request-ID
  errors.py         # safe JSON errors
  abuse.py          # concurrent run limits
```

## AI documentation sync

Any change to auth, roles, rate limits, metrics/UI access, or audit logging **must** update:

- `AGENT.md`, `CLAUDE.md`
- `.cursor/rules/dns-debug-project.mdc`
- `.ai/skills/dns-debug/SKILL.md`
- `.ai/skills/dns-debug/debugging-checklist.md`
- this file and `.env.example`
