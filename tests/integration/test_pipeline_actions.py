"""End-to-end ACTION block pipeline tests against a real PostgreSQL database.

Tests process_actions() from actions.py with the real aria_test database.
Each test constructs a response string with embedded ACTION blocks, calls
process_actions(), then verifies the DB state and returned text.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
Mocks: Redis (for dispatch_action), SMS delivery, phone push.
"""

import json
import re
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

import pytest
from freezegun import freeze_time

import actions
import calendar_store
import health_store
import nutrition_store
import vehicle_store
import legal_store
import timer_store
import fitbit_store
import db

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_legal, seed_vehicle,
)


def _action(payload: dict) -> str:
    """Build an ACTION block string from a dict."""
    return f"<!--ACTION::{json.dumps(payload)}-->"


def _response_with(text: str, *action_dicts) -> str:
    """Build a response string with embedded ACTION blocks."""
    blocks = " ".join(_action(d) for d in action_dicts)
    return f"{text} {blocks}"


# ===========================================================================
# Calendar Actions — Events
# ===========================================================================

class TestAddEvent:
    def test_basic(self):
        resp = _response_with(
            "Done!",
            {"action": "add_event", "title": "Dentist", "date": "2026-04-15",
             "time": "14:30", "notes": "Dr. Smith"},
        )
        cleaned = actions.process_actions(resp)
        assert "Done!" in cleaned
        assert "ACTION" not in cleaned

        events = calendar_store.get_events(start="2026-04-15", end="2026-04-15")
        assert len(events) == 1
        assert events[0]["title"] == "Dentist"
        assert events[0]["date"] == "2026-04-15"
        assert events[0]["time"].startswith("14:30")
        assert events[0]["notes"] == "Dr. Smith"

    def test_unicode_emoji_title(self):
        resp = _response_with(
            "Added!",
            {"action": "add_event", "title": "Birthday Party \U0001f382\u2728",
             "date": "2026-05-01"},
        )
        actions.process_actions(resp)

        events = calendar_store.get_events(start="2026-05-01", end="2026-05-01")
        assert len(events) == 1
        assert "\U0001f382" in events[0]["title"]
        assert "\u2728" in events[0]["title"]


class TestModifyEvent:
    def test_modify(self):
        ev = seed_event(title="Old Title", event_date="2026-04-20", time="09:00")
        eid = ev["id"]

        resp = _response_with(
            "Updated!",
            {"action": "modify_event", "id": eid, "title": "New Title",
             "time": "10:00"},
        )
        actions.process_actions(resp)

        events = calendar_store.get_events(start="2026-04-20", end="2026-04-20")
        assert len(events) == 1
        assert events[0]["title"] == "New Title"
        assert events[0]["time"].startswith("10:00")


