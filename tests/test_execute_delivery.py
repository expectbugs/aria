"""Tests for delivery_engine.execute_delivery() — the async delivery dispatch.

Tests each delivery method path (voice, sms, image, defer) with mocked
dependencies. Verifies single get_user_state() call, proper fallbacks,
and temp file cleanup.

SAFETY: TTS, push_audio, push_image, SMS all mocked. No real delivery.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import config
from delivery_engine import DeliveryDecision, UserState, execute_delivery


def _mock_state():
    return UserState(
        location="home", activity="available",
        channels=["voice", "image", "sms"],
        battery=85, location_fresh=True,
    )


def _decision(method):
    return DeliveryDecision(method=method, reason=f"test: {method}")


@pytest.fixture(autouse=True)
def _mock_engine():
    """Mock evaluate/log/state so execute_delivery only tests dispatch logic."""
    with patch("delivery_engine.get_user_state", return_value=_mock_state()) as mock_gus, \
         patch("delivery_engine.evaluate") as mock_eval, \
         patch("delivery_engine.log_decision") as mock_log, \
         patch("delivery_engine.queue_deferred") as mock_defer:
        # Store on the fixture for tests to configure
        mock_eval.return_value = _decision("voice")
        yield {
            "get_user_state": mock_gus,
            "evaluate": mock_eval,
            "log_decision": mock_log,
            "queue_deferred": mock_defer,
        }


class TestVoiceDelivery:
    @pytest.mark.asyncio
    @patch("push_audio.push_audio", return_value=True)
    @patch("tts._generate_tts", new_callable=AsyncMock, return_value=b"fake_wav")
    async def test_voice_push_true_generates_tts_and_pushes(
            self, mock_tts, mock_push, _mock_engine, tmp_path):
        _mock_engine["evaluate"].return_value = _decision("voice")
        with patch.object(config, "DATA_DIR", tmp_path):
            result = await execute_delivery("Hello", push_voice=True)

        assert result["method"] == "voice"
        assert result["audio"] == b"fake_wav"
        mock_tts.assert_called_once_with("Hello")
        mock_push.assert_called_once()
        # Temp file should be cleaned up
        assert list(tmp_path.glob("voice_*.wav")) == []

    @pytest.mark.asyncio
    @patch("tts._generate_tts", new_callable=AsyncMock, return_value=b"fake_wav")
    async def test_voice_push_false_returns_audio_without_pushing(
            self, mock_tts, _mock_engine):
        _mock_engine["evaluate"].return_value = _decision("voice")
        result = await execute_delivery("Hello", push_voice=False)

        assert result["method"] == "voice"
        assert result["audio"] == b"fake_wav"
        mock_tts.assert_called_once()

    @pytest.mark.asyncio
    @patch("sms.send_long_sms")
    @patch("push_audio.push_audio", return_value=False)
    @patch("tts._generate_tts", new_callable=AsyncMock, return_value=b"fake_wav")
    async def test_voice_push_failure_falls_back_to_sms(
            self, mock_tts, mock_push, mock_sms, _mock_engine, tmp_path):
        _mock_engine["evaluate"].return_value = _decision("voice")
        with patch.object(config, "DATA_DIR", tmp_path), \
             patch.object(config, "OWNER_PHONE_NUMBER", "+10000000000"):
            result = await execute_delivery("Hello", push_voice=True)

        assert result["method"] == "sms"
        mock_sms.assert_called_once_with("+10000000000", "Hello")

    @pytest.mark.asyncio
    @patch("tts._generate_tts", new_callable=AsyncMock,
           side_effect=RuntimeError("TTS broke"))
    async def test_tts_failure_logs_error_returns_empty_audio(
            self, mock_tts, _mock_engine):
        _mock_engine["evaluate"].return_value = _decision("voice")
        result = await execute_delivery("Hello", push_voice=False)

        assert result["audio"] == b""
        assert result["method"] == "voice"


class TestSmsDelivery:
    @pytest.mark.asyncio
    @patch("sms.send_long_sms")
    async def test_sms_sends_to_owner_by_default(self, mock_sms, _mock_engine):
        _mock_engine["evaluate"].return_value = _decision("sms")
        with patch.object(config, "OWNER_PHONE_NUMBER", "+10000000000"):
            result = await execute_delivery("Hello")

        assert result["method"] == "sms"
        mock_sms.assert_called_once_with("+10000000000", "Hello")

    @pytest.mark.asyncio
    @patch("sms.send_long_sms")
    async def test_sms_sends_to_custom_target(self, mock_sms, _mock_engine):
        _mock_engine["evaluate"].return_value = _decision("sms")
        result = await execute_delivery("Hello", sms_target="+15551234567")

        mock_sms.assert_called_once_with("+15551234567", "Hello")

    @pytest.mark.asyncio
    @patch("sms.send_long_sms", side_effect=RuntimeError("SMS broke"))
    async def test_sms_failure_logs_error_no_crash(self, mock_sms, _mock_engine):
        _mock_engine["evaluate"].return_value = _decision("sms")
        with patch.object(config, "OWNER_PHONE_NUMBER", "+10000000000"):
            result = await execute_delivery("Hello")

        assert result["method"] == "sms"
        assert result["audio"] == b""


class TestImageDelivery:
    @pytest.mark.asyncio
    @patch("os.unlink")
    @patch("push_image.push_image", return_value=True)
    @patch("sms._render_sms_image", return_value="/tmp/test_img.png")
    async def test_image_automated_source_pushes_via_tasker(
            self, mock_render, mock_push, mock_unlink, _mock_engine):
        """Automated sources (nudge, timer, etc.) push via Tasker — free, no MMS."""
        _mock_engine["evaluate"].return_value = _decision("image")
        result = await execute_delivery("Hello", source="nudge")

        assert result["method"] == "image"
        mock_render.assert_called_once_with("Hello", header="ARIA")
        mock_push.assert_called_once()
        mock_unlink.assert_called_once_with("/tmp/test_img.png")

    @pytest.mark.asyncio
    @patch("os.unlink")
    @patch("sms.send_image_mms", return_value="msg_test")
    @patch("sms._render_sms_image", return_value="/tmp/test_img.png")
    async def test_image_user_source_sends_mms(
            self, mock_render, mock_mms, mock_unlink, _mock_engine):
        """User-initiated conversation (sms, voice, file, cli) uses MMS."""
        _mock_engine["evaluate"].return_value = _decision("image")
        result = await execute_delivery("Hello", source="sms")

        assert result["method"] == "image"
        mock_render.assert_called_once_with("Hello", header="ARIA")
        mock_mms.assert_called_once()
        mock_unlink.assert_called_once_with("/tmp/test_img.png")

    @pytest.mark.asyncio
    @patch("sms._render_sms_image", side_effect=RuntimeError("render broke"))
    async def test_image_failure_logs_error_no_crash(self, mock_render,
                                                       _mock_engine):
        _mock_engine["evaluate"].return_value = _decision("image")
        result = await execute_delivery("Hello")

        assert result["method"] == "image"
        assert result["audio"] == b""


class TestDeferDelivery:
    @pytest.mark.asyncio
    async def test_defer_queues_deferred(self, _mock_engine):
        _mock_engine["evaluate"].return_value = _decision("defer")
        result = await execute_delivery(
            "Hello", content_type="response", priority="normal", source="voice")

        assert result["method"] == "defer"
        _mock_engine["queue_deferred"].assert_called_once_with(
            "Hello", "response", "normal", "voice", "test: defer")


class TestStateReuse:
    @pytest.mark.asyncio
    @patch("sms.send_long_sms")
    async def test_single_get_user_state_call(self, mock_sms, _mock_engine):
        """evaluate and log_decision should receive the same state object."""
        state = _mock_state()
        _mock_engine["get_user_state"].return_value = state
        _mock_engine["evaluate"].return_value = _decision("sms")

        with patch.object(config, "OWNER_PHONE_NUMBER", "+10000000000"):
            await execute_delivery("Hello")

        _mock_engine["get_user_state"].assert_called_once()
        # Both evaluate and log_decision should get the same _state
        eval_state = _mock_engine["evaluate"].call_args[1].get("_state")
        log_state = _mock_engine["log_decision"].call_args[1].get("_state")
        assert eval_state is state
        assert log_state is state
