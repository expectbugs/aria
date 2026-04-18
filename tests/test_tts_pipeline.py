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
        # Single newline after heading text gets a comma (Kokoro split point)
        assert tts._prepare_for_speech("## Summary\nContent") == "Summary, Content"

    def test_strips_bullet_points(self):
        # Colon-ending line keeps space; subsequent items get commas
        text = "Items:\n- first\n- second"
        assert tts._prepare_for_speech(text) == "Items: first, second"

    def test_strips_numbered_list(self):
        # Same pattern: colon → space, items → commas
        text = "Steps:\n1. first\n2. second"
        assert tts._prepare_for_speech(text) == "Steps: first, second"

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

    def test_strips_parentheses(self):
        """Parentheses produce audible artifacts in Kokoro — strip them."""
        assert tts._prepare_for_speech("I think (maybe) so") == "I think maybe so"

    def test_strips_system_note_parentheses(self):
        """The system note appended by claim detector should not vocalize parens."""
        text = "Done.\n\n(System note: ARIA claimed to store data but no ACTION blocks were emitted.)"
        result = tts._prepare_for_speech(text)
        assert "(" not in result
        assert ")" not in result
        assert "System note:" in result

    def test_markdown_links_still_work_with_paren_stripping(self):
        """Link regex consumes parens before the paren stripper runs."""
        assert tts._prepare_for_speech("[click here](http://example.com)") == "click here"

    def test_empty_string(self):
        assert tts._prepare_for_speech("") == ""

    def test_asterisk_bullet_not_confused_with_italic(self):
        text = "List:\n* item one\n* item two"
        result = tts._prepare_for_speech(text)
        assert "item one" in result
        assert "item two" in result
        assert "*" not in result

    @patch("push_image.push_image", return_value=True)
    @patch("sms._render_sms_image", return_value="/tmp/fake.png")
    def test_strips_action_blocks(self, mock_render, mock_push):
        text = 'Hello <!--ACTION::{"action":"set_delivery","method":"voice"}--> world'
        result = tts._prepare_for_speech(text)
        assert "ACTION" not in result
        assert "set_delivery" not in result
        assert "Hello" in result
        assert "world" in result

    @patch("push_image.push_image", return_value=True)
    @patch("sms._render_sms_image", return_value="/tmp/fake.png")
    def test_strips_multiline_action_blocks(self, mock_render, mock_push):
        text = 'Before\n<!--ACTION::{"action":"log_health",\n"date":"2026-03-25"}-->\nAfter'
        result = tts._prepare_for_speech(text)
        assert "ACTION" not in result
        assert "log_health" not in result

    @patch("push_image.push_image", return_value=True)
    @patch("sms._render_sms_image", return_value="/tmp/fake.png")
    def test_action_block_preserves_surrounding_text(self, mock_render, mock_push):
        text = 'Timer set!<!--ACTION::{"action":"set_timer","minutes":30}-->'
        result = tts._prepare_for_speech(text)
        assert "Timer set" in result
        assert "ACTION" not in result

    @patch("push_image.push_image", return_value=True)
    @patch("sms._render_sms_image", return_value="/tmp/fake.png")
    def test_action_block_triggers_warning_log(self, mock_render, mock_push, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="aria.tts"):
            tts._prepare_for_speech('Hi <!--ACTION::{"action":"test"}-->')
        assert "ACTION blocks reached TTS" in caplog.text

    @patch("push_image.push_image", return_value=True)
    @patch("sms._render_sms_image", return_value="/tmp/fake_alert.png")
    def test_action_block_triggers_tasker_alert(self, mock_render, mock_push):
        tts._prepare_for_speech('Hi <!--ACTION::{"action":"test"}-->')
        mock_render.assert_called_once()
        assert "BUG" in mock_render.call_args[0][0]
        mock_push.assert_called_once()


