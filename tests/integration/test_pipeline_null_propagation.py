"""Integration tests: null/empty propagation through ARIA pipelines.

Tests what happens when functions return None/empty and their callers
use the result. Uses real aria_test PostgreSQL database.
"""

import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import context
import nutrition_store
import fitbit_store
import health_store
import location_store
import calendar_store
import vehicle_store
import tick
import db

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
)


# ---------------------------------------------------------------------------
# Empty database through every context builder (~10 tests)
# ---------------------------------------------------------------------------

class TestEmptyDBContextBuilders:
    """Verify every context builder handles an empty database gracefully."""

    def test_gather_always_context_empty_db(self):
        """gather_always_context() with no data returns string with datetime."""
        # Mock redis to avoid connection issues
        with patch("context.redis_client.get_active_tasks", return_value=[]), \
             patch("context.redis_client.format_task_status", return_value=""):
            result = context.gather_always_context()
        assert isinstance(result, str)
        assert "Current date and time:" in result
        # Should not contain timer, reminder, location, or exercise lines
        assert "Active timers:" not in result
        assert "Active reminders:" not in result
        assert "Location:" not in result
        assert "EXERCISE MODE" not in result

    def test_gather_health_context_empty_db(self):
        """gather_health_context() with no data returns empty string."""
        # Patch out DIET_START_DATE so the diet day counter does not inject text
        with patch.object(context.config, "DIET_START_DATE", ""):
            result = context.gather_health_context()
        assert result == ""

    @pytest.mark.asyncio
    async def test_gather_briefing_context_empty_db(self):
        """gather_briefing_context() with empty DB does not crash."""
        with patch("context.weather.get_current_conditions", new_callable=AsyncMock,
                    return_value={"description": "Clear", "temperature_f": 55,
                                  "humidity": 40, "wind_mph": 5}), \
             patch("context.weather.get_forecast", new_callable=AsyncMock,
                    return_value=[]), \
             patch("context.weather.get_alerts", new_callable=AsyncMock,
                    return_value=[]), \
             patch("context.news.get_news_digest", new_callable=AsyncMock,
                    return_value={}):
            result = await context.gather_briefing_context()
        assert isinstance(result, str)
        # Should have weather but no events, no patterns, etc.
        assert "No appointments today" in result

    @pytest.mark.asyncio
    async def test_gather_debrief_context_empty_db(self):
        """gather_debrief_context() with empty DB does not crash."""
        with patch("context.weather.get_forecast", new_callable=AsyncMock,
                    return_value=[]):
            result = await context.gather_debrief_context()
        assert isinstance(result, str)
        assert "No interactions logged today" in result

    @pytest.mark.asyncio
    async def test_build_request_context_hello_empty_db(self):
        """build_request_context('hello') with empty DB returns string."""
        with patch("context.redis_client.get_active_tasks", return_value=[]), \
             patch("context.redis_client.format_task_status", return_value=""):
            result = await context.build_request_context("hello")
        assert isinstance(result, str)
        assert "Current date and time:" in result

    @pytest.mark.asyncio
    async def test_build_request_context_weather_empty_db(self):
        """build_request_context('weather') with empty DB does not crash."""
        with patch("context.redis_client.get_active_tasks", return_value=[]), \
             patch("context.redis_client.format_task_status", return_value=""), \
             patch("context.weather.get_current_conditions", new_callable=AsyncMock,
                    return_value={"description": "Clear", "temperature_f": 55,
                                  "humidity": 40, "wind_mph": 5}), \
             patch("context.weather.get_forecast", new_callable=AsyncMock,
                    return_value=[]), \
             patch("context.weather.get_alerts", new_callable=AsyncMock,
                    return_value=[]):
            result = await context.build_request_context("weather")
        assert isinstance(result, str)

    def test_nutrition_get_context_empty_db(self):
        """nutrition_store.get_context(today) with no entries returns empty string."""
        today = date.today().isoformat()
        result = nutrition_store.get_context(today)
        assert result == ""

    def test_nutrition_get_weekly_summary_empty_db(self):
        """nutrition_store.get_weekly_summary() with no entries returns empty string."""
        result = nutrition_store.get_weekly_summary()
        assert result == ""

    def test_nutrition_get_net_calories_empty_db(self):
        """nutrition_store.get_net_calories(today) returns consumed=0, burned=0."""
        today = date.today().isoformat()
        result = nutrition_store.get_net_calories(today)
        assert result["consumed"] == 0
        assert result["burned"] == 0

    def test_fitbit_get_trend_empty_db(self):
        """fitbit_store.get_trend(days=7) with no snapshots returns empty string."""
        result = fitbit_store.get_trend(days=7)
        assert result == ""