class TestDeleteEvent:
    def test_delete(self):
        ev = seed_event(title="To Delete", event_date="2026-04-22")
        eid = ev["id"]

        resp = _response_with(
            "Deleted!",
            {"action": "delete_event", "id": eid},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        events = calendar_store.get_events(start="2026-04-22", end="2026-04-22")
        assert len(events) == 0

    def test_nonexistent_id(self):
        resp = _response_with(
            "Deleted!",
            {"action": "delete_event", "id": "nonexistent99"},
        )
        cleaned = actions.process_actions(resp)
        assert "no event found" in cleaned.lower()


# ===========================================================================
# Calendar Actions — Reminders
# ===========================================================================

class TestAddReminder:
    def test_basic(self):
        resp = _response_with(
            "Reminder set!",
            {"action": "add_reminder", "text": "Buy milk", "due": "2026-04-10"},
        )
        actions.process_actions(resp)

        reminders = calendar_store.get_reminders()
        assert any(r["text"] == "Buy milk" for r in reminders)

    def test_location_with_trigger(self):
        resp = _response_with(
            "Set!",
            {"action": "add_reminder", "text": "Pick up package",
             "location": "Post Office", "location_trigger": "depart"},
        )
        actions.process_actions(resp)

        reminders = calendar_store.get_reminders()
        r = next(r for r in reminders if r["text"] == "Pick up package")
        assert r["location"] == "Post Office"
        assert r["location_trigger"] == "depart"

    def test_recurring(self):
        resp = _response_with(
            "Done!",
            {"action": "add_reminder", "text": "Take medication",
             "recurring": "daily"},
        )
        actions.process_actions(resp)

        reminders = calendar_store.get_reminders()
        r = next(r for r in reminders if r["text"] == "Take medication")
        assert r["recurring"] == "daily"


class TestCompleteReminder:
    def test_complete(self):
        rem = seed_reminder(text="Finish report", due="2026-04-08")
        rid = rem["id"]

        resp = _response_with(
            "Marked done!",
            {"action": "complete_reminder", "id": rid},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        # Should not appear in active reminders
        active = calendar_store.get_reminders(include_done=False)
        assert not any(r["id"] == rid for r in active)

        # Should appear in all reminders with done=True
        all_rem = calendar_store.get_reminders(include_done=True)
        r = next(r for r in all_rem if r["id"] == rid)
        assert r["done"] is True


class TestDeleteReminder:
    def test_delete(self):
        rem = seed_reminder(text="To Delete")
        rid = rem["id"]

        resp = _response_with(
            "Deleted!",
            {"action": "delete_reminder", "id": rid},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        all_rem = calendar_store.get_reminders(include_done=True)
        assert not any(r["id"] == rid for r in all_rem)


class TestMultipleCalendarActions:
    def test_event_and_reminder_in_one_response(self):
        resp = _response_with(
            "All set!",
            {"action": "add_event", "title": "Meeting", "date": "2026-04-25",
             "time": "15:00"},
            {"action": "add_reminder", "text": "Prepare slides",
             "due": "2026-04-24"},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        events = calendar_store.get_events(start="2026-04-25", end="2026-04-25")
        assert len(events) == 1
        assert events[0]["title"] == "Meeting"

        reminders = calendar_store.get_reminders()
        assert any(r["text"] == "Prepare slides" for r in reminders)


# ===========================================================================
# Health Actions
# ===========================================================================

class TestLogHealth:
    def test_with_category_and_severity(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_health", "date": "2026-03-27",
             "category": "pain", "description": "Lower back pain, left side",
             "severity": 6},
        )
        actions.process_actions(resp)

        entries = health_store.get_entries(category="pain")
        assert len(entries) == 1
        assert entries[0]["description"] == "Lower back pain, left side"
        assert entries[0]["severity"] == 6

    def test_meal_with_meal_type(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_health", "date": "2026-03-27",
             "category": "meal", "description": "Grilled chicken and rice",
             "meal_type": "dinner"},
        )
        actions.process_actions(resp)

        entries = health_store.get_entries(category="meal")
        assert len(entries) == 1
        assert entries[0]["meal_type"] == "dinner"

    def test_duplicate_blocked(self):
        """Identical log_health with same content_hash should be blocked."""
        action_dict = {
            "action": "log_health", "date": "2026-03-27",
            "category": "pain", "description": "Headache",
        }
        resp1 = _response_with("First!", action_dict)
        actions.process_actions(resp1)

        resp2 = _response_with("Second!", action_dict)
        actions.process_actions(resp2)

        entries = health_store.get_entries(category="pain")
        assert len(entries) == 1

    def test_sleep_with_hours(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_health", "date": "2026-03-27",
             "category": "sleep", "description": "Slept okay, woke once",
             "sleep_hours": 6.5},
        )
        actions.process_actions(resp)

        entries = health_store.get_entries(category="sleep")
        assert len(entries) == 1
        assert entries[0]["sleep_hours"] == pytest.approx(6.5, abs=0.01)


class TestDeleteHealthEntry:
    def test_delete(self):
        result = seed_health("2026-03-27", category="pain",
                             description="Shoulder ache", severity=4)
        eid = result["entry"]["id"]

        resp = _response_with(
            "Deleted!",
            {"action": "delete_health_entry", "id": eid},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        entries = health_store.get_entries(category="pain")
        assert len(entries) == 0

    def test_delete_nonexistent(self):
        resp = _response_with(
            "Deleted!",
            {"action": "delete_health_entry", "id": "ghost999"},
        )
        cleaned = actions.process_actions(resp)
        assert "no entry found" in cleaned.lower()


# ===========================================================================
# Vehicle Actions
# ===========================================================================

class TestLogVehicle:
    def test_with_mileage_and_cost(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_vehicle", "date": "2026-03-20",
             "event_type": "oil_change",
             "description": "Full synthetic 5W-30",
             "mileage": 148500, "cost": 52.99},
        )
        actions.process_actions(resp)

        entries = vehicle_store.get_entries()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "oil_change"
        assert entries[0]["mileage"] == 148500
        assert entries[0]["cost"] == pytest.approx(52.99, abs=0.01)

    def test_without_optional_fields(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_vehicle", "date": "2026-03-20",
             "event_type": "inspection",
             "description": "Annual state inspection passed"},
        )
        actions.process_actions(resp)

        entries = vehicle_store.get_entries()
        assert len(entries) == 1
        assert entries[0]["mileage"] is None
        assert entries[0]["cost"] is None


