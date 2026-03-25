"""Tests for amnesia_pool.py — warm stateless Claude Code pool.

SAFETY: All subprocess creation mocked. No real Claude Code instances spawned.
"""

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import amnesia_pool


@pytest.fixture(autouse=True)
def _reset_pool():
    """Reset pool singleton."""
    amnesia_pool._pool = None
    yield
    amnesia_pool._pool = None


class TestAmnesiaPoolLifecycle:

    @pytest.mark.asyncio
    @patch("amnesia_pool.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_start_spawns_instances(self, mock_exec):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        mock_exec.return_value = mock_proc

        pool = amnesia_pool.AmnesiaPool(size=2)
        await pool.start()
        # Give spawn tasks a moment to complete
        await asyncio.sleep(0.1)

        assert pool._started is True
        await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_state(self):
        pool = amnesia_pool.AmnesiaPool(size=2)
        pool._started = True
        pool._states = ["idle", "idle"]
        await pool.stop()
        assert pool._started is False

    def test_find_idle(self):
        pool = amnesia_pool.AmnesiaPool(size=3)
        pool._states = ["busy", "idle", "busy"]
        assert pool._find_idle() == 1

    def test_find_idle_none_available(self):
        pool = amnesia_pool.AmnesiaPool(size=2)
        pool._states = ["busy", "busy"]
        assert pool._find_idle() is None


class TestAmnesiaAgenticMode:

    @pytest.mark.asyncio
    async def test_returns_error_when_all_busy(self):
        pool = amnesia_pool.AmnesiaPool(size=1)
        pool._states = ["busy"]

        result = await pool.run_agentic("t1", "test task")
        assert result["error"] is not None
        assert "busy" in result["error"]

    @pytest.mark.asyncio
    @patch("amnesia_pool.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_sends_task_and_returns_result(self, mock_exec):
        """Mock a Claude Code process that returns a result."""
        # Create a mock process with proper stdin/stdout
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 99999
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        # Mock stdout to return a result line
        result_line = json.dumps({"type": "result", "result": "Task completed successfully"}).encode() + b"\n"
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(return_value=result_line)

        mock_exec.return_value = mock_proc

        pool = amnesia_pool.AmnesiaPool(size=1)
        pool._instances = [mock_proc]
        pool._states = ["idle"]
        pool._started = True

        result = await pool.run_agentic("t1", "Check service status")
        assert result["result"] == "Task completed successfully"
        assert result["error"] is None

    @pytest.mark.asyncio
    @patch("amnesia_pool.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_handles_error_result(self, mock_exec):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 99998
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        error_line = json.dumps({"type": "result", "is_error": True, "result": "command not found"}).encode() + b"\n"
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(return_value=error_line)
        mock_exec.return_value = mock_proc

        pool = amnesia_pool.AmnesiaPool(size=1)
        pool._instances = [mock_proc]
        pool._states = ["idle"]
        pool._started = True

        result = await pool.run_agentic("t1", "bad command")
        assert result["error"] == "command not found"

    @pytest.mark.asyncio
    @patch("amnesia_pool.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_handles_timeout(self, mock_exec):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 99997
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        # readline that never returns (simulates hang)
        async def slow_readline():
            await asyncio.sleep(999)

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = slow_readline
        mock_exec.return_value = mock_proc

        pool = amnesia_pool.AmnesiaPool(size=1)
        pool._instances = [mock_proc]
        pool._states = ["idle"]
        pool._started = True

        # Override timeout to be very short
        with patch("amnesia_pool.config") as mock_cfg:
            mock_cfg.AMNESIA_TASK_TIMEOUT = 0.1
            mock_cfg.CLAUDE_CLI = "/usr/bin/claude"
            result = await pool.run_agentic("t1", "hanging task")

        assert result["error"] is not None
        assert "timed out" in result["error"].lower()

    @pytest.mark.asyncio
    @patch("amnesia_pool.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_kills_and_respawns_after_use(self, mock_exec):
        """Instance should be killed and replaced after handling a task."""
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 99996
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        result_line = json.dumps({"type": "result", "result": "done"}).encode() + b"\n"
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(return_value=result_line)
        mock_exec.return_value = mock_proc

        pool = amnesia_pool.AmnesiaPool(size=1)
        pool._instances = [mock_proc]
        pool._states = ["idle"]
        pool._started = True

        await pool.run_agentic("t1", "quick task")

        # Instance should have been killed
        mock_proc.kill.assert_called_once()


class TestAmnesiaPoolSingleton:

    def test_get_pool_returns_singleton(self):
        p1 = amnesia_pool.get_pool()
        p2 = amnesia_pool.get_pool()
        assert p1 is p2

    def test_get_pool_uses_config_size(self):
        with patch("amnesia_pool.config") as mock_cfg:
            mock_cfg.AMNESIA_POOL_SIZE = 5
            amnesia_pool._pool = None
            pool = amnesia_pool.get_pool()
            assert pool._size == 5


class TestControlRequestHandling:

    @pytest.mark.asyncio
    @patch("amnesia_pool.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_auto_approves_control_requests(self, mock_exec):
        """Should auto-approve permission requests mid-task."""
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 99995
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        # First line: control request, second line: result
        lines = [
            json.dumps({"type": "control_request", "request_id": "r1"}).encode() + b"\n",
            json.dumps({"type": "result", "result": "approved and done"}).encode() + b"\n",
        ]
        call_count = 0

        async def readline_side_effect():
            nonlocal call_count
            if call_count < len(lines):
                line = lines[call_count]
                call_count += 1
                return line
            return b""

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = readline_side_effect
        mock_exec.return_value = mock_proc

        pool = amnesia_pool.AmnesiaPool(size=1)
        pool._instances = [mock_proc]
        pool._states = ["idle"]
        pool._started = True

        result = await pool.run_agentic("t1", "needs permission")
        assert result["result"] == "approved and done"
        # Verify control response was sent
        assert mock_proc.stdin.write.call_count >= 2  # task + control response
