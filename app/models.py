from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ResolveMode(str, Enum):
    SYSTEM = "system"
    ABSOLUTE_FQDN = "absolute_fqdn"


@dataclass(frozen=True)
class ResolveSpec:
    kind: Literal["system", "absolute_fqdn", "ndots"]
    ndots: int | None = None

    @property
    def label(self) -> str:
        if self.kind == "ndots" and self.ndots is not None:
            return f"ndots:{self.ndots}"
        return self.kind

    def uses_search(self) -> bool:
        return self.kind in ("system", "ndots")

    @classmethod
    def from_resolve_mode(cls, mode: ResolveMode) -> "ResolveSpec":
        return cls(kind=mode.value)  # type: ignore[arg-type]

    @classmethod
    def ndots_override(cls, n: int) -> "ResolveSpec":
        return cls(kind="ndots", ndots=n)


def expand_resolve_specs(resolve_modes: list[str], ndots_values: list[int]) -> list[ResolveSpec]:
    specs: list[ResolveSpec] = []
    for m in resolve_modes:
        specs.append(ResolveSpec.from_resolve_mode(ResolveMode(m)))
    for n in ndots_values:
        specs.append(ResolveSpec.ndots_override(n))
    return specs


def is_search_resolve_mode(label: str) -> bool:
    return label == "system" or label.startswith("ndots:")


def is_absolute_fqdn_mode(label: str) -> bool:
    return label == "absolute_fqdn"


class NoiseType(str, Enum):
    SEARCH_SUFFIX_QUERY = "search_suffix_query"
    SEARCH_SUFFIX_NXDOMAIN = "search_suffix_nxdomain"
    DUPLICATE_QUERY = "duplicate_query"
    EMPTY_ANSWER = "empty_answer"
    AAAA_NOISE = "aaaa_noise"
    EVENTUAL_FQDN_SUCCESS = "eventual_fqdn_success"


class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class QueryOutcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    NXDOMAIN = "nxdomain"
    TIMEOUT = "timeout"


class TestCreateRequest(BaseModel):
    test_name: str = "dns-debug-test"
    records: list[str] = Field(min_length=1)
    query_types: list[str] = Field(default=["A", "AAAA"], min_length=1)
    resolve_modes: list[ResolveMode] = Field(min_length=1)
    ndots_values: list[int] = Field(default_factory=list)
    rps: float = Field(gt=0)
    concurrency: int = Field(ge=1)
    duration_seconds: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)

    @field_validator("query_types")
    @classmethod
    def normalize_query_types(cls, v: list[str]) -> list[str]:
        return [t.upper() for t in v]

    @field_validator("ndots_values")
    @classmethod
    def validate_ndots_values(cls, v: list[int]) -> list[int]:
        seen: set[int] = set()
        result: list[int] = []
        for n in v:
            if not 0 <= n <= 15:
                raise ValueError("ndots_values must be integers between 0 and 15")
            if n not in seen:
                seen.add(n)
                result.append(n)
        return result


class TestListItem(BaseModel):
    test_id: str
    test_name: str
    status: TestStatus
    started_at: datetime | None = None


class RecordStatsResponse(BaseModel):
    record: str
    total_queries: int = 0
    a_queries: int = 0
    aaaa_queries: int = 0
    errors: int = 0
    nxdomains: int = 0
    noisy_system_resolves: int = 0
    fqdn_wins: int = 0
    avg_latency_ms: float = 0.0
    dot_count: int = 0
    search_first_at_configured_ndots: bool = False
    fqdn_latency_delta_ms: float = 0.0


class RecordDnsProfileResponse(BaseModel):
    record: str
    dot_count: int = 0
    search_first_at_configured_ndots: bool = False
    estimated_search_attempts: int = 0
    estimated_queries_per_lookup: int = 0
    avg_latency_by_mode: dict[str, float] = Field(default_factory=dict)
    fqdn_latency_delta_ms: float = 0.0
    ndots_latency_deltas: dict[str, float] = Field(default_factory=dict)
    search_suffix_nxdomain_count: int = 0
    timeout_count_by_mode: dict[str, int] = Field(default_factory=dict)


class NdotsSearchAnalytics(BaseModel):
    query_amplification_ratio: float = 0.0
    search_suffix_nxdomain_ratio: float = 0.0
    avg_fqdn_latency_savings_ms: float = 0.0
    records_search_first_count: int = 0
    records_where_fqdn_faster_count: int = 0
    worst_case_resolve_budget_ms: float = 0.0
    dual_stack_overhead_ratio: float = 0.0
    configured_ndots: int = 1
    configured_timeout_seconds: float = 5.0
    configured_attempts: int = 2
    search_domains_count: int = 0
    per_record: list[RecordDnsProfileResponse] = Field(default_factory=list)


class DiagnosisResponse(BaseModel):
    test_id: str
    signals: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"] = "low"
    likely_ndots_search_issue: bool = False
    recommendations: list[str] = Field(default_factory=list)
    resolver_context: dict[str, Any] = Field(default_factory=dict)
    analytics: NdotsSearchAnalytics | None = None


class TestSummaryResponse(BaseModel):
    total_queries: int = 0
    success_rate: float = 0.0
    error_rate: float = 0.0
    nxdomains: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    duplicate_ratio: float = 0.0
    aaaa_ratio: float = 0.0
    noisy_query_ratio: float = 0.0
    possible_cache_hit_ratio: float = 0.0
    cache_like_behavior: dict[str, Any] = Field(default_factory=dict)
    noise_counts: dict[str, int] = Field(default_factory=dict)
    eventual_fqdn_success_count: int = 0
    ndots_search_analytics: NdotsSearchAnalytics | None = None


class TestDetailResponse(BaseModel):
    test_id: str
    test_name: str
    status: TestStatus
    config: dict[str, Any]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float = 0.0
    counters: dict[str, Any] = Field(default_factory=dict)
    per_record: list[RecordStatsResponse] = Field(default_factory=list)
    summary: TestSummaryResponse | None = None
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


class GlobalSummaryResponse(BaseModel):
    total_tests: int = 0
    active_tests: int = 0
    completed_tests: int = 0
    total_queries: int = 0
    aggregate_summary: TestSummaryResponse = Field(default_factory=TestSummaryResponse)
    tests: list[TestListItem] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    autonomous_mode: bool = False
    autonomous_test_id: str | None = None
    mtr_enabled: bool = False
    mtr_service_name: str | None = None


class MtrHopResponse(BaseModel):
    hop: int
    host: str
    loss_pct: float
    sent: int
    last_ms: float | None = None
    avg_ms: float | None = None
    best_ms: float | None = None
    worst_ms: float | None = None
    stdev_ms: float | None = None


class MtrRunResponse(BaseModel):
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
    status: Literal["running", "completed", "failed", "timeout"]
    parsed_hops: list[MtrHopResponse] = Field(default_factory=list)
    triggered_by: str = "periodic"


class MtrStatusResponse(BaseModel):
    run_id: str
    status: Literal["running", "completed", "failed", "timeout"]


class ResolverSnapshotResponse(BaseModel):
    nameservers: list[str]
    search: list[str]
    options: list[str]
    ndots: int | None = None
    timeout_seconds: float | None = None
    attempts: int | None = None
    raw: str
    captured_at: datetime