class TestDeleteVehicleEntry:
    def test_delete(self):
        entry = seed_vehicle(event_type="tire_rotation",
                             description="All four tires", mileage=150000)
        eid = entry["id"]

        resp = _response_with(
            "Deleted!",
            {"action": "delete_vehicle_entry", "id": eid},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        entries = vehicle_store.get_entries()
        assert len(entries) == 0

    def test_delete_nonexistent(self):
        resp = _response_with(
            "Deleted!",
            {"action": "delete_vehicle_entry", "id": "ghost999"},
        )
        cleaned = actions.process_actions(resp)
        assert "no entry found" in cleaned.lower()


# ===========================================================================
# Legal Actions
# ===========================================================================

class TestLogLegal:
    def test_with_contacts(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_legal", "date": "2026-03-27",
             "entry_type": "court_date",
             "description": "Hearing scheduled for workers comp",
             "contacts": ["Attorney Smith", "Judge Brown"]},
        )
        actions.process_actions(resp)

        entries = legal_store.get_entries()
        assert len(entries) == 1
        assert entries[0]["entry_type"] == "court_date"
        assert "Attorney Smith" in entries[0]["contacts"]
        assert "Judge Brown" in entries[0]["contacts"]

    def test_without_contacts(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_legal", "date": "2026-03-27",
             "entry_type": "note",
             "description": "Reviewed case documents"},
        )
        actions.process_actions(resp)

        entries = legal_store.get_entries()
        assert len(entries) == 1
        assert entries[0]["contacts"] == []


