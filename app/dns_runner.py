import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from itertools import cycle
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.rdatatype

from config import Settings, get_settings
from models import (
    NoiseType,
    QueryOutcome,
    ResolveSpec,
    TestStatus,
    expand_resolve_specs,
)
from resolver_snapshot import get_snapshot
from stats_store import QueryAttempt, get_stats_store
from utils import ensure_trailing_dot, is_absolute_fqdn

import metrics

logger = logging.getLogger(__name__)

_running_tasks: dict[str, asyncio.Task] = {}
_cancel_events: dict[str, asyncio.Event] = {}
_active_count = 0


def get_cancel_event(test_id: str) -> asyncio.Event | None:
    return _cancel_events.get(test_id)


def register_task(test_id: str, task: asyncio.Task, cancel_event: asyncio.Event) -> None:
    _running_tasks[test_id] = task
    _cancel_events[test_id] = cancel_event


def unregister_task(test_id: str) -> None:
    _running_tasks.pop(test_id, None)
    _cancel_events.pop(test_id, None)


async def _adjust_active_count(delta: int) -> None:
    global _active_count
    _active_count = max(0, _active_count + delta)
    metrics.set_active_tests(_active_count)


def expand_work_items(
    records: list[str],
    query_types: list[str],
    resolve_modes: list[str],
    ndots_values: list[int],
) -> list[tuple[str, str, ResolveSpec]]:
    specs = expand_resolve_specs(resolve_modes, ndots_values)
    return [(r, qt, spec) for r in records for spec in specs for qt in query_types]


def build_autonomous_config(settings: Settings) -> dict[str, Any]:
    return {
        "test_name": "autonomous",
        "records": settings.autonomous_records,
        "query_types": [t.upper() for t in settings.autonomous_query_types],
        "resolve_modes": settings.autonomous_resolve_modes,
        "ndots_values": settings.autonomous_ndots_values,
        "rps": settings.autonomous_rps,
        "concurrency": settings.autonomous_concurrency,
        "duration_seconds": 0,
        "timeout_seconds": settings.autonomous_timeout_seconds,
        "continuous": True,
        "cache_latency_threshold_ms": settings.cache_latency_threshold_ms,
        "cache_latency_ratio": settings.cache_latency_ratio,
    }


def build_test_config_from_settings(
    settings: Settings,
    *,
    autonomous: bool = False,
) -> dict[str, Any]:
    if autonomous:
        return build_autonomous_config(settings)
    return {
        "test_name": "dns-debug-test",
        "records": settings.default_records,
        "query_types": [t.upper() for t in settings.default_query_types],
        "resolve_modes": settings.default_resolve_modes,
        "ndots_values": settings.default_ndots_values,
        "rps": settings.default_rps,
        "concurrency": settings.default_concurrency,
        "duration_seconds": settings.default_duration_seconds,
        "timeout_seconds": settings.default_timeout_seconds,
        "continuous": False,
        "cache_latency_threshold_ms": settings.cache_latency_threshold_ms,
        "cache_latency_ratio": settings.cache_latency_ratio,
    }


