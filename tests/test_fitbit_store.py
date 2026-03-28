"""Tests for fitbit_store.py — Fitbit data storage and analysis."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import fitbit_store


def _patch_db():
    mock_conn = MagicMock()
    patcher = patch("fitbit_store.db.get_conn")
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


SAMPLE_SNAPSHOT = {
    "date": "2026-03-20",
    "heart_rate": {
        "value": {
            "restingHeartRate": 65,
            "heartRateZones": [
                {"name": "Out of Range", "minutes": 1200, "caloriesOut": 1500},
                {"name": "Fat Burn", "minutes": 30, "caloriesOut": 200},
            ],
        }
    },
    "hrv": {"value": {"dailyRmssd": 35.5, "deepRmssd": 42.0}},
    "sleep": {
        "sleep": [{
            "isMainSleep": True,
            "minutesAsleep": 420,
            "efficiency": 88,
            "startTime": "2026-03-19T23:00:00",
            "endTime": "2026-03-20T06:00:00",
            "levels": {
                "summary": {
                    "deep": {"minutes": 60},
                    "light": {"minutes": 200},
                    "rem": {"minutes": 120},
                    "wake": {"minutes": 40},
                }
            },
        }]
    },
    "spo2": {"value": {"avg": 96.5, "min": 94, "max": 99}},
    "activity": {
        "steps": 8500,
        "caloriesOut": 2400,
        "activityCalories": 800,
        "fairlyActiveMinutes": 20,
        "veryActiveMinutes": 15,
        "sedentaryMinutes": 600,
        "floors": 5,
        "distances": [{"activity": "total", "distance": 5.2}],
    },
    "breathing_rate": {"value": {"breathingRate": 16}},
    "temperature": {"value": {"nightlyRelative": -0.3}},
    "vo2max": {"value": {"vo2Max": 38.5}},
}


class TestSaveSnapshot:
    def test_insert_new(self):
        mc, p = _patch_db()
        try:
            fitbit_store.save_snapshot({"date": "2026-03-20", "heart_rate": {}})
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO fitbit_snapshots" in sql
            assert "ON CONFLICT" in sql
        finally:
            p.stop()

    def test_resolves_today(self):
        mc, p = _patch_db()
        try:
            fitbit_store.save_snapshot({"date": "today", "test": True})
            params = mc.execute.call_args[0][1]
            assert params[0] == date.today().isoformat()
        finally:
            p.stop()

    def test_resolves_yesterday(self):
        mc, p = _patch_db()
        try:
            fitbit_store.save_snapshot({"date": "yesterday", "test": True})
            params = mc.execute.call_args[0][1]
            expected = (date.today() - timedelta(days=1)).isoformat()
            assert params[0] == expected
        finally:
            p.stop()

    def test_filters_none_values(self):
        mc, p = _patch_db()
        try:
            fitbit_store.save_snapshot({"date": "2026-03-20", "hrv": None, "sleep": {}})
            # Should not include "hrv" key since value is None
            # The JSONB parameter is the second arg
        finally:
            p.stop()


class TestGetSnapshot:
    def test_returns_data(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = {"data": SAMPLE_SNAPSHOT}
        try:
            snap = fitbit_store.get_snapshot("2026-03-20")
            assert snap["heart_rate"]["value"]["restingHeartRate"] == 65
        finally:
            p.stop()

    def test_returns_none_if_missing(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = None
        try:
            assert fitbit_store.get_snapshot("2026-01-01") is None
        finally:
            p.stop()


class TestSleepSummary:
    @patch("fitbit_store.get_snapshot")
    def test_extracts_sleep_data(self, mock_snap):
        mock_snap.return_value = SAMPLE_SNAPSHOT
        result = fitbit_store.get_sleep_summary("2026-03-20")
        assert result["total_minutes"] == 420
        assert result["deep_minutes"] == 60
        assert result["rem_minutes"] == 120
        assert result["light_minutes"] == 200
        assert result["wake_minutes"] == 40
        assert result["efficiency"] == 88
        assert result["duration_hours"] == 7.0

    @patch("fitbit_store.get_snapshot")
    def test_no_sleep_data(self, mock_snap):
        mock_snap.return_value = {"date": "2026-03-20"}
        assert fitbit_store.get_sleep_summary("2026-03-20") is None


class TestHeartSummary:
    @patch("fitbit_store.get_snapshot")
    def test_extracts_heart_data(self, mock_snap):
        mock_snap.return_value = SAMPLE_SNAPSHOT
        result = fitbit_store.get_heart_summary("2026-03-20")
        assert result["resting_hr"] == 65
        assert len(result["zones"]) == 2

    @patch("fitbit_store.get_snapshot")
    def test_no_heart_data(self, mock_snap):
        mock_snap.return_value = {}
        assert fitbit_store.get_heart_summary("2026-03-20") is None


class TestHrvSummary:
    @patch("fitbit_store.get_snapshot")
    def test_extracts_hrv(self, mock_snap):
        mock_snap.return_value = SAMPLE_SNAPSHOT
        result = fitbit_store.get_hrv_summary("2026-03-20")
        assert result["rmssd"] == 35.5
        assert result["deep_rmssd"] == 42.0

    @patch("fitbit_store.get_snapshot")
    def test_no_hrv(self, mock_snap):
        mock_snap.return_value = {"hrv": {"value": {}}}
        assert fitbit_store.get_hrv_summary("2026-03-20") is None


class TestActivitySummary:
    @patch("fitbit_store.get_snapshot")
    def test_extracts_activity(self, mock_snap):
        mock_snap.return_value = SAMPLE_SNAPSHOT
        result = fitbit_store.get_activity_summary("2026-03-20")
        assert result["steps"] == 8500
        assert result["calories_total"] == 2400
        assert result["active_minutes"] == 35
        assert result["distance_miles"] == 5.2


class TestSpo2Summary:
    @patch("fitbit_store.get_snapshot")
    def test_extracts_spo2(self, mock_snap):
        mock_snap.return_value = SAMPLE_SNAPSHOT
        result = fitbit_store.get_spo2_summary("2026-03-20")
        assert result["avg"] == 96.5
        assert result["min"] == 94


class TestBreathingRateSummary:
    @patch("fitbit_store.get_snapshot")
    def test_extracts_breathing_rate(self, mock_snap):
        mock_snap.return_value = SAMPLE_SNAPSHOT
        result = fitbit_store.get_breathing_rate_summary("2026-03-20")
        assert result["rate"] == 16.0
        assert isinstance(result["rate"], float)

    @patch("fitbit_store.get_snapshot")
    def test_no_breathing_data(self, mock_snap):
        mock_snap.return_value = {}
        assert fitbit_store.get_breathing_rate_summary("2026-03-20") is None

    @patch("fitbit_store.get_snapshot")
    def test_string_value(self, mock_snap):
        mock_snap.return_value = {"breathing_rate": {"value": {"breathingRate": "16"}}}
        result = fitbit_store.get_breathing_rate_summary("2026-03-20")
        assert result["rate"] == 16.0


class TestTemperatureSummary:
    @patch("fitbit_store.get_snapshot")
    def test_extracts_temperature(self, mock_snap):
        mock_snap.return_value = SAMPLE_SNAPSHOT
        result = fitbit_store.get_temperature_summary("2026-03-20")
        assert result["nightly_relative"] == -0.3
        assert isinstance(result["nightly_relative"], float)

    @patch("fitbit_store.get_snapshot")
    def test_no_temperature_data(self, mock_snap):
        mock_snap.return_value = {}
        assert fitbit_store.get_temperature_summary("2026-03-20") is None

    @patch("fitbit_store.get_snapshot")
    def test_string_value(self, mock_snap):
        mock_snap.return_value = {"temperature": {"value": {"nightlyRelative": "-0.3"}}}
        result = fitbit_store.get_temperature_summary("2026-03-20")
        assert result["nightly_relative"] == -0.3


class TestVo2maxSummary:
    @patch("fitbit_store.get_snapshot")
    def test_extracts_vo2max(self, mock_snap):
        mock_snap.return_value = SAMPLE_SNAPSHOT
        result = fitbit_store.get_vo2max_summary("2026-03-20")
        assert result["vo2max"] == 38.5
        assert isinstance(result["vo2max"], float)

    @patch("fitbit_store.get_snapshot")
    def test_no_vo2max_data(self, mock_snap):
        mock_snap.return_value = {}
        assert fitbit_store.get_vo2max_summary("2026-03-20") is None

    @patch("fitbit_store.get_snapshot")
    def test_string_value(self, mock_snap):
        mock_snap.return_value = {"vo2max": {"value": {"vo2Max": "38.5"}}}
        result = fitbit_store.get_vo2max_summary("2026-03-20")
        assert result["vo2max"] == 38.5


class TestBriefingContext:
    @patch("fitbit_store.get_snapshot")
    def test_builds_full_context(self, mock_snap):
        mock_snap.return_value = {
            "sleep": {
                "sleep": [{
                    "isMainSleep": True,
                    "minutesAsleep": 420,
                    "efficiency": 88,
                    "startTime": "2026-03-19T23:00:00",
                    "endTime": "2026-03-20T06:00:00",
                    "levels": {"summary": {
                        "deep": {"minutes": 60},
                        "rem": {"minutes": 120},
                        "light": {"minutes": 200},
                        "wake": {"minutes": 40},
                    }},
                }],
            },
            "heart_rate": {"value": {"restingHeartRate": 65, "heartRateZones": []}},
            "hrv": {"value": {"dailyRmssd": 35.5, "deepRmssd": 42.0}},
            "spo2": {"value": {"avg": 96.5, "min": 94, "max": 99}},
            "activity": {
                "steps": 8500,
                "distances": [{"activity": "total", "distance": 5.2}],
                "caloriesOut": 2400,
                "activityCalories": 500,
                "fairlyActiveMinutes": 15,
                "veryActiveMinutes": 20,
                "sedentaryMinutes": 600,
                "floors": 10,
            },
            "breathing_rate": {"value": {"breathingRate": 16.0}},
            "temperature": {"value": {"nightlyRelative": -0.3}},
            "vo2max": {"value": {"vo2Max": 38.5}},
        }

        ctx = fitbit_store.get_briefing_context("2026-03-20")
        assert "Sleep: 7.0h" in ctx
        assert "Resting heart rate: 65 bpm" in ctx
        assert "HRV" in ctx
        assert "SpO2" in ctx
        assert "8,500 steps" in ctx
        assert "Breathing rate: 16.0" in ctx
        assert "Skin temp variation: -0.3" in ctx
        assert "VO2 Max: 38.5" in ctx

    @patch("fitbit_store.get_snapshot")
    def test_empty_data(self, mock_snap):
        mock_snap.return_value = None
        assert fitbit_store.get_briefing_context("2026-03-20") == ""


class TestRestingHrHistory:
    def test_returns_int_values(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            {"data": {"heart_rate": {"value": {"restingHeartRate": 65}}}},
            {"data": {"heart_rate": {"value": {"restingHeartRate": 68}}}},
        ]
        try:
            hrs = fitbit_store.get_resting_hr_history(days=7)
            assert hrs == [65, 68]
            assert all(isinstance(h, int) for h in hrs)
        finally:
            p.stop()

    def test_casts_string_values(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            {"data": {"heart_rate": {"value": {"restingHeartRate": "65"}}}},
        ]
        try:
            hrs = fitbit_store.get_resting_hr_history(days=7)
            assert hrs == [65]
            assert isinstance(hrs[0], int)
        finally:
            p.stop()

    def test_skips_missing_hr_data(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            {"data": {"heart_rate": {"value": {"restingHeartRate": 65}}}},
            {"data": {"heart_rate": {}}},  # no value
            {"data": {}},  # no heart_rate at all
        ]
        try:
            hrs = fitbit_store.get_resting_hr_history(days=7)
            assert hrs == [65]
        finally:
            p.stop()

    def test_empty_snapshots(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert fitbit_store.get_resting_hr_history(days=7) == []
        finally:
            p.stop()


class TestGetTrend:
    def test_builds_trend_string(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            {"date": date(2026, 3, 19), "data": {
                "heart_rate": {"value": {"restingHeartRate": 65}},
                "hrv": {"value": {"dailyRmssd": 35}},
                "sleep": {"sleep": [{"isMainSleep": True, "minutesAsleep": 420}]},
                "activity": {"steps": 8000},
            }},
            {"date": date(2026, 3, 20), "data": {
                "heart_rate": {"value": {"restingHeartRate": 67}},
                "hrv": {"value": {"dailyRmssd": 33}},
                "sleep": {"sleep": [{"isMainSleep": True, "minutesAsleep": 390}]},
                "activity": {"steps": 9000},
            }},
        ]
        try:
            trend = fitbit_store.get_trend(days=7)
            assert "Avg resting HR" in trend
            assert "Avg HRV" in trend
            assert "Avg sleep" in trend
            assert "Avg steps" in trend
        finally:
            p.stop()

    def test_empty_trend(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert fitbit_store.get_trend() == ""
        finally:
            p.stop()


class TestExerciseMode:
    @patch("fitbit_store.get_heart_summary")
    def test_start_exercise(self, mock_hr):
        mock_hr.return_value = {"resting_hr": 65}
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = {
            "id": 1, "active": True, "exercise_type": "stationary_bike",
            "started_at": datetime(2026, 3, 20, 14, 0, tzinfo=timezone.utc),
            "ended_at": None, "end_reason": None,
            "resting_hr": 65, "max_hr": 178,
            "target_zones": {
                "warm_up": {"min": 110, "max": 121},
                "fat_burn": {"min": 121, "max": 144},
                "cardio": {"min": 144, "max": 161},
                "peak": {"min": 161, "max": 178},
            },
            "hr_readings": [], "nudge_count": 0, "summary": None,
        }
        try:
            result = fitbit_store.start_exercise("stationary_bike")
            assert result["exercise_type"] == "stationary_bike"
            assert result["resting_hr"] == 65
            # Should deactivate existing sessions first
            calls = mc.execute.call_args_list
            deactivate_sql = calls[0][0][0]
            assert "SET active = FALSE" in deactivate_sql
        finally:
            p.stop()

    def test_end_exercise(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = {
            "id": 1, "active": True, "exercise_type": "general",
            "started_at": datetime(2026, 3, 20, 14, 0, tzinfo=timezone.utc),
            "ended_at": None, "end_reason": None,
            "resting_hr": 65, "max_hr": 178,
            "target_zones": {}, "hr_readings": [
                {"hr": 130, "time": "14:10:00"},
                {"hr": 140, "time": "14:11:00"},
            ],
            "nudge_count": 5, "summary": None,
        }
        try:
            result = fitbit_store.end_exercise("user ended")
            assert result["active"] is False
            assert result["end_reason"] == "user ended"
        finally:
            p.stop()

    def test_end_exercise_not_active(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = None
        try:
            result = fitbit_store.end_exercise()
            assert result["status"] == "not_active"
        finally:
            p.stop()

    def test_get_exercise_state_auto_expire(self):
        """Expired sessions are cleaned up via SQL UPDATE, not Python datetime."""
        mc, p = _patch_db()
        # After the UPDATE, no active rows remain → fetchone returns None
        mc.execute.return_value.fetchone.return_value = None
        try:
            result = fitbit_store.get_exercise_state()
            assert result is None
            # Verify the SQL UPDATE for auto-expire was issued
            calls = mc.execute.call_args_list
            expire_sql = calls[0][0][0]
            assert "UPDATE fitbit_exercise" in expire_sql
            assert "auto-expired after 90 minutes" in expire_sql
            assert "INTERVAL '90 minutes'" in expire_sql
        finally:
            p.stop()

    def test_record_exercise_hr(self):
        mc, p = _patch_db()
        try:
            fitbit_store.record_exercise_hr([
                {"time": "14:10:00", "value": 130},
                {"time": "14:11:00", "value": 135},
            ])
            sql = mc.execute.call_args[0][0]
            assert "hr_readings = hr_readings ||" in sql
        finally:
            p.stop()

    @patch("fitbit_store.get_exercise_state")
    def test_coaching_context(self, mock_state):
        mock_state.return_value = {
            "exercise_type": "stationary_bike",
            "started_at": (datetime.now() - timedelta(minutes=20)).isoformat(),
            "resting_hr": 65, "max_hr": 178,
            "target_zones": {
                "fat_burn": {"min": 121, "max": 144},
                "cardio": {"min": 144, "max": 161},
            },
            "hr_readings": [
                {"hr": 130, "time": "14:10"},
                {"hr": 135, "time": "14:11"},
                {"hr": 140, "time": "14:12"},
            ],
        }
        ctx = fitbit_store.get_exercise_coaching_context()
        assert "EXERCISE MODE ACTIVE" in ctx
        assert "stationary_bike" in ctx
        assert "fat burn zone" in ctx.lower()
        assert "Recent HR" in ctx

    @patch("fitbit_store.get_exercise_state")
    def test_coaching_context_not_active(self, mock_state):
        mock_state.return_value = None
        assert fitbit_store.get_exercise_coaching_context() == ""

    @patch("fitbit_store.get_exercise_state")
    def test_coaching_context_with_preloaded_state(self, mock_state):
        """Passing state= should skip the internal get_exercise_state() call."""
        preloaded = {
            "exercise_type": "walking",
            "started_at": (datetime.now() - timedelta(minutes=10)).isoformat(),
            "resting_hr": 65, "max_hr": 178,
            "target_zones": {"fat_burn": {"min": 121, "max": 144}},
            "hr_readings": [],
        }
        ctx = fitbit_store.get_exercise_coaching_context(state=preloaded)
        assert "EXERCISE MODE ACTIVE" in ctx
        assert "walking" in ctx
        # The internal get_exercise_state() should NOT have been called
        mock_state.assert_not_called()


class TestSafeCasting:
    """Verify Fitbit API string values are cast to int/float at extraction boundary."""

    def test_safe_int_normal(self):
        assert fitbit_store._safe_int(42) == 42

    def test_safe_int_string(self):
        assert fitbit_store._safe_int("8500") == 8500

    def test_safe_int_none(self):
        assert fitbit_store._safe_int(None) == 0

    def test_safe_int_none_custom_default(self):
        assert fitbit_store._safe_int(None, default=-1) == -1

    def test_safe_int_garbage(self):
        assert fitbit_store._safe_int("abc") == 0

    def test_safe_float_normal(self):
        assert fitbit_store._safe_float(35.5) == 35.5

    def test_safe_float_string(self):
        assert fitbit_store._safe_float("96.5") == 96.5

    def test_safe_float_none(self):
        assert fitbit_store._safe_float(None) == 0.0

    def test_safe_float_garbage(self):
        assert fitbit_store._safe_float("abc") == 0.0

    @patch("fitbit_store.get_snapshot")
    def test_activity_summary_with_string_values(self, mock_snap):
        """Fitbit has returned string ints before — verify no crash."""
        snap = dict(SAMPLE_SNAPSHOT)
        snap["activity"] = {
            "steps": "8500",
            "caloriesOut": "2400",
            "activityCalories": "800",
            "fairlyActiveMinutes": "20",
            "veryActiveMinutes": "15",
            "sedentaryMinutes": "600",
            "floors": "5",
            "distances": [{"activity": "total", "distance": "5.2"}],
        }
        mock_snap.return_value = snap
        result = fitbit_store.get_activity_summary("2026-03-20")
        assert result["steps"] == 8500
        assert isinstance(result["steps"], int)
        assert result["calories_total"] == 2400
        assert result["active_minutes"] == 35
        assert result["distance_miles"] == 5.2
        assert result["sedentary_minutes"] == 600
        assert result["floors"] == 5

    @patch("fitbit_store.get_snapshot")
    def test_sleep_summary_with_string_values(self, mock_snap):
        snap = dict(SAMPLE_SNAPSHOT)
        snap["sleep"] = {
            "sleep": [{
                "isMainSleep": True,
                "minutesAsleep": "420",
                "efficiency": "88",
                "startTime": "2026-03-19T23:00:00",
                "endTime": "2026-03-20T06:00:00",
                "levels": {
                    "summary": {
                        "deep": {"minutes": "60"},
                        "light": {"minutes": "200"},
                        "rem": {"minutes": "120"},
                        "wake": {"minutes": "40"},
                    }
                },
            }]
        }
        mock_snap.return_value = snap
        result = fitbit_store.get_sleep_summary("2026-03-20")
        assert result["total_minutes"] == 420
        assert isinstance(result["total_minutes"], int)
        assert result["deep_minutes"] == 60
        assert result["duration_hours"] == 7.0

    @patch("fitbit_store.get_snapshot")
    def test_heart_summary_with_string_values(self, mock_snap):
        snap = dict(SAMPLE_SNAPSHOT)
        snap["heart_rate"] = {
            "value": {
                "restingHeartRate": "65",
                "heartRateZones": [
                    {"name": "Fat Burn", "minutes": "30", "caloriesOut": "200.5"},
                ],
            }
        }
        mock_snap.return_value = snap
        result = fitbit_store.get_heart_summary("2026-03-20")
        assert result["resting_hr"] == 65
        assert isinstance(result["resting_hr"], int)
        assert result["zones"][0]["minutes"] == 30
        assert result["zones"][0]["calories_out"] == 200.5

    @patch("fitbit_store.get_snapshot")
    def test_hrv_summary_with_string_values(self, mock_snap):
        snap = dict(SAMPLE_SNAPSHOT)
        snap["hrv"] = {"value": {"dailyRmssd": "35.5", "deepRmssd": "42.0"}}
        mock_snap.return_value = snap
        result = fitbit_store.get_hrv_summary("2026-03-20")
        assert result["rmssd"] == 35.5
        assert isinstance(result["rmssd"], float)

    @patch("fitbit_store.get_snapshot")
    def test_spo2_summary_with_string_values(self, mock_snap):
        snap = dict(SAMPLE_SNAPSHOT)
        snap["spo2"] = {"value": {"avg": "96.5", "min": "94", "max": "99"}}
        mock_snap.return_value = snap
        result = fitbit_store.get_spo2_summary("2026-03-20")
        assert result["avg"] == 96.5
        assert isinstance(result["avg"], float)

    def test_trend_with_string_values(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            {"date": date(2026, 3, 19), "data": {
                "heart_rate": {"value": {"restingHeartRate": "65"}},
                "hrv": {"value": {"dailyRmssd": "35"}},
                "sleep": {"sleep": [{"isMainSleep": True, "minutesAsleep": "420"}]},
                "activity": {"steps": "8000"},
            }},
        ]
        try:
            trend = fitbit_store.get_trend(days=7)
            assert "Avg resting HR" in trend
            assert "Avg HRV" in trend
            assert "Avg sleep" in trend
            assert "Avg steps" in trend
        finally:
            p.stop()
