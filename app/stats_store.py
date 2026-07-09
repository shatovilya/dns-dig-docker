from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import deque
from typing import Any

from models import NoiseType, QueryOutcome, TestStatus, TestSummaryResponse, is_absolute_fqdn_mode, is_search_resolve_mode
from ndots_analytics import build_test_analytics
from resolver_snapshot import get_snapshot
from utils import percentile, safe_ratio


@dataclass
class QueryAttempt:
    timestamp: datetime
    record: str
    query_type: str
    resolve_mode: str
    effective_name: str
    outcome: QueryOutcome
    latency_ms: float
    answers_count: int = 0
    ttl_min: int | None = None
    is_noisy: bool = False
    noise_type: NoiseType | None = None
    error_message: str | None = None
    is_search_probe: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "record": self.record,
            "query_type": self.query_type,
            "resolve_mode": self.resolve_mode,
            "effective_name": self.effective_name,
            "outcome": self.outcome.value,
            "latency_ms": round(self.latency_ms, 3),
            "answers_count": self.answers_count,
            "ttl_min": self.ttl_min,
            "is_noisy": self.is_noisy,
            "noise_type": self.noise_type.value if self.noise_type else None,
            "error_message": self.error_message,
            "is_search_probe": self.is_search_probe,
        }


@dataclass
class RecordStats:
    record: str
    total_queries: int = 0
    a_queries: int = 0
    aaaa_queries: int = 0
    errors: int = 0
    nxdomains: int = 0
    noisy_system_resolves: int = 0
    fqdn_wins: int = 0
    latency_sum_ms: float = 0.0
    latency_by_mode: dict[str, float] = field(default_factory=dict)
    mode_query_counts: dict[str, int] = field(default_factory=dict)
    search_suffix_nxdomain_count: int = 0
    timeouts_by_mode: dict[str, int] = field(default_factory=dict)
    system_failures: set[tuple[str, str]] = field(default_factory=set)
    fqdn_successes: set[tuple[str, str]] = field(default_factory=set)

    @property
    def avg_latency_ms(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.latency_sum_ms / self.total_queries


@dataclass
class TestCounters:
    total: int = 0
    success: int = 0
    error: int = 0
    nxdomain: int = 0
    timeout: int = 0
    noisy: int = 0
    possible_cache_hits: int = 0
    by_query_type: dict[str, int] = field(default_factory=dict)
    by_resolve_mode: dict[str, int] = field(default_factory=dict)
    by_outcome: dict[str, int] = field(default_factory=dict)


@dataclass
class TestState:
    test_id: str
    test_name: str
    status: TestStatus
    config: dict[str, Any]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    counters: TestCounters = field(default_factory=TestCounters)
    per_record: dict[str, RecordStats] = field(default_factory=dict)
    noise_counts: dict[str, int] = field(default_factory=dict)
    latency_samples: list[float] = field(default_factory=list)
    events: deque = field(default_factory=deque)
    summary: TestSummaryResponse | None = None
    progress: float = 0.0

    def ensure_record(self, record: str) -> RecordStats:
        if record not in self.per_record:
            self.per_record[record] = RecordStats(record=record)
        return self.per_record[record]


class StatsStore:
    def __init__(self, event_buffer_size: int = 1000) -> None:
        self._tests: dict[str, TestState] = {}
        self._lock = None  # set lazily to avoid event loop issues
        self.event_buffer_size = event_buffer_size

    def _get_lock(self):
        import asyncio

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def create_test(self, test_id: str, test_name: str, config: dict[str, Any]) -> TestState:
        async with self._get_lock():
            state = TestState(
                test_id=test_id,
                test_name=test_name,
                status=TestStatus.PENDING,
                config=config,
                events=deque(maxlen=self.event_buffer_size),
            )
            self._tests[test_id] = state
            return state

    async def get_test(self, test_id: str) -> TestState | None:
        async with self._get_lock():
            return self._tests.get(test_id)

    async def list_tests(self) -> list[TestState]:
        async with self._get_lock():
            return list(self._tests.values())

    async def update_status(self, test_id: str, status: TestStatus) -> None:
        async with self._get_lock():
            test = self._tests.get(test_id)
            if test:
                test.status = status
                if status == TestStatus.RUNNING and test.started_at is None:
                    test.started_at = datetime.now(timezone.utc)
                if status in (TestStatus.COMPLETED, TestStatus.CANCELLED, TestStatus.FAILED):
                    test.finished_at = datetime.now(timezone.utc)

    async def set_progress(self, test_id: str, progress: float) -> None:
        async with self._get_lock():
            test = self._tests.get(test_id)
            if test:
                test.progress = min(1.0, max(0.0, progress))

    async def record_attempt(self, test_id: str, attempt: QueryAttempt) -> None:
        async with self._get_lock():
            test = self._tests.get(test_id)
            if not test:
                return

            test.counters.total += 1
            test.counters.by_query_type[attempt.query_type] = (
                test.counters.by_query_type.get(attempt.query_type, 0) + 1
            )
            test.counters.by_resolve_mode[attempt.resolve_mode] = (
                test.counters.by_resolve_mode.get(attempt.resolve_mode, 0) + 1
            )
            test.counters.by_outcome[attempt.outcome.value] = (
                test.counters.by_outcome.get(attempt.outcome.value, 0) + 1
            )

            if attempt.outcome == QueryOutcome.SUCCESS:
                test.counters.success += 1
            elif attempt.outcome == QueryOutcome.NXDOMAIN:
                test.counters.nxdomain += 1
            elif attempt.outcome == QueryOutcome.TIMEOUT:
                test.counters.timeout += 1
            else:
                test.counters.error += 1

            if attempt.is_noisy:
                test.counters.noisy += 1
                if attempt.noise_type:
                    key = attempt.noise_type.value
                    test.noise_counts[key] = test.noise_counts.get(key, 0) + 1

            test.latency_samples.append(attempt.latency_ms)
            test.events.append(attempt)

            rec = test.ensure_record(attempt.record)
            rec.total_queries += 1
            rec.latency_sum_ms += attempt.latency_ms
            if attempt.query_type == "A":
                rec.a_queries += 1
            elif attempt.query_type == "AAAA":
                rec.aaaa_queries += 1
            if attempt.outcome in (QueryOutcome.ERROR, QueryOutcome.TIMEOUT):
                rec.errors += 1
            if attempt.outcome == QueryOutcome.NXDOMAIN:
                rec.nxdomains += 1
            if attempt.is_noisy and is_search_resolve_mode(attempt.resolve_mode):
                rec.noisy_system_resolves += 1

            if not attempt.is_search_probe:
                rec.latency_by_mode[attempt.resolve_mode] = (
                    rec.latency_by_mode.get(attempt.resolve_mode, 0) + attempt.latency_ms
                )
                rec.mode_query_counts[attempt.resolve_mode] = (
                    rec.mode_query_counts.get(attempt.resolve_mode, 0) + 1
                )

            if attempt.noise_type == NoiseType.SEARCH_SUFFIX_NXDOMAIN:
                rec.search_suffix_nxdomain_count += 1

            if attempt.outcome == QueryOutcome.TIMEOUT and not attempt.is_search_probe:
                rec.timeouts_by_mode[attempt.resolve_mode] = (
                    rec.timeouts_by_mode.get(attempt.resolve_mode, 0) + 1
                )

            key = (attempt.query_type, attempt.record)
            if is_search_resolve_mode(attempt.resolve_mode) and attempt.outcome in (
                QueryOutcome.ERROR,
                QueryOutcome.NXDOMAIN,
                QueryOutcome.TIMEOUT,
            ):
                rec.system_failures.add(key)
            if is_absolute_fqdn_mode(attempt.resolve_mode) and attempt.outcome == QueryOutcome.SUCCESS:
                rec.fqdn_successes.add(key)

            rec.fqdn_wins = len(rec.system_failures & rec.fqdn_successes)

    async def increment_cache_hits(self, test_id: str) -> None:
        async with self._get_lock():
            test = self._tests.get(test_id)
            if test:
                test.counters.possible_cache_hits += 1

    async def set_summary(self, test_id: str, summary: TestSummaryResponse) -> None:
        async with self._get_lock():
            test = self._tests.get(test_id)
            if test:
                test.summary = summary

    def build_summary(self, test: TestState) -> TestSummaryResponse:
        total = test.counters.total
        success = test.counters.success
        errors = test.counters.error + test.counters.timeout
        nxdomains = test.counters.nxdomain
        noisy = test.counters.noisy
        duplicates = test.noise_counts.get(NoiseType.DUPLICATE_QUERY.value, 0)
        aaaa_count = test.counters.by_query_type.get("AAAA", 0)
        cache_hits = test.counters.possible_cache_hits

        eventual_fqdn = 0
        for rec in test.per_record.values():
            eventual_fqdn += len(rec.system_failures & rec.fqdn_successes)

        avg_latency = safe_ratio(sum(test.latency_samples), len(test.latency_samples))
        p95 = percentile(test.latency_samples, 95)

        snapshot = get_snapshot()
        ndots_analytics = build_test_analytics(test, snapshot)

        return TestSummaryResponse(
            total_queries=total,
            success_rate=safe_ratio(success, total),
            error_rate=safe_ratio(errors, total),
            nxdomains=nxdomains,
            avg_latency_ms=round(avg_latency, 3),
            p95_latency_ms=round(p95, 3),
            duplicate_ratio=safe_ratio(duplicates, total),
            aaaa_ratio=safe_ratio(aaaa_count, total),
            noisy_query_ratio=safe_ratio(noisy, total),
            possible_cache_hit_ratio=safe_ratio(cache_hits, total),
            cache_like_behavior={
                "possible_cache_hits": cache_hits,
                "threshold_ms": test.config.get("cache_latency_threshold_ms", 5.0),
                "ratio_threshold": test.config.get("cache_latency_ratio", 0.5),
            },
            noise_counts=dict(test.noise_counts),
            eventual_fqdn_success_count=eventual_fqdn,
            ndots_search_analytics=ndots_analytics,
        )

    async def get_global_summary(self) -> dict[str, Any]:
        async with self._get_lock():
            tests = list(self._tests.values())
            active = sum(1 for t in tests if t.status == TestStatus.RUNNING)
            completed = sum(1 for t in tests if t.status == TestStatus.COMPLETED)
            total_queries = sum(t.counters.total for t in tests)
            all_samples: list[float] = []
            total_success = 0
            total_errors = 0
            total_nx = 0
            total_noisy = 0
            total_duplicates = 0
            total_aaaa = 0
            total_cache = 0
            noise_agg: dict[str, int] = {}
            eventual = 0

            for t in tests:
                all_samples.extend(t.latency_samples)
                total_success += t.counters.success
                total_errors += t.counters.error + t.counters.timeout
                total_nx += t.counters.nxdomain
                total_noisy += t.counters.noisy
                total_duplicates += t.noise_counts.get(NoiseType.DUPLICATE_QUERY.value, 0)
                total_aaaa += t.counters.by_query_type.get("AAAA", 0)
                total_cache += t.counters.possible_cache_hits
                for k, v in t.noise_counts.items():
                    noise_agg[k] = noise_agg.get(k, 0) + v
                for rec in t.per_record.values():
                    eventual += len(rec.system_failures & rec.fqdn_successes)

            agg = TestSummaryResponse(
                total_queries=total_queries,
                success_rate=safe_ratio(total_success, total_queries),
                error_rate=safe_ratio(total_errors, total_queries),
                nxdomains=total_nx,
                avg_latency_ms=round(safe_ratio(sum(all_samples), len(all_samples)), 3),
                p95_latency_ms=round(percentile(all_samples, 95), 3),
                duplicate_ratio=safe_ratio(total_duplicates, total_queries),
                aaaa_ratio=safe_ratio(total_aaaa, total_queries),
                noisy_query_ratio=safe_ratio(total_noisy, total_queries),
                possible_cache_hit_ratio=safe_ratio(total_cache, total_queries),
                cache_like_behavior={"possible_cache_hits": total_cache},
                noise_counts=noise_agg,
                eventual_fqdn_success_count=eventual,
            )

            return {
                "total_tests": len(tests),
                "active_tests": active,
                "completed_tests": completed,
                "total_queries": total_queries,
                "aggregate_summary": agg,
            }

    async def get_running_test_ids(self) -> list[str]:
        async with self._get_lock():
            return [t.test_id for t in self._tests.values() if t.status == TestStatus.RUNNING]


_store: StatsStore | None = None


def get_stats_store() -> StatsStore:
    global _store
    if _store is None:
        from config import get_settings

        _store = StatsStore(event_buffer_size=get_settings().event_buffer_size)
    return _store
