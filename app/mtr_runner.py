import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from config import Settings, get_settings
from mtr_store import MtrHop, MtrRunStatus, get_mtr_store

import metrics

logger = logging.getLogger(__name__)

_run_lock = asyncio.Lock()
_periodic_task: asyncio.Task | None = None
_cancel_event: asyncio.Event | None = None

_HOP_RE = re.compile(
    r"^\s*(\d+)\.\s+(?:\|--\s+)?(?:AS\S+\s+)?(\S+)\s+([\d.]+%)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)"
)


def build_mtr_command(service_name: str, port: int, count: int) -> list[str]:
    return [
        "mtr",
        "-rzbw",
        service_name,
        "--tcp",
        "-P",
        str(port),
        "-c",
        str(count),
    ]


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def parse_mtr_report(report: str) -> list[MtrHop]:
    hops: list[MtrHop] = []
    for line in report.splitlines():
        match = _HOP_RE.match(line)
        if not match:
            continue
        hops.append(
            MtrHop(
                hop=int(match.group(1)),
                host=match.group(2),
                loss_pct=float(match.group(3).rstrip("%")),
                sent=int(match.group(4)),
                last_ms=_parse_float(match.group(5)),
                avg_ms=_parse_float(match.group(6)),
                best_ms=_parse_float(match.group(7)),
                worst_ms=_parse_float(match.group(8)),
                stdev_ms=_parse_float(match.group(9)),
            )
        )
    return hops


async def run_mtr(
    service_name: str,
    port: int,
    count: int,
    *,
    triggered_by: str = "periodic",
    run_id: str | None = None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    store = get_mtr_store()
    run_id = run_id or str(uuid.uuid4())

    existing = await store.get_run(run_id)
    if existing is None:
        await store.create_run(
            service_name,
            port,
            count,
            triggered_by=triggered_by,
            run_id=run_id,
        )

    cmd = build_mtr_command(service_name, port, count)
    logger.info(
        "Starting MTR run",
        extra={
            "event": "mtr_start",
            "extra": {
                "run_id": run_id,
                "command": cmd,
                "service_name": service_name,
                "port": port,
                "count": count,
                "triggered_by": triggered_by,
            },
        },
    )

    start = time.perf_counter()
    status = MtrRunStatus.FAILED
    exit_code: int | None = None
    raw_report = ""
    stderr_text = ""
    parsed_hops: list[MtrHop] = []

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=settings.mtr_timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            status = MtrRunStatus.TIMEOUT
            stderr_text = f"mtr timed out after {settings.mtr_timeout_seconds}s"
            logger.warning(
                "MTR run timed out",
                extra={"event": "mtr_timeout", "extra": {"run_id": run_id}},
            )
        else:
            raw_report = stdout_bytes.decode(errors="replace")
            stderr_text = stderr_bytes.decode(errors="replace")
            exit_code = proc.returncode
            parsed_hops = parse_mtr_report(raw_report)
            if exit_code == 0:
                status = MtrRunStatus.COMPLETED
            else:
                status = MtrRunStatus.FAILED
                logger.warning(
                    "MTR run failed",
                    extra={
                        "event": "mtr_failed",
                        "extra": {
                            "run_id": run_id,
                            "exit_code": exit_code,
                            "stderr": stderr_text[:500],
                        },
                    },
                )
    except FileNotFoundError:
        status = MtrRunStatus.FAILED
        stderr_text = "mtr binary not found"
        logger.error("mtr binary not found", extra={"event": "mtr_not_found"})
    except Exception as exc:
        status = MtrRunStatus.FAILED
        stderr_text = str(exc)
        logger.exception("MTR run error", extra={"event": "mtr_error", "extra": {"run_id": run_id}})

    duration_ms = (time.perf_counter() - start) * 1000.0
    finished_at = datetime.now(timezone.utc)

    await store.finalize_run(
        run_id,
        finished_at=finished_at,
        duration_ms=duration_ms,
        exit_code=exit_code,
        raw_report=raw_report,
        stderr=stderr_text,
        status=status,
        parsed_hops=parsed_hops,
    )

    result = await store.get_run(run_id)
    if result:
        try:
            from db.repository import persist_mtr_run

            await persist_mtr_run(result)
        except Exception as exc:
            logger.warning("MTR persistence hook failed for %s: %s", run_id, exc)

    metrics.record_mtr_run(status, exit_code, finished_at.timestamp())

    logger.info(
        "MTR run finished",
        extra={
            "event": "mtr_finished",
            "extra": {
                "run_id": run_id,
                "status": status,
                "exit_code": exit_code,
                "duration_ms": round(duration_ms, 3),
                "hops": len(parsed_hops),
            },
        },
    )
    return run_id


async def _run_with_lock(
    service_name: str,
    port: int,
    count: int,
    *,
    triggered_by: str,
    run_id: str | None = None,
) -> str:
    async with _run_lock:
        return await run_mtr(
            service_name,
            port,
            count,
            triggered_by=triggered_by,
            run_id=run_id,
        )


class MtrPeriodicRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cancel_event = asyncio.Event()

    async def run(self) -> None:
        interval = self.settings.mtr_interval_seconds
        service_name = self.settings.mtr_service_name
        port = self.settings.mtr_service_port
        count = self.settings.mtr_count

        while not self.cancel_event.is_set():
            try:
                await _run_with_lock(
                    service_name,
                    port,
                    count,
                    triggered_by="periodic",
                )
            except Exception:
                logger.exception("Periodic MTR run failed", extra={"event": "mtr_periodic_error"})

            if self.cancel_event.is_set():
                break

            try:
                await asyncio.wait_for(self.cancel_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                continue


def start_mtr_background(settings: Settings | None = None) -> asyncio.Task:
    global _periodic_task, _cancel_event
    settings = settings or get_settings()
    _cancel_event = asyncio.Event()

    runner = MtrPeriodicRunner(settings)

    async def _wrapper() -> None:
        runner.cancel_event = _cancel_event  # type: ignore[assignment]
        await runner.run()

    _periodic_task = asyncio.create_task(_wrapper())
    return _periodic_task


async def cancel_mtr(timeout: float = 5.0) -> None:
    global _periodic_task, _cancel_event
    if _cancel_event:
        _cancel_event.set()
    if _periodic_task:
        try:
            await asyncio.wait_for(_periodic_task, timeout=timeout)
        except asyncio.TimeoutError:
            _periodic_task.cancel()
        _periodic_task = None
    _cancel_event = None


def is_mtr_running() -> bool:
    return _run_lock.locked()


async def trigger_mtr_now(
    service_name: str,
    port: int,
    count: int,
) -> str:
    if _run_lock.locked():
        raise MtrAlreadyRunningError("an MTR run is already in progress")

    run_id = str(uuid.uuid4())
    store = get_mtr_store()
    await store.create_run(
        service_name,
        port,
        count,
        triggered_by="api",
        run_id=run_id,
    )
    asyncio.create_task(
        _run_with_lock(
            service_name,
            port,
            count,
            triggered_by="api",
            run_id=run_id,
        )
    )
    return run_id


class MtrAlreadyRunningError(Exception):
    pass
