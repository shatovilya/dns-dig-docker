# DNS Debug — September 2026 Improvement Backlog

**Audit date:** 2026-09-10  
**Repository:** https://github.com/shatovilya/dns-dig-docker  
**Current release:** v0.5.2  
**Audited by:** Cloud Agent (investigation only, no code changes)

---

## Executive Summary

**Project maturity:** Production-ready core DNS engine with comprehensive security layer, optional Web UI, and PostgreSQL persistence. Well-documented for AI agents. **No critical blockers found.**

**Key strengths:**
- Strong security defaults (auth, rate limits, IP allowlists, abuse protection)
- Comprehensive AI documentation (AGENT.md, skills, rules)
- PostgreSQL 7-day retention with automatic cleanup
- 52 test cases covering security, UI, DB, retention
- Clear Docker DNS constraints respected throughout

**Top improvement areas:**
1. **No CI/CD pipeline** — tests exist but no automated runs
2. **Unpinned dependencies** — supply chain risk (10 packages with `>=`)
3. **develop branch significantly diverged** — 4273 lines behind main
4. **Missing test coverage** — core DNS/MTR logic untested
5. **No dependency scanning** — Dependabot/Renovate absent

**Effort distribution:**
- **P0 (5 items):** Security/correctness fixes — ~1-2 engineering days
- **P1 (12 items):** High-value improvements — ~3-5 days
- **P2 (10 items):** Nice-to-have polish — ~2-3 days

---

## Priority 0 — Fix/Correctness/Security

### 1. **Dependency pinning — supply chain risk**
**File:** `app/requirements.txt`  
**Evidence:** All 10 dependencies use `>=` without upper bounds:
```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
dnspython>=2.7.0
...
```
**Risk:** Breaking changes in minor/patch releases can break production. Supply chain attacks on unpinned transitive deps.  
**Fix:** Pin exact versions or use `~=` (compatible release):
```
fastapi==0.115.4  # or fastapi~=0.115.0
uvicorn[standard]==0.32.1
dnspython==2.7.0
```
Add `requirements-dev.txt` for test tooling (pytest, httpx).  
**Cost:** 30 min + test run validation.

---

### 2. **Commented-out secret placeholder in .env.example**
**File:** `.env.example:68`  
**Evidence:**
```bash
# API_STATIC_CREDENTIALS_JSON=[{"id":"reader","secret":"CHANGE_ME","role":"read-only"}...]
```
**Risk:** Users may uncomment and deploy with `CHANGE_ME` tokens, creating trivial auth bypass.  
**Fix:** Replace `CHANGE_ME` with `<GENERATE-STRONG-SECRET-HERE>` or remove example entirely with reference to docs/SECURITY.md for credential generation.  
**Cost:** 5 min.

---

### 3. **PostgreSQL credentials hardcoded in .env.example and docker-compose.yml**
**Files:** `.env.example:141-142`, `docker-compose.yml:5-7`  
**Evidence:**
```env
DNS_DEBUG_DB_USER=dns_debug
DNS_DEBUG_DB_PASSWORD=dns_debug
```
**Risk:** Default weak credentials shipped in examples. Users deploy without changing.  
**Fix:**
- `.env.example`: Use placeholder `DNS_DEBUG_DB_PASSWORD=<CHANGE_ME>`
- Add startup warning if `dns_debug_db_password` matches common weak passwords
- Document password rotation in docs/SECURITY.md  
**Cost:** 20 min.

---

### 4. **SQL table name interpolation in cleanup.py**
**File:** `app/db/cleanup.py:174`  
**Evidence:**
```python
await conn.execute(f"DELETE FROM {table} WHERE snapshot_id = $1", snapshot_id)
```
**Risk:** If `table` variable is ever user-controlled (not currently, but future refactor risk), SQL injection. Even if safe now, violates secure coding best practice.  
**Fix:** Whitelist valid table names or use explicit statements:
```python
VALID_TABLES = {"test_runs", "run_aggregates", ...}
if table not in VALID_TABLES:
    raise ValueError(f"Invalid table: {table}")
await conn.execute(f"DELETE FROM {table} WHERE snapshot_id = $1", snapshot_id)
```
**Cost:** 10 min.

