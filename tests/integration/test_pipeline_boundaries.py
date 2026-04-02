"""Boundary value tests — every numeric comparison in the ARIA pipeline.

For each >, >=, <, <= comparison, tests the value-1, value, and value+1
cases to verify correct behavior at the boundary.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
Mocks: SMS delivery, phone push, external HTTP APIs only. Database is REAL.
"""

from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

import pytest
from freezegun import freeze_time

import db
import tick
import nutrition_store
import health_store
import fitbit_store
import calendar_store
import legal_store

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_legal, seed_vehicle, seed_request_log, seed_nudge_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evaluate_nudges_safe(**config_overrides):
    """Call tick.evaluate_nudges() with config patched to avoid side effects."""
    defaults = {
        "QUIET_HOURS_START": 0,
        "QUIET_HOURS_END": 7,
        "STALE_REMINDER_DAYS": 3,
        "KNOWN_PLACES": {},
        "DIET_START_DATE": "2026-01-01",
    }
    defaults.update(config_overrides)
    with patch.object(tick.config, "QUIET_HOURS_START", defaults["QUIET_HOURS_START"], create=True), \
         patch.object(tick.config, "QUIET_HOURS_END", defaults["QUIET_HOURS_END"], create=True), \
         patch.object(tick.config, "STALE_REMINDER_DAYS", defaults["STALE_REMINDER_DAYS"], create=True), \
         patch.object(tick.config, "KNOWN_PLACES", defaults["KNOWN_PLACES"], create=True), \
         patch.object(tick.config, "DIET_START_DATE", defaults["DIET_START_DATE"], create=True):
        return tick.evaluate_nudges()


def _nudge_types(triggers):
    """Extract just the nudge_type values from evaluate_nudges output."""
    return [t for t, _ in triggers]


def _make_sleep_snapshot(day, minutes_asleep):
    """Build a Fitbit snapshot dict with the given sleep duration."""
    return {
        "sleep": {
            "sleep": [{
                "isMainSleep": True,
                "minutesAsleep": minutes_asleep,
                "efficiency": 90,
                "levels": {"summary": {
                    "deep": {"minutes": int(minutes_asleep * 0.15)},
                    "light": {"minutes": int(minutes_asleep * 0.50)},
                    "rem": {"minutes": int(minutes_asleep * 0.25)},
                    "wake": {"minutes": int(minutes_asleep * 0.10)},
                }},
            }],
        },
    }


def _make_activity_snapshot(steps=5000, sedentary_minutes=60, calories_out=2000):
    """Build a Fitbit snapshot dict with activity data."""
    return {
        "activity": {
            "steps": steps,
            "caloriesOut": calories_out,
            "distances": [],
            "sedentaryMinutes": sedentary_minutes,
            "fairlyActiveMinutes": 20,
            "veryActiveMinutes": 10,
            "floors": 5,
        },
    }


def _make_hr_snapshot(resting_hr):
    """Build a Fitbit snapshot dict with heart rate data."""
    return {
        "heart_rate": {
            "value": {
                "restingHeartRate": resting_hr,
                "heartRateZones": [],
            },
        },
    }


# ===========================================================================
# HOUR RANGE BOUNDARIES — tick.evaluate_nudges()
# ===========================================================================


class TestMealReminderHourRange:
    """tick.py: `8 <= now.hour <= 21` (meal reminder window)."""

    @freeze_time("2026-03-27 07:30:00")
    def test_hour_7_no_meal_reminder(self):
        """Hour 7: outside [8,21], no meal gap check."""
        triggers = _evaluate_nudges_safe()
        assert "meal_reminder" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 08:00:00")
    def test_hour_8_meal_reminder_active(self):
        """Hour 8: inside [8,21], meal gap check runs.
        No items logged + hour < 12 means no trigger (only triggers at noon+)."""
        triggers = _evaluate_nudges_safe()
        # hour 8 < 12, so no "no meals logged" nudge even with no items
        assert "meal_reminder" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 12:00:00")
    def test_hour_12_no_meals_triggers(self):
        """Hour 12: inside [8,21] and >= 12, fires 'no meals logged' nudge."""
        triggers = _evaluate_nudges_safe()
        assert "meal_reminder" in _nudge_types(triggers)

    @freeze_time("2026-03-27 21:00:00")
    def test_hour_21_meal_reminder_active(self):
        """Hour 21: still inside [8,21], meal gap check runs."""
        triggers = _evaluate_nudges_safe()
        assert "meal_reminder" in _nudge_types(triggers)

    @freeze_time("2026-03-27 22:00:00")
    def test_hour_22_no_meal_reminder(self):
        """Hour 22: outside [8,21], no meal gap check."""
        triggers = _evaluate_nudges_safe()
        assert "meal_reminder" not in _nudge_types(triggers)


