import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import get_settings


class MtrRunStatus:
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class MtrHop:
    hop: int
    host: str
    loss_pct: float
    sent: int
    last_ms: float | None
    avg_ms: float | None
    best_ms: float | None
    worst_ms: float | None
    stdev_ms: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hop": self.hop,
            "host": self.host,
            "loss_pct": self.loss_pct,
            "sent": self.sent,
            "last_ms": self.last_ms,
            "avg_ms": self.avg_ms,
            "best_ms": self.best_ms,
            "worst_ms": self.worst_ms,
            "stdev_ms": self.stdev_ms,
        }


@dataclass
class MtrRunResult:
    run_id: str
    service_name: str
    port: int
    count: int
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: float | None = None
    exit_code: int | None = None
    raw_report: str = ""
    stderr: str = ""
    status: str = MtrRunStatus.RUNNING
    parsed_hops: list[MtrHop] = field(default_factory=list)
    triggered_by: str = "periodic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "service_name": self.service_name,
            "port": self.port,
            "count": self.count,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": round(self.duration_ms, 3) if self.duration_ms is not None else None,
            "exit_code": self.exit_code,
            "raw_report": self.raw_report,
            "stderr": self.stderr,
            "status": self.status,
            "parsed_hops": [h.to_dict() for h in self.parsed_hops],
            "triggered_by": self.triggered_by,
        }


class MtrStore:
    def __init__(self, max_history: int = 10) -> None:
        self._runs: deque[MtrRunResult] = deque(maxlen=max_history)
        self._by_id: dict[str, MtrRunResult] = {}
        self._latest: MtrRunResult | None = None
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def create_run(
        self,
        service_name: str,
        port: int,
        count: int,
        *,
        triggered_by: str = "periodic",
        run_id: str | None = None,
    ) -> MtrRunResult:
        async with self._get_lock():
            result = MtrRunResult(
                run_id=run_id or str(uuid.uuid4()),
                service_name=service_name,
                port=port,
                count=count,
                started_at=datetime.now(timezone.utc),
                triggered_by=triggered_by,
            )
            self._by_id[result.run_id] = result
            self._latest = result
            return result

    async def complete_run(self, run_id: str, **updates: Any) -> MtrRunResult | None:
        async with self._get_lock():
            result = self._by_id.get(run_id)
            if not result:
                return None
            for key, value in updates.items():
                setattr(result, key, value)
            if result.status != MtrRunStatus.RUNNING:
                self._runs.append(result)
                self._latest = result
            return result

    async def finalize_run(self, run_id: str, **updates: Any) -> MtrRunResult | None:
        async with self._get_lock():
            result = self._by_id.get(run_id)
            if not result:
                return None
            for key, value in updates.items():
                setattr(result, key, value)
            self._runs.append(result)
            self._latest = result
            return result

    async def get_run(self, run_id: str) -> MtrRunResult | None:
        async with self._get_lock():
            return self._by_id.get(run_id)

    async def get_latest(self) -> MtrRunResult | None:
        async with self._get_lock():
            return self._latest

    async def list_runs(self) -> list[MtrRunResult]:
        async with self._get_lock():
            return list(reversed(self._runs))

    async def is_running(self) -> bool:
        async with self._get_lock():
            if self._latest and self._latest.status == MtrRunStatus.RUNNING:
                return True
            return False


_store: MtrStore | None = None


def get_mtr_store() -> MtrStore:
    global _store
    if _store is None:
        _store = MtrStore(max_history=get_settings().mtr_max_history)
    return _store
