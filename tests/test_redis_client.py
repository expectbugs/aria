"""Tests for redis_client.py — Redis singleton and task status functions.

SAFETY: All Redis calls are mocked. No real Redis connections.
"""

from unittest.mock import patch, MagicMock, PropertyMock
import logging

import pytest

import redis_client


@pytest.fixture(autouse=True)
def _reset_redis_client():
    """Reset the singleton state before each test."""
    redis_client._client = None
    redis_client._warned = False
    yield
    redis_client._client = None
    redis_client._warned = False


class TestGetClient:
    @patch("redis_client._redis_lib")
    def test_returns_client_when_available(self, mock_redis_lib):
        mock_client = MagicMock()
        mock_redis_lib.Redis.from_url.return_value = mock_client

        client = redis_client.get_client()
        assert client is mock_client
        mock_client.ping.assert_called_once()

    @patch("redis_client._redis_lib")
    def test_returns_none_when_unavailable(self, mock_redis_lib):
        mock_redis_lib.Redis.from_url.side_effect = ConnectionError("refused")

        client = redis_client.get_client()
        assert client is None

    @patch("redis_client._redis_lib")
    def test_singleton_returns_same_client(self, mock_redis_lib):
        mock_client = MagicMock()
        mock_redis_lib.Redis.from_url.return_value = mock_client

        client1 = redis_client.get_client()
        client2 = redis_client.get_client()
        assert client1 is client2
        # Should only create once
        assert mock_redis_lib.Redis.from_url.call_count == 1

    @patch("redis_client._redis_lib")
    def test_warns_once_on_failure(self, mock_redis_lib, caplog):
        mock_redis_lib.Redis.from_url.side_effect = ConnectionError("refused")

        with caplog.at_level(logging.WARNING, logger="aria.redis"):
            redis_client.get_client()
            redis_client._client = None  # reset to retry
            redis_client.get_client()

        # Should warn only once due to _warned flag
        warnings = [r for r in caplog.records if "Redis unavailable" in r.message]
        assert len(warnings) == 1

    def test_returns_none_when_package_not_installed(self):
        original = redis_client._redis_lib
        redis_client._redis_lib = None
        try:
            client = redis_client.get_client()
            assert client is None
        finally:
            redis_client._redis_lib = original


class TestClose:
    @patch("redis_client._redis_lib")
    def test_closes_client(self, mock_redis_lib):
        mock_client = MagicMock()
        redis_client._client = mock_client

        redis_client.close()
        mock_client.close.assert_called_once()
        assert redis_client._client is None

    def test_safe_when_no_client(self):
        redis_client._client = None
        redis_client.close()  # should not raise


class TestGetActiveTasks:
    @patch("redis_client.get_client")
    def test_returns_tasks_when_present(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.smembers.return_value = {"task-1"}
        mock_client.hgetall.return_value = {
            "status": "running",
            "description": "generating image",
            "progress": "45",
            "message": "upscaling",
            "eta_seconds": "30",
        }

        tasks = redis_client.get_active_tasks()
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "task-1"
        assert tasks[0]["description"] == "generating image"
        assert tasks[0]["progress"] == 45  # cast to int
        assert tasks[0]["eta_seconds"] == 30  # cast to int

    @patch("redis_client.get_client")
    def test_returns_empty_when_no_tasks(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.smembers.return_value = set()

        assert redis_client.get_active_tasks() == []

    @patch("redis_client.get_client")
    def test_returns_empty_when_redis_unavailable(self, mock_get_client):
        mock_get_client.return_value = None
        assert redis_client.get_active_tasks() == []

    @patch("redis_client.get_client")
    def test_cleans_stale_entries(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.smembers.return_value = {"task-old"}
        mock_client.hgetall.return_value = {
            "status": "completed",  # not queued/running
            "description": "done task",
        }

        tasks = redis_client.get_active_tasks()
        assert len(tasks) == 0
        mock_client.srem.assert_called_once()

    @patch("redis_client.get_client")
    def test_handles_missing_fields(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.smembers.return_value = {"task-2"}
        mock_client.hgetall.return_value = {
            "status": "queued",
            # no description, progress, message, eta_seconds
        }

        tasks = redis_client.get_active_tasks()
        assert len(tasks) == 1
        assert tasks[0]["description"] == "unknown task"
        assert tasks[0]["progress"] == 0
        assert tasks[0]["eta_seconds"] is None

    @patch("redis_client.get_client")
    def test_handles_redis_error_gracefully(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.smembers.side_effect = Exception("connection lost")

        tasks = redis_client.get_active_tasks()
        assert tasks == []


class TestFormatTaskStatus:
    def test_single_task(self):
        tasks = [{
            "task_id": "t1", "description": "generating image",
            "progress": 45, "status": "running",
            "message": "upscaling", "eta_seconds": 120,
        }]
        result = redis_client.format_task_status(tasks)
        assert "Background task [running]:" in result
        assert "generating image" in result
        assert "45%" in result
        assert "upscaling" in result
        assert "~2m remaining" in result

    def test_multiple_tasks(self):
        tasks = [
            {"task_id": "t1", "description": "image gen", "progress": 80,
             "status": "running", "message": "", "eta_seconds": None},
            {"task_id": "t2", "description": "web search", "progress": 0,
             "status": "queued", "message": "", "eta_seconds": None},
        ]
        result = redis_client.format_task_status(tasks)
        assert "image gen" in result
        assert "web search" in result
        assert result.count("Background task") == 2

    def test_empty_list(self):
        assert redis_client.format_task_status([]) == ""

    def test_missing_optional_fields(self):
        tasks = [{
            "task_id": "t1", "description": "task",
            "progress": 0, "status": "queued",
            "message": "", "eta_seconds": None,
        }]
        result = redis_client.format_task_status(tasks)
        assert "Background task [queued]: task" in result
        assert "%" not in result  # no progress shown at 0
        assert "remaining" not in result

    def test_eta_seconds_format(self):
        tasks = [{
            "task_id": "t1", "description": "quick task",
            "progress": 90, "status": "running",
            "message": "", "eta_seconds": 15,
        }]
        result = redis_client.format_task_status(tasks)
        assert "~15s remaining" in result
