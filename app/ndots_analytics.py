"""Analytics and diagnosis for ndots/search-domain DNS behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from config import Settings, get_settings
from models import (
    DiagnosisResponse,
    NdotsSearchAnalytics,
    RecordDnsProfileResponse,
)
from resolver_snapshot import ResolverSnapshot
from utils import safe_ratio

if TYPE_CHECKING:
    from stats_store import RecordStats, TestState

DEFAULT_RESOLV_TIMEOUT_SECONDS = 5.0
DEFAULT_RESOLV_ATTEMPTS = 2
DEFAULT_NDOTS = 1


def count_dots(name: str) -> int:
    return name.rstrip(".").count(".")


def resolves_search_first(name: str, ndots: int) -> bool:
    return count_dots(name) < ndots


def estimate_search_attempts(name: str, ndots: int, search_count: int) -> int:
    if search_count <= 0:
        return 0
    if resolves_search_first(name, ndots):
        return search_count
    return 0


def estimate_queries_per_lookup(search_attempts: int, query_type_count: int) -> int:
    return (search_attempts + 1) * max(1, query_type_count)


def effective_ndots(snapshot: ResolverSnapshot) -> int:
    return snapshot.ndots if snapshot.ndots is not None else DEFAULT_NDOTS


def effective_timeout_seconds(snapshot: ResolverSnapshot) -> float:
    return (
        snapshot.timeout_seconds
        if snapshot.timeout_seconds is not None
        else DEFAULT_RESOLV_TIMEOUT_SECONDS
    )


def effective_attempts(snapshot: ResolverSnapshot) -> int:
    return snapshot.attempts if snapshot.attempts is not None else DEFAULT_RESOLV_ATTEMPTS


def worst_case_resolve_budget_ms(
    search_count: int,
    attempts: int,
    timeout_s: float,
    query_type_count: int,
) -> float:
    total_lookups = search_count + 1
    return total_lookups * max(1, query_type_count) * max(1, attempts) * timeout_s * 1000.0


def _avg_latency(latency_sum: float, count: int) -> float:
    if count <= 0:
        return 0.0
    return latency_sum / count


def build_record_profile(
    record: str,
    rec_stats: RecordStats,
    snapshot: ResolverSnapshot,
    query_types: list[str],
) -> RecordDnsProfileResponse:
    ndots = effective_ndots(snapshot)
    search_count = len(snapshot.search)
    search_attempts = estimate_search_attempts(record, ndots, search_count)
    query_type_count = max(1, len(query_types))

    avg_by_mode: dict[str, float] = {}
    for mode, total_ms in rec_stats.latency_by_mode.items():
        count = rec_stats.mode_query_counts.get(mode, 0)
        if count > 0:
            avg_by_mode[mode] = round(_avg_latency(total_ms, count), 3)

    system_avg = avg_by_mode.get("system", 0.0)
    fqdn_avg = avg_by_mode.get("absolute_fqdn", 0.0)
    fqdn_delta = round(system_avg - fqdn_avg, 3) if system_avg and fqdn_avg else 0.0

    ndots_deltas: dict[str, float] = {}
    for mode, avg in avg_by_mode.items():
        if mode.startswith("ndots:") and fqdn_avg:
            ndots_deltas[mode] = round(avg - fqdn_avg, 3)

    return RecordDnsProfileResponse(
        record=record,
        dot_count=count_dots(record),
        search_first_at_configured_ndots=resolves_search_first(record, ndots),
        estimated_search_attempts=search_attempts,
        estimated_queries_per_lookup=estimate_queries_per_lookup(search_attempts, query_type_count),
        avg_latency_by_mode=avg_by_mode,
        fqdn_latency_delta_ms=fqdn_delta,
        ndots_latency_deltas=ndots_deltas,
        search_suffix_nxdomain_count=rec_stats.search_suffix_nxdomain_count,
        timeout_count_by_mode=dict(rec_stats.timeouts_by_mode),
    )


def build_test_analytics(test: TestState, snapshot: ResolverSnapshot) -> NdotsSearchAnalytics:
    query_types: list[str] = test.config.get("query_types", ["A", "AAAA"])
    query_type_count = max(1, len(query_types))
    search_count = len(snapshot.search)
    timeout_s = effective_timeout_seconds(snapshot)
    attempts = effective_attempts(snapshot)
    budget_ms = worst_case_resolve_budget_ms(search_count, attempts, timeout_s, query_type_count)

    per_record: list[RecordDnsProfileResponse] = []
    fqdn_savings: list[float] = []
    search_first_count = 0
    fqdn_faster_count = 0
    estimated_primary = 0

    for rec in test.per_record.values():
        profile = build_record_profile(rec.record, rec, snapshot, query_types)
        per_record.append(profile)
        if profile.search_first_at_configured_ndots:
            search_first_count += 1
        if profile.fqdn_latency_delta_ms > 0:
            fqdn_faster_count += 1
            fqdn_savings.append(profile.fqdn_latency_delta_ms)
        estimated_primary += profile.estimated_queries_per_lookup

    total = test.counters.total
    search_nx = test.noise_counts.get("search_suffix_nxdomain", 0)
    aaaa_count = test.counters.by_query_type.get("AAAA", 0)

    return NdotsSearchAnalytics(
        query_amplification_ratio=round(safe_ratio(total, estimated_primary), 3),
        search_suffix_nxdomain_ratio=round(safe_ratio(search_nx, total), 3),
        avg_fqdn_latency_savings_ms=round(
            safe_ratio(sum(fqdn_savings), len(fqdn_savings)), 3
        ),
        records_search_first_count=search_first_count,
        records_where_fqdn_faster_count=fqdn_faster_count,
        worst_case_resolve_budget_ms=round(budget_ms, 3),
        dual_stack_overhead_ratio=round(safe_ratio(aaaa_count, total), 3),
        configured_ndots=effective_ndots(snapshot),
        configured_timeout_seconds=timeout_s,
        configured_attempts=attempts,
        search_domains_count=search_count,
        per_record=per_record,
    )


def _severity_from_signals(signal_count: int) -> Literal["low", "medium", "high"]:
    if signal_count >= 3:
        return "high"
    if signal_count >= 1:
        return "medium"
    return "low"


def build_diagnosis(
    test: TestState,
    analytics: NdotsSearchAnalytics,
    snapshot: ResolverSnapshot,
    settings: Settings | None = None,
) -> DiagnosisResponse:
    settings = settings or get_settings()
    signals: list[str] = []
    recommendations: list[str] = []

    ndots = effective_ndots(snapshot)
    error_rate = safe_ratio(
        test.counters.error + test.counters.timeout,
        test.counters.total,
    )

    max_fqdn_delta = max(
        (p.fqdn_latency_delta_ms for p in analytics.per_record),
        default=0.0,
    )
    if max_fqdn_delta >= settings.diagnosis_fqdn_latency_delta_ms:
        signals.append(
            f"Trailing-dot FQDN faster than system for {analytics.records_where_fqdn_faster_count} "
            f"record(s); max delta {max_fqdn_delta:.1f} ms"
        )
        recommendations.append("Use trailing-dot FQDN for external hosts")
    elif max_fqdn_delta > 0:
        signals.append(
            f"FQDN faster than system (max delta {max_fqdn_delta:.1f} ms), "
            f"but below threshold {settings.diagnosis_fqdn_latency_delta_ms} ms"
        )

    if analytics.search_suffix_nxdomain_ratio > settings.diagnosis_search_nxdomain_ratio:
        signals.append(
            f"High search_suffix NXDOMAIN ratio: {analytics.search_suffix_nxdomain_ratio:.1%}"
        )
        recommendations.append(
            "Reduce unnecessary search attempts: trailing dot or lower ndots"
        )

    search_timeouts = 0
    fqdn_timeouts = 0
    for rec in test.per_record.values():
        for mode, count in rec.timeouts_by_mode.items():
            if mode == "absolute_fqdn":
                fqdn_timeouts += count
            elif mode == "system" or mode.startswith("ndots:"):
                search_timeouts += count

    if search_timeouts > 0 and fqdn_timeouts == 0:
        signals.append(
            f"Timeouts only in search modes ({search_timeouts}), not in absolute_fqdn"
        )
        recommendations.append(
            "Timeouts linked to search/ndots — use absolute FQDN or lower ndots"
        )

    short_external = [
        p for p in analytics.per_record
        if p.dot_count < ndots and p.search_first_at_configured_ndots
    ]
    if ndots >= 5 and short_external:
        signals.append(
            f"ndots={ndots} and {len(short_external)} record(s) with dot_count < ndots "
            "(likely external names going through search first)"
        )
        recommendations.append(
            f"Lower ndots (currently {ndots}) for workloads dominated by external DNS"
        )

    if (
        error_rate > settings.diagnosis_error_rate_threshold
        and analytics.query_amplification_ratio > settings.diagnosis_amplification_ratio
    ):
        signals.append(
            f"Degradation under load: error_rate={error_rate:.1%}, "
            f"amplification={analytics.query_amplification_ratio:.1f}x"
        )
        recommendations.append(
            "High QPS multiplies extra search queries — reduce amplification or ndots"
        )

    if analytics.dual_stack_overhead_ratio > 0.4:
        signals.append(
            f"High dual-stack overhead (AAAA ratio {analytics.dual_stack_overhead_ratio:.1%})"
        )
        recommendations.append("Review whether AAAA queries are needed for external hosts")

    if analytics.worst_case_resolve_budget_ms > 5000:
        signals.append(
            f"High worst-case resolve budget: {analytics.worst_case_resolve_budget_ms:.0f} ms"
        )
        recommendations.append(
            "Risk of timeout before bare FQDN — reduce search domains, attempts, or timeout in resolv.conf"
        )

    likely = len(signals) >= 2 or (
        analytics.records_where_fqdn_faster_count > 0
        and analytics.search_suffix_nxdomain_ratio > settings.diagnosis_search_nxdomain_ratio
    )

    if likely and not recommendations:
        recommendations.append("Review ndots and search domains in /etc/resolv.conf")

    # dedupe recommendations
    seen: set[str] = set()
    unique_recs: list[str] = []
    for r in recommendations:
        if r not in seen:
            seen.add(r)
            unique_recs.append(r)

    resolver_context: dict[str, Any] = {
        "ndots": snapshot.ndots,
        "timeout_seconds": snapshot.timeout_seconds,
        "attempts": snapshot.attempts,
        "search": snapshot.search,
        "options": snapshot.options,
    }

    return DiagnosisResponse(
        test_id=test.test_id,
        signals=signals,
        severity=_severity_from_signals(len(signals)),
        likely_ndots_search_issue=likely,
        recommendations=unique_recs,
        resolver_context=resolver_context,
        analytics=analytics,
    )