class DnsTestRunner:
    def __init__(self, test_id: str, config: dict[str, Any]) -> None:
        self.test_id = test_id
        self.config = config
        self.settings = get_settings()
        self.store = get_stats_store()
        self.cancel_event = asyncio.Event()
        self._duplicate_window: deque[tuple[str, str, str, float]] = deque()
        self._cache_tracker: dict[tuple[str, str, str], float] = {}
        self._a_success_records: set[str] = set()

    def _effective_name(self, record: str, spec: ResolveSpec) -> str:
        if spec.kind == "absolute_fqdn":
            return ensure_trailing_dot(record)
        return record

    def _check_duplicate(self, record: str, query_type: str, spec: ResolveSpec) -> bool:
        now = time.monotonic()
        window = self.settings.duplicate_window_seconds
        key = (record, query_type, spec.label)
        while self._duplicate_window and self._duplicate_window[0][3] < now - window:
            self._duplicate_window.popleft()
        for r, qt, m, _ in self._duplicate_window:
            if (r, qt, m) == key:
                return True
        self._duplicate_window.append((record, query_type, spec.label, now))
        return False

    def _check_cache(
        self, effective_name: str, query_type: str, spec: ResolveSpec, latency_ms: float
    ) -> float | None:
        key = (effective_name, query_type, spec.label)
        if key not in self._cache_tracker:
            self._cache_tracker[key] = latency_ms
            return None
        first_ms = self._cache_tracker[key]
        delta = first_ms - latency_ms
        threshold_ms = self.settings.cache_latency_threshold_ms
        ratio = self.settings.cache_latency_ratio
        if latency_ms < threshold_ms and latency_ms < first_ms * ratio:
            return delta
        return None

    def _classify_noise(
        self,
        record: str,
        query_type: str,
        spec: ResolveSpec,
        outcome: QueryOutcome,
        answers_count: int,
        is_search_probe: bool,
        probe_nxdomain: bool,
    ) -> tuple[bool, NoiseType | None]:
        if is_search_probe:
            if probe_nxdomain:
                return True, NoiseType.SEARCH_SUFFIX_NXDOMAIN
            return True, NoiseType.SEARCH_SUFFIX_QUERY
        if self._check_duplicate(record, query_type, spec):
            return True, NoiseType.DUPLICATE_QUERY
        if outcome == QueryOutcome.SUCCESS and answers_count == 0:
            return True, NoiseType.EMPTY_ANSWER
        if query_type == "AAAA" and record in self._a_success_records:
            return True, NoiseType.AAAA_NOISE
        return False, None

    async def _resolve(
        self,
        record: str,
        query_type: str,
        spec: ResolveSpec,
        timeout: float,
        *,
        is_search_probe: bool = False,
        probe_name: str | None = None,
    ) -> QueryAttempt:
        effective = probe_name if probe_name else self._effective_name(record, spec)
        rdtype = dns.rdatatype.from_text(query_type)
        use_search = spec.uses_search() and not is_absolute_fqdn(effective) and not is_search_probe

        start = time.perf_counter()
        outcome = QueryOutcome.ERROR
        answers_count = 0
        ttl_min: int | None = None
        error_message: str | None = None

        try:
            resolver = dns.asyncresolver.Resolver(configure=True)
            if spec.kind == "ndots" and spec.ndots is not None:
                resolver.ndots = spec.ndots
            answer = await resolver.resolve(
                effective,
                rdtype,
                search=use_search,
                lifetime=timeout,
            )
            answers_count = len(answer.rrset) if answer.rrset else 0
            if answer.rrset is not None:
                ttl_min = answer.rrset.ttl
            outcome = QueryOutcome.SUCCESS
            if query_type == "A" and not is_search_probe:
                self._a_success_records.add(record)
        except dns.resolver.NXDOMAIN:
            outcome = QueryOutcome.NXDOMAIN
        except dns.exception.Timeout:
            outcome = QueryOutcome.TIMEOUT
            error_message = "timeout"
        except Exception as exc:
            outcome = QueryOutcome.ERROR
            error_message = str(exc)

        latency_ms = (time.perf_counter() - start) * 1000.0
        probe_nxdomain = is_search_probe and outcome == QueryOutcome.NXDOMAIN
        is_noisy, noise_type = self._classify_noise(
            record,
            query_type,
            spec,
            outcome,
            answers_count,
            is_search_probe,
            probe_nxdomain,
        )

        attempt = QueryAttempt(
            timestamp=datetime.now(timezone.utc),
            record=record,
            query_type=query_type,
            resolve_mode=spec.label,
            effective_name=effective,
            outcome=outcome,
            latency_ms=latency_ms,
            answers_count=answers_count,
            ttl_min=ttl_min,
            is_noisy=is_noisy,
            noise_type=noise_type,
            error_message=error_message,
            is_search_probe=is_search_probe,
        )
        return attempt

    async def _record_attempt(self, attempt: QueryAttempt) -> None:
        await self.store.record_attempt(self.test_id, attempt)
        metrics.record_query(
            self.test_id,
            attempt.resolve_mode,
            attempt.query_type,
            attempt.outcome.value,
            attempt.latency_ms,
        )
        if attempt.is_noisy and attempt.noise_type:
            metrics.record_noisy(self.test_id, attempt.noise_type.value)
        if not attempt.is_search_probe:
            spec = self._spec_from_label(attempt.resolve_mode)
            delta = self._check_cache(
                attempt.effective_name,
                attempt.query_type,
                spec,
                attempt.latency_ms,
            )
            if delta is not None:
                await self.store.increment_cache_hits(self.test_id)
                metrics.record_possible_cache(self.test_id, delta)

    @staticmethod
    def _spec_from_label(label: str) -> ResolveSpec:
        if label.startswith("ndots:"):
            return ResolveSpec.ndots_override(int(label.split(":", 1)[1]))
        return ResolveSpec(kind=label)  # type: ignore[arg-type]

    async def _run_search_probes(
        self, record: str, query_type: str, spec: ResolveSpec, timeout: float
    ) -> None:
        if is_absolute_fqdn(record):
            return
        snapshot = get_snapshot()
        for domain in snapshot.search:
            probe_name = f"{record}.{domain}"
            attempt = await self._resolve(
                record,
                query_type,
                spec,
                timeout,
                is_search_probe=True,
                probe_name=probe_name,
            )
            await self._record_attempt(attempt)

    async def _execute_query(
        self,
        record: str,
        query_type: str,
        spec: ResolveSpec,
        timeout: float,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            if self.cancel_event.is_set():
                return
            attempt = await self._resolve(record, query_type, spec, timeout)
            await self._record_attempt(attempt)
            if spec.uses_search() and not is_absolute_fqdn(record):
                await self._run_search_probes(record, query_type, spec, timeout)

    async def run(self) -> None:
        config = self.config
        records: list[str] = config["records"]
        query_types: list[str] = config["query_types"]
        resolve_modes: list[str] = config["resolve_modes"]
        ndots_values: list[int] = config.get("ndots_values", [])
        rps: float = config["rps"]
        concurrency: int = config["concurrency"]
        duration_seconds: int | None = config.get("duration_seconds")
        continuous: bool = config.get("continuous", duration_seconds in (0, None))
        timeout: float = config["timeout_seconds"]

        work_items = expand_work_items(records, query_types, resolve_modes, ndots_values)
        if not work_items:
            await self.store.update_status(self.test_id, TestStatus.FAILED)
            return

        work_cycle = cycle(work_items)
        semaphore = asyncio.Semaphore(concurrency)
        interval = 1.0 / rps if rps > 0 else 0.1
        start_time = time.monotonic()
        pending: set[asyncio.Task] = set()
        last_snapshot_checkpoint = start_time
        snapshot_checkpoint_interval = 300.0  # 5 minutes for long/autonomous runs

        await self.store.update_status(self.test_id, TestStatus.RUNNING)
        await _adjust_active_count(1)

        try:
            while not self.cancel_event.is_set():
                elapsed = time.monotonic() - start_time
                if not continuous and duration_seconds is not None and elapsed >= duration_seconds:
                    break

                if continuous:
                    progress = 0.0
                else:
                    progress = min(1.0, elapsed / duration_seconds) if duration_seconds else 1.0
                await self.store.set_progress(self.test_id, progress)
                metrics.set_test_progress(self.test_id, progress)

                record, query_type, spec = next(work_cycle)
                task = asyncio.create_task(
                    self._execute_query(record, query_type, spec, timeout, semaphore)
                )
                pending.add(task)
                task.add_done_callback(pending.discard)

                if continuous and (time.monotonic() - last_snapshot_checkpoint) >= snapshot_checkpoint_interval:
                    try:
                        from snapshot_store import save_test_snapshot

                        await save_test_snapshot(self.test_id)
                        last_snapshot_checkpoint = time.monotonic()
                    except Exception:
                        logger.exception(
                            "Failed periodic UI snapshot checkpoint for test %s",
                            self.test_id,
                            extra={"test_id": self.test_id},
                        )

                await asyncio.sleep(interval)

            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if self.cancel_event.is_set():
                status = TestStatus.CANCELLED
            elif continuous:
                status = TestStatus.RUNNING
            else:
                status = TestStatus.COMPLETED
            await self.store.set_progress(self.test_id, 0.0 if continuous else 1.0)
            metrics.set_test_progress(self.test_id, 0.0 if continuous else 1.0)
        except Exception:
            logger.exception("Test %s failed", self.test_id, extra={"test_id": self.test_id})
            status = TestStatus.FAILED
        finally:
            await self.store.update_status(self.test_id, status)
            test = await self.store.get_test(self.test_id)
            if test:
                summary = self.store.build_summary(test)
                await self.store.set_summary(self.test_id, summary)
                if status == TestStatus.COMPLETED:
                    try:
                        from snapshot_store import save_test_snapshot

                        await save_test_snapshot(self.test_id)
                    except Exception:
                        logger.exception(
                            "Failed to save UI snapshot for test %s",
                            self.test_id,
                            extra={"test_id": self.test_id},
                        )
                if summary.ndots_search_analytics:
                    a = summary.ndots_search_analytics
                    deltas = {
                        p.record: p.fqdn_latency_delta_ms
                        for p in a.per_record
                        if p.fqdn_latency_delta_ms > 0
                    }
                    metrics.set_test_analytics(
                        self.test_id,
                        a.query_amplification_ratio,
                        a.search_suffix_nxdomain_ratio,
                        deltas,
                    )
            await _adjust_active_count(-1)
            unregister_task(self.test_id)


async def start_test(test_id: str, config: dict[str, Any]) -> None:
    runner = DnsTestRunner(test_id, config)
    register_task(test_id, asyncio.current_task(), runner.cancel_event)  # type: ignore[arg-type]
    await runner.run()


def start_test_background(test_id: str, config: dict[str, Any]) -> asyncio.Task:
    cancel_event = asyncio.Event()
    _cancel_events[test_id] = cancel_event

    async def _wrapper() -> None:
        runner = DnsTestRunner(test_id, config)
        runner.cancel_event = cancel_event
        task = asyncio.current_task()
        if task:
            register_task(test_id, task, cancel_event)
        await runner.run()

    task = asyncio.create_task(_wrapper())
    return task


async def cancel_test(test_id: str) -> bool:
    event = get_cancel_event(test_id)
    if event is None:
        return False
    event.set()
    task = _running_tasks.get(test_id)
    if task:
        try:
            await asyncio.wait_for(task, timeout=get_settings().shutdown_timeout_seconds)
        except asyncio.TimeoutError:
            task.cancel()
    return True


async def cancel_all_tests(timeout: float = 5.0) -> None:
    for test_id in list(_cancel_events.keys()):
        event = _cancel_events.get(test_id)
        if event:
            event.set()
    tasks = list(_running_tasks.values())
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
