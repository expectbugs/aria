"""Tests for action_aria.py — persistent Claude Code worker for complex tasks.

SAFETY: All subprocess creation mocked. No real Claude Code instances spawned.
"""

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import action_aria
import task_dispatcher


@pytest.fixture(autouse=True)
def _reset_singleton():
    action_aria._action_aria = None
    yield
    action_aria._action_aria = None


class TestActionAriaExecution:

    @pytest.mark.asyncio
    @patch("action_aria.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("action_aria.redis_client")
    async def test_executes_task_and_returns_result(self, mock_rc, mock_exec):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 55555
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        result_line = json.dumps({
            "type": "result",
            "result": "Image generated at /tmp/sunset.png and pushed to phone"
        }).encode() + b"\n"
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(return_value=result_line)
        mock_exec.return_value = mock_proc

        action = action_aria.ActionAria()
        result = await action.execute("t1", "Generate sunset image at 1080x2424", "warm tones")

        assert result["result"] == "Image generated at /tmp/sunset.png and pushed to phone"
        assert result["error"] is None
        mock_rc.update_task_state.assert_called()

    @pytest.mark.asyncio
    @patch("action_aria.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("action_aria.redis_client")
    async def test_handles_error_result(self, mock_rc, mock_exec):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 55554
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        error_line = json.dumps({
            "type": "result", "is_error": True,
            "result": "generate.py not found"
        }).encode() + b"\n"
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(return_value=error_line)
        mock_exec.return_value = mock_proc

        action = action_aria.ActionAria()
        result = await action.execute("t1", "Generate image")

        assert result["error"] == "generate.py not found"

    @pytest.mark.asyncio
    @patch("action_aria.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("action_aria.redis_client")
    async def test_handles_timeout(self, mock_rc, mock_exec):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 55553
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        async def slow_readline():
            await asyncio.sleep(999)

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = slow_readline
        mock_exec.return_value = mock_proc

        action = action_aria.ActionAria()
        with patch("action_aria.config") as mock_cfg:
            mock_cfg.CLAUDE_TIMEOUT = 0.1
            mock_cfg.CLAUDE_CLI = "/usr/bin/claude"
            result = await action.execute("t1", "Slow task")

        assert "timed out" in result["error"].lower()

    @pytest.mark.asyncio
    @patch("action_aria.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("action_aria.redis_client")
    async def test_kills_process_after_completion(self, mock_rc, mock_exec):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 55552
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        result_line = json.dumps({"type": "result", "result": "done"}).encode() + b"\n"
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(return_value=result_line)
        mock_exec.return_value = mock_proc

        action = action_aria.ActionAria()
        await action.execute("t1", "Quick task")

        mock_proc.kill.assert_called()

    @pytest.mark.asyncio
    @patch("action_aria.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("action_aria.redis_client")
    async def test_injects_task_id_in_prompt(self, mock_rc, mock_exec):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 55551
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        result_line = json.dumps({"type": "result", "result": "ok"}).encode() + b"\n"
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(return_value=result_line)
        mock_exec.return_value = mock_proc

        action = action_aria.ActionAria()
        await action.execute("my_task_42", "Test task")

        # Verify the user message included the task_id
        sent_data = mock_proc.stdin.write.call_args[0][0].decode()
        assert "my_task_42" in sent_data


class TestActionAriaRouting:
    """Test the routing heuristic in task_dispatcher."""

    def test_image_gen_routes_to_action(self):
        assert task_dispatcher._needs_action_aria("Generate an image of a sunset") is True
        assert task_dispatcher._needs_action_aria("upscale this to 4K") is True
        assert task_dispatcher._needs_action_aria("Use FLUX to create art") is True

    def test_simple_tasks_route_to_amnesia(self):
        assert task_dispatcher._needs_action_aria("Check if nginx is running") is False
        assert task_dispatcher._needs_action_aria("What packages are installed?") is False
        assert task_dispatcher._needs_action_aria("Read the contents of /etc/hostname") is False

    def test_file_operations_route_to_action(self):
        assert task_dispatcher._needs_action_aria("Create file at /tmp/test.txt") is True
        assert task_dispatcher._needs_action_aria("Modify file permissions") is True


class TestActionAriaSingleton:

    def test_returns_singleton(self):
        a1 = action_aria.get_action_aria()
        a2 = action_aria.get_action_aria()
        assert a1 is a2