# ---------------------------------------------------------------------------
# None returns through tick.py evaluation (~8 tests)
# ---------------------------------------------------------------------------

class TestTickNullPropagation:
    """Verify tick.py functions handle None/empty data from all stores."""

    def test_evaluate_nudges_all_empty(self):
        """evaluate_nudges() with all tables empty returns empty list."""
        with patch("tick.config.STALE_REMINDER_DAYS", 3, create=True):
            result = tick.evaluate_nudges()
        assert isinstance(result, list)
        # With empty tables, only time-gated nudges could trigger
        # (meal_reminder if after noon), but no crashes
        for nudge_type, description in result:
            assert isinstance(nudge_type, str)
            assert isinstance(description, str)

    def test_evaluate_nudges_no_fitbit_data(self):
        """evaluate_nudges() with no Fitbit data does not trigger fitbit nudges."""
        with patch("tick.config.STALE_REMINDER_DAYS", 3, create=True):
            result = tick.evaluate_nudges()
        fitbit_types = [t for t, _ in result if t.startswith("fitbit_")]
        assert fitbit_types == [], f"Unexpected Fitbit nudges without data: {fitbit_types}"

    def test_check_location_reminders_no_location(self):
        """check_location_reminders() returns without crash when no location."""
        # No location in DB means get_latest() returns None
        with patch("tick.sms.send_to_owner"):
            tick.check_location_reminders()
        # If we get here without exception, test passes

    def test_process_exercise_tick_no_exercise(self):
        """process_exercise_tick() returns immediately when no active exercise."""
        # No exercise state in DB
        tick.process_exercise_tick()
        # If we get here without exception, test passes

    def test_activity_summary_none_no_crash_in_nudge(self):
        """evaluate_nudges() handles None activity summary without crash."""
        # Ensure no Fitbit data exists, then check sedentary logic
        # The function checks 9 <= now.hour <= 21 for sedentary
        with patch("tick.config.STALE_REMINDER_DAYS", 3, create=True):
            result = tick.evaluate_nudges()
        # No sedentary trigger since there is no activity data
        sedentary = [t for t, _ in result if t == "fitbit_sedentary"]
        assert sedentary == []

    def test_evaluate_nudges_no_sleep_no_crash(self):
        """evaluate_nudges() handles None sleep summary without crash."""
        with patch("tick.config.STALE_REMINDER_DAYS", 3, create=True):
            result = tick.evaluate_nudges()
        sleep_types = [t for t, _ in result if t == "fitbit_sleep"]
        assert sleep_types == []

    def test_evaluate_nudges_no_heart_rate_no_crash(self):
        """evaluate_nudges() handles None heart rate summary without crash."""
        with patch("tick.config.STALE_REMINDER_DAYS", 3, create=True):
            result = tick.evaluate_nudges()
        hr_types = [t for t, _ in result if t == "fitbit_hr_anomaly"]
        assert hr_types == []

    def test_location_get_latest_none_in_nudge_battery(self):
        """evaluate_nudges() handles None location for battery check."""
        with patch("tick.config.STALE_REMINDER_DAYS", 3, create=True):
            result = tick.evaluate_nudges()
        battery_types = [t for t, _ in result if t == "battery_low"]
        assert battery_types == []


# ---------------------------------------------------------------------------
# None/empty through nutrition pipeline (~6 tests)
# ---------------------------------------------------------------------------

