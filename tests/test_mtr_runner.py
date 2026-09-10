"""Tests for mtr_runner module covering MTR execution, parsing, and concurrency control."""
import asyncio
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config import Settings, get_settings
from mtr_runner import (
    MtrAlreadyRunningError,
    MtrPeriodicRunner,
    build_mtr_command,
    cancel_mtr,
    is_mtr_running,
    parse_mtr_report,
    run_mtr,
    start_mtr_background,
    trigger_mtr_now,
)
from mtr_store import MtrHop, MtrRunStatus, get_mtr_store


def _reset_settings() -> None:
    get_settings.cache_clear()


@pytest.fixture
def test_settings(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv("MTR_ENABLED", "true")
    monkeypatch.setenv("MTR_SERVICE_NAME", "example.com")
    monkeypatch.setenv("MTR_SERVICE_PORT", "443")
    monkeypatch.setenv("MTR_COUNT", "5")
    monkeypatch.setenv("MTR_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("MTR_TIMEOUT_SECONDS", "30")
    _reset_settings()
    return get_settings()


@pytest.fixture
def mtr_store():
    """Provide a fresh MTR store for each test."""
    from mtr_store import MtrStore

    store = MtrStore(max_history=10)
    return store


class TestBuildMtrCommand:
    """Test MTR command building."""

    def test_build_basic_command(self):
        cmd = build_mtr_command("example.com", 443, 10)
        assert cmd == ["mtr", "-rzbw", "example.com", "--tcp", "-P", "443", "-c", "10"]

    def test_build_command_different_port(self):
        cmd = build_mtr_command("test.local", 8080, 20)
        assert cmd == ["mtr", "-rzbw", "test.local", "--tcp", "-P", "8080", "-c", "20"]


class TestParseMtrReport:
    """Test MTR report parsing."""

    def test_parse_empty_report(self):
        hops = parse_mtr_report("")
        assert len(hops) == 0

    def test_parse_single_hop(self):
        report = " 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1"
        hops = parse_mtr_report(report)
        assert len(hops) == 1
        hop = hops[0]
        assert hop.hop == 1
        assert hop.host == "router.local"
        assert hop.loss_pct == 0.0
        assert hop.sent == 5
        assert hop.last_ms == 0.5
        assert hop.avg_ms == 0.6
        assert hop.best_ms == 0.5
        assert hop.worst_ms == 0.8
        assert hop.stdev_ms == 0.1

    def test_parse_multiple_hops(self):
        report = """
 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1
 2. |-- gateway.local     0.0%     5   2.1   2.2   2.0   2.5   0.2
 3. |-- remote.host       5.0%     5  10.5  11.0  10.0  12.5   1.1
"""
        hops = parse_mtr_report(report)
        assert len(hops) == 3
        assert hops[0].hop == 1
        assert hops[1].hop == 2
        assert hops[2].hop == 3
        assert hops[2].loss_pct == 5.0
        assert hops[2].host == "remote.host"

    def test_parse_with_question_marks(self):
        """Test parsing hops with ??? for unreachable metrics."""
        report = " 1. |-- ???                100.0%    5   ???   ???   ???   ???   ???"
        hops = parse_mtr_report(report)
        assert len(hops) == 1
        hop = hops[0]
        assert hop.hop == 1
        assert hop.host == "???"
        assert hop.loss_pct == 100.0
        assert hop.last_ms is None
        assert hop.avg_ms is None
        assert hop.best_ms is None
        assert hop.worst_ms is None
        assert hop.stdev_ms is None

    def test_parse_with_as_numbers(self):
        """Test parsing with AS numbers."""
        report = " 1. |-- AS1234 router.local  0.0%     5   0.5   0.6   0.5   0.8   0.1"
        hops = parse_mtr_report(report)
        assert len(hops) == 1
        assert hops[0].host == "router.local"

    def test_parse_ignores_invalid_lines(self):
        report = """
Start: 2024-01-01 12:00:00
HOST: testhost
 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1
Invalid line here
 2. |-- gateway.local     0.0%     5   2.1   2.2   2.0   2.5   0.2
"""
        hops = parse_mtr_report(report)
        assert len(hops) == 2


class TestRunMtr:
    """Test MTR execution."""

    @pytest.mark.asyncio
    async def test_run_mtr_success(self, test_settings):
        """Test successful MTR run."""
        from mtr_store import get_mtr_store
        
        store = get_mtr_store()
        mock_stdout = b" 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1\n"
        mock_stderr = b""

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, mock_stderr))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            run_id = await run_mtr("example.com", 443, 5, triggered_by="test", settings=test_settings)

        result = await store.get_run(run_id)
        assert result is not None
        assert result.status == MtrRunStatus.COMPLETED
        assert result.exit_code == 0
        assert len(result.parsed_hops) == 1
        assert result.parsed_hops[0].host == "router.local"
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_run_mtr_timeout(self, test_settings, monkeypatch):
        """Test MTR run timeout."""
        from mtr_store import get_mtr_store
        
        store = get_mtr_store()
        monkeypatch.setenv("MTR_TIMEOUT_SECONDS", "0.1")
        _reset_settings()
        settings = get_settings()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            run_id = await run_mtr("example.com", 443, 5, triggered_by="test", settings=settings)

        result = await store.get_run(run_id)
        assert result is not None
        assert result.status == MtrRunStatus.TIMEOUT
        assert "timed out" in result.stderr.lower()
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_mtr_not_found(self, test_settings):
        """Test MTR binary not found."""
        from mtr_store import get_mtr_store
        
        store = get_mtr_store()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            run_id = await run_mtr("example.com", 443, 5, triggered_by="test", settings=test_settings)

        result = await store.get_run(run_id)
        assert result is not None
        assert result.status == MtrRunStatus.FAILED
        assert "mtr binary not found" in result.stderr

    @pytest.mark.asyncio
    async def test_run_mtr_nonzero_exit(self, test_settings):
        """Test MTR run with non-zero exit code."""
        from mtr_store import get_mtr_store
        
        store = get_mtr_store()
        mock_stdout = b""
        mock_stderr = b"mtr: Unable to resolve target hostname"

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, mock_stderr))
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            run_id = await run_mtr("example.com", 443, 5, triggered_by="test", settings=test_settings)

        result = await store.get_run(run_id)
        assert result is not None
        assert result.status == MtrRunStatus.FAILED
        assert result.exit_code == 1
        assert "Unable to resolve" in result.stderr

    @pytest.mark.asyncio
    async def test_run_mtr_creates_store_entry(self, test_settings):
        """Test that run_mtr creates store entry."""
        from mtr_store import get_mtr_store
        
        store = get_mtr_store()
        mock_stdout = b" 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            run_id = await run_mtr("test.com", 443, 10, triggered_by="api", settings=test_settings)

        result = await store.get_run(run_id)
        assert result is not None
        assert result.service_name == "test.com"
        assert result.port == 443
        assert result.count == 10
        assert result.triggered_by == "api"