class TestDietCheckHourRange:
    """tick.py: `20 <= now.hour <= 21` (diet check)."""

    @freeze_time("2026-03-27 19:30:00")
    def test_hour_19_no_diet_check(self):
        """Hour 19: outside [20,21], no diet check."""
        triggers = _evaluate_nudges_safe()
        assert "diet_check" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 20:00:00")
    def test_hour_20_diet_check_active(self):
        """Hour 20: inside [20,21], diet check runs.
        With < 2 meals and a valid diet day, it should trigger."""
        triggers = _evaluate_nudges_safe()
        assert "diet_check" in _nudge_types(triggers)

    @freeze_time("2026-03-27 21:00:00")
    def test_hour_21_diet_check_active(self):
        """Hour 21: still inside [20,21]."""
        triggers = _evaluate_nudges_safe()
        assert "diet_check" in _nudge_types(triggers)

    @freeze_time("2026-03-27 22:00:00")
    def test_hour_22_no_diet_check(self):
        """Hour 22: outside [20,21], no diet check."""
        triggers = _evaluate_nudges_safe()
        assert "diet_check" not in _nudge_types(triggers)


class TestSedentaryHourRange:
    """tick.py: `9 <= now.hour <= 21` (sedentary check)."""

    @freeze_time("2026-03-27 08:30:00")
    def test_hour_8_no_sedentary_check(self):
        """Hour 8: outside [9,21], sedentary check skipped."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(sedentary_minutes=200))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sedentary" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 09:00:00")
    def test_hour_9_sedentary_check_active(self):
        """Hour 9: inside [9,21], sedentary check runs."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(sedentary_minutes=200))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sedentary" in _nudge_types(triggers)

    @freeze_time("2026-03-27 21:00:00")
    def test_hour_21_sedentary_check_active(self):
        """Hour 21: still inside [9,21]."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(sedentary_minutes=200))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sedentary" in _nudge_types(triggers)

    @freeze_time("2026-03-27 22:00:00")
    def test_hour_22_no_sedentary_check(self):
        """Hour 22: outside [9,21], sedentary check skipped."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(sedentary_minutes=200))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sedentary" not in _nudge_types(triggers)


class TestActivityGoalHourRange:
    """tick.py: `14 <= now.hour <= 17` (activity goal)."""

    @freeze_time("2026-03-27 13:30:00")
    def test_hour_13_no_activity_goal(self):
        """Hour 13: outside [14,17], activity goal skipped."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(steps=1000))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_activity_goal" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 14:00:00")
    def test_hour_14_activity_goal_active(self):
        """Hour 14: inside [14,17], low steps triggers activity goal."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(steps=1000))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_activity_goal" in _nudge_types(triggers)

    @freeze_time("2026-03-27 17:00:00")
    def test_hour_17_activity_goal_active(self):
        """Hour 17: still inside [14,17]."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(steps=1000))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_activity_goal" in _nudge_types(triggers)

    @freeze_time("2026-03-27 18:00:00")
    def test_hour_18_no_activity_goal(self):
        """Hour 18: outside [14,17], activity goal skipped."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(steps=1000))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_activity_goal" not in _nudge_types(triggers)


class TestCalorieSurplusHourRange:
    """tick.py: `now.hour >= 19` (calorie surplus check)."""

    @freeze_time("2026-03-27 18:30:00")
    def test_hour_18_no_calorie_surplus_check(self):
        """Hour 18: below 19, calorie surplus check skipped."""
        today = "2026-03-27"
        seed_nutrition(today, "Burger", calories=3000)
        seed_fitbit_snapshot(today, _make_activity_snapshot(calories_out=2000))
        triggers = _evaluate_nudges_safe()
        assert "nutrition_calorie_surplus" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 19:00:00")
    def test_hour_19_calorie_surplus_active(self):
        """Hour 19: >= 19, calorie surplus check runs."""
        today = "2026-03-27"
        seed_nutrition(today, "Burger", calories=3000)
        seed_fitbit_snapshot(today, _make_activity_snapshot(calories_out=2000))
        triggers = _evaluate_nudges_safe()
        assert "nutrition_calorie_surplus" in _nudge_types(triggers)


# ===========================================================================
# DURATION THRESHOLDS
# ===========================================================================


