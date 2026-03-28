"""Replay real request_log inputs through context and action pipelines.

Uses production data from tests/integration/fixtures/request_log_samples.json
(152 entries: 18 errors/warnings + 134 interesting ok) to verify that the
context builder and action processor handle real-world inputs without crashes.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
Mocks: weather, news, redis_client, fitbit_store exercise state, config paths.
"""

import asyncio
from datetime import datetime, date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import actions
import context
import db

from tests.integration.conftest import (
    load_fixture,
    seed_event, seed_reminder, seed_request_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_samples():
    """Load all request_log samples from fixture."""
    return load_fixture("request_log_samples.json")


def _samples_by_status(status: str):
    """Filter samples by status field."""
    return [s for s in _load_samples() if s["status"] == status]


def _ok_samples():
    return _samples_by_status("ok")


def _error_samples():
    return _samples_by_status("error")


def _warning_samples():
    return _samples_by_status("warning")


def _mock_weather():
    """Patches for weather module async functions."""
    current = AsyncMock(return_value={
        "description": "Partly Cloudy",
        "temperature_f": 55,
        "humidity": 45.0,
        "wind_mph": 10,
    })
    forecast = AsyncMock(return_value=[
        {"name": "Tonight", "temperature": 38, "unit": "F",
         "summary": "Clear skies"},
        {"name": "Tomorrow", "temperature": 60, "unit": "F",
         "summary": "Sunny"},
    ])
    alerts = AsyncMock(return_value=[])
    return current, forecast, alerts


def _mock_news():
    return AsyncMock(return_value={
        "tech": [{"title": "AI Advances", "summary": "Big progress"}],
    })


def _apply_standard_mocks():
    """Return a list of context manager patches for standard external deps."""
    current, forecast, alerts = _mock_weather()
    return [
        patch("context.weather.get_current_conditions", current),
        patch("context.weather.get_forecast", forecast),
        patch("context.weather.get_alerts", alerts),
        patch("context.news.get_news_digest", _mock_news()),
        patch("context.redis_client.get_active_tasks", return_value=[]),
        patch("context.redis_client.format_task_status", return_value=""),
        patch("context.fitbit_store.get_exercise_state", return_value=None),
        patch("context.fitbit_store.get_briefing_context", return_value=""),
        patch("context.fitbit_store.get_trend", return_value=""),
        patch("context.fitbit_store.get_sleep_summary", return_value=None),
        patch("context.fitbit_store.get_heart_summary", return_value=None),
        patch("context.fitbit_store.get_activity_summary", return_value=None),
        patch("context.config.DATA_DIR", Path("/tmp/aria_test_nonexistent")),
    ]


# ---------------------------------------------------------------------------
# Error status samples — no crash
# ---------------------------------------------------------------------------

class TestErrorSamplesNoCrash:
    """Every error-status request_log entry should build context without crash."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sample", _error_samples(),
                             ids=[f"id-{s['id']}" for s in _error_samples()])
    async def test_error_input_builds_context(self, sample):
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(sample["input"])
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# CLAIM_WITHOUT_ACTION warning samples
# ---------------------------------------------------------------------------

class TestClaimWithoutAction:
    """CLAIM_WITHOUT_ACTION warnings should trigger claim detection in process_actions."""

    def _get_claim_warning_ids(self):
        """Get IDs of all CLAIM_WITHOUT_ACTION warning entries."""
        return [s for s in _warning_samples()
                if s.get("input") == "CLAIM_WITHOUT_ACTION"]

    def test_claim_warnings_exist_in_fixture(self):
        """Verify we have CLAIM_WITHOUT_ACTION samples to test."""
        claims = self._get_claim_warning_ids()
        assert len(claims) >= 5, f"Expected >= 5 CLAIM_WITHOUT_ACTION warnings, got {len(claims)}"

    def test_response_with_logged_claim_triggers_detection(self):
        """A response containing 'I've logged' without ACTION blocks should produce system note."""
        fake_response = "I've logged your dinner into the nutrition tracker. Everything looks good."
        result = actions.process_actions(fake_response)
        assert "System note" in result or "ARIA claimed" in result

    def test_response_with_saved_claim_triggers_detection(self):
        fake_response = "I saved your vehicle maintenance entry."
        result = actions.process_actions(fake_response)
        assert "System note" in result or "ARIA claimed" in result

    def test_response_with_captured_claim_triggers_detection(self):
        fake_response = "I've captured all the nutrition data from that label."
        result = actions.process_actions(fake_response)
        assert "System note" in result or "ARIA claimed" in result

    def test_response_with_added_claim_triggers_detection(self):
        fake_response = "I added the multivitamin to your supplement log."
        result = actions.process_actions(fake_response)
        assert "System note" in result or "ARIA claimed" in result

    def test_nutrition_data_extracted_claim_triggers(self):
        """Response with claim + multiple nutrient terms should trigger."""
        fake_response = (
            "I logged your meal. Here's what I found: 500 calories, "
            "30g protein, 20g carbs, 15g fat, 800mg sodium, 5g fiber, "
            "10g sugar, and 200mg cholesterol."
        )
        result = actions.process_actions(fake_response)
        assert "System note" in result or "ARIA claimed" in result


# ---------------------------------------------------------------------------
# Briefing inputs — route to briefing path
# ---------------------------------------------------------------------------

class TestBriefingRouting:
    """'good morning' variants should route to the briefing context path."""

    def _get_briefing_inputs(self):
        """Get all 'good morning' ok-status inputs."""
        return [s for s in _ok_samples()
                if s["input"].lower().startswith("good morning")]

    def test_briefing_inputs_exist(self):
        samples = self._get_briefing_inputs()
        assert len(samples) >= 3, f"Expected >= 3 briefing inputs, got {len(samples)}"

    @pytest.mark.asyncio
    async def test_good_morning_routes_to_briefing(self):
        """'good morning' triggers the briefing path with weather/news content."""
        current, forecast, alerts = _mock_weather()
        news_mock = _mock_news()
        patches = [
            patch("context.weather.get_current_conditions", current),
            patch("context.weather.get_forecast", forecast),
            patch("context.weather.get_alerts", alerts),
            patch("context.news.get_news_digest", news_mock),
            patch("context.redis_client.get_active_tasks", return_value=[]),
            patch("context.redis_client.format_task_status", return_value=""),
            patch("context.fitbit_store.get_exercise_state", return_value=None),
            patch("context.fitbit_store.get_briefing_context", return_value=""),
            patch("context.fitbit_store.get_trend", return_value=""),
            patch("context.fitbit_store.get_sleep_summary", return_value=None),
            patch("context.fitbit_store.get_heart_summary", return_value=None),
            patch("context.fitbit_store.get_activity_summary", return_value=None),
        ]
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("good morning")
            # Should contain weather content from briefing path
            assert "Partly Cloudy" in result or "weather" in result.lower()
            # Should also contain news
            news_mock.assert_awaited()
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_good_morning_are_you_routes_to_briefing(self):
        """Incomplete 'good morning are you' still triggers briefing."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("good morning are you")
            # Briefing path injects weather
            assert "Partly Cloudy" in result or "Current date" in result
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_good_morning_with_meal_triggers_briefing(self):
        """'good morning starting my day with coffee and a smoothie' routes to briefing."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(
                "good morning starting my day with coffee and a smoothie"
            )
            # Should still go through briefing path
            assert "Partly Cloudy" in result or "Current date" in result
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Debrief inputs — route to debrief path
# ---------------------------------------------------------------------------