class TestNutritionNullPropagation:
    """Verify nutrition pipeline handles None/empty inputs gracefully."""

    def test_get_daily_totals_empty(self):
        """get_daily_totals() with no items returns item_count=0."""
        today = date.today().isoformat()
        result = nutrition_store.get_daily_totals(today)
        assert result["item_count"] == 0
        assert result["calories"] == 0
        assert result["protein_g"] == 0

    def test_check_limits_empty(self):
        """check_limits() with no items returns empty list."""
        today = date.today().isoformat()
        result = nutrition_store.check_limits(today)
        assert result == []

    def test_get_items_empty(self):
        """get_items() with no items returns empty list."""
        today = date.today().isoformat()
        result = nutrition_store.get_items(day=today)
        assert result == []

    def test_validate_entry_nutrients_none(self):
        """_validate_entry() with nutrients=None handles gracefully."""
        today = date.today().isoformat()
        errors = nutrition_store._validate_entry("Apple", today, 1.0, None)
        # None nutrients should not cause a crash
        assert isinstance(errors, list)
        # No sanity bound errors since there are no nutrient values to check
        assert len(errors) == 0

    def test_validate_entry_nutrients_empty_dict(self):
        """_validate_entry() with nutrients={} handles gracefully."""
        today = date.today().isoformat()
        errors = nutrition_store._validate_entry("Apple", today, 1.0, {})
        assert isinstance(errors, list)
        assert len(errors) == 0

    def test_add_item_nutrients_none_becomes_empty_dict(self):
        """add_item() with nutrients=None stores {} (not crash)."""
        today = date.today().isoformat()
        result = nutrition_store.add_item(
            food_name="Plain Water",
            meal_type="snack",
            nutrients=None,
            entry_date=today,
        )
        assert result["inserted"] is True
        assert result["entry"] is not None
        # Verify the stored nutrients is a dict
        stored = nutrition_store.get_items(day=today)
        assert len(stored) == 1
        assert isinstance(stored[0]["nutrients"], dict)


# ---------------------------------------------------------------------------
# Empty KNOWN_PLACES through location logic (~3 tests)
# ---------------------------------------------------------------------------

class TestEmptyKnownPlaces:
    """Verify location logic handles empty KNOWN_PLACES without crash."""

    def test_location_reminder_empty_known_places(self):
        """check_location_reminders() works with empty KNOWN_PLACES."""
        seed_location("Downtown", lat=42.58, lon=-88.43)
        seed_reminder(text="Test reminder", location="downtown",
                      location_trigger="arrive")
        with patch.object(tick.config, "KNOWN_PLACES", {}), \
             patch("tick.sms.send_to_owner"):
            tick.check_location_reminders()
        # No crash is the test

    def test_context_location_keywords_empty_known_places(self):
        """build_request_context with location keywords works with empty KNOWN_PLACES."""
        seed_location("Home", lat=42.58, lon=-88.43)
        with patch("context.redis_client.get_active_tasks", return_value=[]), \
             patch("context.redis_client.format_task_status", return_value=""):
            result = asyncio.get_event_loop().run_until_complete(
                context.build_request_context("where am i")
            )
        assert isinstance(result, str)

    def test_entity_extraction_empty_known_places(self):
        """extract_entities with empty KNOWN_PLACES returns no place entities."""
        import training_store
        with patch.object(training_store.config, "KNOWN_PLACES", {}):
            result = training_store.extract_entities(
                "I am at home now", source="test"
            )
        place_entities = [r for r in result if r[0] == "place"]
        assert place_entities == []


# ---------------------------------------------------------------------------
# Fitbit None paths (~3 tests)
# ---------------------------------------------------------------------------

class TestFitbitNullPaths:
    """Verify Fitbit summary functions return None when no data exists."""

    def test_get_sleep_summary_no_snapshot(self):
        """get_sleep_summary() returns None when no snapshot exists."""
        result = fitbit_store.get_sleep_summary(date.today().isoformat())
        assert result is None

    def test_get_heart_summary_no_snapshot(self):
        """get_heart_summary() returns None when no snapshot exists."""
        result = fitbit_store.get_heart_summary(date.today().isoformat())
        assert result is None

    def test_get_activity_summary_no_snapshot(self):
        """get_activity_summary() returns None when no snapshot exists."""
        result = fitbit_store.get_activity_summary(date.today().isoformat())
        assert result is None