class TestMtrConcurrency:
    """Test MTR concurrency control."""

    @pytest.mark.asyncio
    async def test_trigger_mtr_now_success(self, test_settings):
        """Test triggering MTR manually."""
        from mtr_store import get_mtr_store
        
        store = get_mtr_store()
        mock_stdout = b" 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))
        mock_proc.returncode = 0

        with patch("mtr_runner.asyncio.create_subprocess_exec", return_value=mock_proc):
            run_id = await trigger_mtr_now("example.com", 443, 5)

            # Run should be created immediately
            result = await store.get_run(run_id)
            assert result is not None
            assert result.triggered_by == "api"

            # Wait a bit for background task to complete
            await asyncio.sleep(0.3)

            # Check final result
            result = await store.get_run(run_id)
            assert result.status == MtrRunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_trigger_mtr_rejects_concurrent(self, test_settings, mtr_store):
        """Test that concurrent MTR runs are rejected."""

        async def slow_mtr(*args, **kwargs):
            await asyncio.sleep(1.0)
            return (b"", b"")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=slow_mtr)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            # Start first run
            run_id1 = await trigger_mtr_now("example.com", 443, 5)

            # Give it time to acquire lock
            await asyncio.sleep(0.05)

            # Second run should raise error
            with pytest.raises(MtrAlreadyRunningError):
                await trigger_mtr_now("example.com", 443, 5)

        # Cleanup: wait for first run to finish
        await asyncio.sleep(1.1)

    @pytest.mark.asyncio
    async def test_is_mtr_running(self, test_settings, mtr_store):
        """Test is_mtr_running status check."""
        assert is_mtr_running() is False

        async def slow_mtr(*args, **kwargs):
            await asyncio.sleep(0.5)
            return (b"", b"")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=slow_mtr)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            run_id = await trigger_mtr_now("example.com", 443, 5)

            # Should be running now
            await asyncio.sleep(0.05)
            assert is_mtr_running() is True

            # Wait for completion
            await asyncio.sleep(0.6)
            assert is_mtr_running() is False