class TestNewlinesToCommas:
    """Tests for the single-newline → comma conversion (Kokoro split points)."""

    def test_data_listing_gets_commas(self):
        """Data items on separate lines get comma separators."""
        text = "Calories: 1435\nProtein: 97g\nFiber: 19g"
        result = tts._prepare_for_speech(text)
        assert result == "Calories: 1435, Protein: 97g, Fiber: 19g"

    def test_line_ending_with_period_no_comma(self):
        """Lines ending with periods don't get redundant commas."""
        text = "Great job today.\nProtein was solid."
        result = tts._prepare_for_speech(text)
        assert result == "Great job today. Protein was solid."

    def test_line_ending_with_exclamation_no_comma(self):
        text = "Nice work!\nKeep it up"
        result = tts._prepare_for_speech(text)
        assert result == "Nice work! Keep it up"

    def test_line_ending_with_question_no_comma(self):
        text = "How are you?\nI'm fine"
        result = tts._prepare_for_speech(text)
        assert result == "How are you? I'm fine"

    def test_line_ending_with_comma_no_double(self):
        text = "First,\nsecond"
        result = tts._prepare_for_speech(text)
        assert result == "First, second"

    def test_line_ending_with_colon_no_comma(self):
        """Colons are natural pause points — don't add a comma after them."""
        text = "Summary:\nItem A\nItem B"
        result = tts._prepare_for_speech(text)
        assert result == "Summary: Item A, Item B"

    def test_paragraph_break_still_period(self):
        """Double newlines still become periods (paragraph breaks)."""
        text = "First paragraph.\n\nSecond paragraph."
        result = tts._prepare_for_speech(text)
        assert result == "First paragraph.. Second paragraph."

    def test_mixed_paragraphs_and_lists(self):
        """Paragraph breaks get periods, list items get commas."""
        text = "Your totals:\n\nCalories: 1435\nProtein: 97g\n\nGood job."
        result = tts._prepare_for_speech(text)
        assert result == "Your totals:. Calories: 1435, Protein: 97g. Good job."

    def test_stripped_bullets_get_commas(self):
        """Markdown bullets stripped, then newlines between items become commas."""
        text = "Your meals:\n- Smoothie for breakfast\n- Factor meal for lunch\n- Salmon for dinner"
        result = tts._prepare_for_speech(text)
        assert result == "Your meals: Smoothie for breakfast, Factor meal for lunch, Salmon for dinner"


class TestEnsureTtsSplits:
    """Tests for the safety-net that breaks up long punctuation-free runs."""

    def test_short_text_unchanged(self):
        text = "Hello, how are you today?"
        assert tts._ensure_tts_splits(text) == text

    def test_text_with_punctuation_unchanged(self):
        """Text with frequent punctuation is never modified."""
        text = "First sentence. Second sentence! Third one? Yes, it is; certainly."
        assert tts._ensure_tts_splits(text) == text

    def test_long_run_gets_comma(self):
        """A 200+ char stretch without [.,!?;] gets a comma inserted."""
        # Build a string of >200 chars with no Kokoro split points
        words = ["word"] * 60  # 60 × 5 chars = 300 chars with spaces
        text = " ".join(words)
        result = tts._ensure_tts_splits(text)
        assert "," in result
        # Each resulting segment should be under 200 chars
        for segment in result.split(","):
            assert len(segment.strip()) < 200

    def test_does_not_modify_text_under_threshold(self):
        """199 chars without punctuation — just under the limit, left alone."""
        text = "a " * 99 + "a"  # 199 chars
        assert len(text) == 199
        assert tts._ensure_tts_splits(text) == text

    def test_splits_at_word_boundary(self):
        """Comma is inserted at a space, not mid-word."""
        text = "alpha " * 40  # 240 chars
        text = text.strip()
        result = tts._ensure_tts_splits(text)
        assert ",alpha" not in result  # no comma glued to a word
        assert ", " in result or " ," in result  # comma is near a space

    def test_nutrition_data_listing(self):
        """Realistic ARIA nutrition listing that previously caused truncation."""
        text = (
            "Calories: 1435 of 1600 to 1900 target "
            "Protein: 97g of 100 to 130g target "
            "Fiber: 19g of 25 to 35g target "
            "Added sugar: 2g of under 10g "
            "Saturated fat: 14g of under 15g "
            "Sodium: 1580mg of 1200 to 1800mg "
            "Total fat: 58g "
            "Total carbs: 135g"
        )
        assert len(text) > 200
        result = tts._ensure_tts_splits(text)
        assert "," in result
        # Verify no segment exceeds max_chars
        for segment in result.split(","):
            assert len(segment.strip()) < 200

    def test_no_space_in_run_no_crash(self):
        """A 200+ char string with no spaces doesn't crash — just returns as-is."""
        text = "a" * 250
        result = tts._ensure_tts_splits(text)
        assert result == text  # Can't split without spaces

    def test_multiple_long_runs(self):
        """Multiple long runs in one text all get commas."""
        run1 = " ".join(["alpha"] * 50)  # ~300 chars
        run2 = " ".join(["beta"] * 50)   # ~250 chars
        text = run1 + ". " + run2
        result = tts._ensure_tts_splits(text)
        # Both runs should have commas
        parts = result.split(".")
        assert "," in parts[0]
        assert "," in parts[1]

    def test_custom_threshold(self):
        """Can override the max_chars threshold."""
        text = "a " * 55 + "a"  # 111 chars
        assert "," not in tts._ensure_tts_splits(text, max_chars=200)
        assert "," in tts._ensure_tts_splits(text, max_chars=100)


