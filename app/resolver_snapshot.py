from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import get_settings


@dataclass
class ResolverSnapshot:
    nameservers: list[str] = field(default_factory=list)
    search: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    ndots: int | None = None
    timeout_seconds: float | None = None
    attempts: int | None = None
    raw: str = ""
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_snapshot: ResolverSnapshot | None = None


def parse_ndots(options: list[str]) -> int | None:
    for opt in options:
        if opt.startswith("ndots:"):
            return int(opt.split(":", 1)[1])
    return None


def parse_timeout(options: list[str]) -> float | None:
    for opt in options:
        if opt.startswith("timeout:"):
            return float(opt.split(":", 1)[1])
    return None


def parse_attempts(options: list[str]) -> int | None:
    for opt in options:
        if opt.startswith("attempts:"):
            return int(opt.split(":", 1)[1])
    return None


def parse_resolv_conf(
    content: str,
) -> tuple[list[str], list[str], list[str], int | None, float | None, int | None]:
    nameservers: list[str] = []
    search: list[str] = []
    options: list[str] = []

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        parts = line.split()
        if not parts:
            continue
        keyword = parts[0].lower()
        values = parts[1:]
        if keyword == "nameserver":
            nameservers.extend(values)
        elif keyword == "search":
            search.extend(values)
        elif keyword == "domain":
            if values:
                search.append(values[0])
        elif keyword == "options":
            options.extend(values)

    return (
        nameservers,
        search,
        options,
        parse_ndots(options),
        parse_timeout(options),
        parse_attempts(options),
    )


def capture(path: str | None = None) -> ResolverSnapshot:
    settings = get_settings()
    resolv_path = Path(path or settings.resolv_conf_path)
    try:
        raw = resolv_path.read_text(encoding="utf-8")
    except OSError:
        raw = ""
    nameservers, search, options, ndots, timeout_seconds, attempts = parse_resolv_conf(raw)
    return ResolverSnapshot(
        nameservers=nameservers,
        search=search,
        options=options,
        ndots=ndots,
        timeout_seconds=timeout_seconds,
        attempts=attempts,
        raw=raw,
        captured_at=datetime.now(timezone.utc),
    )


def get_snapshot(refresh: bool = False) -> ResolverSnapshot:
    global _snapshot
    if _snapshot is None or refresh:
        _snapshot = capture()
    return _snapshot


def set_snapshot(snapshot: ResolverSnapshot) -> None:
    global _snapshot
    _snapshot = snapshot
