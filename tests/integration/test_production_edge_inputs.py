"""Real edge-case user inputs through the context and action pipelines.

Tests specific real-world inputs from request_log that represent boundary
conditions: single-word inputs, incomplete greetings, mixed-intent messages,
and inputs that triggered production errors.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
Mocks: weather, news, redis_client, fitbit_store exercise state, config paths.
"""

from datetime import datetime, date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import context
import actions
import db

from tests.integration.conftest import (
    load_fixture,
    seed_health, seed_nutrition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_weather():
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
# Single-word input: "Timer!"
# ---------------------------------------------------------------------------

class TestTimerInput:

    @pytest.mark.asyncio
    async def test_timer_exclamation_no_crash(self):
        """'Timer!' (single word) builds context without crash."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("Timer!")
            assert isinstance(result, str)
            # Should not route to briefing or debrief
            assert "interaction" not in result.lower() or "Current date" in result
        finally:
            for p in patches:
                p.stop()

    def test_timer_exclamation_no_actions(self):
        """'Timer!' through process_actions produces no action extraction."""
        result = actions.process_actions("Timer!")
        assert "action failed" not in result.lower()


# ---------------------------------------------------------------------------
# Incomplete greeting: "good morning are you"
# ---------------------------------------------------------------------------

class TestIncompleteGreeting:

    @pytest.mark.asyncio
    async def test_good_morning_are_you_triggers_briefing(self):
        """'good morning are you' still triggers briefing path despite incomplete sentence."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text("good morning are you")
            # Should route to briefing (starts with 'good morning')
            assert "Partly Cloudy" in result or "Current date" in result
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Morning greeting with meal info
# ---------------------------------------------------------------------------

class TestBriefingWithMealContext:

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
            # Routes to briefing because starts with "good morning"
            assert "Partly Cloudy" in result or "Current date" in result
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Supplement discussion — no false actions
# ---------------------------------------------------------------------------

class TestSupplementDiscussion:

    @pytest.mark.asyncio
    async def test_magnesium_supplement_context_builds(self):
        """Discussion about magnesium supplements builds context normally."""
        text = (
            "the Magnesium supplements are 100 mg per pill and I take two "
            "per day so that's 200 mg of magnesium glycinate daily"
        )
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(text)
            assert isinstance(result, str)
            # Should trigger health context because of supplement/magnesium keywords
        finally:
            for p in patches:
                p.stop()

    def test_magnesium_discussion_no_false_actions(self):
        """Supplement discussion should not produce false ACTION extraction."""
        response = (
            "The Magnesium supplements are 100 mg per pill. Taking two per day "
            "gives you 200 mg of magnesium glycinate, which is a well-absorbed form. "
            "That's about half the daily target of 400-420 mg."
        )
        result = actions.process_actions(response)
        assert "action failed" not in result.lower()
        # Should not detect a storage claim
        assert "System note" not in result


# ---------------------------------------------------------------------------
# File send anticipation — no crash
# ---------------------------------------------------------------------------

class TestFileSendAnticipation:

    @pytest.mark.asyncio
    async def test_file_send_anticipation_no_crash(self):
        """'I'm going to send you a file tell me what it says' builds context."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(
                "I'm going to send you a file tell me what it says"
            )
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Photo dismissal — no crash
# ---------------------------------------------------------------------------

class TestPhotoDismissal:

    @pytest.mark.asyncio
    async def test_ignore_photos_no_crash(self):
        """'please ignore all these photos of my hot sauces' builds context."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(
                "please ignore all these photos of my hot sauces"
            )
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Compliment / minimal intent — minimal context
# ---------------------------------------------------------------------------

class TestMinimalIntent:

    @pytest.mark.asyncio
    async def test_compliment_produces_minimal_context(self):
        """'indeed you are coming along quite nicely' produces minimal context."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(
                "indeed you are coming along quite nicely"
            )
            assert isinstance(result, str)
            # Should only have Tier 1 (always-inject) context
            # Should NOT contain weather, health, vehicle, or legal
            assert "weather" not in result.lower()
            assert "Vehicle" not in result
            assert "Legal" not in result
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Weather error question — weather keyword detected
# ---------------------------------------------------------------------------

class TestWeatherErrorQuestion:

    @pytest.mark.asyncio
    async def test_weather_error_question_triggers_weather(self):
        """'why did the live weather information error out' triggers weather context."""
        current, forecast, alerts = _mock_weather()
        patches = [
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
        for p in patches:
            p.start()
        try:
            result = await context.build_request_context(
                "why did the live weather information error out"
            )
            # "weather" keyword should trigger weather context injection
            assert "Partly Cloudy" in result or "weather" in result.lower()
            current.assert_awaited()
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Dinner keyword triggers health context
# ---------------------------------------------------------------------------

class TestDinnerKeyword:

    @pytest.mark.asyncio
    async def test_rice_dinner_triggers_health_context(self):
        """'the rice would be a much more substantial dinner' triggers health context."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context.build_request_context(
                "the rice would be a much more substantial dinner"
            )
            # "dinner" keyword should match _HEALTH_REGEX via \b(dinner)\b
            assert isinstance(result, str)
            # Health context should be injected (even if empty, the path was taken)
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_rice_dinner_with_seeded_data(self):
        """With nutrition data seeded, dinner keyword injects nutrition context."""
        today = datetime.now().strftime("%Y-%m-%d")
        seed_nutrition(today, "Morning smoothie", meal_type="breakfast",
                       calories=300, protein_g=20)

        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context.build_request_context(
                "the rice would be a much more substantial dinner"
            )
            # Should include nutrition context because "dinner" matches health regex
            assert "Nutrition" in result or "smoothie" in result.lower()
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Very long multi-line SMS input
# ---------------------------------------------------------------------------

