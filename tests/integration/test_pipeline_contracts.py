"""Cross-module API contract verification.

Ensures store functions return documented shapes and that cross-module
interfaces are compatible. Uses real aria_test PostgreSQL database.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

import json
import sys
from datetime import date, timedelta, datetime, time as dt_time, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import calendar_store
import health_store
import nutrition_store
import vehicle_store
import legal_store
import timer_store
import fitbit_store
import location_store
import db
import actions
import context

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot, seed_location,
    seed_timer, seed_reminder, seed_event, seed_legal, seed_vehicle,
)


# ---------------------------------------------------------------------------
# Store return shape contracts
# ---------------------------------------------------------------------------

class TestHealthStoreContract:
    """health_store.get_entries() returns dicts with required keys."""

    def test_entry_shape(self):
        today = date.today().isoformat()
        seed_health(today, category="meal", description="test lunch",
                    meal_type="lunch")
        entries = health_store.get_entries(days=1)
        assert len(entries) >= 1
        entry = entries[0]
        for key in ("id", "date", "category", "description",
                    "severity", "sleep_hours", "meal_type"):
            assert key in entry, f"Missing key '{key}' in health entry"


class TestNutritionStoreContract:
    """nutrition_store.get_items() returns dicts with required keys."""

    def test_entry_shape(self):
        today = date.today().isoformat()
        seed_nutrition(today, "Test Food", meal_type="lunch")
        items = nutrition_store.get_items(day=today)
        assert len(items) >= 1
        item = items[0]
        for key in ("id", "date", "time", "meal_type", "food_name",
                    "servings", "nutrients"):
            assert key in item, f"Missing key '{key}' in nutrition entry"


class TestVehicleStoreContract:
    """vehicle_store.get_entries() returns dicts with required keys."""

    def test_entry_shape(self):
        seed_vehicle(event_type="oil_change", description="Full synthetic",
                     mileage=45000, cost=65.0)
        entries = vehicle_store.get_entries()
        assert len(entries) >= 1
        entry = entries[0]
        for key in ("id", "date", "event_type", "description",
                    "mileage", "cost"):
            assert key in entry, f"Missing key '{key}' in vehicle entry"


class TestLegalStoreContract:
    """legal_store.get_entries() returns dicts with required keys."""

    def test_entry_shape(self):
        seed_legal(entry_type="note", description="Test legal note",
                   contacts=["John"])
        entries = legal_store.get_entries()
        assert len(entries) >= 1
        entry = entries[0]
        for key in ("id", "date", "entry_type", "description", "contacts"):
            assert key in entry, f"Missing key '{key}' in legal entry"


class TestEventStoreContract:
    """calendar_store.get_events() returns dicts with required keys."""

    def test_entry_shape(self):
        today = date.today().isoformat()
        seed_event(title="Test Event", event_date=today, time="14:00")
        events = calendar_store.get_events(start=today, end=today)
        assert len(events) >= 1
        event = events[0]
        for key in ("id", "title", "date", "time"):
            assert key in event, f"Missing key '{key}' in event"


class TestReminderStoreContract:
    """calendar_store.get_reminders() returns dicts with required keys."""

    def test_entry_shape(self):
        seed_reminder(text="Buy groceries", due=date.today().isoformat())
        reminders = calendar_store.get_reminders()
        assert len(reminders) >= 1
        reminder = reminders[0]
        for key in ("id", "text", "due", "done"):
            assert key in reminder, f"Missing key '{key}' in reminder"


class TestTimerStoreContract:
    """timer_store.get_active() returns dicts with required keys."""

    def test_entry_shape(self):
        seed_timer(label="Test Timer", delivery="sms",
                   priority="gentle", message="Timer fired")
        timers = timer_store.get_active()
        assert len(timers) >= 1
        timer = timers[0]
        for key in ("id", "label", "fire_at", "delivery",
                    "priority", "message"):
            assert key in timer, f"Missing key '{key}' in timer"


class TestLocationStoreContract:
    """location_store.get_latest() returns dict with required keys."""

    def test_entry_shape(self):
        seed_location("Home", lat=42.58, lon=-88.43, battery_pct=85)
        loc = location_store.get_latest()
        assert loc is not None
        for key in ("id", "timestamp", "lat", "lon",
                    "location", "battery_pct"):
            assert key in loc, f"Missing key '{key}' in location"


class TestFitbitActivityContract:
    """fitbit_store.get_activity_summary() returns dict with required keys."""

    def test_entry_shape(self):
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"activity": {
            "steps": 8500,
            "distances": [{"activity": "total", "distance": 3.5}],
            "caloriesOut": 2100,
            "activityCalories": 800,
            "fairlyActiveMinutes": 20,
            "veryActiveMinutes": 10,
            "sedentaryMinutes": 600,
            "floors": 5,
        }})
        result = fitbit_store.get_activity_summary(today)
        assert result is not None
        for key in ("steps", "distance_miles", "calories_total",
                    "active_minutes", "sedentary_minutes"):
            assert key in result, f"Missing key '{key}' in activity summary"


class TestFitbitSleepContract:
    """fitbit_store.get_sleep_summary() returns dict with required keys."""

    def test_entry_shape(self):
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"sleep": {
            "sleep": [{
                "isMainSleep": True,
                "minutesAsleep": 420,
                "efficiency": 88,
                "startTime": "2026-03-26T23:00:00",
                "endTime": "2026-03-27T06:00:00",
                "levels": {"summary": {
                    "deep": {"minutes": 80},
                    "light": {"minutes": 200},
                    "rem": {"minutes": 90},
                    "wake": {"minutes": 50},
                }},
            }]
        }})
        result = fitbit_store.get_sleep_summary(today)
        assert result is not None
        for key in ("duration_hours", "deep_minutes", "light_minutes",
                    "rem_minutes", "wake_minutes"):
            assert key in result, f"Missing key '{key}' in sleep summary"


class TestFitbitHeartContract:
    """fitbit_store.get_heart_summary() returns dict with required keys."""

    def test_entry_shape(self):
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"heart_rate": {
            "value": {
                "restingHeartRate": 68,
                "heartRateZones": [
                    {"name": "Fat Burn", "minutes": 30, "caloriesOut": 120.5},
                ],
            }
        }})
        result = fitbit_store.get_heart_summary(today)
        assert result is not None
        for key in ("resting_hr", "zones"):
            assert key in result, f"Missing key '{key}' in heart summary"


class TestNutritionDailyTotalsContract:
    """get_daily_totals() includes ALL NUTRIENT_FIELDS as keys."""

    def test_all_nutrient_fields_present(self):
        today = date.today().isoformat()
        seed_nutrition(today, "Test Food", calories=500)
        totals = nutrition_store.get_daily_totals(today)
        for field in nutrition_store.NUTRIENT_FIELDS:
            assert field in totals, f"Missing nutrient field '{field}' in daily totals"
        assert "item_count" in totals


# ---------------------------------------------------------------------------
# process_actions return contract
# ---------------------------------------------------------------------------

class TestProcessActionsContract:
    """process_actions always returns str and respects metadata/log_fn."""

    def test_always_returns_str(self):
        result = actions.process_actions_sync("no actions here")
        assert isinstance(result.to_response(), str)

    def test_always_returns_str_with_actions(self):
        text = 'Done <!--ACTION::{"action":"set_delivery","method":"voice"}-->'
        result = actions.process_actions_sync(text)
        assert isinstance(result.to_response(), str)

    def test_metadata_delivery_set(self):
        text = 'Ok <!--ACTION::{"action":"set_delivery","method":"voice"}-->'
        metadata = {}
        actions.process_actions_sync(text, metadata=metadata)
        assert metadata.get("delivery") == "voice"

    def test_log_fn_called_on_failure(self):
        text = '<!--ACTION::{"action":"complete_reminder","id":"nonexistent"}-->'
        log_calls = []
        def log_fn(text, status, **kwargs):
            log_calls.append((text, status, kwargs))
        actions.process_actions_sync(text, log_fn=log_fn)
        assert len(log_calls) > 0
        assert any(call[1] == "error" for call in log_calls)


# ---------------------------------------------------------------------------
# Context return contracts
# ---------------------------------------------------------------------------

class TestContextReturnContracts:
    """Context functions always return str."""

    @patch("context.redis_client")
    def test_gather_always_context_returns_str(self, mock_redis):
        mock_redis.get_active_tasks.return_value = []
        mock_redis.format_task_status.return_value = ""
        result = context.gather_always_context()
        assert isinstance(result, str)

    def test_gather_health_context_returns_str(self):
        result = context.gather_health_context()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    @patch("context.redis_client")
    @patch("context.weather")
    async def test_build_request_context_returns_str(self, mock_weather, mock_redis):
        mock_redis.get_active_tasks.return_value = []
        mock_redis.format_task_status.return_value = ""
        result = await context.build_request_context("hello")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    @patch("context.redis_client")
    @patch("context.weather")
    async def test_get_context_for_text_returns_str(self, mock_weather, mock_redis):
        mock_redis.get_active_tasks.return_value = []
        mock_redis.format_task_status.return_value = ""
        result = await context._get_context_for_text("hello")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Serialization contracts
# ---------------------------------------------------------------------------

class TestSerializationContracts:
    """serialize_row produces JSON-serializable output."""

    def test_serialized_row_is_json_serializable(self):
        today = date.today().isoformat()
        seed_health(today, category="meal", description="test food",
                    meal_type="lunch")
        entries = health_store.get_entries(days=1)
        assert len(entries) >= 1
        # Must not raise
        json_str = json.dumps(entries[0])
        assert isinstance(json_str, str)

    def test_serialize_row_converts_date_to_iso(self):
        row = {"my_date": date(2026, 3, 27), "name": "test"}
        result = db.serialize_row(row)
        assert result["my_date"] == "2026-03-27"
        assert isinstance(result["my_date"], str)

    def test_serialize_row_converts_tz_datetime_to_naive(self):
        aware_dt = datetime(2026, 3, 27, 14, 30, 0, tzinfo=timezone.utc)
        row = {"ts": aware_dt, "val": 42}
        result = db.serialize_row(row)
        # Result should be a naive ISO string (no +00:00)
        assert isinstance(result["ts"], str)
        assert "+" not in result["ts"]
        assert "Z" not in result["ts"]


# ---------------------------------------------------------------------------
# Cross-store type contracts
# ---------------------------------------------------------------------------

class TestCrossStoreTypeContracts:
    """Types match between cooperating stores."""

    def test_daily_totals_compatible_with_check_limits(self):
        """get_daily_totals() return type matches what check_limits() expects."""
        today = date.today().isoformat()
        seed_nutrition(today, "Test Food", calories=500, protein_g=30)
        totals = nutrition_store.get_daily_totals(today)
        # check_limits reads numeric values from totals dict
        for nutrient in nutrition_store.DAILY_TARGETS:
            val = totals.get(nutrient, 0)
            assert isinstance(val, (int, float)), (
                f"Daily total '{nutrient}' is {type(val)}, expected numeric"
            )

    @patch("nutrition_store.fitbit_store")
    def test_activity_summary_compatible_with_net_calories(self, mock_fitbit):
        """get_activity_summary() return type matches get_net_calories() expectation."""
        mock_fitbit.get_activity_summary.return_value = {
            "steps": 8000,
            "calories_total": 2200,
            "active_minutes": 30,
            "sedentary_minutes": 600,
            "distance_miles": 3.5,
            "floors": 5,
            "calories_active": 800,
        }
        today = date.today().isoformat()
        seed_nutrition(today, "Test Food", calories=500)
        net = nutrition_store.get_net_calories(today)
        assert isinstance(net, dict)
        assert isinstance(net["consumed"], (int, float))
        assert isinstance(net["burned"], (int, float))
        assert isinstance(net["net"], (int, float))

    def test_is_simple_query_always_returns_bool(self):
        """_is_simple_query always returns bool regardless of input."""
        import aria_api
        test_inputs = [
            "hello", "set a timer", "what's the weather",
            "", "good morning", "hey", "hi",
            "a" * 5000, "\n\n\n",
        ]
        for text in test_inputs:
            result = aria_api._is_simple_query(text)
            assert isinstance(result, bool), (
                f"_is_simple_query({text!r}) returned {type(result)}"
            )
