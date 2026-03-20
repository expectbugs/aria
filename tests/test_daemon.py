"""Tests for daemon.py — API endpoints.

SAFETY: Claude CLI is never spawned. All external I/O is mocked.
The TestClient exercises the FastAPI routes with mocked backends.
"""

import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from starlette.testclient import TestClient

import daemon
import config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """TestClient with lifespan dependencies mocked."""
    with patch("daemon.db.get_pool"), \
         patch("daemon.db.close"):
        # Mock _claude_session._kill which is called in lifespan shutdown
        original_kill = daemon._claude_session._kill
        daemon._claude_session._kill = AsyncMock()
        try:
            with TestClient(daemon.app) as c:
                yield c
        finally:
            daemon._claude_session._kill = original_kill


AUTH = {"Authorization": f"Bearer {config.AUTH_TOKEN}"}
BAD_AUTH = {"Authorization": "Bearer wrong_token"}


# ---------------------------------------------------------------------------
# Health endpoint (unauthenticated)
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_returns_status(self, client):
        with patch("daemon.db.get_conn") as mock_gc:
            mc = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mc)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "uptime_s" in data
        assert "version" in data
        assert "checks" in data

    def test_checks_include_subsystems(self, client):
        with patch("daemon.db.get_conn") as mock_gc:
            mc = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mc)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.get("/health")
        checks = resp.json()["checks"]
        assert "database" in checks
        assert "claude" in checks
        assert "tts" in checks


# ---------------------------------------------------------------------------
# Auth verification
# ---------------------------------------------------------------------------

class TestAuth:
    def test_missing_auth_returns_401(self, client):
        resp = client.post("/ask", json={"text": "hello"})
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        resp = client.post("/ask", json={"text": "hello"}, headers=BAD_AUTH)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /ask endpoint
# ---------------------------------------------------------------------------

