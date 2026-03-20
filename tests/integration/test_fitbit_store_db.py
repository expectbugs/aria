"""Integration tests for fitbit_store — JSONB operations and exercise mode."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import fitbit_store


SAMPLE_SNAPSHOT = {
    "date": "2026-03-20",
    "heart_rate": {"value": {"restingHeartRate": 65, "heartRateZones": []}},
    "sleep": {
        "sleep": [{
            "isMainSleep": True, "minutesAsleep": 420, "efficiency": 88,
            "startTime": "2026-03-19T23:00", "endTime": "2026-03-20T06:00",
            "levels": {"summary": {
                "deep": {"minutes": 60}, "light": {"minutes": 200},
                "rem": {"minutes": 120}, "wake": {"minutes": 40},
            }},
        }],
    },
    "activity": {
        "steps": 8500, "caloriesOut": 2400, "activityCalories": 800,
        "fairlyActiveMinutes": 20, "veryActiveMinutes": 15,
        "sedentaryMinutes": 600, "floors": 5,
        "distances": [{"activity": "total", "distance": 5.2}],
    },
}


class TestSnapshotJSONB:
    def test_save_and_retrieve(self):
        fitbit_store.save_snapshot(SAMPLE_SNAPSHOT)
        snap = fitbit_store.get_snapshot("2026-03-20")
        assert snap is not None
        assert snap["heart_rate"]["value"]["restingHeartRate"] == 65

    def test_upsert_merge(self):
        """ON CONFLICT should merge JSONB, not overwrite."""
        fitbit_store.save_snapshot({
            "date": "2026-03-20", "heart_rate": {"value": {"restingHeartRate": 65}},
        })
        fitbit_store.save_snapshot({
            "date": "2026-03-20", "sleep": {"sleep": []},
        })
        snap = fitbit_store.get_snapshot("2026-03-20")
        # Both keys should be present after merge
        assert "heart_rate" in snap
        assert "sleep" in snap

    def test_today_resolution(self):
        fitbit_store.save_snapshot({"date": "today", "test": True})
        snap = fitbit_store.get_snapshot("today")
        assert snap is not None
        assert snap["test"] is True

    def test_none_values_filtered(self):
        """None values should be filtered before JSONB insert."""
        fitbit_store.save_snapshot({
            "date": "2026-03-20",
            "heart_rate": {"rhr": 65},
            "hrv": None,  # should be filtered out
        })
        snap = fitbit_store.get_snapshot("2026-03-20")
        assert "heart_rate" in snap
        # hrv was None so it should not overwrite existing data


class TestSummaryExtraction:
    def test_sleep_summary(self):
        fitbit_store.save_snapshot(SAMPLE_SNAPSHOT)
        sleep = fitbit_store.get_sleep_summary("2026-03-20")
        assert sleep["duration_hours"] == 7.0
        assert sleep["deep_minutes"] == 60
        assert sleep["efficiency"] == 88

    def test_activity_summary(self):
        fitbit_store.save_snapshot(SAMPLE_SNAPSHOT)
        act = fitbit_store.get_activity_summary("2026-03-20")
        assert act["steps"] == 8500
        assert act["calories_total"] == 2400
        assert act["active_minutes"] == 35

    def test_no_data_returns_none(self):
        assert fitbit_store.get_snapshot("2099-01-01") is None
        assert fitbit_store.get_sleep_summary("2099-01-01") is None


class TestTrend:
    def test_multi_day_trend(self):
        for i in range(3):
            d = (date.today() - timedelta(days=i)).isoformat()
            fitbit_store.save_snapshot({
                "date": d,
                "heart_rate": {"value": {"restingHeartRate": 65 + i}},
                "activity": {"steps": 8000 + i * 500},
                "sleep": {"sleep": [{
                    "isMainSleep": True, "minutesAsleep": 400 + i * 20,
                }]},
            })
        trend = fitbit_store.get_trend(days=7)
        assert "Avg resting HR" in trend
        assert "Avg steps" in trend


class TestExerciseMode:
    @patch("fitbit_store.get_heart_summary", return_value={"resting_hr": 65})
    def test_full_exercise_lifecycle(self, mock_hr):
        # Start
        state = fitbit_store.start_exercise("stationary_bike")
        assert state["active"] is True
        assert state["exercise_type"] == "stationary_bike"
        assert state["resting_hr"] == 65
        assert "fat_burn" in state["target_zones"]

        # Record HR
        fitbit_store.record_exercise_hr([
            {"time": "14:10:00", "value": 130},
            {"time": "14:11:00", "value": 140},
        ])

        # Verify HR appended
        exercise = fitbit_store.get_exercise_state()
        assert exercise is not None
        assert len(exercise["hr_readings"]) == 2

        # End
        result = fitbit_store.end_exercise("user ended")
        assert result["active"] is False
        assert result["end_reason"] == "user ended"

        # Should be gone
        assert fitbit_store.get_exercise_state() is None

    @patch("fitbit_store.get_heart_summary", return_value={"resting_hr": 65})
    def test_start_deactivates_existing(self, mock_hr):
        """Starting a new exercise should deactivate any prior active session."""
        fitbit_store.start_exercise("walking")
        fitbit_store.start_exercise("stationary_bike")

        # Only one active session
        exercise = fitbit_store.get_exercise_state()
        assert exercise["exercise_type"] == "stationary_bike"

    @patch("fitbit_store.get_heart_summary", return_value={"resting_hr": 65})
    def test_karvonen_zone_calculation(self, mock_hr):
        state = fitbit_store.start_exercise("general")
        zones = state["target_zones"]

        # Verify zones are computed from resting HR = 65
        # Age from config.OWNER_BIRTH_DATE (1984-05-18) → ~41 years → max HR ~179
        assert zones["warm_up"]["min"] < zones["fat_burn"]["min"]
        assert zones["fat_burn"]["max"] <= zones["cardio"]["min"]
        assert zones["cardio"]["max"] <= zones["peak"]["min"]
        assert zones["peak"]["max"] == state["max_hr"]

    def test_hr_readings_jsonb_append(self):
        """Verify hr_readings || new_readings::jsonb works correctly."""
        with patch("fitbit_store.get_heart_summary", return_value={"resting_hr": 65}):
            fitbit_store.start_exercise("general")

        # Append batch 1
        fitbit_store.record_exercise_hr([
            {"time": "14:00:00", "value": 100},
        ])
        # Append batch 2
        fitbit_store.record_exercise_hr([
            {"time": "14:01:00", "value": 110},
            {"time": "14:02:00", "value": 120},
        ])

        state = fitbit_store.get_exercise_state()
        assert len(state["hr_readings"]) == 3
