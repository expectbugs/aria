"""Tests for push_audio.py — audio push to phone.

SAFETY: All HTTP requests to the phone are mocked.
"""

from unittest.mock import patch, MagicMock

import push_audio


class TestPushAudio:
    def test_success(self, tmp_path):
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"RIFF fake wav data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("push_audio.httpx.post", return_value=mock_resp):
            assert push_audio.push_audio(str(wav)) is True

    def test_file_not_found(self):
        assert push_audio.push_audio("/nonexistent/audio.wav") is False

    def test_phone_unreachable(self, tmp_path):
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"RIFF data")

        import httpx
        with patch("push_audio.httpx.post", side_effect=httpx.ConnectError("")):
            assert push_audio.push_audio(str(wav)) is False

    def test_phone_error_status(self, tmp_path):
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"RIFF data")

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal error"

        with patch("push_audio.httpx.post", return_value=mock_resp):
            assert push_audio.push_audio(str(wav)) is False

    def test_with_caption(self, tmp_path):
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"RIFF data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("push_audio.httpx.post", return_value=mock_resp) as mock_post:
            push_audio.push_audio(str(wav), caption="Timer done")
            kwargs = mock_post.call_args
            assert kwargs[1]["data"]["caption"] == "Timer done"

    def test_url_uses_config(self, tmp_path):
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"RIFF data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("push_audio.httpx.post", return_value=mock_resp) as mock_post, \
             patch("push_audio.config") as mock_config:
            mock_config.PHONE_IP = "100.1.2.3"
            mock_config.PHONE_PORT = 9999
            push_audio.push_audio(str(wav))
            url = mock_post.call_args[0][0]
            assert "100.1.2.3:9999" in url
            assert "/audio" in url
