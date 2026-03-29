"""Tests for session_pool.py — CLI session pool with deep + fast sessions.

SAFETY: No real Claude CLI processes are spawned. asyncio.create_subprocess_exec
is mocked to return fake processes with controllable stdin/stdout.
"""

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import session_pool


# ---------------------------------------------------------------------------
# Test helpers (same pattern as test_claude_session.py)
# ---------------------------------------------------------------------------

class MockStreamWriter:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        pass

    def get_messages(self):
        msgs = []
        for chunk in self.written:
            for line in chunk.decode().strip().split("\n"):
                if line.strip():
                    msgs.append(json.loads(line))
        return msgs


class MockStreamReader:
    def __init__(self):
        self._queue = asyncio.Queue()

    def push_line(self, data: dict):
        self._queue.put_nowait((json.dumps(data) + "\n").encode())

    def push_eof(self):
        self._queue.put_nowait(b"")

    async def readline(self):
        return await self._queue.get()


def make_mock_process():
    proc = MagicMock()
    proc.stdin = MockStreamWriter()
    proc.stdout = MockStreamReader()
    proc.stderr = MagicMock()
    proc.returncode = None
    proc.pid = 99999
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


@pytest.fixture(autouse=True)
def _reset_singleton():
    session_pool._pool = None
    yield
    session_pool._pool = None


# ---------------------------------------------------------------------------
# _format_history_for_injection
# ---------------------------------------------------------------------------

class TestFormatHistory:
    def test_empty(self):
        assert session_pool._format_history_for_injection([]) == ""

    def test_basic_turns(self):
        turns = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = session_pool._format_history_for_injection(turns)
        assert "[CONVERSATION HISTORY" in result
        assert "USER: Hello" in result
        assert "ASSISTANT: Hi there!" in result
        assert "[/CONVERSATION HISTORY]" in result

    def test_truncates_long_content(self):
        turns = [
            {"role": "user", "content": "x" * 3000},
            {"role": "assistant", "content": "short"},
        ]
        result = session_pool._format_history_for_injection(turns)
        assert "..." in result
        # 1500 chars + "..." = should be significantly shorter than 3000
        assert len(result) < 2500

    def test_handles_non_string_content(self):
        turns = [{"role": "user", "content": [{"type": "text", "text": "multi"}]}]
        result = session_pool._format_history_for_injection(turns)
        assert "USER:" in result


# ---------------------------------------------------------------------------
# _Session spawn
# ---------------------------------------------------------------------------

class TestSessionSpawn:
    @pytest.mark.asyncio
    async def test_spawn_passes_correct_args(self):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await s._spawn()

        args = mock_exec.call_args[0]
        assert "--dangerously-skip-permissions" in args
        assert "--output-format" in args
        assert "--model" in args
        # Check effort level set via env
        env = mock_exec.call_args[1]["env"]
        assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "max"
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
        assert "CLAUDECODE" not in env

    @pytest.mark.asyncio
    async def test_spawn_fast_effort(self):
        s = session_pool._Session("fast", "auto", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await s._spawn()

        env = mock_exec.call_args[1]["env"]
        assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "auto"

    @pytest.mark.asyncio
    async def test_spawn_resets_request_count(self):
        s = session_pool._Session("deep", "max", 150)
        s._request_count = 50
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            await s._spawn()

        assert s._request_count == 0
        assert s._history_injected is False


# ---------------------------------------------------------------------------
# _Session query
# ---------------------------------------------------------------------------

class TestSessionQuery:
    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[])
    async def test_successful_query(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            # Push result before query (it will be read during query)
            mock_proc.stdout.push_line({"type": "result", "result": "Hello!"})
            result = await s.query("Hi")

        assert result.text == "Hello!"
        assert s._request_count == 1

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[])
    async def test_context_included(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            mock_proc.stdout.push_line({"type": "result", "result": "ok"})
            await s.query("test", extra_context="Weather: sunny 55F")

        msgs = mock_proc.stdin.get_messages()
        user_msg = msgs[-1]  # last written message
        content = user_msg["message"]["content"]
        assert "[CONTEXT]" in content
        assert "sunny" in content

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[
        {"role": "user", "content": "prev question"},
        {"role": "assistant", "content": "prev answer"},
    ])
    async def test_history_injected_on_first_query(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            mock_proc.stdout.push_line({"type": "result", "result": "ok"})
            await s.query("first question")

        msgs = mock_proc.stdin.get_messages()
        content = msgs[-1]["message"]["content"]
        assert "[CONVERSATION HISTORY" in content
        assert "prev question" in content

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[
        {"role": "user", "content": "prev"},
        {"role": "assistant", "content": "answer"},
    ])
    async def test_history_not_injected_on_second_query(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            # First query — history injected
            mock_proc.stdout.push_line({"type": "result", "result": "ok"})
            await s.query("first")

            # Second query — no history
            mock_proc.stdout.push_line({"type": "result", "result": "ok2"})
            result = await s.query("second")

        assert result.text == "ok2"
        msgs = mock_proc.stdin.get_messages()
        second_msg = msgs[-1]["message"]["content"]
        assert "CONVERSATION HISTORY" not in second_msg

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[])
    async def test_file_blocks_multimodal(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        file_blocks = [{"type": "image", "source": {"type": "base64",
                        "media_type": "image/jpeg", "data": "abc123"}}]

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            mock_proc.stdout.push_line({"type": "result", "result": "I see an image"})
            result = await s.query("What is this?", file_blocks=file_blocks)

        assert result.text == "I see an image"
        msgs = mock_proc.stdin.get_messages()
        content = msgs[-1]["message"]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image"

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[])
    async def test_error_result_raises(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            mock_proc.stdout.push_line(
                {"type": "result", "result": "something broke", "is_error": True})
            with pytest.raises(RuntimeError, match="Session 'deep' error"):
                await s.query("test")

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[])
    async def test_eof_raises(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            mock_proc.stdout.push_eof()
            with pytest.raises(RuntimeError, match="exited unexpectedly"):
                await s.query("test")

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[])
    async def test_control_request_auto_approved(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            mock_proc.stdout.push_line(
                {"type": "control_request", "request_id": "r1"})
            mock_proc.stdout.push_line(
                {"type": "result", "result": "done"})
            result = await s.query("test")

        assert result.text == "done"
        # Verify approval was sent
        msgs = mock_proc.stdin.get_messages()
        approvals = [m for m in msgs if m.get("type") == "control_response"]
        assert len(approvals) == 1
        assert approvals[0]["response"]["request_id"] == "r1"

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[])
    async def test_assistant_events_ignored(self, _):
        s = session_pool._Session("deep", "max", 150)
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            mock_proc.stdout.push_line(
                {"type": "assistant", "message": "thinking..."})
            mock_proc.stdout.push_line(
                {"type": "result", "result": "final answer"})
            result = await s.query("test")

        assert result.text == "final answer"