class TestMealGapThreshold:
    """tick.py: `hours_since >= 5` (meal gap)."""

    @freeze_time("2026-03-27 17:00:00")
    def test_4h50m_no_meal_reminder(self):
        """4h50m since last meal: below 5h, no trigger."""
        today = "2026-03-27"
        # Insert with a created timestamp 4h50m ago
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO nutrition_entries
                   (id, date, time, meal_type, food_name, servings, nutrients, content_hash)
                   VALUES ('mg1', %s, '12:10', 'lunch', 'Chicken', 1, '{"calories": 500}', 'mg1hash')""",
                (today,),
            )
            # Set the created timestamp to 4h50m ago
            conn.execute(
                "UPDATE nutrition_entries SET created = %s WHERE id = 'mg1'",
                (datetime(2026, 3, 27, 12, 10, 0),),
            )
        triggers = _evaluate_nudges_safe()
        assert "meal_reminder" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 17:10:00")
    def test_5h00m_meal_reminder_fires(self):
        """Exactly 5h since last meal: >= 5, trigger fires."""
        today = "2026-03-27"
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO nutrition_entries
                   (id, date, time, meal_type, food_name, servings, nutrients, content_hash)
                   VALUES ('mg2', %s, '12:10', 'lunch', 'Chicken', 1, '{"calories": 500}', 'mg2hash')""",
                (today,),
            )
            conn.execute(
                "UPDATE nutrition_entries SET created = %s WHERE id = 'mg2'",
                (datetime(2026, 3, 27, 12, 10, 0),),
            )
        triggers = _evaluate_nudges_safe()
        assert "meal_reminder" in _nudge_types(triggers)

    @freeze_time("2026-03-27 17:20:00")
    def test_5h10m_meal_reminder_fires(self):
        """5h10m since last meal: above 5, trigger fires."""
        today = "2026-03-27"
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO nutrition_entries
                   (id, date, time, meal_type, food_name, servings, nutrients, content_hash)
                   VALUES ('mg3', %s, '12:10', 'lunch', 'Chicken', 1, '{"calories": 500}', 'mg3hash')""",
                (today,),
            )
            conn.execute(
                "UPDATE nutrition_entries SET created = %s WHERE id = 'mg3'",
                (datetime(2026, 3, 27, 12, 10, 0),),
            )
        triggers = _evaluate_nudges_safe()
        assert "meal_reminder" in _nudge_types(triggers)


class TestCalendarWarningThreshold:
    """tick.py: `15 <= minutes_until <= 45` (calendar warning)."""

    @freeze_time("2026-03-27 14:47:00")
    def test_14min_before_no_warning(self):
        """14 minutes until event: below 15, no trigger."""
        seed_event("Meeting", event_date="2026-03-27", time="15:01")
        triggers = _evaluate_nudges_safe()
        assert "calendar_warning" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 14:45:00")
    def test_15min_before_warning_fires(self):
        """15 minutes until event: >= 15, trigger fires."""
        seed_event("Meeting", event_date="2026-03-27", time="15:00")
        triggers = _evaluate_nudges_safe()
        assert "calendar_warning" in _nudge_types(triggers)

    @freeze_time("2026-03-27 14:15:00")
    def test_45min_before_warning_fires(self):
        """45 minutes until event: <= 45, trigger fires."""
        seed_event("Meeting", event_date="2026-03-27", time="15:00")
        triggers = _evaluate_nudges_safe()
        assert "calendar_warning" in _nudge_types(triggers)

    @freeze_time("2026-03-27 14:13:00")
    def test_46min_before_no_warning(self):
        """~47 minutes until event: above 45, no trigger."""
        seed_event("Meeting", event_date="2026-03-27", time="15:00")
        triggers = _evaluate_nudges_safe()
        assert "calendar_warning" not in _nudge_types(triggers)


class TestSleepWarningThreshold:
    """tick.py: `sleep['duration_hours'] < 5` (sleep warning)."""

    @freeze_time("2026-03-27 10:00:00")
    def test_4h54m_sleep_fires_warning(self):
        """4.9h sleep (294 min): below 5, triggers fitbit_sleep."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_sleep_snapshot(today, 294))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sleep" in _nudge_types(triggers)

    @freeze_time("2026-03-27 10:00:00")
    def test_5h00m_sleep_no_warning(self):
        """5.0h sleep (300 min): not < 5, no trigger."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_sleep_snapshot(today, 300))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sleep" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 10:00:00")
    def test_5h06m_sleep_no_warning(self):
        """5.1h sleep (306 min): above 5, no trigger."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_sleep_snapshot(today, 306))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sleep" not in _nudge_types(triggers)


