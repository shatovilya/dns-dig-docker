import logging
import re
from pathlib import Path

from db.connection import get_db_pool

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_VERSION_RE = re.compile(r"^(\d+)_")


async def run_migrations() -> None:
    pool = get_db_pool()
    if pool is None:
        return

    files = sorted(_MIGRATIONS_DIR.glob("*.sql"), key=_migration_version)
    if not files:
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row["version"]
            for row in await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
        }
        for path in files:
            version = _migration_version(path)
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                for statement in _split_sql_statements(sql):
                    if statement.strip():
                        await conn.execute(statement)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT DO NOTHING",
                    version,
                )
            logger.info(
                "Applied database migration %s",
                path.name,
                extra={"event": "db_migration_applied", "extra": {"version": version}},
            )


def _migration_version(path: Path) -> int:
    match = _VERSION_RE.match(path.name)
    if not match:
        raise ValueError(f"Invalid migration filename: {path.name}")
    return int(match.group(1))


def _split_sql_statements(sql: str) -> list[str]:
    """Split migration file into executable statements (no semicolons inside strings)."""
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements
