import asyncio
from collections import defaultdict

from fastapi import HTTPException, Request

from config import get_settings
from security.principal import Principal

_active_jobs_by_cred: dict[str, int] = defaultdict(int)
_concurrent_runs_lock = asyncio.Lock()


async def check_dns_run_allowed(request: Request, principal: Principal) -> None:
    settings = get_settings()
    from stats_store import get_stats_store

    store = get_stats_store()
    tests = await store.list_tests()
    running = sum(1 for t in tests if t.status.value == "running")

    async with _concurrent_runs_lock:
        if running >= settings.dns_max_concurrent_runs:
            raise HTTPException(429, "max concurrent DNS runs reached")
        active_for_cred = sum(
            1
            for t in tests
            if t.status.value == "running"
            and t.config.get("triggered_by") == principal.credential_id
        )
        if active_for_cred >= settings.dns_max_active_jobs_per_token:
            raise HTTPException(429, "max active jobs per credential reached")


def validate_run_duration(duration_seconds: int) -> None:
    settings = get_settings()
    cap = min(settings.max_duration_seconds, settings.dns_max_run_duration_seconds)
    if duration_seconds > cap:
        raise HTTPException(400, f"duration_seconds exceeds max ({cap})")