class TestDeleteLegalEntry:
    def test_delete(self):
        entry = seed_legal(entry_type="note", description="Test note")
        eid = entry["id"]

        resp = _response_with(
            "Deleted!",
            {"action": "delete_legal_entry", "id": eid},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        entries = legal_store.get_entries()
        assert len(entries) == 0

    def test_delete_nonexistent(self):
        resp = _response_with(
            "Deleted!",
            {"action": "delete_legal_entry", "id": "ghost999"},
        )
        cleaned = actions.process_actions(resp)
        assert "no entry found" in cleaned.lower()


# ===========================================================================
# Nutrition Actions
# ===========================================================================

class TestLogNutrition:
    @freeze_time("2026-03-27 12:00:00")
    def test_full_nutrients(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Chicken breast",
             "date": "2026-03-27", "meal_type": "lunch",
             "servings": 1.0, "serving_size": "6 oz",
             "source": "manual",
             "nutrients": {
                 "calories": 280, "protein_g": 53, "total_fat_g": 6,
                 "saturated_fat_g": 1.5, "cholesterol_mg": 130,
                 "sodium_mg": 85, "total_carb_g": 0,
             }},
        )
        actions.process_actions(resp)

        items = nutrition_store.get_items(day="2026-03-27")
        assert len(items) == 1
        assert items[0]["food_name"] == "Chicken breast"
        assert items[0]["nutrients"]["calories"] == 280
        assert items[0]["nutrients"]["protein_g"] == 53
        assert items[0]["meal_type"] == "lunch"

    @freeze_time("2026-03-27 12:00:00")
    def test_sparse_nutrients(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Protein bar",
             "date": "2026-03-27", "meal_type": "snack",
             "nutrients": {"calories": 200, "protein_g": 20}},
        )
        actions.process_actions(resp)

        items = nutrition_store.get_items(day="2026-03-27")
        assert len(items) == 1
        assert items[0]["nutrients"]["calories"] == 200
        assert items[0]["nutrients"]["protein_g"] == 20

    @freeze_time("2026-03-27 12:00:00")
    def test_servings_multiplier(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Rice",
             "date": "2026-03-27", "meal_type": "dinner",
             "servings": 2.0,
             "nutrients": {"calories": 200, "protein_g": 4}},
        )
        actions.process_actions(resp)

        # Daily totals should multiply by servings
        totals = nutrition_store.get_daily_totals("2026-03-27")
        assert totals["calories"] == pytest.approx(400, abs=1)
        assert totals["protein_g"] == pytest.approx(8, abs=1)

    @freeze_time("2026-03-27 12:00:00")
    def test_duplicate_blocked(self):
        action_dict = {
            "action": "log_nutrition", "food_name": "Apple",
            "date": "2026-03-27", "meal_type": "snack",
            "nutrients": {"calories": 95},
        }
        resp1 = _response_with("First!", action_dict)
        actions.process_actions(resp1)

        resp2 = _response_with("Second!", action_dict)
        actions.process_actions(resp2)

        items = nutrition_store.get_items(day="2026-03-27")
        assert len(items) == 1

    @freeze_time("2026-03-27 12:00:00")
    def test_missing_date_defaults_to_today(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Banana",
             "meal_type": "snack",
             "nutrients": {"calories": 105}},
        )
        actions.process_actions(resp)

        items = nutrition_store.get_items(day="2026-03-27")
        assert len(items) == 1
        assert items[0]["food_name"] == "Banana"

    @freeze_time("2026-03-27 12:00:00")
    def test_future_date_rejected(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Future food",
             "date": "2026-04-01", "meal_type": "lunch",
             "nutrients": {"calories": 300}},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" in cleaned.lower() or "future" in cleaned.lower()

        items = nutrition_store.get_items(day="2026-04-01")
        assert len(items) == 0

    @freeze_time("2026-03-27 12:00:00")
    def test_stale_date_rejected(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Old food",
             "date": "2026-03-15", "meal_type": "lunch",
             "nutrients": {"calories": 300}},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" in cleaned.lower() or "7 days" in cleaned.lower()

        items = nutrition_store.get_items(day="2026-03-15")
        assert len(items) == 0

    @freeze_time("2026-03-27 12:00:00")
    def test_zero_servings_rejected(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Zero",
             "date": "2026-03-27", "meal_type": "lunch",
             "servings": 0,
             "nutrients": {"calories": 300}},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" in cleaned.lower()

    @freeze_time("2026-03-27 12:00:00")
    def test_extreme_calories_rejected(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Extreme",
             "date": "2026-03-27", "meal_type": "lunch",
             "nutrients": {"calories": 6000}},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" in cleaned.lower() or "sanity" in cleaned.lower()

        items = nutrition_store.get_items(day="2026-03-27")
        assert len(items) == 0