class TestAskEndpoint:
    @patch("daemon.process_actions")
    @patch("daemon.ask_claude", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon.log_request")
    def test_successful_ask(self, mock_log, mock_ctx, mock_claude, mock_actions, client):
        mock_ctx.return_value = "context"
        mock_claude.return_value = "Hello from ARIA!"
        mock_actions.return_value = "Hello from ARIA!"

        resp = client.post("/ask", json={"text": "Hello"}, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Hello from ARIA!"
        assert data["source"] == "claude"

    def test_empty_text_returns_400(self, client):
        resp = client.post("/ask", json={"text": "  "}, headers=AUTH)
        assert resp.status_code == 400

    @patch("daemon.ask_claude", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon.log_request")
    def test_claude_error_returns_500(self, mock_log, mock_ctx, mock_claude, client):
        mock_ctx.return_value = ""
        mock_claude.side_effect = RuntimeError("Claude timed out")

        resp = client.post("/ask", json={"text": "test"}, headers=AUTH)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /ask/start + /ask/status + /ask/result lifecycle
# ---------------------------------------------------------------------------

class TestAsyncTaskLifecycle:
    @patch("daemon.ask_claude", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon._generate_tts", new_callable=AsyncMock)
    @patch("daemon.process_actions")
    @patch("daemon.log_request")
    def test_full_lifecycle(self, mock_log, mock_actions, mock_tts,
                            mock_ctx, mock_claude, client):
        mock_ctx.return_value = ""
        mock_claude.return_value = "Test response"
        mock_actions.return_value = "Test response"
        mock_tts.return_value = b"fake wav data"

        # Start task
        resp = client.post("/ask/start", json={"text": "Hello"}, headers=AUTH)
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        # Wait for background task to complete
        for _ in range(20):
            time.sleep(0.1)
            status_resp = client.get(f"/ask/status/{task_id}", headers=AUTH)
            if status_resp.json().get("status") != "processing":
                break

        # Get result
        result_resp = client.get(f"/ask/result/{task_id}", headers=AUTH)
        if result_resp.status_code == 200:
            assert result_resp.headers["content-type"] == "audio/wav"

    def test_unknown_task_returns_404(self, client):
        resp = client.get("/ask/status/nonexistent", headers=AUTH)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /location endpoint
# ---------------------------------------------------------------------------

class TestLocationEndpoint:
    @patch("daemon.location_store.record", new_callable=AsyncMock)
    def test_records_location(self, mock_record, client):
        mock_record.return_value = {
            "timestamp": "2026-03-20T14:00:00",
            "location": "Home",
        }
        resp = client.post("/location", json={
            "lat": 42.58, "lon": -88.43,
            "accuracy": 10.0, "speed": 0.0, "battery": 85,
        }, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_missing_auth(self, client):
        resp = client.post("/location", json={"lat": 42.58, "lon": -88.43})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /nudge endpoint
# ---------------------------------------------------------------------------

class TestNudgeEndpoint:
    @patch("daemon.ask_claude", new_callable=AsyncMock)
    def test_composes_nudge(self, mock_claude, client):
        mock_claude.return_value = "Hey, don't forget to eat!"

        resp = client.post("/nudge", json={
            "triggers": ["No meals logged today"],
            "context": "",
        }, headers=AUTH)
        assert resp.status_code == 200
        assert "message" in resp.json()


# ---------------------------------------------------------------------------
# /sms webhook
# ---------------------------------------------------------------------------

class TestSmsWebhook:
    @patch("daemon.sms.validate_request")
    def test_stop_keyword(self, mock_validate, client):
        mock_validate.return_value = True
        resp = client.post("/sms", data={
            "From": "+15551234567", "Body": "STOP", "NumMedia": "0",
        }, headers={"X-Twilio-Signature": "test"})
        assert resp.status_code == 200
        assert "<Response></Response>" in resp.text

    @patch("daemon.sms.validate_request")
    def test_help_keyword(self, mock_validate, client):
        mock_validate.return_value = True
        resp = client.post("/sms", data={
            "From": "+15551234567", "Body": "HELP", "NumMedia": "0",
        }, headers={"X-Twilio-Signature": "test"})
        assert resp.status_code == 200
        assert "ARIA" in resp.text

    @patch("daemon.sms.validate_request")
    def test_invalid_signature(self, mock_validate, client):
        mock_validate.return_value = False
        resp = client.post("/sms", data={
            "From": "+15551234567", "Body": "Hello", "NumMedia": "0",
        }, headers={"X-Twilio-Signature": "bad"})
        assert resp.status_code == 403

    @patch("daemon.sms.validate_request")
    def test_unknown_sender_ignored(self, mock_validate, client):
        mock_validate.return_value = True
        resp = client.post("/sms", data={
            "From": "+19999999999", "Body": "Hello", "NumMedia": "0",
        }, headers={"X-Twilio-Signature": "test"})
        assert resp.status_code == 200
        assert "<Response></Response>" in resp.text


# ---------------------------------------------------------------------------
# /fitbit endpoints
# ---------------------------------------------------------------------------

class TestFitbitEndpoints:
    @patch("daemon.fitbit_store.save_snapshot")
    @patch("daemon.fitbit.get_client")
    def test_fitbit_sync(self, mock_client, mock_save, client):
        mock_fb = MagicMock()
        mock_fb.fetch_daily_snapshot = AsyncMock(return_value={
            "date": "2026-03-20", "heart_rate": {},
        })
        mock_client.return_value = mock_fb

        resp = client.post("/fitbit/sync", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_fitbit_webhook_verify(self, client):
        resp = client.get(f"/webhook/fitbit?verify={config.FITBIT_WEBHOOK_VERIFY}")
        assert resp.status_code == 200

    def test_fitbit_webhook_verify_bad_code(self, client):
        resp = client.get("/webhook/fitbit?verify=wrong")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /mms_media endpoint
# ---------------------------------------------------------------------------

class TestMmsMedia:
    def test_file_not_found(self, client):
        resp = client.get("/mms_media/nonexistent.png")
        assert resp.status_code == 404

    def test_sanitizes_filename(self, client):
        # Attempt path traversal
        resp = client.get("/mms_media/../../etc/passwd")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /snippet endpoint
# ---------------------------------------------------------------------------

class TestSnippetEndpoint:
    def test_snippet_not_found(self, client):
        resp = client.get("/snippet/nonexistent")
        assert resp.status_code == 404

    def test_sanitizes_name(self, client):
        resp = client.get("/snippet/../../etc/passwd")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /stt endpoint
# ---------------------------------------------------------------------------

class TestSttEndpoint:
    @patch("daemon.config")
    def test_disabled_returns_503(self, mock_config, client):
        mock_config.ENABLE_WHISPER = False
        mock_config.AUTH_TOKEN = config.AUTH_TOKEN
        resp = client.post("/stt", content=b"audio data", headers=AUTH)
        assert resp.status_code == 503

    def test_empty_audio_returns_400(self, client):
        with patch("daemon.config") as mc:
            mc.ENABLE_WHISPER = True
            mc.AUTH_TOKEN = config.AUTH_TOKEN
            resp = client.post("/stt", content=b"", headers=AUTH)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ClaudeSession (unit tests, no real subprocess)
# ---------------------------------------------------------------------------

class TestClaudeSession:
    def test_is_alive_false_when_no_proc(self):
        session = daemon.ClaudeSession()
        assert session._is_alive() is False

    def test_max_requests_constant(self):
        assert daemon.ClaudeSession.MAX_REQUESTS == 200


# ---------------------------------------------------------------------------
# Log request
# ---------------------------------------------------------------------------

class TestLogRequest:
    @patch("daemon.db.get_conn")
    def test_logs_successfully(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        daemon.log_request("test input", "ok", response="test output", duration=1.5)
        sql = mc.execute.call_args[0][0]
        assert "INSERT INTO request_log" in sql

    @patch("daemon.db.get_conn")
    def test_truncates_long_response(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        long_response = "x" * 1000
        daemon.log_request("test", "ok", response=long_response)
        params = mc.execute.call_args[0][1]
        assert len(params[2]) == 500  # truncated to 500

    @patch("daemon.db.get_conn")
    def test_handles_db_error(self, mock_get_conn):
        mock_get_conn.side_effect = Exception("DB down")
        # Should not raise
        daemon.log_request("test", "ok")
