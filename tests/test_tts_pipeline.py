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

    @patch("tts._get_kokoro")
    def test_strips_markdown_before_kokoro(self, mock_get_kokoro):
        import numpy as np
        mock_kokoro = MagicMock()
        mock_kokoro.create.return_value = (np.zeros(100, dtype=np.float32), 24000)
        mock_get_kokoro.return_value = mock_kokoro

        tts._tts_sync("**bold** and *italic*")
        text_sent = mock_kokoro.create.call_args[0][0]
        assert "**" not in text_sent
        assert "*" not in text_sent
        assert text_sent == "bold and italic"


class TestPrepareForSpeech:
    """Tests for markdown stripping before TTS."""

    def test_strips_bold(self):
        assert tts._prepare_for_speech("**hello**") == "hello"

    def test_strips_italic(self):
        assert tts._prepare_for_speech("*hello*") == "hello"

    def test_strips_bold_and_italic(self):
        assert tts._prepare_for_speech("**bold** and *italic*") == "bold and italic"

    def test_strips_bold_italic_combined(self):
        assert tts._prepare_for_speech("***bold italic***") == "bold italic"

    def test_strips_inline_code(self):
        assert tts._prepare_for_speech("use `foo` here") == "use foo here"

    def test_strips_code_blocks(self):
        text = "before\n```python\nprint('hi')\n```\nafter"
        assert tts._prepare_for_speech(text) == "before. after"

    def test_strips_headings(self):
        assert tts._prepare_for_speech("## Summary\nContent") == "Summary Content"

    def test_strips_bullet_points(self):
        text = "Items:\n- first\n- second"
        assert tts._prepare_for_speech(text) == "Items: first second"

    def test_strips_numbered_list(self):
        text = "Steps:\n1. first\n2. second"
        assert tts._prepare_for_speech(text) == "Steps: first second"

    def test_strips_markdown_links(self):
        assert tts._prepare_for_speech("[click here](http://example.com)") == "click here"

    def test_paragraph_breaks_become_pauses(self):
        text = "First paragraph.\n\nSecond paragraph."
        assert tts._prepare_for_speech(text) == "First paragraph.. Second paragraph."

    def test_normalizes_whitespace(self):
        assert tts._prepare_for_speech("too   many   spaces") == "too many spaces"

    def test_plain_text_unchanged(self):
        text = "Hello, how are you today?"
        assert tts._prepare_for_speech(text) == text

    def test_real_world_response(self):
        """The response that triggered the original bug."""
        text = (
            "OK so you've got solid nutrition data. Here's what I have:\n\n"
            "**Smoothie ingredients:** frozen banana, blueberries, cherries\n\n"
            "**Dinner staples:** Spanish rice, canned salmon, broccoli\n\n"
            "**Snacks:** Amy's burritos, Chomps beef sticks"
        )
        result = tts._prepare_for_speech(text)
        assert "**" not in result
        assert "Smoothie ingredients:" in result
        assert "Dinner staples:" in result

    def test_empty_string(self):
        assert tts._prepare_for_speech("") == ""

    def test_asterisk_bullet_not_confused_with_italic(self):
        text = "List:\n* item one\n* item two"
        result = tts._prepare_for_speech(text)
        assert "item one" in result
        assert "item two" in result
        assert "*" not in result


class TestGenerateTts:
    @pytest.mark.asyncio
    @patch("tts._tts_sync", return_value=b"wav bytes")
    async def test_runs_in_thread(self, mock_sync):
        result = await tts._generate_tts("Hello")
        assert result == b"wav bytes"
        mock_sync.assert_called_once_with("Hello")
