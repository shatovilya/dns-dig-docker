# DNS Debug — Claude Code Guide

## What this repo is

A FastAPI DNS debug service that runs **inside a Docker container** and measures DNS behavior through the embedded resolver (`127.0.0.11`). It load-tests names under configurable RPS, compares resolve modes, classifies noisy queries, and exposes Prometheus metrics.

## Hard constraints

- No `/etc/resolv.conf` changes
- No `dns:` / `dns_search:` in docker-compose
- No `network_mode: host`
- No sidecar DNS resolvers
- No fake cache claims — `dns_debug_possible_cached_response_total` is a latency heuristic

## Key modules

```
app/config.py           → settings
app/resolver_snapshot.py → read resolv.conf
app/dns_runner.py       → run tests, resolve, search probes
app/stats_store.py      → aggregate attempts
app/ndots_analytics.py  → ndots/search analytics + diagnosis
app/api.py              → REST API
app/metrics.py          → Prometheus
```

## Resolution modes

| Label | Description |
|-------|-------------|
| `system` | Normal resolver with search domains |
| `absolute_fqdn` | Trailing dot, bypasses search |
| `ndots:N` | Programmatic ndots override (from `ndots_values`) |

Search suffix probes are **diagnostic** — they measure overhead, not primary app traffic.

## API

`/health`, `/resolver`, `/tests`, `/tests/{id}`, `/tests/{id}/diagnosis`, `/summary`, `/metrics` on port 8080.

## Safe changes

- Diagnosis thresholds, new noise types, summary fields from existing data
- Documentation and tests
- Logging improvements

## Risky changes

- Infrastructure (compose networking, DNS overrides, sidecars)
- Renaming metrics or API routes
- Modifying resolv.conf or resolver configuration at the OS level
- Misrepresenting cache heuristics as real cache introspection

## Development

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8080/health
```

See `AGENT.md` and `.ai/skills/dns-debug/` for detailed engineering guidance, debugging checklist, and metrics reference.
