"""Tests for tick.py — cron job: timers, nudges, location reminders, Fitbit.

SAFETY: All SMS sending, HTTP requests, and database access are mocked.
No real timers fire, no real nudges are sent.
"""

from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

import tick


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------

class TestIsQuietHours:
    @patch("tick.config")
    def test_inside_quiet_hours(self, mock_cfg):
        mock_cfg.QUIET_HOURS_START = 0
        mock_cfg.QUIET_HOURS_END = 7
        with patch("tick.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 20, 3, 0)
            assert tick.is_quiet_hours() is True

    @patch("tick.config")
    def test_outside_quiet_hours(self, mock_cfg):
        mock_cfg.QUIET_HOURS_START = 0
        mock_cfg.QUIET_HOURS_END = 7
        with patch("tick.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 20, 12, 0)
            assert tick.is_quiet_hours() is False

    @patch("tick.config")
    def test_wrapping_midnight(self, mock_cfg):
        mock_cfg.QUIET_HOURS_START = 22
        mock_cfg.QUIET_HOURS_END = 7
        with patch("tick.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 20, 23, 0)
            assert tick.is_quiet_hours() is True

        with patch("tick.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 20, 5, 0)
            assert tick.is_quiet_hours() is True


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

class TestStateManagement:
    @patch("tick.db.get_conn")
    def test_load_state(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mc.execute.return_value.fetchall.return_value = [
            {"key": "last_nudge", "value": "2026-03-20T14:00:00"},
        ]
        state = tick.load_state()
        assert state["last_nudge"] == "2026-03-20T14:00:00"

    @patch("tick.db.get_conn")
    def test_save_state(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        tick.save_state({"key1": "val1", "key2": "val2"})
        assert mc.execute.call_count == 2


class TestCooldowns:
    def test_is_cooled_down_first_time(self):
        assert tick.is_cooled_down({}, "meal_reminder", 4) is True

    def test_is_cooled_down_recent(self):
        recent = datetime.now().isoformat()
        assert tick.is_cooled_down({"meal_reminder": recent}, "meal_reminder", 4) is False

    def test_is_cooled_down_expired(self):
        old = (datetime.now() - timedelta(hours=5)).isoformat()
        assert tick.is_cooled_down({"meal_reminder": old}, "meal_reminder", 4) is True


# ---------------------------------------------------------------------------
# Timer firing
# ---------------------------------------------------------------------------

class TestFireTimer:
    @patch("tick.sms.send_to_owner")
    @patch("tick.timer_store.complete_timer")
    @patch("tick.is_quiet_hours", return_value=False)
    def test_sms_delivery(self, mock_quiet, mock_complete, mock_send):
        timer = {
            "id": "t1", "delivery": "sms", "message": "Laundry done!",
            "label": "Laundry", "priority": "gentle",
        }
        result = tick.fire_timer(timer)
        assert result is True
        mock_send.assert_called_once_with("Laundry done!")
        mock_complete.assert_called_once_with("t1")

    @patch("tick.is_quiet_hours", return_value=True)
    def test_deferred_during_quiet_hours(self, mock_quiet):
        timer = {
            "id": "t1", "delivery": "sms", "message": "Test",
            "label": "Test", "priority": "gentle",
        }
        result = tick.fire_timer(timer)
        assert result is False

    @patch("tick.sms.send_to_owner")
    @patch("tick.timer_store.complete_timer")
    @patch("tick.is_quiet_hours", return_value=True)
    def test_urgent_bypasses_quiet_hours(self, mock_quiet, mock_complete, mock_send):
        timer = {
            "id": "t1", "delivery": "sms", "message": "WAKE UP",
            "label": "Alarm", "priority": "urgent",
        }
        result = tick.fire_timer(timer)
        assert result is True

    @patch("tick.sms.send_to_owner")
    @patch("tick.timer_store.complete_timer")
    @patch("httpx.post", side_effect=Exception("Connection refused"))
    @patch("tick.is_quiet_hours", return_value=False)
    def test_voice_delivery_fallback_to_sms(self, mock_quiet, mock_httpx_post,
                                            mock_complete, mock_send):
        timer = {
            "id": "t1", "delivery": "voice", "message": "Timer!",
            "label": "Voice Timer", "priority": "gentle",
        }
        tick.fire_timer(timer)
        # Should fall back to SMS
        mock_send.assert_called()


class TestProcessTimers:
    @patch("tick.fire_timer")
    @patch("tick.timer_store.get_due")
    def test_fires_all_due_timers(self, mock_get_due, mock_fire):
        mock_get_due.return_value = [
            {"id": "t1", "label": "A"},
            {"id": "t2", "label": "B"},
        ]
        tick.process_timers()
        assert mock_fire.call_count == 2


# ---------------------------------------------------------------------------
# Location-based reminders
# ---------------------------------------------------------------------------

class TestLocationReminders:
    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    @patch("tick.sms.send_to_owner")
    @patch("tick.calendar_store.complete_reminder")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.location_store.get_latest")
    @patch("tick.is_quiet_hours", return_value=False)
    def test_arrive_trigger(self, mock_quiet, mock_loc, mock_reminders,
                             mock_complete, mock_send, mock_load, mock_save):
        mock_loc.return_value = {
            "location": "3549 rapids trail, waukesha, wisconsin",
        }
        mock_reminders.return_value = [
            {"id": "r1", "text": "Check mail", "location": "home",
             "location_trigger": "arrive", "done": False},
        ]
        tick.check_location_reminders()
        mock_send.assert_called_once()
        mock_complete.assert_called_once_with("r1")

    @patch("tick.save_state")
    @patch("tick.load_state")
    @patch("tick.sms.send_to_owner")
    @patch("tick.calendar_store.complete_reminder")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.location_store.get_latest")
    @patch("tick.is_quiet_hours", return_value=False)
    def test_leave_trigger(self, mock_quiet, mock_loc, mock_reminders,
                            mock_complete, mock_send, mock_load, mock_save):
        # Previous state: was at home
        mock_load.return_value = {"loc_reminder:r1": "at"}
        mock_loc.return_value = {
            "location": "boxhorn, mukwonago, wisconsin",  # at work, not home
        }
        mock_reminders.return_value = [
            {"id": "r1", "text": "Lock door", "location": "home",
             "location_trigger": "leave", "done": False},
        ]
        tick.check_location_reminders()
        mock_send.assert_called_once()
        assert "left" in mock_send.call_args[0][0].lower()

    @patch("tick.location_store.get_latest")
    def test_no_location_data(self, mock_loc):
        mock_loc.return_value = None
        tick.check_location_reminders()  # should not raise


# ---------------------------------------------------------------------------
# Nudge evaluation
# ---------------------------------------------------------------------------

class TestEvaluateNudges:
    @patch("tick.nutrition_store.get_daily_totals")
    @patch("tick.nutrition_store.get_net_calories")
    @patch("tick.fitbit_store.get_activity_summary")
    @patch("tick.fitbit_store.get_heart_summary")
    @patch("tick.fitbit_store.get_sleep_summary")
    @patch("tick.location_store.get_latest")
    @patch("tick.legal_store.get_upcoming_dates")
    @patch("tick.health_store.get_patterns")
    @patch("tick.health_store.get_entries")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    def test_meal_gap_detection(self, mock_events, mock_reminders,
                                 mock_health, mock_patterns, mock_legal,
                                 mock_loc, mock_sleep, mock_hr,
                                 mock_activity, mock_net_cal, mock_totals):
        mock_events.return_value = []
        mock_reminders.return_value = []
        mock_health.return_value = []  # no meals today
        mock_patterns.return_value = []
        mock_legal.return_value = []
        mock_loc.return_value = None
        mock_sleep.return_value = None
        mock_hr.return_value = None
        mock_activity.return_value = None
        mock_totals.return_value = {"item_count": 0}

        with patch("tick.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 20, 14, 0)
            mock_dt.strptime = datetime.strptime
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            triggers = tick.evaluate_nudges()

        meal_triggers = [t for t in triggers if t[0] == "meal_reminder"]
        assert len(meal_triggers) > 0

    @patch("tick.nutrition_store.get_daily_totals")
    @patch("tick.nutrition_store.get_net_calories")
    @patch("tick.fitbit_store.get_activity_summary")
    @patch("tick.fitbit_store.get_heart_summary")
    @patch("tick.fitbit_store.get_sleep_summary")
    @patch("tick.location_store.get_latest")
    @patch("tick.legal_store.get_upcoming_dates")
    @patch("tick.health_store.get_patterns")
    @patch("tick.health_store.get_entries")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    def test_overdue_reminder(self, mock_events, mock_reminders,
                               mock_health, mock_patterns, mock_legal,
                               mock_loc, mock_sleep, mock_hr,
                               mock_activity, mock_net_cal, mock_totals):
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        mock_events.return_value = []
        mock_reminders.return_value = [
            {"id": "r1", "text": "Pay bill", "due": yesterday, "done": False},
        ]
        mock_health.return_value = []
        mock_patterns.return_value = []
        mock_legal.return_value = []
        mock_loc.return_value = None
        mock_sleep.return_value = None
        mock_hr.return_value = None
        mock_activity.return_value = None
        mock_totals.return_value = {"item_count": 0}

        triggers = tick.evaluate_nudges()
        reminder_triggers = [t for t in triggers if t[0] == "reminder_due"]
        assert len(reminder_triggers) > 0

    @patch("tick.nutrition_store.get_daily_totals")
    @patch("tick.nutrition_store.get_net_calories")
    @patch("tick.fitbit_store.get_activity_summary")
    @patch("tick.fitbit_store.get_heart_summary")
    @patch("tick.fitbit_store.get_sleep_summary")
    @patch("tick.location_store.get_latest")
    @patch("tick.legal_store.get_upcoming_dates")
    @patch("tick.health_store.get_patterns")
    @patch("tick.health_store.get_entries")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    def test_battery_low(self, mock_events, mock_reminders,
                          mock_health, mock_patterns, mock_legal,
                          mock_loc, mock_sleep, mock_hr,
                          mock_activity, mock_net_cal, mock_totals):
        mock_events.return_value = []
        mock_reminders.return_value = []
        mock_health.return_value = []
        mock_patterns.return_value = []
        mock_legal.return_value = []
        mock_loc.return_value = {"battery_pct": 10}
        mock_sleep.return_value = None
        mock_hr.return_value = None
        mock_activity.return_value = None
        mock_totals.return_value = {"item_count": 0}

        triggers = tick.evaluate_nudges()
        batt_triggers = [t for t in triggers if t[0] == "battery_low"]
        assert len(batt_triggers) > 0

    @patch("tick.nutrition_store.get_daily_totals")
    @patch("tick.nutrition_store.get_net_calories")
    @patch("tick.fitbit_store.get_activity_summary")
    @patch("tick.fitbit_store.get_heart_summary")
    @patch("tick.fitbit_store.get_sleep_summary")
    @patch("tick.location_store.get_latest")
    @patch("tick.legal_store.get_upcoming_dates")
    @patch("tick.health_store.get_patterns")
    @patch("tick.health_store.get_entries")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    def test_sugar_warning(self, mock_events, mock_reminders,
                            mock_health, mock_patterns, mock_legal,
                            mock_loc, mock_sleep, mock_hr,
                            mock_activity, mock_net_cal, mock_totals):
        mock_events.return_value = []
        mock_reminders.return_value = []
        mock_health.return_value = []
        mock_patterns.return_value = []
        mock_legal.return_value = []
        mock_loc.return_value = None
        mock_sleep.return_value = None
        mock_hr.return_value = None
        mock_activity.return_value = None
        mock_totals.return_value = {
            "item_count": 3, "added_sugars_g": 30, "sodium_mg": 1000,
        }

        triggers = tick.evaluate_nudges()
        sugar_triggers = [t for t in triggers if t[0] == "nutrition_sugar_warn"]
        assert len(sugar_triggers) > 0


class TestRunNudgeEvaluation:
    @patch("tick.save_cooldowns")
    @patch("tick.sms.send_to_owner")
    @patch("tick.evaluate_nudges")
    @patch("tick.load_cooldowns", return_value={})
    @patch("tick.is_quiet_hours", return_value=False)
    def test_sends_nudge_sms(self, mock_quiet, mock_cooldowns,
                              mock_nudges, mock_send,
                              mock_save_cd):
        mock_nudges.return_value = [("meal_reminder", "No meals today")]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "Hey, grab some food!"}

        with patch("httpx.post", return_value=mock_resp):
            tick.run_nudge_evaluation()
        mock_send.assert_called_once_with("Hey, grab some food!")
        mock_save_cd.assert_called_once()

    @patch("tick.is_quiet_hours", return_value=True)
    def test_skips_during_quiet_hours(self, mock_quiet):
        tick.run_nudge_evaluation()  # should return immediately


# ---------------------------------------------------------------------------
# Fitbit polling
# ---------------------------------------------------------------------------

class TestFitbitPolling:
    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    @patch("tick.fetch_fitbit_snapshot")
    @patch("tick.is_quiet_hours", return_value=False)
    def test_polls_when_due(self, mock_quiet, mock_fetch, mock_load, mock_save):
        tick.process_fitbit_poll()
        mock_fetch.assert_called_once()

    @patch("tick.is_quiet_hours", return_value=True)
    def test_skips_during_quiet_hours(self, mock_quiet):
        tick.process_fitbit_poll()  # should not poll

    @patch("tick.save_state")
    @patch("tick.load_state")
    @patch("tick.fetch_fitbit_snapshot")
    @patch("tick.is_quiet_hours", return_value=False)
    def test_skips_if_recently_polled(self, mock_quiet, mock_fetch,
                                       mock_load, mock_save):
        mock_load.return_value = {
            "last_fitbit_sync": datetime.now().isoformat(),
        }
        tick.process_fitbit_poll()
        mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Exercise tick
# ---------------------------------------------------------------------------

class TestExerciseTick:
    @patch("tick.fitbit_store.get_exercise_state", return_value=None)
    def test_noop_when_not_exercising(self, mock_state):
        tick.process_exercise_tick()  # should not raise

    @patch("tick.db.get_conn")
    @patch("tick.send_exercise_nudge")
    @patch("tick.fitbit_store.record_exercise_hr")
    @patch("tick.fetch_exercise_hr")
    @patch("tick.fitbit_store.get_exercise_state")
    def test_records_hr_and_nudges(self, mock_state, mock_fetch_hr,
                                    mock_record, mock_nudge, mock_conn):
        mc = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        started = (datetime.now() - timedelta(minutes=30)).isoformat()
        mock_state.return_value = {
            "id": 1, "started_at": started,
            "exercise_type": "stationary_bike",
            "target_zones": {
                "warm_up": {"min": 110, "max": 121},
                "fat_burn": {"min": 121, "max": 144},
                "cardio": {"min": 144, "max": 161},
                "peak": {"min": 161, "max": 178},
            },
            "hr_readings": [],
            "nudge_count": 0,
        }
        mock_fetch_hr.return_value = {
            "readings": [{"time": "14:30:00", "value": 135}],
        }

        tick.process_exercise_tick()
        mock_record.assert_called_once()


# ---------------------------------------------------------------------------
# Main tick
# ---------------------------------------------------------------------------

class TestMainTick:
    @patch("tick.run_nudge_evaluation")
    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    @patch("tick.process_fitbit_poll")
    @patch("tick.process_exercise_tick")
    @patch("tick.check_location_reminders")
    @patch("tick.process_timers")
    def test_runs_all_jobs(self, mock_timers, mock_loc, mock_exercise,
                           mock_fitbit, mock_load, mock_save, mock_nudge):
        with patch("tick.config") as mock_cfg:
            mock_cfg.NUDGE_INTERVAL_MIN = 0  # always run
            tick.main()

        mock_timers.assert_called_once()
        mock_loc.assert_called_once()
        mock_exercise.assert_called_once()
        mock_fitbit.assert_called_once()

    @patch("tick.process_timers", side_effect=Exception("Timer crash"))
    @patch("tick.check_location_reminders")
    @patch("tick.process_exercise_tick")
    @patch("tick.process_fitbit_poll")
    @patch("tick.run_nudge_evaluation")
    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    def test_job_isolation(self, mock_load, mock_save, mock_nudge,
                           mock_fitbit, mock_exercise, mock_loc, mock_timers):
        """One job failing should not prevent others from running."""
        with patch("tick.config") as mock_cfg:
            mock_cfg.NUDGE_INTERVAL_MIN = 30
            tick.main()

        # Location reminders should still run despite timer crash
        mock_loc.assert_called_once()
        mock_exercise.assert_called_once()
        mock_fitbit.assert_called_once()


# ---------------------------------------------------------------------------
# Nudge cooldown constants
# ---------------------------------------------------------------------------

class TestNudgeCooldownConstants:
    def test_all_cooldowns_defined(self):
        expected_types = [
            "meal_reminder", "calendar_warning", "reminder_due",
            "diet_check", "health_pattern", "vehicle_maintenance",
            "legal_deadline", "battery_low", "location_aware",
            "fitbit_sleep", "fitbit_hr_anomaly", "fitbit_sedentary",
            "fitbit_activity_goal", "nutrition_sugar_warn",
            "nutrition_sodium_warn", "nutrition_calorie_surplus",
        ]
        for nudge_type in expected_types:
            assert nudge_type in tick.NUDGE_COOLDOWNS
            assert isinstance(tick.NUDGE_COOLDOWNS[nudge_type], (int, float))