class TestDeleteNutritionEntry:
    @freeze_time("2026-03-27 12:00:00")
    def test_delete(self):
        result = seed_nutrition("2026-03-27", "Chicken", calories=500)
        eid = result["entry"]["id"]

        resp = _response_with(
            "Deleted!",
            {"action": "delete_nutrition_entry", "id": eid},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        items = nutrition_store.get_items(day="2026-03-27")
        assert len(items) == 0


class TestNutritionIntraResponseDuplicate:
    @freeze_time("2026-03-27 12:00:00")
    def test_same_food_twice_in_one_response(self):
        """Intra-response dedup: same food_name+date+meal_type appears twice."""
        action_dict = {
            "action": "log_nutrition", "food_name": "Apple",
            "date": "2026-03-27", "meal_type": "snack",
            "nutrients": {"calories": 95},
        }
        resp = f"Logged! {_action(action_dict)} {_action(action_dict)}"
        actions.process_actions(resp)

        items = nutrition_store.get_items(day="2026-03-27")
        assert len(items) == 1


class TestNutritionDateCrossCheck:
    @freeze_time("2026-03-27 12:00:00")
    def test_health_nutrition_date_mismatch_aborts(self):
        """log_health and log_nutrition for same meal_type with different dates."""
        resp = _response_with(
            "Logged your lunch!",
            {"action": "log_health", "date": "2026-03-27",
             "category": "meal", "description": "Chicken and rice",
             "meal_type": "lunch"},
            {"action": "log_nutrition", "food_name": "Chicken and rice",
             "date": "2026-03-26", "meal_type": "lunch",
             "nutrients": {"calories": 500}},
        )
        cleaned = actions.process_actions(resp)
        assert "DATA QUALITY ERROR" in cleaned
        assert "Date mismatch" in cleaned

        # Neither action should have executed
        items = nutrition_store.get_items(day="2026-03-26")
        assert len(items) == 0
        health = health_store.get_entries(category="meal")
        assert len(health) == 0


# ===========================================================================
# Timer Actions
# ===========================================================================

class TestSetTimer:
    @freeze_time("2026-03-27 14:00:00")
    def test_with_minutes(self):
        resp = _response_with(
            "Timer set!",
            {"action": "set_timer", "label": "Laundry",
             "minutes": 30, "message": "Laundry is done!"},
        )
        actions.process_actions(resp)

        timers = timer_store.get_active()
        assert len(timers) == 1
        assert timers[0]["label"] == "Laundry"
        fire_at = datetime.fromisoformat(timers[0]["fire_at"])
        expected = datetime(2026, 3, 27, 14, 30, 0)
        assert abs((fire_at - expected).total_seconds()) < 5

    @freeze_time("2026-03-27 14:00:00")
    def test_absolute_time_future_today(self):
        resp = _response_with(
            "Timer set!",
            {"action": "set_timer", "label": "Meeting reminder",
             "time": "16:00", "message": "Meeting in 15 min"},
        )
        actions.process_actions(resp)

        timers = timer_store.get_active()
        assert len(timers) == 1
        fire_at = datetime.fromisoformat(timers[0]["fire_at"])
        assert fire_at.date() == date(2026, 3, 27)
        assert fire_at.hour == 16
        assert fire_at.minute == 0

    @freeze_time("2026-03-27 18:00:00")
    def test_absolute_time_past_today_wraps_to_tomorrow(self):
        resp = _response_with(
            "Timer set!",
            {"action": "set_timer", "label": "Morning alarm",
             "time": "07:00", "message": "Wake up!"},
        )
        actions.process_actions(resp)

        timers = timer_store.get_active()
        assert len(timers) == 1
        fire_at = datetime.fromisoformat(timers[0]["fire_at"])
        assert fire_at.date() == date(2026, 3, 28)
        assert fire_at.hour == 7

    @freeze_time("2026-03-27 14:00:00")
    def test_voice_delivery_and_urgent_priority(self):
        resp = _response_with(
            "Timer set!",
            {"action": "set_timer", "label": "Urgent alarm",
             "minutes": 5, "delivery": "voice", "priority": "urgent",
             "message": "CHECK THE OVEN!"},
        )
        actions.process_actions(resp)

        timers = timer_store.get_active()
        assert len(timers) == 1
        assert timers[0]["delivery"] == "voice"
        assert timers[0]["priority"] == "urgent"
        assert timers[0]["message"] == "CHECK THE OVEN!"


class TestCancelTimer:
    @freeze_time("2026-03-27 14:00:00")
    def test_cancel(self):
        timer = seed_timer(label="Cancel me",
                           fire_at=(datetime(2026, 3, 27, 15, 0)).isoformat())
        tid = timer["id"]

        resp = _response_with(
            "Cancelled!",
            {"action": "cancel_timer", "id": tid},
        )
        cleaned = actions.process_actions(resp)
        assert "failed" not in cleaned.lower()

        t = timer_store.get_timer(tid)
        assert t["status"] == "cancelled"

    def test_cancel_nonexistent(self):
        resp = _response_with(
            "Cancelled!",
            {"action": "cancel_timer", "id": "ghost999"},
        )
        cleaned = actions.process_actions(resp)
        assert "no active timer found" in cleaned.lower()


# ===========================================================================
# Exercise Actions
# ===========================================================================

class TestStartExercise:
    @patch("fitbit_store.get_heart_summary", return_value={"resting_hr": 65})
    def test_start(self, _mock_hr):
        resp = _response_with(
            "Exercise started!",
            {"action": "start_exercise", "exercise_type": "walking"},
        )
        actions.process_actions(resp)

        state = fitbit_store.get_exercise_state()
        assert state is not None
        assert state["active"] is True
        assert state["exercise_type"] == "walking"

    @patch("fitbit_store.get_heart_summary", return_value={"resting_hr": 65})
    def test_deactivates_existing_ghost(self, _mock_hr):
        """Starting a new exercise deactivates any existing active session."""
        # Start first session
        fitbit_store.start_exercise("walking")
        state1 = fitbit_store.get_exercise_state()
        assert state1 is not None

        # Start second via ACTION block
        resp = _response_with(
            "New exercise!",
            {"action": "start_exercise", "exercise_type": "stationary_bike"},
        )
        actions.process_actions(resp)

        state2 = fitbit_store.get_exercise_state()
        assert state2 is not None
        assert state2["exercise_type"] == "stationary_bike"

        # Only one active session should exist
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM fitbit_exercise WHERE active = TRUE"
            ).fetchone()["c"]
        assert count == 1