---

### 5. **Bare `except Exception:` in critical paths**
**Files:** `app/ui/filters.py:230`, `app/security/middleware.py:35`, `app/dns_runner.py:360,380,394`  
**Evidence:**
```python
except Exception:
    pass  # or generic fallback
```
**Risk:** Hides bugs, swallows KeyboardInterrupt/asyncio.CancelledError in some cases (though most are safe). Debugging nightmare.  
**Fix:** Narrow exception types:
```python
except (ValueError, KeyError, TypeError) as exc:
    logger.warning("Specific failure context: %s", exc)
```
Preserve `asyncio.CancelledError` propagation in async code.  
**Cost:** 30 min (audit all bare excepts, add specific types + logging).

---

## Priority 1 — High Value

### 6. **No CI/CD pipeline**
**Evidence:** No `.github/workflows/` directory. Tests exist (52 cases, 713 lines) but no automation.  
**Impact:** Manual testing burden. No PR checks. Regression risk on merges.  
**Fix:** Add GitHub Actions workflow:
```yaml
.github/workflows/ci.yml:
  - Lint (ruff/black)
  - Type check (mypy)
  - Unit tests (pytest)
  - Docker build smoke test
  - Dependency audit (pip-audit or safety)
```
Add CI badge to README.md.  
**Cost:** 2 hours (workflow + badge + first green run).

---

### 7. **develop branch massively diverged from main**
**Evidence:** `git diff main develop --stat` shows **4273 lines deleted**, 400 added. develop is at v0.3.0, main at v0.5.2.  
**Impact:** Confusion for contributors. Stale feature branches. Merge conflicts inevitable.  
**Fix:**
- If develop is obsolete: delete `git push origin --delete develop`
- If it has valuable WIP: rebase onto main or document the divergence in README  
**Hypothesis:** develop may be a stale pre-0.4.0 snapshot before PostgreSQL features landed. Verify with maintainer before deleting.  
**Cost:** 30 min investigation + 10 min cleanup.

---

### 8. **feat/ui-web-ui branch not merged or deleted**
**Evidence:** `origin/feat/ui-web-ui` exists but not in main (UI code is in main via different path).  
**Impact:** Stale branch clutter.  
**Fix:** Delete after confirming UI features are in main: `git push origin --delete feat/ui-web-ui`.  
**Cost:** 5 min.

---

### 9. **No dependency vulnerability scanning**
**Evidence:** No Dependabot config, no Renovate, no Snyk.  
**Impact:** CVEs in fastapi, uvicorn, dnspython go unnoticed.  
**Fix:** Enable GitHub Dependabot:
```yaml
.github/dependabot.yml:
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/app"
    schedule:
      interval: "weekly"
```
Or add `pip-audit` to CI workflow.  
**Cost:** 15 min.

---

### 10. **Core DNS and MTR logic untested**
**Evidence:** Tests cover security, UI, DB, but no `test_dns_runner.py`, `test_mtr_runner.py`, `test_api.py` (the POST/DELETE endpoints).  
**Impact:** Async race conditions, MTR mutex deadlocks, DNS resolution edge cases unvalidated.  
**Missing coverage:**
- `dns_runner._resolve()` — timeout/retry/NXDOMAIN paths
- `dns_runner._classify_noise()` — all 6 NoiseType branches
- `mtr_runner.run_mtr()` — timeout, parse failure, concurrent runs
- `api.py POST /tests` — validation, autonomous mode conflict
- `stats_store.record_attempt()` — concurrent access, event buffer overflow  
**Fix:** Add integration tests with mocked `dns.asyncresolver` and `asyncio.subprocess`.  
**Cost:** 4-6 hours (requires careful async test setup).

---

