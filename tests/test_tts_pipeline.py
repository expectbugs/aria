"""Tests for the TTS pipeline — Kokoro model caching and audio generation.

SAFETY: Kokoro model is not loaded. All TTS calls are mocked.
"""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import tts


class TestGetKokoro:
    def test_caches_model(self):
        mock_instance = MagicMock()
        tts._kokoro = None
        # Directly set the cache to test caching logic
        tts._kokoro = mock_instance
        result = tts._get_kokoro()
        assert result is mock_instance
        tts._kokoro = None  # cleanup

    def test_returns_cached_on_second_call(self):
        mock = MagicMock()
        tts._kokoro = mock
        assert tts._get_kokoro() is mock
        assert tts._get_kokoro() is mock  # same object
        tts._kokoro = None  # cleanup


class TestTtsSync:
    @patch("tts._get_kokoro")
    def test_generates_wav(self, mock_get_kokoro):
        import numpy as np
        mock_kokoro = MagicMock()
        mock_kokoro.create.return_value = (
            np.zeros(16000, dtype=np.float32),  # 1s of silence
            24000,  # sample rate
        )
        mock_get_kokoro.return_value = mock_kokoro

        result = tts._tts_sync("Hello world")
        assert isinstance(result, bytes)
        assert len(result) > 0
        # Should be a WAV file (starts with RIFF header)
        assert result[:4] == b"RIFF"

        mock_kokoro.create.assert_called_once()
        call_kwargs = mock_kokoro.create.call_args
        assert call_kwargs[1]["voice"] == "af_heart"

    @patch("tts._get_kokoro")
    def test_uses_config_voice(self, mock_get_kokoro):
        import numpy as np
        mock_kokoro = MagicMock()
        mock_kokoro.create.return_value = (np.zeros(100, dtype=np.float32), 24000)
        mock_get_kokoro.return_value = mock_kokoro

        tts._tts_sync("test")
        # Should use config.KOKORO_VOICE
        call_kwargs = mock_kokoro.create.call_args
        assert "voice" in call_kwargs[1]


class TestGenerateTts:
    @pytest.mark.asyncio
    @patch("tts._tts_sync", return_value=b"wav bytes")
    async def test_runs_in_thread(self, mock_sync):
        result = await tts._generate_tts("Hello")
        assert result == b"wav bytes"
        mock_sync.assert_called_once_with("Hello")