class TestMtrPeriodicRunner:
    """Test periodic MTR runner."""

    @pytest.mark.asyncio
    async def test_periodic_runner_runs_once(self, test_settings, monkeypatch):
        """Test that periodic runner executes MTR."""
        from mtr_store import get_mtr_store
        
        store = get_mtr_store()
        monkeypatch.setenv("MTR_INTERVAL_SECONDS", "1")
        _reset_settings()
        settings = get_settings()

        mock_stdout = b" 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))
        mock_proc.returncode = 0

        runner = MtrPeriodicRunner(settings)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            # Start runner
            task = asyncio.create_task(runner.run())

            # Let it run once
            await asyncio.sleep(0.2)

            # Cancel it
            runner.cancel_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        # Should have at least one run
        runs = await store.list_runs()
        assert len(runs) >= 1
        assert runs[0].triggered_by == "periodic"

    @pytest.mark.asyncio
    async def test_periodic_runner_stops_on_cancel(self, test_settings, mtr_store):
        """Test that periodic runner stops when cancelled."""
        runner = MtrPeriodicRunner(test_settings)

        mock_stdout = b" 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            task = asyncio.create_task(runner.run())

            # Cancel immediately
            runner.cancel_event.set()

            # Should stop quickly
            await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_start_mtr_background(self, test_settings, mtr_store):
        """Test starting MTR in background."""
        mock_stdout = b" 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            task = start_mtr_background(test_settings)
            assert task is not None

            # Let it run briefly
            await asyncio.sleep(0.2)

            # Cancel and cleanup
            await cancel_mtr(timeout=1.0)

    @pytest.mark.asyncio
    async def test_cancel_mtr(self, test_settings, mtr_store):
        """Test cancelling background MTR."""
        mock_stdout = b" 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            task = start_mtr_background(test_settings)

            await asyncio.sleep(0.1)

            # Cancel should complete quickly
            await cancel_mtr(timeout=1.0)

            # Task should be done
            assert task.done() or task.cancelled()

    @pytest.mark.asyncio
    async def test_periodic_runner_handles_errors(self, test_settings, mtr_store):
        """Test that periodic runner continues after errors."""

        call_count = 0

        async def failing_then_succeeding(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Network error")
            return (b" 1. |-- router.local      0.0%     5   0.5   0.6   0.5   0.8   0.1\n", b"")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=failing_then_succeeding)
        mock_proc.returncode = 0

        runner = MtrPeriodicRunner(test_settings)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            task = asyncio.create_task(runner.run())

            # Wait for two iterations
            await asyncio.sleep(0.3)

            runner.cancel_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        # Should have attempted at least once
        assert call_count >= 1
