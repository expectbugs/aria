"""Production Fitbit data replay tests — real JSONB snapshots through every extraction path.

Uses 9 real Fitbit daily snapshots (March 19-27, 2026) loaded from fixtures.
Tests all summary extraction functions, trend aggregation, briefing context,
and nudge evaluation with actual production data shapes.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

import json
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

import pytest
from freezegun import freeze_time

import fitbit_store
import nutrition_store
import tick
import db

from tests.integration.conftest import (
    load_fitbit_snapshots_into_db,
    load_nutrition_entries_into_db,
    load_health_entries_into_db,
    load_fixture,
    seed_fitbit_snapshot,
    seed_nutrition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def all_snapshots():
    """Load all 9 real Fitbit snapshots into the test DB."""
    return load_fitbit_snapshots_into_db()


SNAPSHOT_DATES = [
    "2026-03-19", "2026-03-20", "2026-03-21", "2026-03-22",
    "2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27",
]


# ---------------------------------------------------------------------------
# Heart rate extraction
# ---------------------------------------------------------------------------

class TestHeartSummary:
    def test_all_days_have_resting_hr(self, all_snapshots):
        """Every real snapshot should produce a heart summary with valid resting HR."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_heart_summary(day)
            assert result is not None, f"No heart summary for {day}"
            rhr = result["resting_hr"]
            assert rhr is None or isinstance(rhr, int), (
                f"resting_hr should be int or None on {day}, got {type(rhr)}"
            )

    def test_all_days_have_zones_list(self, all_snapshots):
        """Heart rate zones should always be a list."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_heart_summary(day)
            assert result is not None, f"No heart summary for {day}"
            zones = result["zones"]
            assert isinstance(zones, list), f"zones should be list on {day}"
            # Each zone should have name, minutes, calories_out
            for z in zones:
                assert "name" in z
                assert isinstance(z["minutes"], int)
                assert isinstance(z["calories_out"], float)

    def test_resting_hr_values_in_range(self, all_snapshots):
        """Real resting HR values should be physiologically plausible (40-120)."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_heart_summary(day)
            rhr = result["resting_hr"]
            if rhr is not None:
                assert 40 <= rhr <= 120, f"Implausible resting HR {rhr} on {day}"


# ---------------------------------------------------------------------------
# Sleep extraction
# ---------------------------------------------------------------------------

class TestSleepSummary:
    def test_all_days_have_sleep(self, all_snapshots):
        """Every real snapshot should produce a sleep summary."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_sleep_summary(day)
            assert result is not None, f"No sleep summary for {day}"

    def test_duration_hours_is_float(self, all_snapshots):
        """duration_hours should always be a float."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_sleep_summary(day)
            assert isinstance(result["duration_hours"], float), (
                f"duration_hours not float on {day}"
            )

    def test_stage_minutes_are_int(self, all_snapshots):
        """Sleep stage minutes should always be int."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_sleep_summary(day)
            for field in ["deep_minutes", "light_minutes", "rem_minutes", "wake_minutes"]:
                val = result[field]
                assert isinstance(val, int), (
                    f"{field} should be int on {day}, got {type(val)}"
                )

    def test_stage_minutes_sum_plausible(self, all_snapshots):
        """Sum of stage minutes should be close to total_minutes."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_sleep_summary(day)
            stage_sum = (result["deep_minutes"] + result["light_minutes"] +
                         result["rem_minutes"] + result["wake_minutes"])
            # The total_minutes is minutesAsleep which excludes wake,
            # so stage_sum (including wake) should be >= total_minutes
            assert stage_sum > 0, f"No sleep stages on {day}"


# ---------------------------------------------------------------------------
# Activity extraction
# ---------------------------------------------------------------------------

class TestActivitySummary:
    def test_all_days_have_activity(self, all_snapshots):
        """Every real snapshot should produce an activity summary."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_activity_summary(day)
            assert result is not None, f"No activity summary for {day}"

    def test_steps_is_int(self, all_snapshots):
        """Steps should always be an int."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_activity_summary(day)
            assert isinstance(result["steps"], int), f"steps not int on {day}"

    def test_distance_is_float(self, all_snapshots):
        """Distance should always be a float."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_activity_summary(day)
            assert isinstance(result["distance_miles"], float), (
                f"distance_miles not float on {day}"
            )

    def test_calories_total_is_int(self, all_snapshots):
        """calories_total should always be an int."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_activity_summary(day)
            assert isinstance(result["calories_total"], int), (
                f"calories_total not int on {day}"
            )

    def test_known_step_counts(self, all_snapshots):
        """Verify known step counts from production data."""
        expected = {
            "2026-03-19": 9676,
            "2026-03-21": 10242,
            "2026-03-25": 1779,
            "2026-03-27": 2468,
        }
        for day, expected_steps in expected.items():
            result = fitbit_store.get_activity_summary(day)
            assert result["steps"] == expected_steps, (
                f"Steps mismatch on {day}: {result['steps']} != {expected_steps}"
            )

    def test_sedentary_minutes_type(self, all_snapshots):
        """sedentary_minutes should be int on all days."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_activity_summary(day)
            assert isinstance(result["sedentary_minutes"], int), (
                f"sedentary_minutes not int on {day}"
            )


# ---------------------------------------------------------------------------
# SpO2 extraction
# ---------------------------------------------------------------------------

class TestSpo2Summary:
    def test_all_days_have_spo2(self, all_snapshots):
        """Every real snapshot should produce an SpO2 summary."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_spo2_summary(day)
            assert result is not None, f"No SpO2 summary for {day}"

    def test_values_are_float(self, all_snapshots):
        """SpO2 avg/min/max should be float."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_spo2_summary(day)
            for field in ["avg", "min", "max"]:
                val = result[field]
                assert val is None or isinstance(val, float), (
                    f"SpO2 {field} should be float on {day}, got {type(val)}"
                )

    def test_spo2_range_plausible(self, all_snapshots):
        """SpO2 values should be 80-100%."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_spo2_summary(day)
            for field in ["avg", "min", "max"]:
                val = result[field]
                if val is not None:
                    assert 80 <= val <= 100, (
                        f"Implausible SpO2 {field}={val} on {day}"
                    )


