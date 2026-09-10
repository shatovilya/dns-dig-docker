"""Tests for dns_runner module covering resolve modes, noise classification, and cancellation."""
import asyncio
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import dns.asyncresolver
import dns.exception
import dns.resolver
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config import get_settings
from dns_runner import (
    DnsTestRunner,
    build_autonomous_config,
    build_test_config_from_settings,
    cancel_test,
    expand_work_items,
    start_test_background,
)
from models import NoiseType, QueryOutcome, ResolveSpec, TestStatus
from stats_store import get_stats_store


def _reset_settings() -> None:
    get_settings.cache_clear()


@pytest.fixture
def test_settings(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_MODE", "false")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    _reset_settings()
    return get_settings()


@pytest.fixture
def stats_store():
    """Provide a fresh stats store for each test."""
    from stats_store import StatsStore

    store = StatsStore(event_buffer_size=100)
    return store


@pytest.fixture
def mock_resolver_snapshot(monkeypatch):
    """Mock resolver snapshot with search domains."""

    class MockSnapshot:
        nameservers = ["127.0.0.11"]
        search = ["cluster.local", "svc.cluster.local"]
        options = ["ndots:5"]
        ndots = 5
        timeout_seconds = 5.0
        attempts = 2
        raw = "nameserver 127.0.0.11\nsearch cluster.local svc.cluster.local\noptions ndots:5"
        captured_at = datetime.now(timezone.utc)

    monkeypatch.setattr("dns_runner.get_snapshot", lambda: MockSnapshot())
    return MockSnapshot()


class TestExpandWorkItems:
    """Test work item expansion logic."""

    def test_expand_basic_system_mode(self):
        items = expand_work_items(
            records=["example.com"],
            query_types=["A"],
            resolve_modes=["system"],
            ndots_values=[],
        )
        assert len(items) == 1
        record, qt, spec = items[0]
        assert record == "example.com"
        assert qt == "A"
        assert spec.kind == "system"

    def test_expand_multiple_resolve_modes(self):
        items = expand_work_items(
            records=["example.com"],
            query_types=["A"],
            resolve_modes=["system", "absolute_fqdn"],
            ndots_values=[],
        )
        assert len(items) == 2
        modes = [spec.kind for _, _, spec in items]
        assert "system" in modes
        assert "absolute_fqdn" in modes

    def test_expand_with_ndots_override(self):
        items = expand_work_items(
            records=["example.com"],
            query_types=["A"],
            resolve_modes=["system"],
            ndots_values=[3, 5],
        )
        assert len(items) == 3
        labels = [spec.label for _, _, spec in items]
        assert "system" in labels
        assert "ndots:3" in labels
        assert "ndots:5" in labels

    def test_expand_multiple_records_and_query_types(self):
        items = expand_work_items(
            records=["example.com", "test.local"],
            query_types=["A", "AAAA"],
            resolve_modes=["system"],
            ndots_values=[],
        )
        assert len(items) == 4
        records = [r for r, _, _ in items]
        assert records.count("example.com") == 2
        assert records.count("test.local") == 2


class TestBuildTestConfig:
    """Test configuration builders."""

    def test_build_test_config_from_settings(self, test_settings):
        config = build_test_config_from_settings(test_settings, autonomous=False)
        assert config["test_name"] == "dns-debug-test"
        assert config["records"] == test_settings.default_records
        assert config["rps"] == test_settings.default_rps
        assert config["continuous"] is False

    def test_build_autonomous_config(self, test_settings, monkeypatch):
        monkeypatch.setenv("AUTONOMOUS_RECORDS", '["test.example.com", "prod.example.com"]')
        monkeypatch.setenv("AUTONOMOUS_RPS", "20")
        _reset_settings()
        settings = get_settings()

        config = build_autonomous_config(settings)
        assert config["test_name"] == "autonomous"
        assert config["continuous"] is True
        assert config["duration_seconds"] == 0
        assert config["rps"] == 20


class TestDnsTestRunner:
    """Test DnsTestRunner core functionality."""

    def test_effective_name_absolute_fqdn(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="absolute_fqdn")
        assert runner._effective_name("example.com", spec) == "example.com."

    def test_effective_name_system_mode(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")
        assert runner._effective_name("example.com", spec) == "example.com"

    def test_check_duplicate_window(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        # First query should not be duplicate
        assert runner._check_duplicate("example.com", "A", spec) is False

        # Immediate second query should be duplicate
        assert runner._check_duplicate("example.com", "A", spec) is True

        # Different record should not be duplicate
        assert runner._check_duplicate("other.com", "A", spec) is False

    def test_check_cache_detection(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        # First query establishes baseline
        delta = runner._check_cache("example.com", "A", spec, 10.0)
        assert delta is None

        # Second query much faster triggers cache detection
        delta = runner._check_cache("example.com", "A", spec, 2.0)
        assert delta is not None
        assert delta > 0

        # Second query not fast enough should not trigger
        delta = runner._check_cache("other.com", "A", spec, 10.0)
        assert delta is None
        delta = runner._check_cache("other.com", "A", spec, 9.0)
        assert delta is None

    def test_classify_noise_search_probe_nxdomain(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        is_noisy, noise_type = runner._classify_noise(
            record="example",
            query_type="A",
            spec=spec,
            outcome=QueryOutcome.NXDOMAIN,
            answers_count=0,
            is_search_probe=True,
            probe_nxdomain=True,
        )
        assert is_noisy is True
        assert noise_type == NoiseType.SEARCH_SUFFIX_NXDOMAIN

    def test_classify_noise_search_probe_query(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        is_noisy, noise_type = runner._classify_noise(
            record="example",
            query_type="A",
            spec=spec,
            outcome=QueryOutcome.SUCCESS,
            answers_count=1,
            is_search_probe=True,
            probe_nxdomain=False,
        )
        assert is_noisy is True
        assert noise_type == NoiseType.SEARCH_SUFFIX_QUERY

    def test_classify_noise_duplicate_query(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        # First query not noisy
        is_noisy, noise_type = runner._classify_noise(
            record="example.com",
            query_type="A",
            spec=spec,
            outcome=QueryOutcome.SUCCESS,
            answers_count=1,
            is_search_probe=False,
            probe_nxdomain=False,
        )
        assert is_noisy is False

        # Second query is duplicate
        is_noisy, noise_type = runner._classify_noise(
            record="example.com",
            query_type="A",
            spec=spec,
            outcome=QueryOutcome.SUCCESS,
            answers_count=1,
            is_search_probe=False,
            probe_nxdomain=False,
        )
        assert is_noisy is True
        assert noise_type == NoiseType.DUPLICATE_QUERY

    def test_classify_noise_empty_answer(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        is_noisy, noise_type = runner._classify_noise(
            record="example.com",
            query_type="A",
            spec=spec,
            outcome=QueryOutcome.SUCCESS,
            answers_count=0,
            is_search_probe=False,
            probe_nxdomain=False,
        )
        assert is_noisy is True
        assert noise_type == NoiseType.EMPTY_ANSWER

    def test_classify_noise_aaaa_after_a_success(self, test_settings, stats_store):
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        # Mark A record as successful
        runner._a_success_records.add("example.com")

        # AAAA query for same record should be noisy
        is_noisy, noise_type = runner._classify_noise(
            record="example.com",
            query_type="AAAA",
            spec=spec,
            outcome=QueryOutcome.SUCCESS,
            answers_count=1,
            is_search_probe=False,
            probe_nxdomain=False,
        )
        assert is_noisy is True
        assert noise_type == NoiseType.AAAA_NOISE

    @pytest.mark.asyncio
    async def test_resolve_success(self, test_settings, stats_store, mock_resolver_snapshot):
        """Test successful DNS resolution."""
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        # Mock successful DNS response
        mock_rrset = MagicMock()
        mock_rrset.ttl = 300
        mock_rrset.__len__ = lambda self: 1
        mock_answer = MagicMock()
        mock_answer.rrset = mock_rrset

        with patch("dns.asyncresolver.Resolver") as MockResolver:
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.resolve = AsyncMock(return_value=mock_answer)
            MockResolver.return_value = mock_resolver_instance

            attempt = await runner._resolve("example.com", "A", spec, timeout=2.0)

        assert attempt.record == "example.com"
        assert attempt.query_type == "A"
        assert attempt.outcome == QueryOutcome.SUCCESS
        assert attempt.answers_count == 1
        assert attempt.ttl_min == 300
        assert attempt.latency_ms > 0

    @pytest.mark.asyncio
    async def test_resolve_nxdomain(self, test_settings, stats_store, mock_resolver_snapshot):
        """Test NXDOMAIN response."""
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["nonexistent.example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        with patch("dns.asyncresolver.Resolver") as MockResolver:
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.resolve = AsyncMock(side_effect=dns.resolver.NXDOMAIN())
            MockResolver.return_value = mock_resolver_instance

            attempt = await runner._resolve("nonexistent.example.com", "A", spec, timeout=2.0)

        assert attempt.outcome == QueryOutcome.NXDOMAIN
        assert attempt.answers_count == 0

    @pytest.mark.asyncio
    async def test_resolve_timeout(self, test_settings, stats_store, mock_resolver_snapshot):
        """Test DNS timeout."""
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        with patch("dns.asyncresolver.Resolver") as MockResolver:
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.resolve = AsyncMock(side_effect=dns.exception.Timeout())
            MockResolver.return_value = mock_resolver_instance

            attempt = await runner._resolve("example.com", "A", spec, timeout=0.1)

        assert attempt.outcome == QueryOutcome.TIMEOUT
        assert attempt.error_message == "timeout"

    @pytest.mark.asyncio
    async def test_resolve_error(self, test_settings, stats_store, mock_resolver_snapshot):
        """Test DNS error."""
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="system")

        with patch("dns.asyncresolver.Resolver") as MockResolver:
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.resolve = AsyncMock(side_effect=Exception("Network error"))
            MockResolver.return_value = mock_resolver_instance

            attempt = await runner._resolve("example.com", "A", spec, timeout=2.0)

        assert attempt.outcome == QueryOutcome.ERROR
        assert "Network error" in attempt.error_message

    @pytest.mark.asyncio
    async def test_resolve_absolute_fqdn_mode(self, test_settings, stats_store, mock_resolver_snapshot):
        """Test absolute FQDN resolution mode adds trailing dot."""
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec(kind="absolute_fqdn")

        mock_rrset = MagicMock()
        mock_rrset.ttl = 300
        mock_rrset.__len__ = lambda self: 1
        mock_answer = MagicMock()
        mock_answer.rrset = mock_rrset

        with patch("dns.asyncresolver.Resolver") as MockResolver:
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.resolve = AsyncMock(return_value=mock_answer)
            MockResolver.return_value = mock_resolver_instance

            attempt = await runner._resolve("example.com", "A", spec, timeout=2.0)

        assert attempt.effective_name == "example.com."
        assert attempt.outcome == QueryOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_resolve_ndots_override(self, test_settings, stats_store, mock_resolver_snapshot):
        """Test ndots override sets resolver ndots value."""
        runner = DnsTestRunner(
            test_id="test1",
            config={"records": ["example.com"], "cache_latency_threshold_ms": 5.0, "cache_latency_ratio": 0.5},
        )
        spec = ResolveSpec.ndots_override(3)

        mock_rrset = MagicMock()
        mock_rrset.ttl = 300
        mock_rrset.__len__ = lambda self: 1
        mock_answer = MagicMock()
        mock_answer.rrset = mock_rrset

        with patch("dns.asyncresolver.Resolver") as MockResolver:
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.resolve = AsyncMock(return_value=mock_answer)
            MockResolver.return_value = mock_resolver_instance

            attempt = await runner._resolve("example.com", "A", spec, timeout=2.0)

        # Verify ndots was set on resolver
        assert mock_resolver_instance.ndots == 3
        assert attempt.resolve_mode == "ndots:3"


class TestCancellation:
    """Test cancellation and stopping behavior."""

    @pytest.mark.asyncio
    async def test_cancel_test_sets_event(self, test_settings, stats_store):
        """Test that cancel_test sets the cancel event."""
        from dns_runner import register_task

        test_id = "cancel-test"
        cancel_event = asyncio.Event()
        task = asyncio.create_task(asyncio.sleep(10))
        register_task(test_id, task, cancel_event)

        assert not cancel_event.is_set()
        result = await cancel_test(test_id)
        assert result is True
        assert cancel_event.is_set()

        # Cleanup
        try:
            task.cancel()
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_test(self, test_settings, stats_store):
        """Test cancelling a test that doesn't exist."""
        result = await cancel_test("nonexistent-test-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_runner_respects_cancel_event(self, test_settings):
        """Test that runner stops when cancel event is set."""

        store = get_stats_store()
        await store.create_test("cancel-early-test", "test", {})

        config = {
            "records": ["example.com"],
            "query_types": ["A"],
            "resolve_modes": ["system"],
            "ndots_values": [],
            "rps": 1.0,
            "concurrency": 1,
            "duration_seconds": 60,
            "timeout_seconds": 2.0,
            "continuous": False,
            "cache_latency_threshold_ms": 5.0,
            "cache_latency_ratio": 0.5,
        }

        runner = DnsTestRunner(test_id="cancel-early-test", config=config)
        runner.store = store

        # Set cancel event before run
        runner.cancel_event.set()

        with patch("dns.asyncresolver.Resolver") as MockResolver:
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.resolve = AsyncMock(return_value=MagicMock())
            MockResolver.return_value = mock_resolver_instance

            await runner.run()

        # Test should be cancelled
        test = await store.get_test("cancel-early-test")
        assert test.status == TestStatus.CANCELLED


class TestBackgroundExecution:
    """Test background task management."""

    @pytest.mark.asyncio
    async def test_start_test_background(self, test_settings):
        """Test starting a test in background."""

        store = get_stats_store()
        test_id = "background-test"
        await store.create_test(test_id, "test", {})

        config = {
            "records": ["example.com"],
            "query_types": ["A"],
            "resolve_modes": ["system"],
            "ndots_values": [],
            "rps": 10.0,
            "concurrency": 1,
            "duration_seconds": 1,
            "timeout_seconds": 2.0,
            "continuous": False,
            "cache_latency_threshold_ms": 5.0,
            "cache_latency_ratio": 0.5,
        }

        mock_rrset = MagicMock()
        mock_rrset.ttl = 300
        mock_rrset.__len__ = lambda self: 1
        mock_answer = MagicMock()
        mock_answer.rrset = mock_rrset

        with patch("dns.asyncresolver.Resolver") as MockResolver:
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.resolve = AsyncMock(return_value=mock_answer)
            MockResolver.return_value = mock_resolver_instance

            task = start_test_background(test_id, config)
            assert task is not None

            # Wait for test to complete
            await asyncio.wait_for(task, timeout=5.0)

        test = await store.get_test(test_id)
        assert test.status in (TestStatus.COMPLETED, TestStatus.RUNNING)
        assert test.counters.total > 0
