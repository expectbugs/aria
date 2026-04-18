"""Tests for tick._sync_deliver() — synchronous delivery dispatch.

Tests each method path (voice via daemon HTTP, SMS, image) with mocked
external dependencies. Verifies voice→SMS fallback and temp file cleanup.

SAFETY: httpx, push_audio, push_image, SMS all mocked. No real delivery.
"""

from unittest.mock import patch, MagicMock

import pytest

import tick


@pytest.fixture(autouse=True)
def _mock_sms():
    with patch.object(tick, "sms") as mock:
        yield mock


class TestSyncDeliverVoice:
    def test_voice_success(self, _mock_sms, tmp_path):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_wav_data"

        with patch("httpx.post", return_value=mock_resp), \
             patch("push_audio.push_audio", return_value=True), \
             patch.object(tick.config, "DATA_DIR", tmp_path):
            success, method = tick._sync_deliver("Hello", "voice")

        assert success is True
        assert method == "voice"
        _mock_sms.send_to_owner.assert_not_called()
        # Temp file should be cleaned up
        assert list(tmp_path.glob("voice_*.wav")) == []

    def test_voice_push_failure_falls_back_to_sms(self, _mock_sms, tmp_path):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_wav_data"

        with patch("httpx.post", return_value=mock_resp), \
             patch("push_audio.push_audio", return_value=False), \
             patch.object(tick.config, "DATA_DIR", tmp_path):
            success, method = tick._sync_deliver("Hello", "voice")

        assert success is True
        assert method == "sms"
        _mock_sms.send_to_owner.assert_called_once_with("Hello")

    def test_tts_http_failure_falls_back_to_sms(self, _mock_sms):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("httpx.post", return_value=mock_resp):
            success, method = tick._sync_deliver("Hello", "voice")

        assert success is True
        assert method == "sms"
        _mock_sms.send_to_owner.assert_called_once_with("Hello")

    def test_voice_with_custom_sms_target(self, _mock_sms):
        """When voice fails and sms_target is set, fallback SMS goes to that target."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("httpx.post", return_value=mock_resp):
            success, method = tick._sync_deliver("Hello", "voice",
                                                  sms_target="+15551234567")

        assert success is True
        assert method == "sms"
        _mock_sms.send_long_sms.assert_called_once_with("+15551234567", "Hello")

    def test_voice_network_error_falls_back_to_sms(self, _mock_sms):
        with patch("httpx.post", side_effect=Exception("network down")):
            success, method = tick._sync_deliver("Hello", "voice")

        assert success is True
        assert method == "sms"


class TestSyncDeliverSms:
    def test_sms_to_owner(self, _mock_sms):
        success, method = tick._sync_deliver("Hello", "sms")

        assert success is True
        assert method == "sms"
        _mock_sms.send_to_owner.assert_called_once_with("Hello")

    def test_sms_to_custom_target(self, _mock_sms):
        success, method = tick._sync_deliver("Hello", "sms",
                                              sms_target="+15551234567")

        assert success is True
        assert method == "sms"
        _mock_sms.send_long_sms.assert_called_once_with("+15551234567", "Hello")

    def test_sms_failure(self, _mock_sms):
        _mock_sms.send_to_owner.side_effect = RuntimeError("SMS broke")
        success, method = tick._sync_deliver("Hello", "sms")

        assert success is False
        assert method == "sms"


class TestSyncDeliverImage:
    def test_image_renders_and_pushes_via_tasker(self, _mock_sms):
        """tick.py image deliveries are automated triggers → Tasker push_image (free)."""
        with patch("sms._render_sms_image", return_value="/tmp/test.png") as mock_render, \
             patch("push_image.push_image", return_value=True) as mock_push, \
             patch("os.unlink") as mock_unlink:
            success, method = tick._sync_deliver("Hello", "image")

        assert success is True
        assert method == "image"
        mock_render.assert_called_once()
        mock_push.assert_called_once()
        mock_unlink.assert_called_once()

    def test_image_failure(self, _mock_sms):
        with patch("sms._render_sms_image", side_effect=RuntimeError("render broke")):
            success, method = tick._sync_deliver("Hello", "image")

        assert success is False
        assert method == "image"


class TestSyncDeliverUnknown:
    def test_unknown_method_returns_false(self, _mock_sms):
        success, method = tick._sync_deliver("Hello", "glasses")

        assert success is False
        assert method == "glasses"