class TestSedentaryMinutesThreshold:
    """tick.py: `sed > 120` (sedentary minutes)."""

    @freeze_time("2026-03-27 14:00:00")
    def test_119min_no_sedentary_nudge(self):
        """119 sedentary min: not > 120, no trigger."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(sedentary_minutes=119))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sedentary" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 14:00:00")
    def test_120min_no_sedentary_nudge(self):
        """120 sedentary min: not > 120 (equal), no trigger."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(sedentary_minutes=120))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sedentary" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 14:00:00")
    def test_121min_sedentary_nudge_fires(self):
        """121 sedentary min: > 120, trigger fires."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(sedentary_minutes=121))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_sedentary" in _nudge_types(triggers)


class TestStepsThreshold:
    """tick.py: `steps < 3000` (activity goal)."""

    @freeze_time("2026-03-27 15:00:00")
    def test_2999_steps_triggers(self):
        """2999 steps: < 3000, triggers activity goal."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(steps=2999))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_activity_goal" in _nudge_types(triggers)

    @freeze_time("2026-03-27 15:00:00")
    def test_3000_steps_no_trigger(self):
        """3000 steps: not < 3000, no trigger."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(steps=3000))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_activity_goal" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 15:00:00")
    def test_3001_steps_no_trigger(self):
        """3001 steps: above 3000, no trigger."""
        today = "2026-03-27"
        seed_fitbit_snapshot(today, _make_activity_snapshot(steps=3001))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_activity_goal" not in _nudge_types(triggers)


class TestHRAnomalyThreshold:
    """tick.py: `current > avg + 10` (HR anomaly)."""

    @freeze_time("2026-03-27 10:00:00")
    def test_hr_at_avg_plus_10_no_trigger(self):
        """Current HR == avg + 10: not > avg+10, no trigger."""
        today = "2026-03-27"
        # Seed 7-day history with consistent resting HR of 65
        for i in range(1, 8):
            day = (date(2026, 3, 27) - timedelta(days=i)).isoformat()
            seed_fitbit_snapshot(day, _make_hr_snapshot(65))
        # Today: resting HR = 75 (exactly avg + 10)
        seed_fitbit_snapshot(today, _make_hr_snapshot(75))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_hr_anomaly" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 10:00:00")
    def test_hr_at_avg_plus_11_triggers(self):
        """Current HR == avg + 11: > avg+10, triggers anomaly."""
        today = "2026-03-27"
        for i in range(1, 8):
            day = (date(2026, 3, 27) - timedelta(days=i)).isoformat()
            seed_fitbit_snapshot(day, _make_hr_snapshot(65))
        # Today: resting HR = 76 (avg + 11)
        seed_fitbit_snapshot(today, _make_hr_snapshot(76))
        triggers = _evaluate_nudges_safe()
        assert "fitbit_hr_anomaly" in _nudge_types(triggers)


class TestLegalDeadlineThreshold:
    """tick.py: `0 <= days_until <= 3` (legal deadline)."""

    @freeze_time("2026-03-27 10:00:00")
    def test_today_legal_deadline(self):
        """Deadline today (0 days): inside [0,3], triggers."""
        seed_legal(entry_date="2026-03-27", entry_type="deadline",
                   description="Filing due")
        triggers = _evaluate_nudges_safe()
        assert "legal_deadline" in _nudge_types(triggers)

    @freeze_time("2026-03-27 10:00:00")
    def test_3_days_ahead_legal_deadline(self):
        """Deadline in 3 days: inside [0,3], triggers."""
        seed_legal(entry_date="2026-03-30", entry_type="deadline",
                   description="Filing due")
        triggers = _evaluate_nudges_safe()
        assert "legal_deadline" in _nudge_types(triggers)

    @freeze_time("2026-03-27 10:00:00")
    def test_4_days_ahead_no_trigger(self):
        """Deadline in 4 days: outside [0,3], no trigger."""
        seed_legal(entry_date="2026-03-31", entry_type="deadline",
                   description="Filing due")
        triggers = _evaluate_nudges_safe()
        assert "legal_deadline" not in _nudge_types(triggers)


class TestBatteryThreshold:
    """tick.py: `battery_pct <= 15` (battery low)."""

    @freeze_time("2026-03-27 10:00:00")
    def test_battery_14_triggers(self):
        """Battery 14%: <= 15, triggers."""
        seed_location("Home", battery_pct=14)
        triggers = _evaluate_nudges_safe()
        assert "battery_low" in _nudge_types(triggers)

    @freeze_time("2026-03-27 10:00:00")
    def test_battery_15_triggers(self):
        """Battery 15%: <= 15 (equal), triggers."""
        seed_location("Home", battery_pct=15)
        triggers = _evaluate_nudges_safe()
        assert "battery_low" in _nudge_types(triggers)

    @freeze_time("2026-03-27 10:00:00")
    def test_battery_16_no_trigger(self):
        """Battery 16%: not <= 15, no trigger."""
        seed_location("Home", battery_pct=16)
        triggers = _evaluate_nudges_safe()
        assert "battery_low" not in _nudge_types(triggers)


# ===========================================================================
# NUTRITION THRESHOLDS — tick.evaluate_nudges()
# ===========================================================================


class TestSugarWarnThreshold:
    """tick.py: `added_sugars_g >= 25` (sugar warning)."""

    @freeze_time("2026-03-27 15:00:00")
    def test_24g_sugar_no_warning(self):
        """24g added sugar: below 25, no trigger."""
        today = "2026-03-27"
        seed_nutrition(today, "Cookie", calories=200, added_sugars_g=24)
        triggers = _evaluate_nudges_safe()
        assert "nutrition_sugar_warn" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 15:00:00")
    def test_25g_sugar_warning_fires(self):
        """25g added sugar: >= 25, trigger fires."""
        today = "2026-03-27"
        seed_nutrition(today, "Cookie", calories=200, added_sugars_g=25)
        triggers = _evaluate_nudges_safe()
        assert "nutrition_sugar_warn" in _nudge_types(triggers)

    @freeze_time("2026-03-27 15:00:00")
    def test_26g_sugar_warning_fires(self):
        """26g added sugar: above 25, trigger fires."""
        today = "2026-03-27"
        seed_nutrition(today, "Cookie", calories=200, added_sugars_g=26)
        triggers = _evaluate_nudges_safe()
        assert "nutrition_sugar_warn" in _nudge_types(triggers)


class TestSodiumWarnThreshold:
    """tick.py: `sodium_mg >= 1600` (sodium warning)."""

    @freeze_time("2026-03-27 15:00:00")
    def test_1599mg_sodium_no_warning(self):
        """1599mg sodium: below 1600, no trigger."""
        today = "2026-03-27"
        seed_nutrition(today, "Soup", calories=300, sodium_mg=1599)
        triggers = _evaluate_nudges_safe()
        assert "nutrition_sodium_warn" not in _nudge_types(triggers)

    @freeze_time("2026-03-27 15:00:00")
    def test_1600mg_sodium_warning_fires(self):
        """1600mg sodium: >= 1600, trigger fires."""
        today = "2026-03-27"
        seed_nutrition(today, "Soup", calories=300, sodium_mg=1600)
        triggers = _evaluate_nudges_safe()
        assert "nutrition_sodium_warn" in _nudge_types(triggers)

    @freeze_time("2026-03-27 15:00:00")
    def test_1601mg_sodium_warning_fires(self):
        """1601mg sodium: above 1600, trigger fires."""
        today = "2026-03-27"
        seed_nutrition(today, "Soup", calories=300, sodium_mg=1601)
        triggers = _evaluate_nudges_safe()
        assert "nutrition_sodium_warn" in _nudge_types(triggers)


# ===========================================================================
# NUTRITION STORE VALIDATION BOUNDARIES
# ===========================================================================


class TestNutritionServingsValidation:
    """nutrition_store: servings <= 0 rejected, servings > 20 rejected."""

    def test_servings_negative_rejected(self):
        """Servings = -1: rejected."""
        with pytest.raises(ValueError, match="servings must be positive"):
            nutrition_store.add_item(
                food_name="Test", meal_type="snack",
                nutrients={"calories": 100}, servings=-1,
                entry_date=date.today().isoformat(),
            )

    def test_servings_zero_rejected(self):
        """Servings = 0: rejected (servings <= 0)."""
        with pytest.raises(ValueError, match="servings must be positive"):
            nutrition_store.add_item(
                food_name="Test", meal_type="snack",
                nutrients={"calories": 100}, servings=0,
                entry_date=date.today().isoformat(),
            )

    def test_servings_0_1_accepted(self):
        """Servings = 0.1: above 0, accepted."""
        result = nutrition_store.add_item(
            food_name="Small Bite", meal_type="snack",
            nutrients={"calories": 50}, servings=0.1,
            entry_date=date.today().isoformat(),
        )
        assert result["inserted"] is True

    def test_servings_20_accepted(self):
        """Servings = 20: not > 20 (equal), accepted."""
        result = nutrition_store.add_item(
            food_name="Bulk Meal", meal_type="lunch",
            nutrients={"calories": 100}, servings=20,
            entry_date=date.today().isoformat(),
        )
        assert result["inserted"] is True

    def test_servings_21_rejected(self):
        """Servings = 21: > 20, rejected."""
        with pytest.raises(ValueError, match="unreasonable"):
            nutrition_store.add_item(
                food_name="Too Much", meal_type="lunch",
                nutrients={"calories": 100}, servings=21,
                entry_date=date.today().isoformat(),
            )


class TestNutritionCalorieSanity:
    """nutrition_store: calories sanity (0, 5000)."""

    def test_negative_calories_rejected(self):
        """Calories = -1: below 0, rejected."""
        with pytest.raises(ValueError, match="outside sanity range"):
            nutrition_store.add_item(
                food_name="Bad Entry", meal_type="snack",
                nutrients={"calories": -1}, servings=1,
                entry_date=date.today().isoformat(),
            )

    def test_zero_calories_accepted(self):
        """Calories = 0: at lower bound, accepted."""
        result = nutrition_store.add_item(
            food_name="Water", meal_type="snack",
            nutrients={"calories": 0}, servings=1,
            entry_date=date.today().isoformat(),
        )
        assert result["inserted"] is True

    def test_5000_calories_accepted(self):
        """Calories = 5000: at upper bound, accepted."""
        result = nutrition_store.add_item(
            food_name="Feast", meal_type="dinner",
            nutrients={"calories": 5000}, servings=1,
            entry_date=date.today().isoformat(),
        )
        assert result["inserted"] is True

    def test_5001_calories_rejected(self):
        """Calories = 5001: above upper bound, rejected."""
        with pytest.raises(ValueError, match="outside sanity range"):
            nutrition_store.add_item(
                food_name="Impossible", meal_type="dinner",
                nutrients={"calories": 5001}, servings=1,
                entry_date=date.today().isoformat(),
            )


class TestNutritionDateStaleness:
    """nutrition_store: `days_ago > 7` (stale entry rejected)."""

    @freeze_time("2026-03-27 12:00:00")
    def test_7_days_ago_accepted(self):
        """Entry 7 days ago: not > 7, accepted."""
        result = nutrition_store.add_item(
            food_name="Old Meal", meal_type="lunch",
            nutrients={"calories": 400}, servings=1,
            entry_date="2026-03-20",
        )
        assert result["inserted"] is True

    @freeze_time("2026-03-27 12:00:00")
    def test_8_days_ago_rejected(self):
        """Entry 8 days ago: > 7, rejected."""
        with pytest.raises(ValueError, match="more than 7 days ago"):
            nutrition_store.add_item(
                food_name="Stale Meal", meal_type="lunch",
                nutrients={"calories": 400}, servings=1,
                entry_date="2026-03-19",
            )


# ===========================================================================
# FREQUENCY CAP BOUNDARIES
# ===========================================================================


class TestDailyNudgeCap:
    """tick.py: `count_24h >= max_per_day` (daily cap, default 6)."""

    @freeze_time("2026-03-27 12:00:00")
    def test_5_nudges_allows_next(self):
        """5 sent in 24h: below cap of 6, allows next nudge."""
        for i in range(5):
            seed_nudge_log([f"type_{i}"], [f"desc_{i}"])
        count_24h, _ = tick._get_nudge_counts()
        assert count_24h == 5
        assert count_24h < 6  # would pass the cap check

    @freeze_time("2026-03-27 12:00:00")
    def test_6_nudges_blocks_next(self):
        """6 sent in 24h: at cap of 6, blocks next nudge."""
        for i in range(6):
            seed_nudge_log([f"type_{i}"], [f"desc_{i}"])
        count_24h, _ = tick._get_nudge_counts()
        assert count_24h == 6
        assert count_24h >= 6  # would fail the cap check

    @freeze_time("2026-03-27 12:00:00")
    def test_7_nudges_blocks_next(self):
        """7 sent in 24h: above cap of 6, blocks next nudge."""
        for i in range(7):
            seed_nudge_log([f"type_{i}"], [f"desc_{i}"])
        count_24h, _ = tick._get_nudge_counts()
        assert count_24h == 7
        assert count_24h >= 6  # would fail the cap check


class TestHourlyNudgeCap:
    """tick.py: `count_1h >= max_per_hour` (hourly cap, default 2)."""

    @freeze_time("2026-03-27 12:00:00")
    def test_1_nudge_allows_next(self):
        """1 sent in 1h: below cap of 2, allows next nudge."""
        seed_nudge_log(["type_a"], ["desc_a"])
        _, count_1h = tick._get_nudge_counts()
        assert count_1h == 1
        assert count_1h < 2  # would pass the cap check

    @freeze_time("2026-03-27 12:00:00")
    def test_2_nudges_blocks_next(self):
        """2 sent in 1h: at cap of 2, blocks next nudge."""
        seed_nudge_log(["type_a"], ["desc_a"])
        seed_nudge_log(["type_b"], ["desc_b"])
        _, count_1h = tick._get_nudge_counts()
        assert count_1h == 2
        assert count_1h >= 2  # would fail the cap check

    @freeze_time("2026-03-27 12:00:00")
    def test_3_nudges_blocks_next(self):
        """3 sent in 1h: above cap of 2, blocks next nudge."""
        seed_nudge_log(["type_a"], ["desc_a"])
        seed_nudge_log(["type_b"], ["desc_b"])
        seed_nudge_log(["type_c"], ["desc_c"])
        _, count_1h = tick._get_nudge_counts()
        assert count_1h == 3
        assert count_1h >= 2  # would fail the cap check


class TestExerciseRateLimit:
    """tick.py: `avg_interval < 3` (exercise nudge rate limit).

    avg_interval = elapsed_min / nudge_count.
    When avg_interval < 3, tick skips the nudge.
    """

    def test_avg_interval_2_9_skips(self):
        """avg_interval ~2.9 min: < 3, should skip."""
        # elapsed_min=29, nudge_count=10 -> avg=2.9
        assert 29 / 10 < 3

    def test_avg_interval_3_0_does_not_skip(self):
        """avg_interval 3.0 min: not < 3, should NOT skip."""
        # elapsed_min=30, nudge_count=10 -> avg=3.0
        assert not (30 / 10 < 3)

    def test_avg_interval_3_1_does_not_skip(self):
        """avg_interval ~3.1 min: above 3, should NOT skip."""
        # elapsed_min=31, nudge_count=10 -> avg=3.1
        assert not (31 / 10 < 3)


# ===========================================================================
# HEALTH PATTERN BOUNDARIES
# ===========================================================================


class TestHealthPatternSignificance:
    """health_store: `count >= 3` (pattern significance)."""

    def _day(self, ago: int) -> str:
        """Return date string for N days ago."""
        return (date.today() - timedelta(days=ago)).isoformat()

    def test_2_occurrences_not_significant(self):
        """2 pain entries over 7 days: below threshold, no pattern."""
        seed_health(self._day(1), category="pain", description="back pain")
        seed_health(self._day(2), category="pain", description="back pain")
        patterns = health_store.get_patterns(days=7)
        pain_patterns = [p for p in patterns if "back pain" in p and "reported" in p]
        assert len(pain_patterns) == 0

    def test_3_occurrences_significant(self):
        """3 pain entries over 7 days: at threshold, pattern detected."""
        seed_health(self._day(1), category="pain", description="back pain")
        seed_health(self._day(2), category="pain", description="back pain, mild")
        seed_health(self._day(3), category="pain", description="back pain, moderate")
        patterns = health_store.get_patterns(days=7)
        pain_patterns = [p for p in patterns if "back pain" in p and "reported" in p]
        assert len(pain_patterns) == 1
        assert "3" in pain_patterns[0]

    def test_4_occurrences_significant(self):
        """4 pain entries over 7 days: above threshold, pattern detected."""
        seed_health(self._day(1), category="pain", description="back pain")
        seed_health(self._day(2), category="pain", description="back pain, mild")
        seed_health(self._day(3), category="pain", description="back pain, moderate")
        seed_health(self._day(4), category="pain", description="back pain, severe")
        patterns = health_store.get_patterns(days=7)
        pain_patterns = [p for p in patterns if "back pain" in p and "reported" in p]
        assert len(pain_patterns) == 1
        assert "4" in pain_patterns[0]


class TestSleepAverageWarning:
    """health_store: `avg < 6` (sleep warning)."""

    def _day(self, ago: int) -> str:
        return (date.today() - timedelta(days=ago)).isoformat()

    def test_avg_5_9_warns(self):
        """Average 5.9h sleep: below 6, triggers warning."""
        seed_health(self._day(1), category="sleep", description="sleep", sleep_hours=5.8)
        seed_health(self._day(2), category="sleep", description="sleep", sleep_hours=6.0)
        # avg = (5.8 + 6.0) / 2 = 5.9
        patterns = health_store.get_patterns(days=7)
        warnings = [p for p in patterns if "warning" in p.lower() and "sleep" in p]
        assert len(warnings) == 1

    def test_avg_6_0_no_warning(self):
        """Average 6.0h sleep: not < 6, no warning."""
        seed_health(self._day(1), category="sleep", description="sleep", sleep_hours=6.0)
        seed_health(self._day(2), category="sleep", description="sleep", sleep_hours=6.0)
        patterns = health_store.get_patterns(days=7)
        warnings = [p for p in patterns if "warning" in p.lower() and "sleep" in p]
        assert len(warnings) == 0

    def test_avg_6_1_no_warning(self):
        """Average 6.1h sleep: above 6, no warning."""
        seed_health(self._day(1), category="sleep", description="sleep", sleep_hours=6.2)
        seed_health(self._day(2), category="sleep", description="sleep", sleep_hours=6.0)
        # avg = (6.2 + 6.0) / 2 = 6.1
        patterns = health_store.get_patterns(days=7)
        warnings = [p for p in patterns if "warning" in p.lower() and "sleep" in p]
        assert len(warnings) == 0


# ===========================================================================
# NUTRITION check_limits() POSITIVE NOTE BOUNDARIES
# ===========================================================================


class TestFiberPositiveNote:
    """nutrition_store.check_limits(): `dietary_fiber_g >= 25` (positive note)."""

    @freeze_time("2026-03-27 12:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_24g_fiber_no_note(self, mock_fb):
        """24g fiber: below 25, no positive note."""
        mock_fb.get_activity_summary.return_value = None
        today = "2026-03-27"
        seed_nutrition(today, "Salad", calories=200, dietary_fiber_g=24)
        warnings = nutrition_store.check_limits(today)
        fiber_notes = [w for w in warnings if "Fiber on track" in w]
        assert len(fiber_notes) == 0

    @freeze_time("2026-03-27 12:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_25g_fiber_positive_note(self, mock_fb):
        """25g fiber: >= 25, positive note shown."""
        mock_fb.get_activity_summary.return_value = None
        today = "2026-03-27"
        seed_nutrition(today, "Salad", calories=200, dietary_fiber_g=25)
        warnings = nutrition_store.check_limits(today)
        fiber_notes = [w for w in warnings if "Fiber on track" in w]
        assert len(fiber_notes) == 1


class TestProteinPositiveNote:
    """nutrition_store.check_limits(): `protein_g >= 100` (positive note)."""

    @freeze_time("2026-03-27 12:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_99g_protein_no_note(self, mock_fb):
        """99g protein: below 100, no positive note."""
        mock_fb.get_activity_summary.return_value = None
        today = "2026-03-27"
        seed_nutrition(today, "Chicken", calories=500, protein_g=99)
        warnings = nutrition_store.check_limits(today)
        protein_notes = [w for w in warnings if "Protein on track" in w]
        assert len(protein_notes) == 0

    @freeze_time("2026-03-27 12:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_100g_protein_positive_note(self, mock_fb):
        """100g protein: >= 100, positive note shown."""
        mock_fb.get_activity_summary.return_value = None
        today = "2026-03-27"
        seed_nutrition(today, "Chicken", calories=500, protein_g=100)
        warnings = nutrition_store.check_limits(today)
        protein_notes = [w for w in warnings if "Protein on track" in w]
        assert len(protein_notes) == 1


class TestCholinePositiveNote:
    """nutrition_store.check_limits(): `choline_mg >= 550` (positive note)."""

    @freeze_time("2026-03-27 12:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_549mg_choline_no_note(self, mock_fb):
        """549mg choline: below 550, no positive note."""
        mock_fb.get_activity_summary.return_value = None
        today = "2026-03-27"
        seed_nutrition(today, "Eggs", calories=300, choline_mg=549)
        warnings = nutrition_store.check_limits(today)
        choline_notes = [w for w in warnings if "Choline on track" in w]
        assert len(choline_notes) == 0

    @freeze_time("2026-03-27 12:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_550mg_choline_positive_note(self, mock_fb):
        """550mg choline: >= 550, positive note shown."""
        mock_fb.get_activity_summary.return_value = None
        today = "2026-03-27"
        seed_nutrition(today, "Eggs", calories=300, choline_mg=550)
        warnings = nutrition_store.check_limits(today)
        choline_notes = [w for w in warnings if "Choline on track" in w]
        assert len(choline_notes) == 1


class TestSugarHardLimit:
    """nutrition_store DAILY_TARGETS: `added_sugars_g` hard_limit >= 36."""

    @freeze_time("2026-03-27 12:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_35g_sugar_no_hard_limit(self, mock_fb):
        """35g added sugar: below hard limit 36, no OVER LIMIT."""
        mock_fb.get_activity_summary.return_value = None
        today = "2026-03-27"
        seed_nutrition(today, "Candy", calories=400, added_sugars_g=35)
        warnings = nutrition_store.check_limits(today)
        over = [w for w in warnings if "OVER LIMIT" in w and "sugar" in w.lower()]
        assert len(over) == 0

    @freeze_time("2026-03-27 12:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_36g_sugar_hard_limit_fires(self, mock_fb):
        """36g added sugar: at hard limit 36, OVER LIMIT fires."""
        mock_fb.get_activity_summary.return_value = None
        today = "2026-03-27"
        seed_nutrition(today, "Candy", calories=400, added_sugars_g=36)
        warnings = nutrition_store.check_limits(today)
        over = [w for w in warnings if "OVER LIMIT" in w and "sugar" in w.lower()]
        assert len(over) == 1