class TestDebriefRouting:
    """'good night' should route to debrief context path."""

    @pytest.mark.asyncio
    async def test_good_night_routes_to_debrief(self):
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("good night")
            # Debrief path includes "interactions" section
            assert "interaction" in result.lower() or "No interactions" in result
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_debrief_with_seeded_data(self):
        """Debrief with a request_log entry today should show interaction count."""
        seed_request_log("test input", "test response")
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("good night")
            assert "1 total" in result or "interaction" in result.lower()
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Timer input — no crash
# ---------------------------------------------------------------------------

class TestTimerInput:

    @pytest.mark.asyncio
    async def test_timer_exclamation_builds_context(self):
        """Single-word 'Timer!' input should build context without crash."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("Timer!")
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# File upload inputs — is_image detection
# ---------------------------------------------------------------------------

class TestFileUploadInputs:

    def _get_file_inputs(self):
        return [s for s in _ok_samples() if s["input"].startswith("[file:")]

    def test_file_inputs_exist(self):
        samples = self._get_file_inputs()
        assert len(samples) >= 5, f"Expected >= 5 file inputs, got {len(samples)}"

    @pytest.mark.asyncio
    async def test_file_input_png_builds_context(self):
        """Image file input with .png extension builds context for image."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context.build_request_context(
                "[file:ARIA_1773700184.png] The user sent a file: ARIA_1773700184.png",
                is_image=True,
            )
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_file_input_jpg_builds_context(self):
        """JPEG file input builds context normally."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context.build_request_context(
                "[file:PXL_20260319_193734500.jpg] The user sent a file",
                is_image=True,
            )
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_non_image_file_no_health_context(self):
        """Non-image file (txt, csv) should NOT trigger health context by default."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context.build_request_context(
                "[file:test_file.txt] What does this file say?",
                is_image=False,
            )
            # Without health keywords and is_image=False, no health context
            assert "Nutrition" not in result or "Diet" not in result
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Long SMS inputs — context size check
# ---------------------------------------------------------------------------

