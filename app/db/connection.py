import logging
from typing import TYPE_CHECKING

from config import Settings, get_settings

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_pool: "asyncpg.Pool | None" = None
_db_available: bool = False


async def init_db_pool(settings: Settings | None = None) -> "asyncpg.Pool | None":
    """Create asyncpg pool when DB persistence is enabled."""
    global _pool, _db_available
    settings = settings or get_settings()
    if not settings.dns_debug_db_enabled:
        _db_available = False
        return None

    import asyncpg

    ssl = False if settings.dns_debug_db_sslmode == "disable" else True
    try:
        _pool = await asyncpg.create_pool(
            host=settings.dns_debug_db_host,
            port=settings.dns_debug_db_port,
            user=settings.dns_debug_db_user,
            password=settings.dns_debug_db_password,
            database=settings.dns_debug_db_name,
            ssl=ssl,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        _db_available = True
        logger.info(
            "PostgreSQL pool initialized",
            extra={
                "event": "db_pool_ready",
                "extra": {
                    "host": settings.dns_debug_db_host,
                    "database": settings.dns_debug_db_name,
                    "retention_days": settings.dns_debug_db_retention_days,
                },
            },
        )
        return _pool
    except Exception as exc:
        _pool = None
        _db_available = False
        logger.error(
            "Failed to initialize PostgreSQL pool: %s",
            exc,
            extra={"event": "db_pool_failed"},
        )
        return None


async def close_db_pool() -> None:
    global _pool, _db_available
    if _pool is not None:
        await _pool.close()
        _pool = None
    _db_available = False


def get_db_pool() -> "asyncpg.Pool | None":
    return _pool


def is_db_available() -> bool:
    return _db_available and _pool is not None
