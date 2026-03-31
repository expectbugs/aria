"""Tests for wake_word.py — ARIA wake word detection in transcript text."""

import wake_word


class TestDetect:
    def test_basic_wake_word(self):
        detected, cmd = wake_word.detect("ARIA set a timer for 5 minutes")
        assert detected is True
        assert "timer" in cmd

    def test_hey_aria(self):
        detected, cmd = wake_word.detect("hey ARIA, what time is it")
        assert detected is True
        assert "what time" in cmd

    def test_lowercase(self):
        detected, cmd = wake_word.detect("aria set a reminder")
        assert detected is True
        assert "reminder" in cmd

    def test_mixed_case(self):
        detected, cmd = wake_word.detect("Aria, how's the weather")
        assert detected is True
        assert "weather" in cmd

    def test_with_comma(self):
        detected, cmd = wake_word.detect("ARIA, add eggs to the grocery list")
        assert detected is True
        assert "eggs" in cmd

    def test_with_period(self):
        detected, cmd = wake_word.detect("ARIA. set a timer")
        assert detected is True
        assert "timer" in cmd

    def test_no_wake_word(self):
        detected, cmd = wake_word.detect("I told Mike we'd have the proposal ready")
        assert detected is False
        assert cmd == ""

    def test_empty_string(self):
        detected, cmd = wake_word.detect("")
        assert detected is False
        assert cmd == ""

    def test_just_wake_word_no_command(self):
        # "ARIA" alone with nothing after — no command to extract
        detected, cmd = wake_word.detect("ARIA")
        assert detected is False
        assert cmd == ""

    def test_false_positive_maria(self):
        detected, cmd = wake_word.detect("Maria said she'd be here at 5")
        assert detected is False
        assert cmd == ""

    def test_false_positive_malaria(self):
        detected, cmd = wake_word.detect("The malaria vaccine is approved")
        assert detected is False
        assert cmd == ""

    def test_aria_plus_maria_in_same_text(self):
        # "Maria" is a false positive but "aria" also appears standalone
        detected, cmd = wake_word.detect("Maria said ARIA should set a timer")
        assert detected is True
        assert "timer" in cmd

    def test_mid_sentence(self):
        detected, cmd = wake_word.detect("I think aria, set a timer for 10 minutes")
        assert detected is True
        assert "timer" in cmd

    def test_colon_separator(self):
        detected, cmd = wake_word.detect("ARIA: what's the weather like")
        assert detected is True
        assert "weather" in cmd

    def test_exclamation(self):
        detected, cmd = wake_word.detect("ARIA! set a timer")
        assert detected is True
        assert "timer" in cmd

    def test_whitespace_only_after(self):
        detected, cmd = wake_word.detect("ARIA   ")
        assert detected is False

    def test_real_ambient_transcript(self):
        """Realistic ambient transcript with wake word embedded."""
        text = ("so yeah the crane needs to come in on Thursday and then "
                "ARIA, remind me to call the inspector on Friday")
        detected, cmd = wake_word.detect(text)
        assert detected is True
        assert "inspector" in cmd
        assert "Friday" in cmd