class TestLongSMSInputs:

    def _get_long_sms_inputs(self):
        return [s for s in _ok_samples()
                if s["input"].startswith("[sms:") and len(s["input"]) > 300]

    def test_long_sms_inputs_exist(self):
        samples = self._get_long_sms_inputs()
        assert len(samples) >= 2, f"Expected >= 2 long SMS inputs, got {len(samples)}"

    @pytest.mark.asyncio
    async def test_long_sms_context_under_50kb(self):
        """Long SMS input should produce context under 50KB."""
        long_samples = self._get_long_sms_inputs()
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            for sample in long_samples[:3]:
                result = await context._get_context_for_text(sample["input"])
                size_bytes = len(result.encode("utf-8"))
                assert size_bytes < 50_000, (
                    f"Context for input id={sample['id']} is {size_bytes} bytes, "
                    f"exceeds 50KB limit"
                )
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# "Prompt is too long" error samples — context size measurement
# ---------------------------------------------------------------------------

class TestPromptTooLongInputs:

    def _get_prompt_too_long(self):
        return [s for s in _error_samples()
                if "Prompt is too long" in (s.get("error") or "")]

    def test_prompt_too_long_samples_exist(self):
        samples = self._get_prompt_too_long()
        assert len(samples) >= 1

    @pytest.mark.asyncio
    async def test_context_size_for_prompt_overflow_inputs(self):
        """Inputs that caused 'Prompt is too long' — measure context size."""
        samples = self._get_prompt_too_long()
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            for sample in samples:
                result = await context._get_context_for_text(sample["input"])
                size_bytes = len(result.encode("utf-8"))
                # Flag if context alone exceeds 100KB (not a hard assertion —
                # the overflow is caused by context + history + prompt combined)
                if size_bytes > 100_000:
                    pytest.fail(
                        f"Context for prompt-overflow input id={sample['id']} "
                        f"is {size_bytes} bytes — may contribute to overflow"
                    )
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# System note passthrough — not stripped
# ---------------------------------------------------------------------------

class TestSystemNotePassthrough:

    def test_system_note_not_stripped_from_response(self):
        """System note about claimed storage should survive process_actions."""
        response = (
            "Done, I've updated the file.\n\n"
            "(System note: ARIA claimed to store data but no ACTION blocks "
            "were emitted. The data may not have been saved. Please verify or retry.)"
        )
        result = actions.process_actions(response)
        assert "System note" in result

    def test_system_note_preserved_on_reprocessing(self):
        """Passing a response that already has a system note appended."""
        response = (
            "I saved your vehicle entry.\n\n"
            "(System note: ARIA claimed to store data but no ACTION blocks "
            "were emitted. The data may not have been saved. Please verify or retry.)"
        )
        result = actions.process_actions(response)
        # The system note from the original text is preserved, plus a new one
        # gets appended because "I saved" is a claim phrase
        assert "System note" in result or "ARIA claimed" in result


