"""Tests for ClaudeSession — stream-json protocol, recycling, error recovery.

SAFETY: No real Claude CLI process is spawned. asyncio.create_subprocess_exec
is mocked to return a fake process with controllable stdin/stdout.
"""

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import claude_session


class MockStreamWriter:
    """Captures writes to simulate subprocess stdin."""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        pass

    def get_messages(self):
        """Parse all written NDJSON messages."""
        msgs = []
        for chunk in self.written:
            for line in chunk.decode().strip().split("\n"):
                if line.strip():
                    msgs.append(json.loads(line))
        return msgs


class MockStreamReader:
    """Provides readline() from a queue to simulate subprocess stdout."""

    def __init__(self):
        self._queue = asyncio.Queue()

    def push_line(self, data: dict):
        """Add a JSON line to the queue."""
        self._queue.put_nowait((json.dumps(data) + "\n").encode())

    def push_eof(self):
        self._queue.put_nowait(b"")

    async def readline(self):
        return await self._queue.get()


def make_mock_process():
    """Create a mock subprocess with controllable I/O."""
    proc = MagicMock()
    proc.stdin = MockStreamWriter()
    proc.stdout = MockStreamReader()
    proc.stderr = MagicMock()
    proc.returncode = None
    proc.pid = 99999
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


class TestClaudeSessionSpawn:
    @pytest.mark.asyncio
    async def test_spawn_args(self):
        session = claude_session.ClaudeSession()
        mock_proc = make_mock_process()

        with patch("claude_session.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await session._spawn()

            args = mock_exec.call_args[0]
            assert "--dangerously-skip-permissions" in args
            assert "--output-format" in args
            assert "stream-json" in args
            assert "--input-format" in args
            assert "--model" in args
            assert "opus" in args
            assert "--system-prompt" in args

    @pytest.mark.asyncio
    async def test_spawn_strips_claudecode_env(self):
        session = claude_session.ClaudeSession()
        mock_proc = make_mock_process()

        with patch("claude_session.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc) as mock_exec, \
             patch.dict("os.environ", {"CLAUDECODE": "true", "PATH": "/usr/bin"}):
            await session._spawn()
            env = mock_exec.call_args[1]["env"]
            assert "CLAUDECODE" not in env
            assert "PATH" in env


class TestClaudeSessionQuery:
    @pytest.mark.asyncio
    async def test_successful_query(self):
        session = claude_session.ClaudeSession()
        proc = make_mock_process()
        session._proc = proc
        session._request_count = 0

        # Queue up the response
        proc.stdout.push_line({"type": "assistant", "message": "thinking..."})
        proc.stdout.push_line({"type": "result", "result": "Hello from ARIA!"})

        result = await session.query("Hi there")
        assert result == "Hello from ARIA!"
        assert session._request_count == 1

        # Verify message was sent correctly
        msgs = proc.stdin.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["type"] == "user"
        assert "Hi there" in str(msgs[0]["message"]["content"])

    @pytest.mark.asyncio
    async def test_control_request_auto_approval(self):
        session = claude_session.ClaudeSession()
        proc = make_mock_process()
        session._proc = proc
        session._request_count = 0

        # Simulate: assistant message, control_request, then result
        proc.stdout.push_line({"type": "assistant", "message": "running..."})
        proc.stdout.push_line({
            "type": "control_request",
            "request_id": "req_123",
        })
        proc.stdout.push_line({"type": "result", "result": "Done!"})

        result = await session.query("Do something")
        assert result == "Done!"

        # Verify approval was sent back
        msgs = proc.stdin.get_messages()
        approvals = [m for m in msgs if m.get("type") == "control_response"]
        assert len(approvals) == 1
        assert approvals[0]["response"]["subtype"] == "success"

    @pytest.mark.asyncio
    async def test_error_result_raises(self):
        session = claude_session.ClaudeSession()
        proc = make_mock_process()
        session._proc = proc
        session._request_count = 0

        proc.stdout.push_line({
            "type": "result", "is_error": True,
            "result": "Context window exceeded",
        })

        with pytest.raises(RuntimeError, match="Claude error"):
            await session.query("test")

    @pytest.mark.asyncio
    async def test_process_death_raises(self):
        session = claude_session.ClaudeSession()
        proc = make_mock_process()
        session._proc = proc
        session._request_count = 0

        proc.stdout.push_eof()  # Process died

        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            await session.query("test")

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        session = claude_session.ClaudeSession()
        proc = make_mock_process()
        session._proc = proc
        session._request_count = 0

        # Don't push any response — will timeout
        with patch("claude_session.config.CLAUDE_TIMEOUT", 0.1):
            with pytest.raises(RuntimeError, match="timed out"):
                await session.query("test")

        # Process should be killed
        assert session._proc is None

    @pytest.mark.asyncio
    async def test_extra_context_included(self):
        session = claude_session.ClaudeSession()
        proc = make_mock_process()
        session._proc = proc
        session._request_count = 0

        proc.stdout.push_line({"type": "result", "result": "ok"})

        await session.query("test", extra_context="Weather: Sunny 55F")
        msgs = proc.stdin.get_messages()
        content = str(msgs[0]["message"]["content"])
        assert "Weather: Sunny 55F" in content
        assert "[CONTEXT]" in content

    @pytest.mark.asyncio
    async def test_file_blocks_multimodal(self):
        session = claude_session.ClaudeSession()
        proc = make_mock_process()
        session._proc = proc
        session._request_count = 0

        proc.stdout.push_line({"type": "result", "result": "I see an image"})

        blocks = [{"type": "image", "source": {"type": "base64",
                   "media_type": "image/jpeg", "data": "base64data"}}]
        await session.query("What is this?", file_blocks=blocks)

        msgs = proc.stdin.get_messages()
        content = msgs[0]["message"]["content"]
        assert isinstance(content, list)  # multimodal = list of blocks
        assert any(b.get("type") == "image" for b in content)


class TestSessionRecycling:
    @pytest.mark.asyncio
    async def test_recycles_after_max_requests(self):
        session = claude_session.ClaudeSession()
        session.MAX_REQUESTS = 2

        proc1 = make_mock_process()
        proc2 = make_mock_process()

        spawn_count = 0
        original_spawn = session._spawn

        async def counting_spawn():
            nonlocal spawn_count
            spawn_count += 1
            if spawn_count == 1:
                session._proc = proc1
                session._request_count = 0
            else:
                session._proc = proc2
                session._request_count = 0

        session._spawn = counting_spawn

        # First two queries use proc1
        proc1.stdout.push_line({"type": "result", "result": "r1"})
        proc1.stdout.push_line({"type": "result", "result": "r2"})
        await session.query("q1")
        await session.query("q2")

        # Third query should trigger recycle (request_count == MAX_REQUESTS)
        proc2.stdout.push_line({"type": "result", "result": "r3"})
        # Need to set proc1 as alive for the kill
        proc1.returncode = None
        await session.query("q3")

        assert spawn_count == 2  # spawned twice


class TestSessionAlive:
    def test_not_alive_when_no_proc(self):
        session = claude_session.ClaudeSession()
        assert session._is_alive() is False

    def test_not_alive_when_proc_exited(self):
        session = claude_session.ClaudeSession()
        session._proc = MagicMock()
        session._proc.returncode = 1
        assert session._is_alive() is False

    def test_alive_when_running(self):
        session = claude_session.ClaudeSession()
        session._proc = MagicMock()
        session._proc.returncode = None
        assert session._is_alive() is True
