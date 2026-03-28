"""Context builder replay tests with real multi-store production data.

Loads actual production fixture data (Fitbit, nutrition, health, locations)
into the test database, freezes time to March 25, 2026, and validates
that context builders produce correct, complete output.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
Mocks: weather module, news module, redis_client (no Redis needed).
"""

import asyncio
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from freezegun import freeze_time

import context
import nutrition_store
import fitbit_store
import health_store
import db

from tests.integration.conftest import (
    load_fitbit_snapshots_into_db,
    load_nutrition_entries_into_db,
    load_health_entries_into_db,
    load_locations_into_db,
    load_fixture,
    seed_reminder,
    seed_event,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_redis():
    """Return a mock for context.redis_client."""
    mock = MagicMock()
    mock.get_active_tasks.return_value = []
    mock.format_task_status.return_value = ""
    return mock


def _mock_weather():
    """Return patches for weather module async functions."""
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
    """Return a mock for the news module digest."""
    return AsyncMock(return_value={
        "tech": [{"title": "AI Advances", "summary": "Big progress"}],
    })


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _load_all_data():
    """Load all production fixture data into the test database."""
    load_fitbit_snapshots_into_db()
    load_nutrition_entries_into_db()
    load_health_entries_into_db()
    load_locations_into_db()


# ---------------------------------------------------------------------------
# Tests with all production data loaded, frozen to March 25, 2026 14:00
# ---------------------------------------------------------------------------

class TestAlwaysContextWithProductionData:
    """gather_always_context() with real multi-store data."""

    @freeze_time("2026-03-25 14:00:00")
    def test_includes_location_battery_date(self):
        """Always context includes location, battery, and date string."""
        _load_all_data()
        with patch("context.redis_client", _mock_redis()):
            result = context.gather_always_context()

        assert isinstance(result, str)
        assert "Current date and time:" in result
        # Location fixture data has location names and battery_pct
        assert "Location:" in result
        assert "battery" in result.lower() or "Battery" in result


class TestHealthContextWithProductionData:
    """gather_health_context() with real production data."""

    @freeze_time("2026-03-25 14:00:00")
    def test_includes_meals_nutrition_fitbit(self):
        """Health context includes meals consumed, nutrition totals,
        Fitbit data, calorie balance, and diet day counter."""
        _load_all_data()
        # Set DIET_START_DATE so diet day counter appears
        with patch.object(context.config, "DIET_START_DATE", "2026-03-17"):
            result = context.gather_health_context()

        assert isinstance(result, str)
        assert len(result) > 0

        # March 25 has 9 health meals
        assert "Meals consumed today" in result

        # March 25 has 5 nutrition entries
        assert "Nutrition today" in result

        # Fitbit snapshot exists for March 25 (1779 steps, 71 resting HR)
        assert "Fitbit" in result

        # Diet day counter (March 25 is day 9 from March 17)
        assert "Diet day 9" in result

    @freeze_time("2026-03-25 14:00:00")
    def test_incomplete_tracking_warning(self):
        """With 9 health meals but only 5 nutrition entries, should warn
        about incomplete tracking."""
        _load_all_data()
        result = context.gather_health_context()

        # 9 meals in diary but fewer have structured nutrition data
        assert "incomplete" in result.lower() or "only" in result.lower()

    @freeze_time("2026-03-25 14:00:00")
    def test_nutrition_calorie_totals(self):
        """Nutrition totals reflect real data: coffee 100 + eggs 156 +
        smoothie 280 + multivitamin 0 + Factor lunch 440 = 976 cal."""
        _load_all_data()
        totals = nutrition_store.get_daily_totals("2026-03-25")

        assert totals["item_count"] == 5
        # Expected: 100 + (78*2) + 280 + 0 + 440 = 976
        assert abs(totals["calories"] - 976) < 1


class TestBuildRequestContextKeywords:
    """build_request_context() keyword-triggered context with real data."""

    @freeze_time("2026-03-25 14:00:00")
    def test_eat_triggers_health_context(self):
        """'what did I eat today' triggers health context."""
        _load_all_data()
        with patch("context.redis_client", _mock_redis()):
            result = _run(context.build_request_context("what did I eat today"))

        assert "Meals consumed today" in result or "Nutrition today" in result

    @freeze_time("2026-03-25 14:00:00")
    def test_heart_rate_triggers_fitbit(self):
        """'how's my heart rate' triggers Fitbit HR data."""
        _load_all_data()
        with patch("context.redis_client", _mock_redis()):
            result = _run(context.build_request_context("how's my heart rate"))

        # March 25 Fitbit snapshot has resting HR of 71
        assert "heart rate" in result.lower() or "71" in result or "Resting" in result

    @freeze_time("2026-03-25 14:00:00")
    def test_joke_gets_minimal_context(self):
        """'tell me a joke' should NOT trigger health/vehicle/legal data."""
        _load_all_data()
        with patch("context.redis_client", _mock_redis()):
            result = _run(context.build_request_context("tell me a joke"))

        # Should have always-context (date, location) but NOT specialist data
        assert "Current date and time:" in result
        assert "Meals consumed today" not in result
        assert "Vehicle log:" not in result
        assert "Legal case log:" not in result

    @freeze_time("2026-03-25 14:00:00")
    def test_diet_triggers_health_context(self):
        """'diet' keyword triggers health context."""
        _load_all_data()
        with patch("context.redis_client", _mock_redis()):
            result = _run(context.build_request_context("how is my diet going"))

        assert "Nutrition today" in result or "Meals consumed today" in result

    @freeze_time("2026-03-25 14:00:00")
    def test_weather_triggers_weather_context(self):
        """'weather' keyword triggers weather context (mocked)."""
        _load_all_data()
        current, forecast, alerts = _mock_weather()
        with patch("context.redis_client", _mock_redis()), \
             patch("context.weather.get_current_conditions", current), \
             patch("context.weather.get_forecast", forecast), \
             patch("context.weather.get_alerts", alerts):
            result = _run(context.build_request_context("what's the weather like"))

        assert "Partly Cloudy" in result or "55" in result


class TestBriefingAndDebriefPaths:
    """_get_context_for_text() routing for briefings and debriefs."""

    @freeze_time("2026-03-25 08:00:00")
    def test_good_morning_briefing_path(self):
        """'good morning' triggers briefing path with all sections."""
        _load_all_data()
        current, forecast, alerts = _mock_weather()
        news_digest = _mock_news()
        with patch("context.redis_client", _mock_redis()), \
             patch("context.weather.get_current_conditions", current), \
             patch("context.weather.get_forecast", forecast), \
             patch("context.weather.get_alerts", alerts), \
             patch("context.news.get_news_digest", news_digest):
            result = _run(context._get_context_for_text("good morning"))

        assert isinstance(result, str)
        # Should have always-context + briefing
        assert "Current date and time:" in result
        # Weather section
        assert "weather" in result.lower() or "Partly Cloudy" in result
        # News section
        assert "AI Advances" in result
        # Health data (Fitbit snapshot exists for March 25)
        assert "Fitbit" in result or "health" in result.lower()

    @freeze_time("2026-03-25 22:00:00")
    def test_good_night_debrief_path(self):
        """'good night' triggers debrief path with today's interactions."""
        _load_all_data()
        with patch("context.redis_client", _mock_redis()), \
             patch("context.weather.get_forecast", AsyncMock(return_value=[])):
            result = _run(context._get_context_for_text("good night"))

        assert isinstance(result, str)
        # Should have always-context + debrief
        assert "Current date and time:" in result
        # Debrief includes health logged today (March 25 has 9 meal entries)
        assert "Health logged today:" in result or "Meals" in result.lower()


class TestContextSizeLimits:
    """Context size should stay within reasonable bounds."""

    @freeze_time("2026-03-25 08:00:00")
    def test_full_briefing_under_50kb(self):
        """Full briefing context with all data loaded stays under 50KB."""
        _load_all_data()
        current, forecast, alerts = _mock_weather()
        news_digest = _mock_news()
        with patch("context.redis_client", _mock_redis()), \
             patch("context.weather.get_current_conditions", current), \
             patch("context.weather.get_forecast", forecast), \
             patch("context.weather.get_alerts", alerts), \
             patch("context.news.get_news_digest", news_digest):
            result = _run(context._get_context_for_text("good morning"))

        size_bytes = len(result.encode("utf-8"))
        assert size_bytes < 50_000, (
            f"Briefing context is {size_bytes} bytes, exceeds 50KB limit"
        )


class TestHealthContextMarch25Detail:
    """Detailed assertions on March 25 health context."""

    @freeze_time("2026-03-25 14:00:00")
    def test_march25_health_meal_count(self):
        """March 25 has exactly 9 health meal entries."""
        _load_all_data()
        today = "2026-03-25"
        meals = [m for m in health_store.get_entries(days=1, category="meal")
                 if m.get("date") == today]
        assert len(meals) == 9

    @freeze_time("2026-03-25 14:00:00")
    def test_march25_nutrition_count(self):
        """March 25 has exactly 5 nutrition entries."""
        _load_all_data()
        items = nutrition_store.get_items(day="2026-03-25")
        assert len(items) == 5

    @freeze_time("2026-03-25 14:00:00")
    def test_march25_fitbit_steps(self):
        """March 25 Fitbit snapshot has 1779 steps."""
        _load_all_data()
        activity = fitbit_store.get_activity_summary("2026-03-25")
        assert activity is not None
        assert activity["steps"] == 1779

    @freeze_time("2026-03-25 14:00:00")
    def test_march25_fitbit_resting_hr(self):
        """March 25 Fitbit snapshot has resting HR of 71."""
        _load_all_data()
        hr = fitbit_store.get_heart_summary("2026-03-25")
        assert hr is not None
        assert hr["resting_hr"] == 71

    @freeze_time("2026-03-25 14:00:00")
    def test_calorie_balance_with_fitbit(self):
        """Net calorie balance integrates nutrition and Fitbit burn."""
        _load_all_data()
        net = nutrition_store.get_net_calories("2026-03-25")
        # Consumed should be 976 cal (from 5 nutrition items)
        assert net["consumed"] == 976
        # Burned should come from Fitbit (1783 total calories)
        assert net["burned"] == 1783
        # Net should be negative (deficit)
        assert net["net"] < 0


class TestWithSeededCalendarData:
    """Test context includes reminders/events alongside real data."""

    @freeze_time("2026-03-25 14:00:00")
    def test_reminders_and_events_in_always_context(self):
        """Seeded reminders and events appear alongside Fitbit/nutrition data."""
        _load_all_data()
        seed_reminder(text="Pick up prescription", due="2026-03-25")
        seed_event(title="Dentist appointment", event_date="2026-03-25",
                   time="15:00")

        with patch("context.redis_client", _mock_redis()):
            result = context.gather_always_context()

        assert "Pick up prescription" in result
        assert "Active reminders:" in result

    @freeze_time("2026-03-25 14:00:00")
    def test_events_in_request_context(self):
        """Events appear in regular request context."""
        _load_all_data()
        seed_event(title="Dentist appointment", event_date="2026-03-25",
                   time="15:00")

        with patch("context.redis_client", _mock_redis()):
            result = _run(context.build_request_context("what's on my schedule"))

        assert "Dentist appointment" in result
