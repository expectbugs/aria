"""Tests for end-to-end delivery routing in daemon.py.

Verifies the full path: request → Claude response with set_delivery
→ handler routes via execute_delivery.

SAFETY: Claude/TTS/SMS/push all mocked.
"""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import daemon


@pytest.fixture(autouse=True)
def reset_tasks():
    daemon._tasks.clear()
    yield
    daemon._tasks.clear()


@pytest.fixture(autouse=True)
def _mock_execute_delivery():
    """Mock execute_delivery to route based on hint — delivery tests verify
    the hint flows from set_delivery ACTION through to execute_delivery."""
    async def _route(response_text, content_type="response", priority="normal",
                     source="voice", hint=None, sms_target=None, push_voice=True):
        method = hint or ("sms" if source == "sms" else "voice")
        audio = b"tts_audio" if method == "voice" else b""
        return {"method": method, "audio": audio, "reason": "test passthrough"}
    with patch("delivery_engine.execute_delivery", side_effect=_route):
        yield


@pytest.fixture(autouse=True)
def _bypass_verification():
    """Delivery tests focus on routing, not verification."""
    async def _passthrough(text, context, result, log_fn=None):
        return result
    with patch("daemon._verify_and_maybe_retry", new=_passthrough):
        yield


class TestVoiceTaskDeliveryRouting:
    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon._route_query", new_callable=AsyncMock,
           return_value='Here is the answer! <!--ACTION::{"action": "set_delivery", "method": "voice"}-->')
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_voice_delivery_generates_tts(self, mock_ctx, mock_claude,
                                                  mock_log):
        mock_engine = MagicMock()
        mock_engine.transcribe_bytes.return_value = MagicMock(
            text="Answer via voice", duration=2.0, processing_time=0.3,
        )

        daemon._tasks["t1"] = {"status": "processing", "created": 0}
        with patch("whisper_engine.get_engine", return_value=mock_engine):
            await daemon._process_voice_task("t1", b"audio")

        assert daemon._tasks["t1"]["status"] == "done"
        assert len(daemon._tasks["t1"]["audio"]) > 0  # TTS generated
        assert "delivery" not in daemon._tasks["t1"]  # default voice

    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon._route_query", new_callable=AsyncMock,
           return_value='Text answer <!--ACTION::{"action": "set_delivery", "method": "sms"}-->')
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_sms_delivery_sends_text(self, mock_ctx, mock_claude,
                                             mock_log):
        mock_engine = MagicMock()
        mock_engine.transcribe_bytes.return_value = MagicMock(
            text="Text me the answer", duration=2.0, processing_time=0.3,
        )

        daemon._tasks["t1"] = {"status": "processing", "created": 0}
        with patch("whisper_engine.get_engine", return_value=mock_engine):
            await daemon._process_voice_task("t1", b"audio")

        assert daemon._tasks["t1"]["delivery"] == "sms"
        assert daemon._tasks["t1"]["audio"] == b""


class TestSmsWebhookDeliveryRouting:
    @pytest.mark.asyncio
    @patch("daemon.db.get_conn")
    @patch("daemon.log_request")
    @patch("daemon._route_query", new_callable=AsyncMock,
           return_value='Voice answer <!--ACTION::{"action": "set_delivery", "method": "voice"}-->')
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_sms_to_voice_routing(self, mock_ctx, mock_claude,
                                          mock_log, mock_conn):
        mc = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        await daemon._process_sms("+15551234567", "Tell me via voice", [])

        # execute_delivery should have been called with hint="voice"
        from delivery_engine import execute_delivery
        execute_delivery.assert_called_once()
        call_kwargs = execute_delivery.call_args[1]
        assert call_kwargs.get("hint") == "voice"
        assert call_kwargs.get("source") == "sms"
        assert call_kwargs.get("sms_target") == "+15551234567"


class TestFileTaskDeliveryRouting:
    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon._route_query", new_callable=AsyncMock, return_value="Analysis")
    @patch("daemon.build_request_context", new_callable=AsyncMock, return_value="")
    async def test_default_voice_delivery(self, mock_ctx, mock_claude,
                                            mock_log):
        daemon._tasks["t1"] = {"status": "processing", "created": 0}
        await daemon._process_file_task(
            "t1", b"img", "photo.jpg", "image/jpeg", "What's this?",
        )
        assert daemon._tasks["t1"]["status"] == "done"
        assert len(daemon._tasks["t1"]["audio"]) > 0  # voice is default
