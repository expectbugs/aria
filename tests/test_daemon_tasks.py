"""Tests for daemon.py task lifecycle — creation, polling, expiration, cleanup."""

import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from starlette.testclient import TestClient

import daemon
import config


@pytest.fixture
def client():
    with patch("daemon.db.get_pool"), patch("daemon.db.close"), \
         patch("daemon.task_dispatcher.start_dispatcher"), \
         patch("daemon.task_dispatcher.stop_dispatcher"), \
         patch("daemon.completion_listener.start_listener"), \
         patch("daemon.completion_listener.stop_listener"), \
         patch("daemon.get_amnesia_pool") as mock_amnesia, \
         patch("daemon.get_session_pool") as mock_session:
        mock_amnesia.return_value.start = AsyncMock()
        mock_amnesia.return_value.stop = AsyncMock()
        mock_session.return_value.start = AsyncMock()
        mock_session.return_value.stop = AsyncMock()
        with TestClient(daemon.app) as c:
            yield c


@pytest.fixture(autouse=True)
def reset_tasks():
    daemon._tasks.clear()
    yield
    daemon._tasks.clear()


AUTH = {"Authorization": f"Bearer {config.AUTH_TOKEN}"}


class TestTaskExpiration:
    def test_expired_tasks_cleaned_on_result_poll(self, client):
        """Tasks older than 2 hours should be cleaned up."""
        daemon._tasks["old1"] = {
            "status": "done", "audio": b"wav",
            "created": time.time() - 8000,  # >2 hours ago
        }
        daemon._tasks["fresh"] = {
            "status": "done", "audio": b"wav",
            "created": time.time(),
        }

        # Poll for a non-existent task to trigger cleanup
        client.get("/ask/result/trigger_cleanup", headers=AUTH)

        assert "old1" not in daemon._tasks
        assert "fresh" in daemon._tasks

    def test_active_tasks_not_cleaned(self, client):
        daemon._tasks["active"] = {
            "status": "processing", "created": time.time(),
        }

        client.get("/ask/result/nonexist", headers=AUTH)
        assert "active" in daemon._tasks


class TestCleanupExpiredTasks:
    def test_removes_old_entries(self):
        daemon._tasks["old"] = {"status": "done", "audio": b"", "created": time.time() - 8000}
        daemon._tasks["fresh"] = {"status": "done", "audio": b"", "created": time.time()}
        daemon._cleanup_expired_tasks()
        assert "old" not in daemon._tasks
        assert "fresh" in daemon._tasks

    def test_preserves_entries_without_created(self):
        """Tasks without 'created' field default to now (not expired)."""
        daemon._tasks["no_created"] = {"status": "processing"}
        daemon._cleanup_expired_tasks()
        assert "no_created" in daemon._tasks

    def test_empty_dict_no_error(self):
        daemon._cleanup_expired_tasks()
        assert daemon._tasks == {}

    def test_cleanup_runs_on_status_poll(self, client):
        """ask_status should clean expired tasks (Tasker's primary polling path)."""
        daemon._tasks["old"] = {"status": "done", "audio": b"", "created": time.time() - 8000}
        daemon._tasks["t1"] = {"status": "processing", "created": time.time()}
        client.get("/ask/status/t1", headers=AUTH)
        assert "old" not in daemon._tasks
        assert "t1" in daemon._tasks


class TestTaskStatusEndpoint:
    def test_processing_returns_202(self, client):
        daemon._tasks["t1"] = {"status": "processing", "created": time.time()}
        resp = client.get("/ask/status/t1", headers=AUTH)
        assert resp.status_code == 202
        assert resp.json()["status"] == "processing"

    def test_done_returns_200(self, client):
        daemon._tasks["t1"] = {
            "status": "done", "audio": b"wav", "created": time.time(),
        }
        resp = client.get("/ask/status/t1", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

    def test_error_returns_500(self, client):
        daemon._tasks["t1"] = {
            "status": "error", "error": "Something broke", "created": time.time(),
        }
        resp = client.get("/ask/status/t1", headers=AUTH)
        assert resp.status_code == 500
        assert "Something broke" in resp.json()["error"]

    def test_includes_transcript(self, client):
        daemon._tasks["t1"] = {
            "status": "processing", "created": time.time(),
            "transcript": "Hello world",
        }
        resp = client.get("/ask/status/t1", headers=AUTH)
        assert resp.json()["transcript"] == "Hello world"

    def test_includes_delivery(self, client):
        daemon._tasks["t1"] = {
            "status": "done", "audio": b"", "created": time.time(),
            "delivery": "sms",
        }
        resp = client.get("/ask/status/t1", headers=AUTH)
        assert resp.json()["delivery"] == "sms"


class TestTaskResultEndpoint:
    def test_done_returns_audio(self, client):
        daemon._tasks["t1"] = {
            "status": "done", "audio": b"RIFF wav data",
            "created": time.time(),
        }
        resp = client.get("/ask/result/t1", headers=AUTH)
        assert resp.status_code == 200
        assert resp.content == b"RIFF wav data"
        assert resp.headers["content-type"] == "audio/wav"
        # Task should be cleaned up after retrieval
        assert "t1" not in daemon._tasks

    def test_error_returns_500_and_cleans_up(self, client):
        daemon._tasks["t1"] = {
            "status": "error", "error": "Broke", "created": time.time(),
        }
        resp = client.get("/ask/result/t1", headers=AUTH)
        assert resp.status_code == 500
        assert "t1" not in daemon._tasks

    def test_processing_returns_202(self, client):
        daemon._tasks["t1"] = {"status": "processing", "created": time.time()}
        resp = client.get("/ask/result/t1", headers=AUTH)
        assert resp.status_code == 202

    def test_unknown_task_returns_404(self, client):
        resp = client.get("/ask/result/nonexist", headers=AUTH)
        assert resp.status_code == 404