class TestTruncationPrevention:
    """End-to-end tests verifying the full pipeline prevents Kokoro truncation."""

    def test_nutrition_response_no_truncation(self):
        """The exact response pattern that triggered truncation in production."""
        text = (
            "Alright here is your full daily breakdown.\n"
            "Calories: 1435 of 1600 to 1900 target\n"
            "Protein: 97g of 100 to 130g target\n"
            "Fiber: 19g of 25 to 35g target\n"
            "Added sugar: 2g of under 10g\n"
            "Saturated fat: 14g of under 15g\n"
            "Sodium: 1580mg of 1200 to 1800mg\n"
            "Total fat: 58g\n"
            "Total carbs: 135g\n"
            "Omega 3: 1150mg from the salmon\n"
            "Cholesterol: 118mg.\n"
            "Your calorie balance looks great today."
        )
        result = tts._prepare_for_speech(text)
        # Every data item should be separated by commas (Kokoro split points)
        assert "1435 of 1600 to 1900 target, Protein" in result
        assert "97g of 100 to 130g target, Fiber" in result
        # No run between split points should exceed 200 chars
        import re
        runs = re.split(r'[.,!?;]', result)
        for run in runs:
            assert len(run) < 200, f"Run too long ({len(run)} chars): {run[:80]}..."

    def test_markdown_list_no_truncation(self):
        """Markdown bullet list that becomes a long run after stripping."""
        text = (
            "**Your daily totals:**\n\n"
            "- Calories: 1435\n"
            "- Protein: 97g\n"
            "- Fiber: 19g\n"
            "- Sodium: 1580mg\n"
            "- Added sugar: 2g\n"
            "- Saturated fat: 14g\n"
            "- Total fat: 58g\n"
            "- Total carbs: 135g"
        )
        result = tts._prepare_for_speech(text)
        assert "**" not in result
        # Items should have commas between them
        assert ", Protein" in result or ",Protein" in result

    def test_single_line_data_dump(self):
        """Data on a single line (no newlines) — caught by the safety net."""
        text = (
            "Calories: 1435 of 1600 to 1900 Protein: 97g of 100 to 130g "
            "Fiber: 19g of 25 to 35g Added sugar: 2g of under 10g "
            "Saturated fat: 14g of under 15g Sodium: 1580mg of 1200 to 1800mg "
            "Total fat: 58g Total carbs: 135g"
        )
        result = tts._prepare_for_speech(text)
        import re
        runs = re.split(r'[.,!?;]', result)
        for run in runs:
            assert len(run) < 200, f"Run too long ({len(run)} chars): {run[:80]}..."


class TestGenerateTts:
    @pytest.mark.asyncio
    @patch("tts._tts_sync", return_value=b"wav bytes")
    async def test_runs_in_thread(self, mock_sync):
        result = await tts._generate_tts("Hello")
        assert result == b"wav bytes"
        mock_sync.assert_called_once_with("Hello")