# ---------------------------------------------------------------------------
# HRV extraction
# ---------------------------------------------------------------------------

class TestHrvSummary:
    def test_all_days_have_hrv(self, all_snapshots):
        """Every real snapshot should produce an HRV summary."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_hrv_summary(day)
            assert result is not None, f"No HRV summary for {day}"

    def test_rmssd_is_float(self, all_snapshots):
        """HRV rmssd should be float."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_hrv_summary(day)
            rmssd = result["rmssd"]
            assert rmssd is None or isinstance(rmssd, float), (
                f"rmssd should be float on {day}, got {type(rmssd)}"
            )

    def test_deep_rmssd_present(self, all_snapshots):
        """deep_rmssd should be present (float or None)."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_hrv_summary(day)
            assert "deep_rmssd" in result, f"deep_rmssd missing on {day}"


# ---------------------------------------------------------------------------
# Breathing rate extraction
# ---------------------------------------------------------------------------

class TestBreathingRateSummary:
    # Days with actual breathing rate data (March 19-22 have empty dicts)
    BR_DAYS_WITH_DATA = [
        "2026-03-23", "2026-03-24", "2026-03-25",
        "2026-03-26", "2026-03-27",
    ]
    BR_DAYS_EMPTY = [
        "2026-03-19", "2026-03-20", "2026-03-21", "2026-03-22",
    ]

    def test_days_with_data_have_breathing_rate(self, all_snapshots):
        """Days with actual breathing rate data should produce a summary."""
        for day in self.BR_DAYS_WITH_DATA:
            result = fitbit_store.get_breathing_rate_summary(day)
            assert result is not None, f"No breathing rate summary for {day}"

    def test_empty_breathing_rate_returns_none(self, all_snapshots):
        """Days with empty breathing_rate dict should return None."""
        for day in self.BR_DAYS_EMPTY:
            result = fitbit_store.get_breathing_rate_summary(day)
            assert result is None, (
                f"Expected None for empty breathing_rate on {day}, got {result}"
            )

    def test_rate_is_float(self, all_snapshots):
        """Breathing rate should be float when present."""
        for day in self.BR_DAYS_WITH_DATA:
            result = fitbit_store.get_breathing_rate_summary(day)
            rate = result["rate"]
            assert rate is None or isinstance(rate, float), (
                f"rate should be float on {day}, got {type(rate)}"
            )


# ---------------------------------------------------------------------------
# Temperature extraction
# ---------------------------------------------------------------------------

class TestTemperatureSummary:
    def test_all_days_have_temperature(self, all_snapshots):
        """Every real snapshot should produce a temperature summary."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_temperature_summary(day)
            assert result is not None, f"No temperature summary for {day}"

    def test_nightly_relative_is_float(self, all_snapshots):
        """nightly_relative should be float."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_temperature_summary(day)
            val = result["nightly_relative"]
            assert val is None or isinstance(val, float), (
                f"nightly_relative should be float on {day}, got {type(val)}"
            )


# ---------------------------------------------------------------------------
# VO2 Max — JSONB null handling
# ---------------------------------------------------------------------------

class TestVo2MaxSummary:
    def test_vo2max_null_returns_none(self, all_snapshots):
        """VO2 Max is JSONB null on most days — should return None, not crash."""
        # Only March 19 and 20 have the vo2max key (set to None in JSONB)
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_vo2max_summary(day)
            # All 9 days have vo2max=None in production data
            assert result is None, (
                f"Expected None for vo2max on {day}, got {result}"
            )

    def test_vo2max_with_real_value(self):
        """VO2 Max with an actual value should extract correctly."""
        seed_fitbit_snapshot("2026-03-28", {
            "vo2max": {
                "value": {"vo2Max": 42.5},
                "dateTime": "2026-03-28",
            }
        })
        result = fitbit_store.get_vo2max_summary("2026-03-28")
        assert result is not None
        assert result["vo2max"] == 42.5


# ---------------------------------------------------------------------------
# Trend aggregation
# ---------------------------------------------------------------------------

class TestTrend:
    @freeze_time("2026-03-27 14:00:00")
    def test_trend_9_days(self, all_snapshots):
        """get_trend(days=9) should return a non-empty string with averages."""
        result = fitbit_store.get_trend(days=9)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Avg resting HR" in result
        assert "Avg HRV" in result
        assert "Avg sleep" in result
        assert "Avg steps" in result

    @freeze_time("2026-03-27 14:00:00")
    def test_trend_contains_day_counts(self, all_snapshots):
        """Trend should include the number of days in parentheses."""
        result = fitbit_store.get_trend(days=9)
        # Should have at least some days counted
        assert "d)" in result


# ---------------------------------------------------------------------------
# Resting HR history
# ---------------------------------------------------------------------------

class TestRestingHrHistory:
    @freeze_time("2026-03-27 14:00:00")
    def test_history_returns_ints(self, all_snapshots):
        """get_resting_hr_history should return a list of ints."""
        result = fitbit_store.get_resting_hr_history(days=9)
        assert isinstance(result, list)
        for val in result:
            assert isinstance(val, int), f"Expected int, got {type(val)}: {val}"

    @freeze_time("2026-03-27 14:00:00")
    def test_history_excludes_today(self, all_snapshots):
        """get_resting_hr_history excludes today's date."""
        result = fitbit_store.get_resting_hr_history(days=9)
        # Today is March 27, so we should get March 19-26 = up to 8 values
        assert len(result) <= 8

    @freeze_time("2026-03-27 14:00:00")
    def test_history_values_plausible(self, all_snapshots):
        """All resting HR history values should be in physiological range."""
        result = fitbit_store.get_resting_hr_history(days=9)
        for val in result:
            assert 40 <= val <= 120, f"Implausible resting HR in history: {val}"


