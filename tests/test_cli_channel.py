"""Tests for CLI channel support — daemon endpoints + delivery engine.

Validates that channel="cli" flows through the request pipeline correctly,
includes audio when requested, and doesn't push to phone.
"""

import base64
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from starlette.testclient import TestClient

import daemon
import config
import delivery_engine
from tests.helpers import make_action_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """TestClient with lifespan dependencies mocked."""
    with patch("daemon.db.get_pool"), \
         patch("daemon.db.close"), \
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


AUTH = {"Authorization": f"Bearer {config.AUTH_TOKEN}"}


# ---------------------------------------------------------------------------
# AskRequest model accepts new fields
# ---------------------------------------------------------------------------

class TestAskRequestModel:
    def test_defaults(self):
        req = daemon.AskRequest(text="hello")
        assert req.channel == "voice"
        assert req.include_audio is False

    def test_cli_channel(self):
        req = daemon.AskRequest(text="hello", channel="cli")
        assert req.channel == "cli"

    def test_include_audio(self):
        req = daemon.AskRequest(text="hello", include_audio=True)
        assert req.include_audio is True


# ---------------------------------------------------------------------------
# /ask with channel="cli" — text only
# ---------------------------------------------------------------------------

class TestAskCliChannel:
    @patch("daemon._verify_and_maybe_retry", new_callable=AsyncMock)
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon.log_request")
    def test_cli_channel_returns_text(self, mock_log, mock_ctx, mock_claude,
                                      mock_actions, mock_verify, client):
        mock_ctx.return_value = "context"
        mock_claude.return_value = "Hello from CLI!"
        result = make_action_result(clean_response="Hello from CLI!")
        mock_actions.return_value = result
        mock_verify.return_value = result

        resp = client.post("/ask", json={"text": "Hi", "channel": "cli"},
                           headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Hello from CLI!"
        assert data["audio"] is None

    @patch("daemon._verify_and_maybe_retry", new_callable=AsyncMock)
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon.log_request")
    def test_cli_channel_passes_to_delivery_meta(self, mock_log, mock_ctx,
                                                   mock_claude, mock_actions,
                                                   mock_verify, client):
        """Verify channel='cli' flows into process_actions metadata."""
        mock_ctx.return_value = ""
        mock_claude.return_value = "ok"
        result = make_action_result(clean_response="ok")
        mock_actions.return_value = result
        mock_verify.return_value = result

        client.post("/ask", json={"text": "test", "channel": "cli"},
                    headers=AUTH)

        # process_actions was called with metadata containing channel="cli"
        call_kwargs = mock_actions.call_args
        metadata = call_kwargs.kwargs.get("metadata") or call_kwargs[1].get("metadata")
        assert metadata["channel"] == "cli"


# ---------------------------------------------------------------------------
# /ask with include_audio=True
# ---------------------------------------------------------------------------

class TestAskIncludeAudio:
    @patch("daemon._generate_tts", new_callable=AsyncMock)
    @patch("daemon._verify_and_maybe_retry", new_callable=AsyncMock)
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon.log_request")
    def test_include_audio_returns_base64(self, mock_log, mock_ctx, mock_claude,
                                          mock_actions, mock_verify, mock_tts,
                                          client):
        mock_ctx.return_value = ""
        mock_claude.return_value = "Audio test"
        result = make_action_result(clean_response="Audio test")
        mock_actions.return_value = result
        mock_verify.return_value = result
        mock_tts.return_value = b"RIFF fake wav"

        resp = client.post("/ask", json={
            "text": "hello", "channel": "cli", "include_audio": True,
        }, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Audio test"
        assert data["audio"] is not None
        # Verify it's valid base64
        decoded = base64.b64decode(data["audio"])
        assert decoded == b"RIFF fake wav"

    @patch("daemon._generate_tts", new_callable=AsyncMock)
    @patch("daemon._verify_and_maybe_retry", new_callable=AsyncMock)
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon.log_request")
    def test_tts_failure_still_returns_text(self, mock_log, mock_ctx, mock_claude,
                                             mock_actions, mock_verify, mock_tts,
                                             client):
        """TTS failure is non-fatal — response text is still returned."""
        mock_ctx.return_value = ""
        mock_claude.return_value = "Text only"
        result = make_action_result(clean_response="Text only")
        mock_actions.return_value = result
        mock_verify.return_value = result
        mock_tts.side_effect = RuntimeError("TTS exploded")

        resp = client.post("/ask", json={
            "text": "hello", "include_audio": True,
        }, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Text only"
        assert data["audio"] is None


# ---------------------------------------------------------------------------
# /ask/status returns response_text
# ---------------------------------------------------------------------------

class TestAskStatusResponseText:
    @patch("daemon._generate_tts", new_callable=AsyncMock)
    @patch("daemon._verify_and_maybe_retry", new_callable=AsyncMock)
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon.log_request")
    def test_status_done_includes_response(self, mock_log, mock_ctx, mock_claude,
                                            mock_actions, mock_verify, mock_tts,
                                            client):
        mock_ctx.return_value = ""
        mock_claude.return_value = "Async hello"
        result = make_action_result(clean_response="Async hello")
        mock_actions.return_value = result
        mock_verify.return_value = result
        mock_tts.return_value = b"wav"

        resp = client.post("/ask/start",
                           json={"text": "Hello", "channel": "cli"},
                           headers=AUTH)
        task_id = resp.json()["task_id"]

        # Wait for completion
        for _ in range(30):
            time.sleep(0.1)
            status_resp = client.get(f"/ask/status/{task_id}", headers=AUTH)
            if status_resp.json().get("status") != "processing":
                break

        data = status_resp.json()
        assert data["status"] == "done"
        assert data.get("response") == "Async hello"


# ---------------------------------------------------------------------------
# _process_task with channel="cli" skips delivery engine
# ---------------------------------------------------------------------------

class TestProcessTaskCliChannel:
    @patch("daemon.delivery_engine.execute_delivery", new_callable=AsyncMock)
    @patch("daemon._generate_tts", new_callable=AsyncMock)
    @patch("daemon._verify_and_maybe_retry", new_callable=AsyncMock)
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock)
    @patch("daemon._get_context_for_text", new_callable=AsyncMock)
    @patch("daemon.log_request")
    def test_cli_channel_skips_delivery(self, mock_log, mock_ctx, mock_claude,
                                         mock_actions, mock_verify, mock_tts,
                                         mock_delivery, client):
        mock_ctx.return_value = ""
        mock_claude.return_value = "CLI response"
        result = make_action_result(clean_response="CLI response")
        mock_actions.return_value = result
        mock_verify.return_value = result
        mock_tts.return_value = b"wav bytes"

        resp = client.post("/ask/start",
                           json={"text": "test", "channel": "cli"},
                           headers=AUTH)
        task_id = resp.json()["task_id"]

        for _ in range(30):
            time.sleep(0.1)
            status_resp = client.get(f"/ask/status/{task_id}", headers=AUTH)
            if status_resp.json().get("status") != "processing":
                break

        # Delivery engine should NOT have been called
        mock_delivery.assert_not_called()


# ---------------------------------------------------------------------------
# Delivery engine: evaluate(source="cli")
# ---------------------------------------------------------------------------

class TestDeliveryEngineCli:
    _available = delivery_engine.UserState(
        location="home", activity="available",
        channels=["voice", "image", "sms"],
        battery=80, location_fresh=True,
    )

    def test_cli_source_returns_text_method(self):
        with patch("delivery_engine.get_user_state", return_value=self._available):
            decision = delivery_engine.evaluate(source="cli")
        assert decision.method == "text"
        assert "CLI" in decision.reason

    def test_cli_source_even_when_urgent(self):
        with patch("delivery_engine.get_user_state", return_value=self._available):
            decision = delivery_engine.evaluate(source="cli", priority="urgent")
        assert decision.method == "text"

    def test_voice_source_still_works(self):
        """Verify we didn't break existing voice routing."""
        with patch("delivery_engine.get_user_state", return_value=self._available):
            decision = delivery_engine.evaluate(source="voice")
            assert decision.method == "voice"


# ---------------------------------------------------------------------------
# AskResponse model includes audio field
# ---------------------------------------------------------------------------

class TestAskResponseModel:
    def test_default_no_audio(self):
        resp = daemon.AskResponse(response="hi")
        assert resp.audio is None

    def test_with_audio(self):
        resp = daemon.AskResponse(response="hi", audio="QUFB")
        assert resp.audio == "QUFB"
