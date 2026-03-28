"""Tests for daemon.py background task workers.

These test _process_task, _process_file_task, _process_voice_task, _process_sms
directly, verifying the full async pipeline with mocked Claude/TTS/SMS.

SAFETY: Claude CLI never spawned. No SMS sent. No audio pushed.
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import daemon
from tests.helpers import make_action_result


@pytest.fixture(autouse=True)
def reset_tasks():
    daemon._tasks.clear()
    yield
    daemon._tasks.clear()


@pytest.fixture(autouse=True)
def _bypass_verification():
    """Worker tests focus on delivery/task mechanics, not verification.
    Make _verify_and_maybe_retry a passthrough that returns its input."""
    async def _passthrough(text, context, result, log_fn=None):
        return result
    with patch("daemon._verify_and_maybe_retry", new=_passthrough):
        yield


class TestProcessTask:
    @pytest.mark.asyncio
    @patch("daemon._generate_tts", new_callable=AsyncMock, return_value=b"wavdata")
    @patch("daemon.process_actions", return_value=make_action_result(clean_response="Hello!"))
    @patch("daemon.ask", new_callable=AsyncMock)
    async def test_success(self, mock_ask, mock_actions, mock_tts):
        mock_ask.return_value = MagicMock(response="Hello!")
        daemon._tasks["t1"] = {"status": "processing", "created": 0}

        req = daemon.AskRequest(text="test")
        request = MagicMock()
        await daemon._process_task("t1", req, request)

        assert daemon._tasks["t1"]["status"] == "done"
        assert daemon._tasks["t1"]["audio"] == b"wavdata"

    @pytest.mark.asyncio
    @patch("daemon.ask", new_callable=AsyncMock, side_effect=RuntimeError("Claude down"))
    async def test_claude_error(self, mock_ask):
        daemon._tasks["t1"] = {"status": "processing", "created": 0}

        req = daemon.AskRequest(text="test")
        await daemon._process_task("t1", req, MagicMock())

        assert daemon._tasks["t1"]["status"] == "error"
        assert "Claude down" in daemon._tasks["t1"]["error"]

    @pytest.mark.asyncio
    @patch("daemon._generate_tts", new_callable=AsyncMock,
           side_effect=RuntimeError("TTS failed"))
    @patch("daemon.process_actions", return_value=make_action_result(clean_response="OK"))
    @patch("daemon.ask", new_callable=AsyncMock)
    async def test_tts_error(self, mock_ask, mock_actions, mock_tts):
        mock_ask.return_value = MagicMock(response="OK")
        daemon._tasks["t1"] = {"status": "processing", "created": 0}

        await daemon._process_task("t1", daemon.AskRequest(text="test"), MagicMock())
        assert daemon._tasks["t1"]["status"] == "error"


class TestProcessFileTask:
    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon._generate_tts", new_callable=AsyncMock, return_value=b"wav")
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock, return_value="Analysis done")
    @patch("daemon.build_request_context", new_callable=AsyncMock, return_value="ctx")
    async def test_image_file(self, mock_ctx, mock_claude, mock_actions,
                                mock_tts, mock_log):
        mock_actions.return_value = make_action_result(clean_response="Analysis done")
        daemon._tasks["t1"] = {"status": "processing", "created": 0}

        await daemon._process_file_task(
            "t1", b"fake jpeg", "photo.jpg", "image/jpeg", "What's this?",
        )
        assert daemon._tasks["t1"]["status"] == "done"
        mock_ctx.assert_called_once()
        # is_image should be True for image mime type
        assert mock_ctx.call_args[1]["is_image"] is True

    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon.sms.send_long_to_owner")
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock, return_value="Result")
    @patch("daemon.build_request_context", new_callable=AsyncMock, return_value="")
    async def test_sms_delivery(self, mock_ctx, mock_claude, mock_actions,
                                  mock_sms, mock_log):
        # Simulate set_delivery metadata
        def actions_side_effect(resp, metadata=None, **kw):
            if metadata is not None:
                metadata["delivery"] = "sms"
            return make_action_result(clean_response="Result")
        mock_actions.side_effect = actions_side_effect

        daemon._tasks["t1"] = {"status": "processing", "created": 0}
        await daemon._process_file_task(
            "t1", b"data", "doc.pdf", "application/pdf", "Summarize",
        )
        mock_sms.assert_called_once_with("Result")
        assert daemon._tasks["t1"]["delivery"] == "sms"


class TestProcessVoiceTask:
    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon._generate_tts", new_callable=AsyncMock, return_value=b"wav")
    @patch("daemon.process_actions", return_value=make_action_result(clean_response="Hello!"))
    @patch("daemon._route_query", new_callable=AsyncMock, return_value="Hello!")
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_full_pipeline(self, mock_ctx, mock_claude, mock_actions,
                                   mock_tts, mock_log):
        mock_engine = MagicMock()
        mock_engine.transcribe_bytes.return_value = MagicMock(
            text="What time is it?", duration=2.0, processing_time=0.3,
        )

        daemon._tasks["t1"] = {"status": "processing", "created": 0}
        with patch("whisper_engine.get_engine", return_value=mock_engine):
            await daemon._process_voice_task("t1", b"audio bytes")

        assert daemon._tasks["t1"]["status"] == "done"
        assert daemon._tasks["t1"]["transcript"] == "What time is it?"
        assert daemon._tasks["t1"]["audio"] == b"wav"

    @pytest.mark.asyncio
    async def test_empty_transcript(self):
        mock_engine = MagicMock()
        mock_engine.transcribe_bytes.return_value = MagicMock(
            text="  ", duration=1.0, processing_time=0.2,
        )

        daemon._tasks["t1"] = {"status": "processing", "created": 0}
        with patch("whisper_engine.get_engine", return_value=mock_engine):
            await daemon._process_voice_task("t1", b"silence")

        assert daemon._tasks["t1"]["status"] == "error"
        assert "No speech" in daemon._tasks["t1"]["error"]

    @pytest.mark.asyncio
    @patch("daemon.log_request")
    @patch("daemon.sms.send_long_to_owner")
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock, return_value="SMS response")
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_sms_delivery_routing(self, mock_ctx, mock_claude,
                                          mock_actions, mock_sms, mock_log):
        def actions_with_sms(resp, metadata=None, **kw):
            if metadata is not None:
                metadata["delivery"] = "sms"
            return make_action_result(clean_response="SMS response")
        mock_actions.side_effect = actions_with_sms

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


class TestProcessSms:
    @pytest.mark.asyncio
    @patch("daemon.db.get_conn")
    @patch("daemon.log_request")
    @patch("daemon.sms.send_sms")
    @patch("daemon.process_actions", return_value=make_action_result(clean_response="Got it!"))
    @patch("daemon._route_query", new_callable=AsyncMock, return_value="Got it!")
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_text_only_sms(self, mock_ctx, mock_claude, mock_actions,
                                   mock_send, mock_log, mock_conn):
        mc = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        await daemon._process_sms("+15551234567", "Hello ARIA", [])
        mock_send.assert_called_once()
        assert "Got it!" in mock_send.call_args[0][1]

    @pytest.mark.asyncio
    @patch("daemon.db.get_conn")
    @patch("daemon.log_request")
    @patch("daemon.sms.send_long_sms")
    @patch("daemon.process_actions", return_value=make_action_result(clean_response="I see the label."))
    @patch("daemon._route_query", new_callable=AsyncMock, return_value="I see the label.")
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_long_response_split(self, mock_ctx, mock_claude,
                                       mock_actions, mock_send_long,
                                       mock_log, mock_conn):
        mc = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        long_response = "x" * 2000
        mock_actions.return_value = make_action_result(clean_response=long_response)

        await daemon._process_sms("+15551234567", "test", [])
        mock_send_long.assert_called_once_with("+15551234567", long_response)

    @pytest.mark.asyncio
    @patch("daemon.db.get_conn")
    @patch("daemon.log_request")
    @patch("daemon.sms.send_sms")
    @patch("daemon.process_actions")
    @patch("daemon._route_query", new_callable=AsyncMock,
           side_effect=RuntimeError("Claude error"))
    @patch("daemon._get_context_for_text", new_callable=AsyncMock, return_value="")
    async def test_error_sends_apology(self, mock_ctx, mock_claude,
                                         mock_actions, mock_send,
                                         mock_log, mock_conn):
        mc = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        await daemon._process_sms("+15551234567", "test", [])
        # Should send an error message
        assert mock_send.call_count >= 1
        assert "wrong" in mock_send.call_args[0][1].lower()
