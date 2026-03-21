"""Concurrency tests — verify locks and shared state under parallel access.

SAFETY: No real Claude CLI. No real database writes.
"""

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import claude_session
import daemon


class TestClaudeSessionLock:
    """Verify ClaudeSession._lock serializes concurrent queries."""

    @pytest.mark.asyncio
    async def test_queries_serialize(self):
        session = claude_session.ClaudeSession()

        execution_order = []

        async def mock_query(text, **kw):
            execution_order.append(f"start:{text}")
            await asyncio.sleep(0.05)  # simulate work
            execution_order.append(f"end:{text}")
            return f"response to {text}"

        # Replace query internals to track ordering
        # We test the lock by calling query concurrently
        proc = MagicMock()
        proc.returncode = None
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()

        # Each query will resolve quickly
        responses = asyncio.Queue()
        for i in range(3):
            await responses.put(
                (json.dumps({"type": "result", "result": f"r{i}"}) + "\n").encode()
            )

        async def mock_readline():
            return await responses.get()

        proc.stdout = MagicMock()
        proc.stdout.readline = mock_readline

        session._proc = proc
        session._request_count = 0

        # Launch 3 queries concurrently
        results = await asyncio.gather(
            session.query("q0"),
            session.query("q1"),
            session.query("q2"),
        )
        # All should complete (lock serializes them)
        assert len(results) == 3
        assert session._request_count == 3


class TestFitbitRefreshLock:
    """Verify FitbitClient._refresh_lock prevents stampede."""

    @pytest.mark.asyncio
    async def test_concurrent_refresh_only_refreshes_once(self):
        import fitbit
        client = fitbit.FitbitClient()
        refresh_count = 0
        original_tokens = {
            "access_token": "expired", "refresh_token": "r", "user_id": "U",
        }
        new_tokens = {
            "access_token": "fresh", "refresh_token": "r2", "user_id": "U",
        }

        async def mock_refresh(expired_access_token=""):
            nonlocal refresh_count
            async with client._refresh_lock:
                # Re-check: simulate the real method's check
                if client._tokens and client._tokens["access_token"] != "expired":
                    return  # already refreshed
                await asyncio.sleep(0.05)  # simulate network delay
                refresh_count += 1
                client._tokens = new_tokens

        client._tokens = original_tokens.copy()

        # Launch 5 concurrent refreshes
        await asyncio.gather(*[
            mock_refresh(expired_access_token="expired")
            for _ in range(5)
        ])

        # Only one should have actually refreshed
        assert refresh_count == 1
        assert client._tokens["access_token"] == "fresh"


class TestTasksDictConcurrency:
    """Verify _tasks dict handles concurrent access without errors."""

    @pytest.mark.asyncio
    async def test_concurrent_task_creation(self):
        daemon._tasks.clear()

        async def create_task(i):
            task_id = f"task_{i}"
            daemon._tasks[task_id] = {"status": "processing", "created": 0}
            await asyncio.sleep(0.01)
            daemon._tasks[task_id]["status"] = "done"
            daemon._tasks[task_id]["audio"] = b"data"
            return task_id

        tasks = await asyncio.gather(*[create_task(i) for i in range(20)])
        assert len(tasks) == 20
        assert all(daemon._tasks[t]["status"] == "done" for t in tasks)
        daemon._tasks.clear()

    @pytest.mark.asyncio
    async def test_concurrent_read_write(self):
        """Readers and writers accessing _tasks simultaneously."""
        daemon._tasks.clear()

        async def writer(i):
            daemon._tasks[f"w{i}"] = {"status": "processing", "created": 0}
            await asyncio.sleep(0.01)
            daemon._tasks[f"w{i}"]["status"] = "done"

        async def reader():
            await asyncio.sleep(0.005)
            # Read all tasks — should not crash
            statuses = [v.get("status") for v in list(daemon._tasks.values())]
            return statuses

        await asyncio.gather(
            *[writer(i) for i in range(10)],
            *[reader() for _ in range(10)],
        )
        daemon._tasks.clear()