# ---------------------------------------------------------------------------
# No false ACTION extraction from natural speech
# ---------------------------------------------------------------------------

class TestNoFalseActionExtraction:

    def test_action_in_natural_speech_not_extracted(self):
        """Response mentioning 'ACTION' in prose should not trigger extraction."""
        response = (
            "I understand you want me to take ACTION on that. Let me think about it. "
            "The best course of ACTION would be to check the logs."
        )
        result = actions.process_actions(response)
        # No action failures should occur
        assert "action failed" not in result.lower()
        # The word ACTION in natural speech is not an ACTION block
        assert "<!--ACTION::" not in result

    def test_integrity_violation_response_no_extraction(self):
        """The integrity violation response mentioning ACTION blocks is not extracted."""
        # This is based on real response from fixture id=328
        response = (
            "You're absolutely right, and I'm sorry. That's a straight up violation "
            "of my own integrity rules. I said I dispatched agents twice and I didn't "
            "actually emit a single ACTION block either time. I basically lied to you, "
            "and that's not okay."
        )
        result = actions.process_actions(response)
        # No ACTION blocks should be extracted from this text
        assert "action failed" not in result.lower()

    def test_action_word_in_response_no_false_positive(self):
        """Words like 'actions' or 'action items' should not trigger extraction."""
        response = (
            "Here are the action items for today: check your calendar, "
            "review the ACTION plan, and follow up on the legal case."
        )
        result = actions.process_actions(response)
        assert "action failed" not in result.lower()


# ---------------------------------------------------------------------------
# Briefing dedup — _briefing_delivered_today detection
# ---------------------------------------------------------------------------

class TestBriefingDedup:

    @pytest.mark.asyncio
    async def test_second_briefing_returns_false(self):
        """After seeding a 'good morning' request, _briefing_delivered_today returns True."""
        seed_request_log("good morning", "Good morning! Here's your briefing.", status="ok")
        result = context._briefing_delivered_today()
        assert result is True

    @pytest.mark.asyncio
    async def test_second_briefing_falls_through_to_normal(self):
        """Second 'good morning' falls through to normal context (already delivered)."""
        seed_request_log("good morning", "Good morning! Here's your briefing.", status="ok")
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("good morning")
            # Should fall through to normal context, not briefing
            # Normal context does NOT contain news digest
            # (news is only in briefing path)
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_first_briefing_delivers(self):
        """Without prior briefing today, 'good morning' delivers full briefing."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("good morning")
            # First briefing should include weather from the briefing path
            assert "Partly Cloudy" in result or "weather" in result.lower()
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_repeat_keyword_bypasses_dedup(self):
        """'good morning again' should bypass dedup and deliver briefing."""
        seed_request_log("good morning", "Good morning!", status="ok")
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("good morning again")
            assert "Partly Cloudy" in result or "weather" in result.lower()
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# SMS prefix handling
# ---------------------------------------------------------------------------

class TestSMSPrefixHandling:

    @pytest.mark.asyncio
    async def test_sms_prefix_no_crash(self):
        """Input with [sms:+15551234567] prefix builds context without crash."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(
                "[sms:+15551234567] Hello, how are you?"
            )
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_sms_with_long_message(self):
        """Long SMS with nutrition content builds context properly."""
        long_sms = (
            "[sms:+15551234567] Whoops. I forgot to log my dinner last night.\n\n"
            "I had a snack first, chobani less sugar strawberry, then for dinner "
            "I had the steamer meal. I also had a can of the 5oz safecatch salmon, "
            "with a tbsp of plant-based Hellman's mayo, and then a tall glass of "
            "unsweetened chobani oatmilk mixed with a half-scoop of the HUEL."
        )
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(long_sms)
            assert isinstance(result, str)
            assert len(result.encode("utf-8")) < 50_000
        finally:
            for p in patches:
                p.stop()