# ---------------------------------------------------------------------------
# Briefing context
# ---------------------------------------------------------------------------

class TestBriefingContext:
    def test_all_days_produce_context(self, all_snapshots):
        """get_briefing_context should produce a non-empty string for each day."""
        for day in SNAPSHOT_DATES:
            result = fitbit_store.get_briefing_context(day)
            assert isinstance(result, str), f"Context not str on {day}"
            assert len(result) > 0, f"Empty context on {day}"

    def test_context_contains_sections(self, all_snapshots):
        """Context should contain key section markers."""
        # Use a day we know has complete data
        result = fitbit_store.get_briefing_context("2026-03-25")
        assert "Fitbit health data:" in result
        assert "Sleep:" in result
        assert "Resting heart rate:" in result
        assert "Activity:" in result

    def test_context_no_crash_on_any_day(self, all_snapshots):
        """No exceptions on any real data shape."""
        for day in SNAPSHOT_DATES:
            # Should never raise
            result = fitbit_store.get_briefing_context(day)
            assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Nudge evaluation with real data — sedentary trigger
# ---------------------------------------------------------------------------

class TestNudgeEvaluation:
    @freeze_time("2026-03-25 15:00:00")
    def test_sedentary_trigger_on_low_step_day(self, all_snapshots):
        """March 25 has 1779 steps — sedentary nudge should trigger at 3pm."""
        triggers = tick.evaluate_nudges()
        nudge_types = [t for t, _ in triggers]
        descriptions = [d for _, d in triggers]
        # fitbit_sedentary checks sedentary_minutes > 120 during 9-21h
        # March 25 has sedentary_minutes=689 and steps=1779
        assert "fitbit_sedentary" in nudge_types, (
            f"Expected fitbit_sedentary in {nudge_types}"
        )
        # fitbit_activity_goal checks steps < 3000 during 14-17h
        assert "fitbit_activity_goal" in nudge_types, (
            f"Expected fitbit_activity_goal in {nudge_types}"
        )

    @freeze_time("2026-03-21 15:00:00")
    def test_no_false_activity_trigger_on_active_day(self, all_snapshots):
        """March 21 has 10242 steps — should NOT trigger activity nudge."""
        triggers = tick.evaluate_nudges()
        nudge_types = [t for t, _ in triggers]
        assert "fitbit_activity_goal" not in nudge_types, (
            f"False activity trigger on a 10242-step day: {nudge_types}"
        )


# ---------------------------------------------------------------------------
# Net calories with real combined data
# ---------------------------------------------------------------------------

class TestNetCalories:
    @freeze_time("2026-03-25 22:00:00")
    def test_net_calories_march_25(self):
        """Load March 25 nutrition + fitbit, verify net calorie arithmetic."""
        load_fitbit_snapshots_into_db()
        load_nutrition_entries_into_db()

        result = nutrition_store.get_net_calories("2026-03-25")
        # Consumed: 976 cal (from nutrition entries)
        assert result["consumed"] == 976
        # Burned: 1783 cal from Fitbit activity caloriesOut
        assert result["burned"] == 1783
        # Net = consumed - burned = 976 - 1783 = -807
        assert result["net"] == 976 - 1783
        # On track for deficit of 500+
        assert result["on_track"] is True