class TestEndExercise:
    @patch("fitbit_store.get_heart_summary", return_value={"resting_hr": 65})
    def test_end(self, _mock_hr):
        fitbit_store.start_exercise("general")

        resp = _response_with(
            "Exercise ended!",
            {"action": "end_exercise"},
        )
        actions.process_actions(resp)

        state = fitbit_store.get_exercise_state()
        assert state is None


# ===========================================================================
# Other Actions
# ===========================================================================

class TestSetDelivery:
    def test_metadata_updated(self):
        metadata = {}
        resp = _response_with(
            "Switching to voice.",
            {"action": "set_delivery", "method": "voice"},
        )
        actions.process_actions(resp, metadata=metadata)
        assert metadata["delivery"] == "voice"


class TestDispatchAction:
    def test_redis_down(self):
        """dispatch_action with Redis unavailable appends failure note."""
        metadata = {"channel": "sms"}
        resp = _response_with(
            "I'll handle that.",
            {"action": "dispatch_action", "mode": "shell",
             "command": "echo hello", "task": "test task"},
        )
        with patch("actions.redis_client.push_task", return_value=None):
            cleaned = actions.process_actions(resp, metadata=metadata)
        assert "Redis unavailable" in cleaned


class TestUnknownAction:
    def test_no_crash(self):
        resp = _response_with(
            "Done!",
            {"action": "totally_made_up", "data": "whatever"},
        )
        cleaned = actions.process_actions(resp)
        # Should not crash, just log warning
        assert "Done!" in cleaned
        assert "ACTION" not in cleaned