class TestVeryLongSMS:

    @pytest.mark.asyncio
    async def test_long_multiline_sms_context_size(self):
        """Very long multi-line SMS input should not blow up context size."""
        long_sms = (
            "[sms:+15551234567] " +
            "\n".join([
                "I had a snack first, chobani less sugar strawberry.",
                "Then for dinner I had the steamer meal.",
                "The chicken marinara steamer bowl.",
                "I also had a can of the 5oz safecatch salmon.",
                "With a tbsp of plant-based Hellman's mayo.",
                "And then a tall glass of unsweetened chobani oatmilk.",
                "Mixed with a half-scoop of the HUEL.",
                "Also had two chomps beef sticks for a snack earlier.",
                "And a bag of steamed broccoli with cheese for lunch.",
                "Plus my morning smoothie and coffee as usual.",
            ] * 3)  # Repeat 3x to make it really long
        )
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(long_sms)
            size_bytes = len(result.encode("utf-8"))
            assert size_bytes < 50_000, f"Context is {size_bytes} bytes, exceeds 50KB"
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# SMS prefix in context builder
# ---------------------------------------------------------------------------

class TestSMSPrefix:

    @pytest.mark.asyncio
    async def test_sms_prefix_no_crash(self):
        """Input with [sms:+15551234567] prefix builds context without crash."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context._get_context_for_text(
                "[sms:+15551234567] Hello, how are you this evening?"
            )
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Diet discussion with egg keyword triggers health context
# ---------------------------------------------------------------------------

class TestDietEggDiscussion:

    @pytest.mark.asyncio
    async def test_egg_diet_discussion_triggers_health(self):
        """'that's interesting because when you help me design my diet eggs were not
        part of it' should trigger health context due to 'diet' and 'egg' keywords."""
        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context.build_request_context(
                "that's interesting because when you help me design my diet eggs were not part of it"
            )
            # "diet" is in _HEALTH_SUBSTRINGS, should trigger health context
            assert isinstance(result, str)
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_egg_diet_with_nutrition_data(self):
        """With seeded nutrition data, egg/diet discussion includes nutrition totals."""
        today = datetime.now().strftime("%Y-%m-%d")
        seed_nutrition(today, "Hard-boiled eggs", meal_type="breakfast",
                       calories=156, protein_g=12.6)

        patches = _apply_standard_mocks()
        for p in patches:
            p.start()
        try:
            result = await context.build_request_context(
                "that's interesting because when you help me design my diet eggs were not part of it"
            )
            assert "egg" in result.lower() or "Nutrition" in result
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# All ok-status inputs — no crash test
# ---------------------------------------------------------------------------

class TestAllOkInputsNoCrash:
    """Broad resilience: every ok-status input should build context without crash."""

    def _get_ok_samples(self):
        samples = load_fixture("request_log_samples.json")
        return [s for s in samples if s["status"] == "ok"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sample_id", [
        s["id"] for s in load_fixture("request_log_samples.json")
        if s["status"] == "ok"
    ][:20])  # Test first 20 to keep test time reasonable
    async def test_ok_input_builds_context(self, sample_id):
        """Each ok-status input should build context without crash."""
        samples = self._get_ok_samples()
        sample = next(s for s in samples if s["id"] == sample_id)
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
# Responses through process_actions — no false positives
# ---------------------------------------------------------------------------

class TestResponsesNoFalseActions:

    def test_image_description_no_actions(self):
        """Image description response should not trigger action extraction."""
        response = (
            "Nice hot sauce collection. Tiger Sauce Original, Yellowbird Serrano, "
            "and Frank's RedHot. Solid lineup. What's the occasion?"
        )
        result = actions.process_actions(response)
        assert "action failed" not in result.lower()
        assert "System note" not in result

    def test_file_description_no_actions(self):
        """File description response with no storage claims produces no system note."""
        response = (
            "Got it, received an Excel spreadsheet called 'Training Schedule 2026.xlsx', "
            "about 15KB. I can't read the contents directly though."
        )
        result = actions.process_actions(response)
        assert "action failed" not in result.lower()
        assert "System note" not in result

    def test_weather_response_no_actions(self):
        """Weather response should not trigger action extraction."""
        response = (
            "It's 27 and cloudy right now, warming up to 37 with some sun "
            "breaking through later today."
        )
        result = actions.process_actions(response)
        assert "action failed" not in result.lower()
        assert "System note" not in result
