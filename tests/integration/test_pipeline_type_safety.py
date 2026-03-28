"""Type safety tests — every unsafe int()/float() cast and external data
used arithmetically.

Ensures Fitbit API string values, None values, and edge cases are all
safely handled by _safe_int/_safe_float and downstream consumers.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

import math
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from freezegun import freeze_time

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import fitbit_store
import tick
import nutrition_store
import db

from tests.integration.conftest import seed_fitbit_snapshot, seed_nutrition


# ---------------------------------------------------------------------------
# _safe_int / _safe_float exhaustive
# ---------------------------------------------------------------------------

class TestSafeIntExhaustive:
    """Exhaustive tests for fitbit_store._safe_int."""

    def test_zero(self):
        assert fitbit_store._safe_int(0) == 0

    def test_negative(self):
        assert fitbit_store._safe_int(-1) == -1

    def test_large_int(self):
        assert fitbit_store._safe_int(2147483647) == 2147483647

    def test_empty_string(self):
        assert fitbit_store._safe_int("") == 0

    def test_non_numeric_string(self):
        assert fitbit_store._safe_int("abc") == 0

    def test_none(self):
        assert fitbit_store._safe_int(None) == 0

    def test_string_int(self):
        assert fitbit_store._safe_int("123") == 123

    def test_custom_default(self):
        assert fitbit_store._safe_int(None, default=42) == 42

    def test_bool_true(self):
        # bool is subclass of int in Python
        assert fitbit_store._safe_int(True) == 1

    def test_float_input(self):
        assert fitbit_store._safe_int(3.7) == 3


class TestSafeFloatExhaustive:
    """Exhaustive tests for fitbit_store._safe_float."""

    def test_zero(self):
        assert fitbit_store._safe_float(0.0) == 0.0

    def test_infinity(self):
        result = fitbit_store._safe_float(float('inf'))
        assert result == float('inf')

    def test_nan(self):
        result = fitbit_store._safe_float(float('nan'))
        assert math.isnan(result)

    def test_empty_string(self):
        assert fitbit_store._safe_float("") == 0.0

    def test_non_numeric_string(self):
        assert fitbit_store._safe_float("abc") == 0.0

    def test_none(self):
        assert fitbit_store._safe_float(None) == 0.0

    def test_string_float(self):
        assert fitbit_store._safe_float("5.2") == 5.2

    def test_custom_default(self):
        assert fitbit_store._safe_float(None, default=1.5) == 1.5


# ---------------------------------------------------------------------------
# Fitbit all-string snapshots
# ---------------------------------------------------------------------------

class TestFitbitAllStringSnapshots:
    """Fitbit API sometimes returns ALL values as strings."""

    def test_activity_all_strings(self):
        """Activity snapshot with ALL values as strings returns int/float."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"activity": {
            "steps": "8500",
            "distances": [{"activity": "total", "distance": "3.5"}],
            "caloriesOut": "2100",
            "activityCalories": "800",
            "fairlyActiveMinutes": "20",
            "veryActiveMinutes": "10",
            "sedentaryMinutes": "600",
            "floors": "5",
        }})
        result = fitbit_store.get_activity_summary(today)
        assert result is not None
        assert isinstance(result["steps"], int)
        assert result["steps"] == 8500
        assert isinstance(result["calories_total"], int)
        assert result["calories_total"] == 2100
        assert isinstance(result["active_minutes"], int)
        assert result["active_minutes"] == 30  # 20 + 10
        assert isinstance(result["sedentary_minutes"], int)
        assert result["sedentary_minutes"] == 600
        assert isinstance(result["distance_miles"], float)

    def test_sleep_string_minutes_asleep(self):
        """Sleep snapshot with string minutesAsleep returns correct int."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"sleep": {
            "sleep": [{
                "isMainSleep": True,
                "minutesAsleep": "420",
                "efficiency": "88",
                "startTime": "2026-03-26T23:00:00",
                "endTime": "2026-03-27T06:00:00",
                "levels": {"summary": {
                    "deep": {"minutes": "80"},
                    "light": {"minutes": "200"},
                    "rem": {"minutes": "90"},
                    "wake": {"minutes": "50"},
                }},
            }]
        }})
        result = fitbit_store.get_sleep_summary(today)
        assert result is not None
        assert isinstance(result["total_minutes"], int)
        assert result["total_minutes"] == 420
        assert isinstance(result["deep_minutes"], int)
        assert result["deep_minutes"] == 80

    def test_heart_string_resting_hr(self):
        """Heart rate with string restingHeartRate returns correct int."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"heart_rate": {
            "value": {
                "restingHeartRate": "68",
                "heartRateZones": [
                    {"name": "Fat Burn", "minutes": "30",
                     "caloriesOut": "120.5"},
                ],
            }
        }})
        result = fitbit_store.get_heart_summary(today)
        assert result is not None
        assert isinstance(result["resting_hr"], int)
        assert result["resting_hr"] == 68
        assert isinstance(result["zones"][0]["minutes"], int)
        assert result["zones"][0]["minutes"] == 30

    def test_hrv_string_value(self):
        """HRV with string value returns float."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"hrv": {
            "value": {
                "dailyRmssd": "35.2",
                "deepRmssd": "42.1",
            }
        }})
        result = fitbit_store.get_hrv_summary(today)
        assert result is not None
        assert isinstance(result["rmssd"], float)
        assert result["rmssd"] == 35.2

    def test_spo2_string_value(self):
        """SpO2 with string values returns correct float."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"spo2": {
            "value": {
                "avg": "97.5",
                "min": "95.0",
                "max": "99.0",
            }
        }})
        result = fitbit_store.get_spo2_summary(today)
        assert result is not None
        assert isinstance(result["avg"], float)
        assert result["avg"] == 97.5

    def test_breathing_rate_string_value(self):
        """Breathing rate with string value returns correct float."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"breathing_rate": {
            "value": {
                "breathingRate": "16.5",
            }
        }})
        result = fitbit_store.get_breathing_rate_summary(today)
        assert result is not None
        assert isinstance(result["rate"], float)
        assert result["rate"] == 16.5


# ---------------------------------------------------------------------------
# Tick.py unsafe cast locations
# ---------------------------------------------------------------------------

class TestTickUnsafeCasts:
    """tick.py code that reads Fitbit activity data and does arithmetic."""

    @freeze_time("2026-03-27 15:00:00")
    def test_sedentary_minutes_as_string(self):
        """String sedentaryMinutes in activity snapshot does not crash nudge eval."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"activity": {
            "steps": 3000,
            "distances": [{"activity": "total", "distance": 2.0}],
            "caloriesOut": 1800,
            "activityCalories": 500,
            "fairlyActiveMinutes": 10,
            "veryActiveMinutes": 5,
            "sedentaryMinutes": "720",
            "floors": 2,
        }})
        # evaluate_nudges reads activity and does int() on sedentary_minutes
        triggers = tick.evaluate_nudges()
        # Should not crash — the string is handled by _safe_int in get_activity_summary
        assert isinstance(triggers, list)

    @freeze_time("2026-03-27 15:00:00")
    def test_steps_as_string(self):
        """String steps in activity snapshot does not crash nudge eval."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"activity": {
            "steps": "8500",
            "distances": [{"activity": "total", "distance": "3.5"}],
            "caloriesOut": "2100",
            "activityCalories": "800",
            "fairlyActiveMinutes": "20",
            "veryActiveMinutes": "10",
            "sedentaryMinutes": "400",
            "floors": "5",
        }})
        triggers = tick.evaluate_nudges()
        assert isinstance(triggers, list)

    @freeze_time("2026-03-27 15:00:00")
    def test_both_string_values_together(self):
        """Both steps and sedentaryMinutes as strings in same snapshot."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"activity": {
            "steps": "500",
            "distances": [{"activity": "total", "distance": "0.5"}],
            "caloriesOut": "1500",
            "activityCalories": "200",
            "fairlyActiveMinutes": "5",
            "veryActiveMinutes": "0",
            "sedentaryMinutes": "720",
            "floors": "0",
        }})
        triggers = tick.evaluate_nudges()
        assert isinstance(triggers, list)
        # Should have sedentary and activity goal triggers
        types = [t[0] for t in triggers]
        assert "fitbit_sedentary" in types or "fitbit_activity_goal" in types

    @freeze_time("2026-03-27 15:00:00")
    def test_activity_none_values(self):
        """Activity dict with None values does not crash nudge evaluation."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"activity": {
            "steps": None,
            "distances": [],
            "caloriesOut": None,
            "activityCalories": None,
            "fairlyActiveMinutes": None,
            "veryActiveMinutes": None,
            "sedentaryMinutes": None,
            "floors": None,
        }})
        triggers = tick.evaluate_nudges()
        assert isinstance(triggers, list)


# ---------------------------------------------------------------------------
# Nutrition type safety
# ---------------------------------------------------------------------------

class TestNutritionTypeSafety:
    """Nutrition store returns numeric types for arithmetic consumers."""

    def test_daily_totals_returns_numeric(self):
        """get_daily_totals returns numeric types for all nutrient fields."""
        today = date.today().isoformat()
        seed_nutrition(today, "Test Food", calories=500, protein_g=30)
        totals = nutrition_store.get_daily_totals(today)
        for field in nutrition_store.NUTRIENT_FIELDS:
            val = totals[field]
            assert isinstance(val, (int, float)), (
                f"Nutrient '{field}' is {type(val).__name__}, expected numeric"
            )

    @patch("nutrition_store.fitbit_store")
    def test_check_limits_with_float_totals(self, mock_fitbit):
        """check_limits works correctly with float values in totals."""
        mock_fitbit.get_activity_summary.return_value = {
            "calories_total": 2000,
        }
        today = date.today().isoformat()
        # Seed data with fractional servings that produce float totals
        seed_nutrition(today, "Mixed Nuts", calories=170.5, protein_g=5.3,
                       total_fat_g=14.2, sodium_mg=85.5, total_carb_g=8.1,
                       dietary_fiber_g=2.1, total_sugars_g=1.5,
                       added_sugars_g=0.5, saturated_fat_g=1.8)
        warnings = nutrition_store.check_limits(today)
        assert isinstance(warnings, list)
        for w in warnings:
            assert isinstance(w, str)