class TestMalformedJson:
    def test_invalid_json(self):
        resp = 'Here you go! <!--ACTION::{not valid json}-->'
        cleaned = actions.process_actions(resp)
        assert "Invalid ACTION JSON" in cleaned or "failed" in cleaned.lower()


class TestMultipleFailures:
    def test_two_of_three_fail(self):
        """3 actions, 2 deletions of nonexistent IDs, verify both failure messages."""
        resp = _response_with(
            "All done!",
            {"action": "delete_event", "id": "ghost1"},
            {"action": "add_event", "title": "Real Event", "date": "2026-04-30"},
            {"action": "delete_reminder", "id": "ghost2"},
        )
        cleaned = actions.process_actions(resp)

        # Both failures reported
        assert "no event found" in cleaned.lower()
        assert "no reminder found" in cleaned.lower()

        # The successful add_event still executed
        events = calendar_store.get_events(start="2026-04-30", end="2026-04-30")
        assert len(events) == 1
        assert events[0]["title"] == "Real Event"


# ===========================================================================
# Response Cleaning
# ===========================================================================

class TestResponseCleaning:
    def test_action_blocks_stripped(self):
        resp = _response_with(
            "I added your event.",
            {"action": "add_event", "title": "Test", "date": "2026-05-01"},
        )
        cleaned = actions.process_actions(resp)
        assert "<!--ACTION::" not in cleaned
        assert "I added your event." in cleaned

    def test_multiline_action_stripped(self):
        payload = json.dumps({
            "action": "add_event", "title": "Test",
            "date": "2026-05-01", "notes": "Line1\nLine2\nLine3",
        })
        resp = f"Done! <!--ACTION::{payload}-->"
        cleaned = actions.process_actions(resp)
        assert "<!--ACTION::" not in cleaned
        assert "Done!" in cleaned

    def test_failure_appends_note_does_not_replace(self):
        """Bug #12 regression: failure note should be appended, not replace the response."""
        resp = _response_with(
            "I deleted the event for you.",
            {"action": "delete_event", "id": "nonexistent"},
        )
        cleaned = actions.process_actions(resp)
        # Original text preserved
        assert "I deleted the event for you." in cleaned
        # Failure note appended
        assert "Note:" in cleaned or "failed" in cleaned.lower()

    def test_expect_actions_missing_triggers_warning(self):
        resp = "I logged your nutrition data with 300 calories."
        cleaned = actions.process_actions(resp, expect_actions=["log_nutrition"])
        assert "WARNING" in cleaned
        assert "NOT actually saved" in cleaned
        assert "log_nutrition" in cleaned

    def test_expect_actions_present_no_warning(self):
        resp = _response_with(
            "Logged!",
            {"action": "add_event", "title": "Test", "date": "2026-05-01"},
        )
        cleaned = actions.process_actions(resp, expect_actions=["add_event"])
        assert "WARNING" not in cleaned
        assert "NOT actually saved" not in cleaned

    def test_claim_without_action_detected(self):
        """Response says 'I've logged your meal' but no ACTION blocks."""
        resp = "I've logged your meal for dinner. Enjoy!"
        cleaned = actions.process_actions(resp)
        assert "System note" in cleaned
        assert "ACTION blocks" in cleaned

    def test_claim_no_false_positive_descriptive_text(self):
        """Descriptive text like 'meals logged 3 of 7 days' should NOT trigger."""
        resp = "Your meals logged 3 of 7 days this week. Keep it up!"
        cleaned = actions.process_actions(resp)
        assert "System note" not in cleaned

    def test_nutrition_claim_detected(self):
        """Claim + 3+ nutrient terms + no actions triggers nutrition warning."""
        resp = ("I've tracked your lunch. It has 500 calories, 30g protein, "
                "15g fat, and 200mg sodium.")
        cleaned = actions.process_actions(resp)
        assert "System note" in cleaned

    def test_mixed_success_failure(self):
        """Some actions succeed, some fail. Verify partial results."""
        ev = seed_event(title="To Delete", event_date="2026-06-01")

        resp = _response_with(
            "Handled everything.",
            {"action": "delete_event", "id": ev["id"]},
            {"action": "delete_event", "id": "nonexistent"},
            {"action": "add_reminder", "text": "New reminder"},
        )
        cleaned = actions.process_actions(resp)

        # Event deleted
        events = calendar_store.get_events(start="2026-06-01", end="2026-06-01")
        assert len(events) == 0

        # Reminder added
        reminders = calendar_store.get_reminders()
        assert any(r["text"] == "New reminder" for r in reminders)

        # Failure noted
        assert "no event found" in cleaned.lower()

    def test_empty_response_no_crash(self):
        cleaned = actions.process_actions("")
        assert cleaned == ""


