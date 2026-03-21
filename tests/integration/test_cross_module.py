"""Cross-module integration tests — verify data flows correctly between modules."""

from datetime import date, datetime, timedelta
from unittest.mock import patch, AsyncMock

import pytest

import calendar_store
import health_store
import nutrition_store
import fitbit_store
import vehicle_store
import legal_store
import location_store
import timer_store
import context


class TestNutritionFitbitIntegration:
    """Verify fitbit_store.get_activity_summary() output feeds into nutrition_store."""

    def test_net_calories_uses_fitbit_snapshot(self):
        today = date.today().isoformat()

        # Seed nutrition data
        nutrition_store.add_item(
            "Lunch", entry_date=today,
            nutrients={"calories": 800},
        )

        # Seed Fitbit activity via snapshot
        fitbit_store.save_snapshot({
            "date": today,
            "activity": {
                "steps": 10000, "caloriesOut": 2500,
                "activityCalories": 900,
                "fairlyActiveMinutes": 20, "veryActiveMinutes": 15,
                "sedentaryMinutes": 500, "floors": 8,
                "distances": [{"activity": "total", "distance": 6.5}],
            },
        })

        net = nutrition_store.get_net_calories(today)
        assert net["consumed"] == 800
        assert net["burned"] == 2500
        assert net["net"] == -1700


class TestHealthContextIntegration:
    """Verify gather_health_context reads from all health-related stores."""

    def test_combined_health_context(self):
        today = datetime.now().strftime("%Y-%m-%d")

        # Seed health meal entry
        health_store.add_entry(
            today, "meal", "grilled chicken", meal_type="lunch",
        )

        # Seed nutrition data
        nutrition_store.add_item(
            "Grilled Chicken", meal_type="lunch", entry_date=today,
            nutrients={"calories": 450, "protein_g": 45},
        )

        # Seed Fitbit data
        fitbit_store.save_snapshot({
            "date": today,
            "heart_rate": {"value": {"restingHeartRate": 66}},
            "activity": {
                "steps": 5000, "caloriesOut": 2200,
                "activityCalories": 600,
                "fairlyActiveMinutes": 10, "veryActiveMinutes": 5,
                "sedentaryMinutes": 700, "floors": 3,
                "distances": [{"activity": "total", "distance": 3.2}],
            },
        })

        ctx = context.gather_health_context()
        assert "grilled chicken" in ctx.lower()
        assert "450" in ctx or "Calories" in ctx
        assert "Diet day" in ctx


class TestBriefingContextIntegration:
    """Verify gather_briefing_context aggregates all data stores."""

    @pytest.mark.asyncio
    @patch("context.weather.get_current_conditions", new_callable=AsyncMock,
           return_value={"description": "Sunny", "temperature_f": 55,
                        "humidity": 40, "wind_mph": 10})
    @patch("context.weather.get_forecast", new_callable=AsyncMock,
           return_value=[{"name": "Today", "temperature": 55, "unit": "F",
                         "summary": "Sunny"}])
    @patch("context.weather.get_alerts", new_callable=AsyncMock, return_value=[])
    @patch("context.news.get_news_digest", new_callable=AsyncMock,
           return_value={"tech": [{"title": "Test Article"}]})
    async def test_briefing_includes_all_stores(self, mock_news, mock_alerts,
                                                  mock_forecast, mock_weather):
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        # Seed calendar
        calendar_store.add_event(title="Doctor", event_date=today, time="10:00")
        calendar_store.add_event(title="Meeting", event_date=tomorrow, time="14:00")

        # Seed reminder
        calendar_store.add_reminder(text="Buy groceries", due=today)

        # Seed vehicle
        vehicle_store.add_entry(today, "inspection", "Annual check", mileage=145000)

        # Seed health
        health_store.add_entry(today, "sleep", "good", sleep_hours=7.5)

        # Seed Fitbit
        fitbit_store.save_snapshot({
            "date": today,
            "heart_rate": {"value": {"restingHeartRate": 64}},
        })

        briefing = await context.gather_briefing_context()
        assert "Doctor" in briefing
        assert "Meeting" in briefing  # tomorrow's prep
        assert "Buy groceries" in briefing
        assert "Sunny" in briefing
        assert "inspection" in briefing or "Annual check" in briefing


class TestDebriefContextIntegration:
    @pytest.mark.asyncio
    @patch("context.weather.get_forecast", new_callable=AsyncMock,
           return_value=[{"name": "Tonight", "temperature": 40, "unit": "F",
                         "summary": "Clear"}])
    async def test_debrief_includes_todays_data(self, mock_forecast):
        today = datetime.now().strftime("%Y-%m-%d")

        # Seed today's activity
        calendar_store.add_event(title="Completed task", event_date=today)
        health_store.add_entry(today, "meal", "dinner salad", meal_type="dinner")

        debrief = await context.gather_debrief_context()
        assert "Completed task" in debrief
        assert "dinner salad" in debrief


class TestBuildRequestContextIntegration:
    @pytest.mark.asyncio
    async def test_vehicle_keywords_inject_real_data(self):
        vehicle_store.add_entry("2026-03-15", "oil_change", "Synthetic 5W-30",
                               mileage=145000)

        ctx = await context.build_request_context("When was my last xterra oil change?")
        assert "oil_change" in ctx
        assert "145000" in ctx or "Synthetic" in ctx

    @pytest.mark.asyncio
    async def test_legal_keywords_inject_real_data(self):
        future = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        legal_store.add_entry(future, "court_date", "Hearing on motion")

        ctx = await context.build_request_context("What's my next court date?")
        assert "court_date" in ctx
        assert "Hearing" in ctx

    @pytest.mark.asyncio
    async def test_timer_keywords_inject_real_data(self):
        fire_at = (datetime.now() + timedelta(hours=1)).isoformat()
        timer_store.add_timer("Laundry", fire_at, delivery="sms",
                              message="Laundry done!")

        ctx = await context.build_request_context("How long is left on my timer?")
        assert "Laundry" in ctx