# ---------------------------------------------------------------------------
# _Session recycling
# ---------------------------------------------------------------------------

class TestSessionRecycling:
    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[])
    async def test_recycles_after_max_requests(self, _):
        s = session_pool._Session("deep", "max", 3)  # recycle after 3
        mock_proc1 = make_mock_process()
        mock_proc2 = make_mock_process()
        spawn_count = 0

        async def mock_spawn(*args, **kwargs):
            nonlocal spawn_count
            spawn_count += 1
            return mock_proc1 if spawn_count <= 1 else mock_proc2

        with patch("session_pool.asyncio.create_subprocess_exec",
                   side_effect=mock_spawn):
            # 3 queries on first session
            for _ in range(3):
                mock_proc1.stdout.push_line({"type": "result", "result": "ok"})
                await s.query("test")

            assert s._request_count == 3

            # 4th query should trigger recycle (new process)
            mock_proc1.returncode = None  # still "alive" for recycle check
            mock_proc2.stdout.push_line({"type": "result", "result": "recycled"})
            result = await s.query("after recycle")

        assert result.text == "recycled"
        assert spawn_count == 2  # spawned twice

    @pytest.mark.asyncio
    @patch("session_pool.get_recent_turns", return_value=[
        {"role": "user", "content": "old msg"},
        {"role": "assistant", "content": "old reply"},
    ])
    async def test_history_reinjected_after_recycle(self, _):
        s = session_pool._Session("deep", "max", 1)  # recycle after every request
        procs = [make_mock_process(), make_mock_process()]
        idx = [0]

        async def mock_spawn(*args, **kwargs):
            p = procs[min(idx[0], len(procs) - 1)]
            idx[0] += 1
            return p

        with patch("session_pool.asyncio.create_subprocess_exec",
                   side_effect=mock_spawn):
            procs[0].stdout.push_line({"type": "result", "result": "ok"})
            await s.query("first")

            procs[1].stdout.push_line({"type": "result", "result": "ok2"})
            await s.query("second after recycle")

        # Second proc should have received history injection
        msgs = procs[1].stdin.get_messages()
        content = msgs[-1]["message"]["content"]
        assert "CONVERSATION HISTORY" in content
        assert "old msg" in content


# ---------------------------------------------------------------------------
# SessionPool
# ---------------------------------------------------------------------------

class TestSessionPool:
    @pytest.mark.asyncio
    async def test_query_deep_routes_to_deep(self):
        pool = session_pool.SessionPool()
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            with patch("session_pool.get_recent_turns", return_value=[]):
                mock_proc.stdout.push_line({"type": "result", "result": "deep answer"})
                result = await pool.query_deep("complex question")

        assert result.text == "deep answer"

    @pytest.mark.asyncio
    async def test_query_fast_routes_to_fast(self):
        pool = session_pool.SessionPool()
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            with patch("session_pool.get_recent_turns", return_value=[]):
                mock_proc.stdout.push_line({"type": "result", "result": "fast answer"})
                result = await pool.query_fast("set a timer")

        assert result.text == "fast answer"

    @pytest.mark.asyncio
    async def test_start_spawns_both(self):
        pool = session_pool.SessionPool()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=make_mock_process()) as mock_exec:
            await pool.start()

        assert mock_exec.call_count == 2  # deep + fast

    @pytest.mark.asyncio
    async def test_stop_kills_both(self):
        pool = session_pool.SessionPool()
        mock_proc = make_mock_process()

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            await pool.start()
            await pool.stop()

        # Both sessions killed
        assert pool._deep._proc is None
        assert pool._fast._proc is None

    def test_get_status(self):
        pool = session_pool.SessionPool()
        status = pool.get_status()
        assert "deep" in status
        assert "fast" in status
        assert status["deep"]["name"] == "deep"
        assert status["fast"]["name"] == "fast"
        assert status["deep"]["alive"] is False  # not started yet


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_session_pool_returns_singleton(self):
        p1 = session_pool.get_session_pool()
        p2 = session_pool.get_session_pool()
        assert p1 is p2

    def test_singleton_reset(self):
        p1 = session_pool.get_session_pool()
        session_pool._pool = None
        p2 = session_pool.get_session_pool()
        assert p1 is not p2