# ===========================================================================
# Nutrition Validation Warnings
# ===========================================================================

class TestNutritionValidationWarnings:
    @freeze_time("2026-03-27 12:00:00")
    def test_fish_without_omega3_warning(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Canned salmon",
             "date": "2026-03-27", "meal_type": "lunch",
             "nutrients": {"calories": 200, "protein_g": 25}},
        )
        cleaned = actions.process_actions(resp)
        assert "Omega-3 missing" in cleaned

    @freeze_time("2026-03-27 12:00:00")
    def test_fish_with_omega3_no_warning(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Canned salmon",
             "date": "2026-03-27", "meal_type": "lunch",
             "nutrients": {"calories": 200, "protein_g": 25,
                           "omega3_mg": 920}},
        )
        cleaned = actions.process_actions(resp)
        assert "Omega-3 missing" not in cleaned

    @freeze_time("2026-03-27 12:00:00")
    def test_egg_low_cholesterol_warning(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Scrambled eggs",
             "date": "2026-03-27", "meal_type": "breakfast",
             "nutrients": {"calories": 200, "protein_g": 14,
                           "cholesterol_mg": 50, "choline_mg": 300}},
        )
        cleaned = actions.process_actions(resp)
        assert "Cholesterol only 50mg" in cleaned

    @freeze_time("2026-03-27 12:00:00")
    def test_egg_missing_choline_warning(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Scrambled eggs",
             "date": "2026-03-27", "meal_type": "breakfast",
             "nutrients": {"calories": 200, "protein_g": 14,
                           "cholesterol_mg": 372}},
        )
        cleaned = actions.process_actions(resp)
        assert "Choline missing" in cleaned

    @freeze_time("2026-03-27 12:00:00")
    def test_label_photo_incomplete_nutrients_warning(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_nutrition", "food_name": "Protein bar",
             "date": "2026-03-27", "meal_type": "snack",
             "source": "label_photo",
             "nutrients": {"calories": 200, "protein_g": 20,
                           "total_fat_g": 8}},
        )
        cleaned = actions.process_actions(resp)
        assert "core nutrients" in cleaned.lower() or "3/8" in cleaned

    @freeze_time("2026-03-27 12:00:00")
    def test_meal_type_mismatch_warning(self):
        resp = _response_with(
            "Logged!",
            {"action": "log_health", "date": "2026-03-27",
             "category": "meal", "description": "Steak and potatoes",
             "meal_type": "lunch"},
            {"action": "log_nutrition", "food_name": "Steak and potatoes",
             "date": "2026-03-27", "meal_type": "dinner",
             "nutrients": {"calories": 700, "protein_g": 50}},
        )
        cleaned = actions.process_actions(resp)
        assert "Meal type mismatch" in cleaned
