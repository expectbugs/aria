"""Tests for tool use enforcement pipeline.

Covers:
  - Per-query tool reminder injection in session_pool
  - Tool call tracking via stream-json
  - _is_conversational() classification
  - _has_factual_claims() detection
  - validate_tool_use() integration
  - SessionResponse dataclass
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from session_pool import SessionResponse, _TOOL_USE_REMINDER


# --- SessionResponse ---

class TestSessionResponse:
    def test_default_empty_tool_calls(self):
        r = SessionResponse(text="hello")
        assert r.text == "hello"
        assert r.tool_calls == []

    def test_with_tool_calls(self):
        r = SessionResponse(text="result", tool_calls=["Bash", "Read"])
        assert r.tool_calls == ["Bash", "Read"]


# --- Tool Reminder Injection ---

class TestToolReminder:
    def test_reminder_constant_exists(self):
        assert "VERIFIED" in _TOOL_USE_REMINDER
        assert "query.py" in _TOOL_USE_REMINDER
        assert "tool" in _TOOL_USE_REMINDER.lower()

    def test_reminder_mentions_key_tools(self):
        assert "health" in _TOOL_USE_REMINDER
        assert "nutrition" in _TOOL_USE_REMINDER
        assert "calendar" in _TOOL_USE_REMINDER
        assert "Bash" in _TOOL_USE_REMINDER


# --- _is_conversational ---

class TestIsConversational:
    @pytest.fixture(autouse=True)
    def _import(self):
        from verification import _is_conversational
        self.fn = _is_conversational

    def test_short_text_no_digits(self):
        assert self.fn("thanks!") is True
        assert self.fn("got it") is True
        assert self.fn("cool") is True

    def test_greeting(self):
        assert self.fn("Good morning!") is True
        assert self.fn("Hey there") is True
        assert self.fn("hi") is True

    def test_farewell(self):
        assert self.fn("bye") is True
        assert self.fn("talk later") is True

    def test_question_short(self):
        assert self.fn("How are you doing?") is True

    def test_factual_not_conversational(self):
        assert self.fn("You ate 1,450 calories today and walked 8,000 steps.") is False

    def test_long_response_not_conversational(self):
        long = "Here's a detailed breakdown of your nutrition today. " * 5
        assert self.fn(long) is False

    def test_exact_match_variants(self):
        for phrase in ["ok", "okay", "sure", "yep", "nope", "lol", "nice"]:
            assert self.fn(phrase) is True, f"'{phrase}' should be conversational"

    def test_exact_match_with_punctuation(self):
        assert self.fn("ok.") is True
        assert self.fn("thanks!") is True

    def test_short_with_numbers_not_conversational(self):
        # Short but has specific numbers — could be factual
        assert self.fn("You had 3 meals") is False  # has digits, > threshold needs checking
        # Actually this is < 40 chars but has digits, so not auto-conversational
        # The function checks: len < 40 AND no digits
        assert self.fn("ok 123") is False  # has digits


# --- _has_factual_claims ---

class TestHasFactualClaims:
    @pytest.fixture(autouse=True)
    def _import(self):
        from verification import _has_factual_claims
        self.fn = _has_factual_claims

    def test_calorie_claim(self):
        assert self.fn("You consumed 1,450 calories today.") is True

    def test_step_count(self):
        assert self.fn("You walked 8,500 steps so far.") is True

    def test_appointment_claim(self):
        assert self.fn("Your appointment is on March 28th at 2pm.") is True

    def test_event_on_date(self):
        assert self.fn("Toni's Birthday is on April 2.") is True

    def test_completeness_claim(self):
        assert self.fn("That's all I see in your calendar.") is True
        assert self.fn("The only event is tomorrow's meeting.") is True

    def test_meal_count(self):
        assert self.fn("You had 3 meals logged today.") is True

    def test_you_have_numeric(self):
        assert self.fn("You have 2 active timers.") is True

    def test_no_factual_claims(self):
        assert self.fn("Sure, I can help with that!") is False
        assert self.fn("What would you like to know?") is False

    def test_banter_no_claims(self):
        assert self.fn("Oh, you want ME to do the math? Bold.") is False

    # --- Temporal claims (Lie #1) ---

    def test_temporal_last_hours(self):
        assert self.fn("You've been awake for the last 3 hours.") is True

    def test_temporal_past_minutes(self):
        assert self.fn("That was about 30 minutes ago.") is True

    def test_temporal_hours_ago(self):
        assert self.fn("You ate 2 hours ago.") is True

    def test_temporal_last_hour_singular(self):
        assert self.fn("In the last 1 hour you had two coffees.") is True

    def test_temporal_past_days(self):
        assert self.fn("Over the past 3 days your sleep averaged 5.8 hours.") is True

    def test_temporal_no_false_positive(self):
        """Vague temporal references without numbers shouldn't trigger."""
        assert self.fn("You had coffee earlier today.") is False
        assert self.fn("That was a while ago.") is False


# --- validate_tool_use ---

class TestValidateToolUse:
    @pytest.fixture(autouse=True)
    def _import(self):
        from verification import validate_tool_use
        self.fn = validate_tool_use

    def test_conversational_no_tools_ok(self):
        ok, reason = self.fn("thanks!", [])
        assert ok is True
        assert reason == "conversational"

    def test_factual_with_tools_ok(self):
        ok, reason = self.fn("You ate 1,200 calories today.", ["Bash"])
        assert ok is True
        assert "tools_used" in reason

    def test_factual_without_tools_not_ok(self):
        ok, reason = self.fn("You ate 1,200 calories today.", [])
        assert ok is False
        assert reason == "factual_claims_without_tool_use"

    def test_no_claims_no_tools_ok(self):
        ok, reason = self.fn(
            "I'll check the details on that and get back to you shortly with what I find.", [])
        assert ok is True
        assert reason == "no_factual_claims"

    def test_greeting_always_ok(self):
        ok, _ = self.fn("Good morning! How can I help?", [])
        assert ok is True

    def test_empty_tool_list(self):
        ok, _ = self.fn("Your next appointment is on April 5.", [])
        assert ok is False

    def test_multiple_tools(self):
        ok, reason = self.fn(
            "You have 3 events this week.", ["Bash", "Read", "Bash"])
        assert ok is True
        assert "Bash" in reason


# --- System Prompt ---

class TestSystemPromptToolUse:
    def test_verify_before_claiming_in_prompt(self):
        from unittest.mock import MagicMock
        import sys
        mock_config = MagicMock()
        mock_config.OWNER_NAME = "Test"
        mock_config.OWNER_LIVING_SITUATION = "test"
        mock_config.OWNER_WORK_SCHEDULE = "test"
        mock_config.OWNER_EMPLOYER = "test"
        mock_config.OWNER_WORK_STATUS = "test"
        mock_config.OWNER_VEHICLE = "test"
        mock_config.OWNER_HEALTH_NOTES = "test"
        mock_config.OWNER_TIMEZONE = "test"
        mock_config.KNOWN_PLACES = {}
        with patch.dict(sys.modules, {"config": mock_config}):
            import importlib
            import system_prompt
            importlib.reload(system_prompt)
            prompt = system_prompt.build_primary_prompt()
            assert "VERIFY BEFORE CLAIMING" in prompt
            assert "query.py" in prompt
            assert "SUBSET" in prompt