### 11. **No health check validation in CI**
**Evidence:** Dockerfile runs as `USER nobody`, no HEALTHCHECK instruction. Compose has postgres healthcheck but not dns-debugger.  
**Impact:** Docker may report container as "running" even if FastAPI crashed.  
**Fix:**
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health').read()"
```
Or use `curl` (requires installing curl in image, violates slim image goal — better to use Python stdlib).  
**Cost:** 20 min.

---

### 12. **API_AUTH_ENABLED=false default in .env.example**
**File:** `.env.example:67`  
**Evidence:**
```env
API_AUTH_ENABLED=false
```
**Risk:** Users copy `.env.example` to `.env` and deploy with auth disabled. Open DNS stress proxy.  
**Mitigation already present:** README.md and docs/SECURITY.md document production hardening. But default is still insecure.  
**Fix:** Change default to `true` in `.env.example`, add big comment:
```env
# SECURITY: Enable auth in production. See docs/SECURITY.md
API_AUTH_ENABLED=true
```
Keep `false` only in developer override instructions.  
**Cost:** 5 min + docs update.

---

### 13. **No explicit request timeout in mtr subprocess**
**File:** `app/mtr_runner.py:120`  
**Evidence:**
```python
stdout_bytes, stderr_bytes = await asyncio.wait_for(
    proc.communicate(),
    timeout=settings.mtr_timeout_seconds,
)
```
**Issue:** `wait_for` kills the process on timeout but doesn't set a SIGTERM grace period. Process may hang if MTR ignores SIGKILL (unlikely but observed with some subprocess wrappers).  
**Fix:** Already correctly implemented with `proc.kill()` after timeout. **Non-issue** — mark as verified correct.  
**Cost:** 0 (no action needed).

---

### 14. **Snapshot file writes not atomic**
**File:** `app/snapshot_store.py:74`  
**Evidence:**
```python
path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
```
**Risk:** If process crashes mid-write, corrupt JSON file. Next read fails silently (caught in try/except but snapshot lost).  
**Fix:** Atomic write via temp file + rename:
```python
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
tmp.rename(path)  # atomic on POSIX
```
**Cost:** 15 min.

---

### 15. **Rate limiter uses token bucket but no per-endpoint cap**
**File:** `app/security/rate_limit.py:70`  
**Evidence:**
```python
key = f"{client_ip}:{cred_id}:{cls.value}"
```
**Issue:** Rate limit key includes `cls.value` (PROTECTED_READ, EXPENSIVE, etc.) but a single IP can exhaust all READ endpoints by rotating between `/resolver`, `/tests`, `/summary`, etc.  
**Hypothesis:** May not be a practical issue (burst=20 is low). But high-frequency scrapers could still cause load.  
**Fix (optional):** Add per-IP global cap or per-endpoint sub-limits.  
**Cost:** 1 hour (requires design decision on multi-tier limits).

---

### 16. **PostgreSQL connection pool not explicitly sized**
**File:** `app/db/connection.py` (inferred from asyncpg defaults)  
**Evidence:** `asyncpg.create_pool()` called without `min_size`/`max_size`.  
**Risk:** Defaults to 10 min / 10 max. Under load (concurrent DNS tests + UI queries), may exhaust pool.  
**Fix:** Add config:
```python
await asyncpg.create_pool(
    settings.database_dsn,
    min_size=5,
    max_size=20,  # tune based on DNS_MAX_CONCURRENT_RUNS
)
```
Add env vars `DNS_DEBUG_DB_POOL_MIN` / `DNS_DEBUG_DB_POOL_MAX`.  
**Cost:** 30 min.

---

### 17. **No structured logging JSON output option**
**Evidence:** `app/utils.py` sets up `logging.basicConfig` with text format. JSON logging only via custom formatter.  
**Impact:** Harder to ingest logs in Loki/Elasticsearch for production deployments.  
**Fix:** Add env var `LOG_FORMAT=json|text` and use `python-json-logger` when `json`.  
**Cost:** 1 hour.

---

## Priority 2 — Nice-to-Have

### 18. **README.md has no CI badges**
**Evidence:** README.md line 1 has title but no build/test/coverage badges.  
**Fix:** Add after CI workflow is set up:
```markdown
[![CI](https://github.com/shatovilya/dns-dig-docker/workflows/CI/badge.svg)](...)
[![codecov](https://codecov.io/gh/shatovilya/dns-dig-docker/branch/main/graph/badge.svg)](...)
```
**Cost:** 5 min (after CI exists).

---

### 19. **Version string in main.py not automated**
**File:** `app/main.py:227`  
**Evidence:**
```python
app = FastAPI(title="DNS Debugger", version="0.5.2", lifespan=lifespan)
```
**Issue:** Manual version sync between main.py, CHANGELOG.md, git tags. Prone to drift.  
**Fix:** Read version from `__version__.py` or git describe:
```python
from importlib.metadata import version
app = FastAPI(title="DNS Debugger", version=version("dns-debug"))
```
Or use `setuptools_scm` for git-tag-based versioning.  
**Cost:** 30 min.

---

### 20. **Prometheus metrics have no HELP text**
**File:** `app/metrics.py` (inferred — not read in full)  
**Hypothesis:** Metrics are defined but may lack comprehensive HELP strings.  
**Fix:** Audit `metrics.py` and ensure every `Counter`/`Gauge`/`Histogram` has descriptive HELP text per Prometheus best practices.  
**Cost:** 30 min.

---

### 21. **Docker image has no non-root USER security context**
**File:** `app/Dockerfile:14`  
**Evidence:**
```dockerfile
USER nobody
```
**Issue:** `nobody` is correct, but UID/GID not explicit. Some k8s security contexts require numeric UID.  
**Fix:**
```dockerfile
RUN adduser --disabled-password --gecos '' --uid 10001 dnsuser
USER 10001
```
**Cost:** 10 min + test.

---

### 22. **No `.dockerignore` file**
**Evidence:** No `.dockerignore` in repo root.  
**Impact:** Docker COPY may include `.git`, `tests/`, `__pycache__`, inflating image size.  
**Fix:** Add `.dockerignore`:
```
.git
.github
tests
*.pyc
__pycache__
.env
.env.example
docs
```
**Cost:** 5 min.

---

### 23. **No CONTRIBUTING.md**
**Evidence:** No `CONTRIBUTING.md` file.  
**Impact:** Contributors don't know branch naming, commit message style, PR checklist.  
**Fix:** Add lightweight CONTRIBUTING.md:
- Branch naming: `feat/`, `fix/`, `chore/`
- Commit style: Conventional Commits
- PR checklist: tests pass, docs updated, CHANGELOG.md entry  
**Cost:** 30 min.

---

### 24. **PostgreSQL schema migration rollback not documented**
**File:** `app/db/migrate.py`  
**Evidence:** Migrations apply forward-only. No rollback mechanism.  
**Issue:** If a migration breaks production, manual SQL rollback required.  
**Fix:** Document manual rollback steps in `app/db/migrations/README.md` or use a migration tool like Alembic.  
**Cost:** 1 hour (doc) or 3 hours (Alembic integration).

---

### 25. **No code coverage reporting**
**Evidence:** No `.coveragerc`, no coverage badge, no CI coverage job.  
**Fix:** Add pytest-cov to CI:
```yaml
- run: pytest --cov=app --cov-report=xml
- uses: codecov/codecov-action@v3
```
**Cost:** 30 min.

---

### 26. **MTR requires NET_RAW but not documented in security model**
**File:** `docker-compose.yml:39`, docs/SECURITY.md  
**Evidence:** `cap_add: NET_RAW` present but docs/SECURITY.md doesn't mention capability implications.  
**Fix:** Add section to SECURITY.md:
```markdown
## Docker Capabilities

MTR diagnostics require `NET_RAW` for ICMP/TCP probing. This is a privileged capability. 
Mitigation: MTR is optional (MTR_ENABLED=false disables it). If enabled, MTR runs in the 
same container as the API (no sidecar), so the blast radius is limited to this service.
```
**Cost:** 10 min.

---

### 27. **No example kubernetes manifests**
**Evidence:** Only docker-compose.yml. No k8s YAML or Helm chart.  
**Impact:** Kubernetes users must write manifests from scratch.  
**Fix:** Add `examples/kubernetes/` with Deployment, Service, ConfigMap.  
**Cost:** 2 hours (basic manifests + README).

---

## Non-Issues (Verified Correct)

### ✅ DNS constraints respected
AGENT.md rules enforced: no dns: override, no host network, no sidecar resolvers. All code respects `127.0.0.11`.

### ✅ API security comprehensive
Roles, IP allowlists, rate limits, abuse protection all implemented. Default `API_AUTH_ENABLED=false` is a P1 documentation issue, not a code bug.

### ✅ MTR mutex correctly implemented
`_run_lock = asyncio.Lock()` in `mtr_runner.py:15` prevents concurrent MTR runs. 409 returned on conflict.

### ✅ PostgreSQL retention cleanup safe
`retention.py` and `db/cleanup.py` correctly compute cutoff, use transactions, and cascade deletes. No orphan risk.

### ✅ Async cancellation handled
`dns_runner.py` and `mtr_runner.py` correctly propagate `asyncio.CancelledError`.

### ✅ Web UI is optional
`DNS_DEBUG_UI_ENABLED=false` disables all UI routes. Core DNS engine works standalone.

### ✅ I18n implementation solid
EN/RU localization uses namespaced JSON, fallback chain, no hardcoded strings in templates.

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total findings | 27 |
| P0 (critical) | 5 |
| P1 (high value) | 12 |
| P2 (nice-to-have) | 10 |
| Lines of code (Python) | 6808 |
| Test lines | 713 (52 test cases) |
| Test coverage gaps | Core DNS/MTR untested |
| Open branches | 2 (develop, feat/ui-web-ui) |
| Dependency count | 10 (all unpinned) |
| CI/CD | None |

---

## Recommended Next Steps (Top 5)

1. **P0 #1 — Pin dependencies** (30 min) — Immediate supply chain risk mitigation.
2. **P1 #6 — Add CI/CD pipeline** (2 hours) — Unlock automated testing and PR checks.
3. **P1 #10 — Test core DNS/MTR logic** (4-6 hours) — Close biggest coverage gap.
4. **P1 #7 — Resolve develop branch divergence** (30 min) — Prevent merge disasters.
5. **P1 #9 — Enable Dependabot** (15 min) — Ongoing vulnerability monitoring.

**After top 5:** Tackle P0 #2-5 (credential security), then P1 #11-17 (health checks, atomic writes, pool sizing).

---

## Methodology

- **Approach:** Static code review, git history analysis, documentation audit, threat modeling.
- **Tools:** grep, git diff, manual Python inspection.
- **Scope:** Security, reliability, tests, CI/CD, DX, observability, code quality.
- **Constraints:** Respected project DNS invariants (AGENT.md). No changes proposed that break Docker DNS model.
- **Limitations:** No runtime testing. No performance profiling. No dependency CVE scan (requires online tools).

---

## Audit Log

- **2026-09-10 13:35 UTC** — Cloned repo, checked branches, reviewed AGENT.md constraints.
- **2026-09-10 13:40 UTC** — Audited `.env.example`, `requirements.txt`, Dockerfile, docker-compose.yml.
- **2026-09-10 13:50 UTC** — Reviewed `app/security/*`, `app/config.py`, docs/SECURITY.md.
- **2026-09-10 14:00 UTC** — Examined `app/dns_runner.py`, `app/mtr_runner.py`, `app/stats_store.py`.
- **2026-09-10 14:10 UTC** — Checked `app/db/*`, `tests/*`, `app/api.py`.
- **2026-09-10 14:20 UTC** — Investigated branch divergence (develop vs main).
- **2026-09-10 14:30 UTC** — Compiled findings, prioritized, wrote report.

---

**End of report.**
