"""Tests for end-to-end delivery routing in daemon.py.

Verifies the full path: request → Claude response with set_delivery
→ handler routes to TTS+push_audio or SMS.

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


class TestVoiceTaskDeliveryRouting:
    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon._generate_tts", new_callable=AsyncMock, return_value=b"wav")
    @patch("daemon.ask_claude", new_callable=AsyncMock,
           return_value='Here is the answer! <!--ACTION::{"action": "set_delivery", "method": "voice"}-->')
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_voice_delivery_generates_tts(self, mock_ctx, mock_claude,
                                                  mock_tts, mock_log):
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
    @patch("daemon.sms.send_to_owner")
    @patch("daemon.ask_claude", new_callable=AsyncMock,
           return_value='Text answer <!--ACTION::{"action": "set_delivery", "method": "sms"}-->')
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_sms_delivery_sends_text(self, mock_ctx, mock_claude,
                                             mock_sms, mock_log):
        mock_engine = MagicMock()
        mock_engine.transcribe_bytes.return_value = MagicMock(
            text="Text me the answer", duration=2.0, processing_time=0.3,
        )

        daemon._tasks["t1"] = {"status": "processing", "created": 0}
        with patch("whisper_engine.get_engine", return_value=mock_engine):
            await daemon._process_voice_task("t1", b"audio")

        mock_sms.assert_called_once()
        assert daemon._tasks["t1"]["delivery"] == "sms"
        assert daemon._tasks["t1"]["audio"] == b""


class TestSmsWebhookDeliveryRouting:
    @pytest.mark.asyncio
    @patch("daemon.db.get_conn")
    @patch("daemon.log_request")
    @patch("daemon._generate_tts", new_callable=AsyncMock, return_value=b"wav")
    @patch("daemon.ask_claude", new_callable=AsyncMock,
           return_value='Voice answer <!--ACTION::{"action": "set_delivery", "method": "voice"}-->')
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_sms_to_voice_routing(self, mock_ctx, mock_claude,
                                          mock_tts, mock_log,
                                          mock_conn):
        mc = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_push_mod = MagicMock()
        mock_push_mod.push_audio.return_value = True

        with patch.dict("sys.modules", {"push_audio": mock_push_mod}):
            await daemon._process_sms("+15551234567", "Tell me via voice", [])

        # Should have generated TTS
        mock_tts.assert_called_once()


class TestFileTaskDeliveryRouting:
    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon._generate_tts", new_callable=AsyncMock, return_value=b"wav")
    @patch("daemon.ask_claude", new_callable=AsyncMock, return_value="Analysis")
    @patch("daemon.build_request_context", new_callable=AsyncMock, return_value="")
    async def test_default_voice_delivery(self, mock_ctx, mock_claude,
                                            mock_tts, mock_log):
        daemon._tasks["t1"] = {"status": "processing", "created": 0}
        await daemon._process_file_task(
            "t1", b"img", "photo.jpg", "image/jpeg", "What's this?",
        )
        assert daemon._tasks["t1"]["status"] == "done"
        mock_tts.assert_called_once()  # voice is default for file tasks
