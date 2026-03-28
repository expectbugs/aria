"""Context building pipeline tests against a real PostgreSQL database.

Tests gather_always_context(), build_request_context(), _get_context_for_text(),
gather_health_context(), and keyword-triggered context injection with real DB data.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
Mocks: weather module, news module, redis_client (no Redis needed).
"""

import asyncio
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import context
import calendar_store
import health_store
import nutrition_store
import vehicle_store
import legal_store
import timer_store
import fitbit_store
import location_store
import db

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_legal, seed_vehicle, seed_request_log, seed_nudge_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Return a patch for the news module digest."""
    return AsyncMock(return_value={
        "tech": [{"title": "AI Advances", "summary": "Big progress"}],
    })


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# gather_always_context()
# ---------------------------------------------------------------------------

class TestGatherAlwaysContext:
    def test_datetime_included(self):
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()
        assert "Current date and time:" in result

    def test_active_timers_shown(self):
        seed_timer(label="Oven Timer", fire_at=(datetime.now() + timedelta(minutes=30)).isoformat())
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()
        assert "Oven Timer" in result
        assert "Active timers:" in result

    def test_active_reminders_shown(self):
        seed_reminder(text="Buy groceries", due=date.today().isoformat())
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()
        assert "Buy groceries" in result
        assert "Active reminders:" in result

    def test_location_shown(self):
        seed_location(location_name="Downtown Milwaukee", battery_pct=72)
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()
        assert "Downtown Milwaukee" in result
        assert "Location:" in result

    def test_battery_shown(self):
        seed_location(location_name="Home", battery_pct=42)
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()
        assert "42%" in result
        assert "Phone battery:" in result

    def test_exercise_mode_shown(self):
        # Start exercise to create an active session
        with patch("fitbit_store.config.OWNER_BIRTH_DATE", "1990-01-01"):
            fitbit_store.start_exercise("running")
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()
        assert "EXERCISE MODE ACTIVE" in result

    def test_empty_db_no_crash(self):
        """Empty database should not crash — just returns datetime."""
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()
        assert "Current date and time:" in result
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Keyword matching in build_request_context()
# ---------------------------------------------------------------------------

class TestKeywordContext:
    def test_weather_keyword_triggers_weather(self):
        current, forecast, alerts = _mock_weather()
        with patch("context.weather.get_current_conditions", current), \
             patch("context.weather.get_forecast", forecast), \
             patch("context.weather.get_alerts", alerts), \
             patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context.build_request_context("What's the weather like?"))
        assert "Partly Cloudy" in result
        assert "55" in result

    def test_health_keyword_triggers_health(self):
        today = date.today().isoformat()
        seed_health(day=today, category="pain", description="back pain", severity=5)
        seed_nutrition(day=today, food_name="Oatmeal", meal_type="breakfast",
                       calories=300, protein_g=10)
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context.build_request_context("How's my health today?"))
        assert "Oatmeal" in result or "Nutrition" in result or "calories" in result.lower()

    def test_vehicle_keyword_triggers_vehicle(self):
        seed_vehicle(description="Full synthetic oil change", event_type="oil_change",
                     mileage=145000)
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context.build_request_context("How is my xterra doing?"))
        assert "oil_change" in result or "synthetic" in result.lower()

    def test_legal_keyword_triggers_legal(self):
        seed_legal(description="Custody hearing", entry_type="court_date",
                   entry_date=(date.today() + timedelta(days=5)).isoformat())
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context.build_request_context("What's going on with my legal case?"))
        assert "Custody hearing" in result

    def test_calendar_keyword_expands_week(self):
        # Event 5 days from now should appear with calendar keyword
        future_date = (date.today() + timedelta(days=5)).isoformat()
        seed_event(title="Team Meeting", event_date=future_date, time="10:00")
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context.build_request_context("What's my schedule this week?"))
        assert "Team Meeting" in result

    def test_no_keywords_minimal_context(self):
        """Query without any keywords should still return Tier 1 context."""
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context.build_request_context("Tell me a joke"))
        assert "Current date and time:" in result
        # Should NOT have weather, vehicle, legal, etc.
        assert "Vehicle log:" not in result
        assert "Legal case log:" not in result

    def test_combined_health_and_vehicle_keywords(self):
        today = date.today().isoformat()
        seed_nutrition(day=today, food_name="Salad", calories=200, protein_g=10)
        seed_vehicle(description="Tire rotation", event_type="tire_rotation")
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context.build_request_context(
                "I ate a salad and need to check my truck tires"
            ))
        # Both health and vehicle should be present
        assert "Salad" in result or "calories" in result.lower()
        assert "tire_rotation" in result or "Tire rotation" in result


# ---------------------------------------------------------------------------
# _get_context_for_text() — briefing and debrief routing
# ---------------------------------------------------------------------------

class TestContextRouting:
    def test_good_morning_triggers_briefing(self):
        current, forecast, alerts = _mock_weather()
        news_digest = _mock_news()
        with patch("context.weather.get_current_conditions", current), \
             patch("context.weather.get_forecast", forecast), \
             patch("context.weather.get_alerts", alerts), \
             patch("context.news.get_news_digest", news_digest), \
             patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context._get_context_for_text("good morning"))
        assert "Partly Cloudy" in result or "weather" in result.lower()

    def test_briefing_includes_calendar(self):
        today = date.today().isoformat()
        seed_event(title="Morning Standup", event_date=today, time="09:00")
        current, forecast, alerts = _mock_weather()
        news_digest = _mock_news()
        with patch("context.weather.get_current_conditions", current), \
             patch("context.weather.get_forecast", forecast), \
             patch("context.weather.get_alerts", alerts), \
             patch("context.news.get_news_digest", news_digest), \
             patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context._get_context_for_text("good morning"))
        assert "Morning Standup" in result

    def test_briefing_includes_nutrition(self):
        today = date.today().isoformat()
        seed_nutrition(day=today, food_name="Eggs", calories=300, protein_g=20)
        current, forecast, alerts = _mock_weather()
        news_digest = _mock_news()
        with patch("context.weather.get_current_conditions", current), \
             patch("context.weather.get_forecast", forecast), \
             patch("context.weather.get_alerts", alerts), \
             patch("context.news.get_news_digest", news_digest), \
             patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context._get_context_for_text("good morning"))
        # Weekly summary is used in briefings
        assert "Nutrition" in result or "nutrition" in result.lower() or "calories" in result.lower()

    def test_good_night_triggers_debrief(self):
        seed_request_log("hello", response="Hi there!")
        current, forecast, alerts = _mock_weather()
        with patch("context.weather.get_forecast", forecast), \
             patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context._get_context_for_text("good night"))
        # Debrief includes interactions
        assert "interaction" in result.lower() or "hello" in result.lower()

    def test_debrief_includes_todays_events(self):
        today = date.today().isoformat()
        seed_event(title="Court Appearance", event_date=today, time="14:00")
        seed_request_log("test")
        current, forecast, alerts = _mock_weather()
        with patch("context.weather.get_forecast", forecast), \
             patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context._get_context_for_text("good night"))
        assert "Court Appearance" in result

    def test_debrief_includes_nutrition(self):
        today = date.today().isoformat()
        seed_nutrition(day=today, food_name="Grilled Chicken", calories=400, protein_g=35)
        current, forecast, alerts = _mock_weather()
        with patch("context.weather.get_forecast", forecast), \
             patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = _run(context._get_context_for_text("good night"))
        assert "Grilled Chicken" in result or "calories" in result.lower()


# ---------------------------------------------------------------------------
# gather_health_context()
# ---------------------------------------------------------------------------

class TestGatherHealthContext:
    def test_meals_today(self):
        today = date.today().isoformat()
        seed_health(day=today, category="meal", description="Oatmeal with berries",
                    meal_type="breakfast")
        result = context.gather_health_context()
        assert "Oatmeal with berries" in result

    def test_nutrition_tracking(self):
        today = date.today().isoformat()
        seed_nutrition(day=today, food_name="Brown Rice", calories=215, protein_g=5,
                       dietary_fiber_g=3.5)
        result = context.gather_health_context()
        assert "Brown Rice" in result or "Nutrition" in result

    def test_fitbit_data(self):
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {
            "sleep": {"sleep": [{"isMainSleep": True, "minutesAsleep": 420,
                                  "efficiency": 90,
                                  "levels": {"summary": {
                                      "deep": {"minutes": 60},
                                      "light": {"minutes": 200},
                                      "rem": {"minutes": 100},
                                      "wake": {"minutes": 60},
                                  }}}]},
            "heart_rate": {"value": {"restingHeartRate": 62,
                                      "heartRateZones": []}},
        })
        result = context.gather_health_context()
        assert "Sleep" in result or "sleep" in result.lower()

    def test_calorie_balance(self):
        today = date.today().isoformat()
        seed_nutrition(day=today, food_name="Lunch", calories=600, protein_g=30)
        seed_fitbit_snapshot(today, {
            "activity": {"steps": 8000, "caloriesOut": 2200,
                         "activityCalories": 500, "fairlyActiveMinutes": 30,
                         "veryActiveMinutes": 15, "sedentaryMinutes": 600,
                         "floors": 5, "distances": [{"activity": "total", "distance": 3.5}]},
        })
        result = context.gather_health_context()
        assert "balance" in result.lower() or "consumed" in result.lower()

    def test_yesterday_summary(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        seed_nutrition(day=yesterday, food_name="Dinner", calories=700, protein_g=40,
                       dietary_fiber_g=8)
        result = context.gather_health_context()
        assert "Yesterday" in result

    def test_health_patterns(self):
        # Seed 3+ days of pain to trigger pattern detection
        for i in range(4):
            d = (date.today() - timedelta(days=i)).isoformat()
            seed_health(day=d, category="pain", description="back pain", severity=5)
        result = context.gather_health_context()
        assert "back pain" in result.lower() or "pattern" in result.lower()

    def test_diet_day_counter(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with patch.object(context.config, "DIET_START_DATE", yesterday):
            result = context.gather_health_context()
        assert "Diet day 2" in result


# ===========================================================================
# Total: 30 tests
# ===========================================================================
