"""Tests for tick.py — cron job: timers, nudges, location reminders, Fitbit.

SAFETY: All SMS sending, HTTP requests, and database access are mocked.
No real timers fire, no real nudges are sent.
"""

from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock, call

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
        # C8: Timer is marked complete BEFORE delivery attempt
        mock_complete.assert_called_once_with("t1")

    @patch("tick.sms.send_to_owner")
    @patch("tick.timer_store.complete_timer")
    @patch("tick.is_quiet_hours", return_value=False)
    def test_complete_before_delivery(self, mock_quiet, mock_complete, mock_send):
        """C8: Timer is marked complete before delivery attempt."""
        call_order = []
        mock_complete.side_effect = lambda *a: call_order.append("complete")
        mock_send.side_effect = lambda *a: call_order.append("send")
        timer = {
            "id": "t1", "delivery": "sms", "message": "Test",
            "label": "Test", "priority": "gentle",
        }
        tick.fire_timer(timer)
        assert call_order == ["complete", "send"]

    @patch("tick.sms.send_to_owner", side_effect=Exception("SMS failed"))
    @patch("tick.timer_store.complete_timer")
    @patch("tick.is_quiet_hours", return_value=False)
    def test_delivery_failure_still_completes_timer(self, mock_quiet, mock_complete, mock_send):
        """C8: Even if delivery fails, timer stays complete (no retry storm)."""
        timer = {
            "id": "t1", "delivery": "sms", "message": "Test",
            "label": "Test", "priority": "gentle",
        }
        result = tick.fire_timer(timer)
        assert result is True
        mock_complete.assert_called_once_with("t1")


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

    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    @patch("tick.sms.send_to_owner", side_effect=Exception("SMS failed"))
    @patch("tick.calendar_store.complete_reminder")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.location_store.get_latest")
    @patch("tick.is_quiet_hours", return_value=False)
    def test_complete_before_delivery(self, mock_quiet, mock_loc, mock_reminders,
                                       mock_complete, mock_send, mock_load, mock_save):
        """C8: Reminder is completed before delivery attempt."""
        mock_loc.return_value = {
            "location": "3549 rapids trail, waukesha, wisconsin",
        }
        mock_reminders.return_value = [
            {"id": "r1", "text": "Check mail", "location": "home",
             "location_trigger": "arrive", "done": False},
        ]
        tick.check_location_reminders()
        # Reminder should be completed even though SMS failed
        mock_complete.assert_called_once_with("r1")


# ---------------------------------------------------------------------------
# Nudge evaluation
# ---------------------------------------------------------------------------

def _base_evaluate_mocks():
    """Return a dict of all evaluate_nudges mock targets with safe defaults."""
    return {
        "tick.calendar_store.auto_expire_stale_reminders": [],
        "tick.calendar_store.get_events": [],
        "tick.calendar_store.get_reminders": [],
        "tick.nutrition_store.get_items": [],
        "tick.health_store.get_patterns": [],
        "tick.legal_store.get_upcoming_dates": [],
        "tick.location_store.get_latest": None,
        "tick.fitbit_store.get_sleep_summary": None,
        "tick.fitbit_store.get_heart_summary": None,
        "tick.fitbit_store.get_activity_summary": None,
        "tick.nutrition_store.get_daily_totals": {"item_count": 0},
        "tick.nutrition_store.get_net_calories": {"consumed": 0, "burned": 0, "net": 0},
    }


class TestEvaluateNudges:
    @patch("tick.nutrition_store.get_daily_totals")
    @patch("tick.nutrition_store.get_net_calories")
    @patch("tick.fitbit_store.get_activity_summary")
    @patch("tick.fitbit_store.get_heart_summary")
    @patch("tick.fitbit_store.get_sleep_summary")
    @patch("tick.location_store.get_latest")
    @patch("tick.legal_store.get_upcoming_dates")
    @patch("tick.health_store.get_patterns")
    @patch("tick.nutrition_store.get_items")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    @patch("tick.calendar_store.auto_expire_stale_reminders", return_value=[])
    def test_meal_gap_detection(self, mock_expire, mock_events, mock_reminders,
                                 mock_items, mock_patterns, mock_legal,
                                 mock_loc, mock_sleep, mock_hr,
                                 mock_activity, mock_net_cal, mock_totals):
        mock_events.return_value = []
        mock_reminders.return_value = []
        mock_items.return_value = []  # C7: no nutrition items today
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
    @patch("tick.nutrition_store.get_items")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    @patch("tick.calendar_store.auto_expire_stale_reminders", return_value=[])
    def test_overdue_reminder(self, mock_expire, mock_events, mock_reminders,
                               mock_items, mock_patterns, mock_legal,
                               mock_loc, mock_sleep, mock_hr,
                               mock_activity, mock_net_cal, mock_totals):
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        mock_events.return_value = []
        mock_reminders.return_value = [
            {"id": "r1", "text": "Pay bill", "due": yesterday, "done": False},
        ]
        mock_items.return_value = []
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
    @patch("tick.nutrition_store.get_items")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    @patch("tick.calendar_store.auto_expire_stale_reminders", return_value=[])
    def test_battery_low(self, mock_expire, mock_events, mock_reminders,
                          mock_items, mock_patterns, mock_legal,
                          mock_loc, mock_sleep, mock_hr,
                          mock_activity, mock_net_cal, mock_totals):
        mock_events.return_value = []
        mock_reminders.return_value = []
        mock_items.return_value = []
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
    @patch("tick.nutrition_store.get_items")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    @patch("tick.calendar_store.auto_expire_stale_reminders", return_value=[])
    def test_sugar_warning(self, mock_expire, mock_events, mock_reminders,
                            mock_items, mock_patterns, mock_legal,
                            mock_loc, mock_sleep, mock_hr,
                            mock_activity, mock_net_cal, mock_totals):
        mock_events.return_value = []
        mock_reminders.return_value = []
        mock_items.return_value = []
        mock_patterns.return_value = []
        mock_legal.return_value = []
        mock_loc.return_value = None
        mock_sleep.return_value = None
        mock_hr.return_value = None
        mock_activity.return_value = None
        mock_net_cal.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_totals.return_value = {
            "item_count": 3, "added_sugars_g": 30, "sodium_mg": 1000,
        }

        triggers = tick.evaluate_nudges()
        sugar_triggers = [t for t in triggers if t[0] == "nutrition_sugar_warn"]
        assert len(sugar_triggers) > 0

    @patch("tick.nutrition_store.get_daily_totals")
    @patch("tick.nutrition_store.get_net_calories")
    @patch("tick.fitbit_store.get_activity_summary")
    @patch("tick.fitbit_store.get_heart_summary")
    @patch("tick.fitbit_store.get_sleep_summary")
    @patch("tick.location_store.get_latest")
    @patch("tick.legal_store.get_upcoming_dates")
    @patch("tick.health_store.get_patterns")
    @patch("tick.nutrition_store.get_items")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    @patch("tick.calendar_store.auto_expire_stale_reminders")
    def test_zombie_auto_expiry_called(self, mock_expire, mock_events, mock_reminders,
                                        mock_items, mock_patterns, mock_legal,
                                        mock_loc, mock_sleep, mock_hr,
                                        mock_activity, mock_net_cal, mock_totals):
        """C1: auto_expire_stale_reminders is called at start of evaluate_nudges."""
        mock_expire.return_value = [
            {"id": "r1", "text": "Call movers", "due": "2026-03-20"},
        ]
        mock_events.return_value = []
        mock_reminders.return_value = []
        mock_items.return_value = []
        mock_patterns.return_value = []
        mock_legal.return_value = []
        mock_loc.return_value = None
        mock_sleep.return_value = None
        mock_hr.return_value = None
        mock_activity.return_value = None
        mock_totals.return_value = {"item_count": 0}

        tick.evaluate_nudges()
        mock_expire.assert_called_once()

    @patch("tick.nutrition_store.get_daily_totals")
    @patch("tick.nutrition_store.get_net_calories")
    @patch("tick.fitbit_store.get_activity_summary")
    @patch("tick.fitbit_store.get_heart_summary")
    @patch("tick.fitbit_store.get_sleep_summary")
    @patch("tick.location_store.get_latest")
    @patch("tick.legal_store.get_upcoming_dates")
    @patch("tick.health_store.get_patterns")
    @patch("tick.nutrition_store.get_items")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    @patch("tick.calendar_store.auto_expire_stale_reminders", return_value=[])
    def test_time_context_in_triggers(self, mock_expire, mock_events, mock_reminders,
                                       mock_items, mock_patterns, mock_legal,
                                       mock_loc, mock_sleep, mock_hr,
                                       mock_activity, mock_net_cal, mock_totals):
        """C3: Time-sensitive triggers include current time context."""
        mock_events.return_value = []
        mock_reminders.return_value = [
            {"id": "r1", "text": "Pay bill", "due": "2026-03-19", "done": False},
        ]
        mock_items.return_value = []
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
        # C3: description should contain time context
        assert "current time:" in reminder_triggers[0][1]

    @patch("tick.nutrition_store.get_daily_totals")
    @patch("tick.nutrition_store.get_net_calories")
    @patch("tick.fitbit_store.get_activity_summary")
    @patch("tick.fitbit_store.get_heart_summary")
    @patch("tick.fitbit_store.get_sleep_summary")
    @patch("tick.location_store.get_latest")
    @patch("tick.legal_store.get_upcoming_dates")
    @patch("tick.health_store.get_patterns")
    @patch("tick.nutrition_store.get_items")
    @patch("tick.calendar_store.get_reminders")
    @patch("tick.calendar_store.get_events")
    @patch("tick.calendar_store.auto_expire_stale_reminders", return_value=[])
    def test_meal_gap_uses_nutrition_store(self, mock_expire, mock_events, mock_reminders,
                                            mock_items, mock_patterns, mock_legal,
                                            mock_loc, mock_sleep, mock_hr,
                                            mock_activity, mock_net_cal, mock_totals):
        """C7: Meal gap check uses nutrition_store.get_items, not health_store."""
        mock_events.return_value = []
        mock_reminders.return_value = []
        # Nutrition items exist, but old enough to trigger gap
        # Must use same date as the mocked datetime (2026-03-20)
        old_time = datetime(2026, 3, 20, 8, 0).isoformat()
        mock_items.return_value = [
            {"date": "2026-03-20", "created": old_time, "food_name": "oatmeal"},
        ]
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
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            triggers = tick.evaluate_nudges()

        # nutrition_store.get_items was called (not health_store.get_entries)
        mock_items.assert_called()
        meal_triggers = [t for t in triggers if t[0] == "meal_reminder"]
        assert len(meal_triggers) > 0


class TestRunNudgeEvaluation:
    @patch("tick._log_nudge")
    @patch("tick.save_cooldowns")
    @patch("tick.sms.send_long_to_owner")
    @patch("tick._get_nudge_counts", return_value=(0, 0))
    @patch("tick.evaluate_nudges")
    @patch("tick.load_cooldowns", return_value={})
    @patch("tick.is_quiet_hours", return_value=False)
    def test_sends_nudge_sms(self, mock_quiet, mock_cooldowns,
                              mock_nudges, mock_counts, mock_send,
                              mock_save_cd, mock_log_nudge):
        mock_nudges.return_value = [("meal_reminder", "No meals today")]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "Hey, grab some food!"}

        with patch("httpx.post", return_value=mock_resp):
            tick.run_nudge_evaluation()
        mock_send.assert_called_once_with("Hey, grab some food!")
        mock_save_cd.assert_called_once()
        # C5: Logged as "sent"
        mock_log_nudge.assert_called_once()
        assert mock_log_nudge.call_args[0][3] == "sent"

    @patch("tick.is_quiet_hours", return_value=True)
    def test_skips_during_quiet_hours(self, mock_quiet):
        tick.run_nudge_evaluation()  # should return immediately

    @patch("tick._log_nudge")
    @patch("tick.save_cooldowns")
    @patch("tick.sms.send_long_to_owner")
    @patch("tick._get_nudge_counts", return_value=(0, 0))
    @patch("tick.evaluate_nudges")
    @patch("tick.load_cooldowns", return_value={})
    @patch("tick.is_quiet_hours", return_value=False)
    def test_compose_failure_no_cooldown_update(self, mock_quiet, mock_cooldowns,
                                                 mock_nudges, mock_counts,
                                                 mock_send, mock_save_cd,
                                                 mock_log_nudge):
        """C5: On compose failure, do NOT update cooldowns."""
        mock_nudges.return_value = [("meal_reminder", "No meals today")]
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("httpx.post", return_value=mock_resp):
            tick.run_nudge_evaluation()
        mock_send.assert_not_called()
        mock_save_cd.assert_not_called()
        mock_log_nudge.assert_called_once()
        assert mock_log_nudge.call_args[0][3] == "compose_failed"

    @patch("tick._log_nudge")
    @patch("tick.save_cooldowns")
    @patch("tick.sms.send_long_to_owner", side_effect=Exception("SMS failed"))
    @patch("tick._get_nudge_counts", return_value=(0, 0))
    @patch("tick.evaluate_nudges")
    @patch("tick.load_cooldowns", return_value={})
    @patch("tick.is_quiet_hours", return_value=False)
    def test_delivery_failure_no_cooldown_update(self, mock_quiet, mock_cooldowns,
                                                   mock_nudges, mock_counts,
                                                   mock_send, mock_save_cd,
                                                   mock_log_nudge):
        """C5: On delivery failure, do NOT update cooldowns."""
        mock_nudges.return_value = [("meal_reminder", "No meals today")]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "Hey, eat something!"}

        with patch("httpx.post", return_value=mock_resp):
            tick.run_nudge_evaluation()
        mock_send.assert_called_once()
        mock_save_cd.assert_not_called()
        mock_log_nudge.assert_called_once()
        assert mock_log_nudge.call_args[0][3] == "delivery_failed"

    @patch("tick._log_nudge")
    @patch("tick.save_cooldowns")
    @patch("tick.sms.send_long_to_owner")
    @patch("tick._get_nudge_counts", return_value=(6, 0))
    @patch("tick.evaluate_nudges")
    @patch("tick.load_cooldowns", return_value={})
    @patch("tick.is_quiet_hours", return_value=False)
    def test_daily_cap_suppresses_nudge(self, mock_quiet, mock_cooldowns,
                                         mock_nudges, mock_counts,
                                         mock_send, mock_save_cd,
                                         mock_log_nudge):
        """C4: Daily nudge cap prevents sending."""
        mock_nudges.return_value = [("meal_reminder", "No meals today")]

        with patch("httpx.post") as mock_httpx:
            tick.run_nudge_evaluation()
        mock_httpx.assert_not_called()
        mock_send.assert_not_called()
        mock_save_cd.assert_not_called()
        mock_log_nudge.assert_called_once()
        assert mock_log_nudge.call_args[0][3] == "suppressed_daily_cap"

    @patch("tick._log_nudge")
    @patch("tick.save_cooldowns")
    @patch("tick.sms.send_long_to_owner")
    @patch("tick._get_nudge_counts", return_value=(0, 2))
    @patch("tick.evaluate_nudges")
    @patch("tick.load_cooldowns", return_value={})
    @patch("tick.is_quiet_hours", return_value=False)
    def test_hourly_cap_suppresses_nudge(self, mock_quiet, mock_cooldowns,
                                          mock_nudges, mock_counts,
                                          mock_send, mock_save_cd,
                                          mock_log_nudge):
        """C4: Hourly nudge cap prevents sending."""
        mock_nudges.return_value = [("meal_reminder", "No meals today")]

        with patch("httpx.post") as mock_httpx:
            tick.run_nudge_evaluation()
        mock_httpx.assert_not_called()
        mock_send.assert_not_called()
        mock_log_nudge.assert_called_once()
        assert mock_log_nudge.call_args[0][3] == "suppressed_hourly_cap"

    @patch("tick._log_nudge")
    @patch("tick.save_cooldowns")
    @patch("tick.sms.send_long_to_owner")
    @patch("tick._get_nudge_counts", return_value=(0, 0))
    @patch("tick.evaluate_nudges")
    @patch("tick.load_cooldowns", return_value={})
    @patch("tick.is_quiet_hours", return_value=False)
    def test_success_updates_cooldowns(self, mock_quiet, mock_cooldowns,
                                        mock_nudges, mock_counts,
                                        mock_send, mock_save_cd,
                                        mock_log_nudge):
        """C5: On success, cooldowns ARE updated."""
        mock_nudges.return_value = [("meal_reminder", "No meals today")]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "Time to eat!"}

        with patch("httpx.post", return_value=mock_resp):
            tick.run_nudge_evaluation()
        mock_send.assert_called_once()
        mock_save_cd.assert_called_once()


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

    @patch("tick.db.get_conn")
    @patch("tick.send_exercise_nudge")
    @patch("tick.fitbit_store.record_exercise_hr")
    @patch("tick.fetch_exercise_hr")
    @patch("tick.fitbit_store.get_exercise_state")
    def test_rate_limiting_skips_when_too_frequent(self, mock_state, mock_fetch_hr,
                                                     mock_record, mock_nudge, mock_conn):
        """C9: Exercise nudges rate limited to at least 3-minute intervals."""
        mc = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        # 5 minutes in, 5 nudges already sent = avg 1 min/nudge (< 3 min threshold)
        started = (datetime.now() - timedelta(minutes=5)).isoformat()
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
            "nudge_count": 5,
        }
        mock_fetch_hr.return_value = {
            "readings": [{"time": "14:30:00", "value": 135}],
        }

        tick.process_exercise_tick()
        # HR should not even be fetched because rate limit kicks in first
        mock_fetch_hr.assert_not_called()


# ---------------------------------------------------------------------------
# Main tick
# ---------------------------------------------------------------------------

class TestMainTick:
    @patch("tick.run_nudge_evaluation")
    @patch("tick.db.get_transaction")
    @patch("tick.process_fitbit_poll")
    @patch("tick.process_exercise_tick")
    @patch("tick.check_location_reminders")
    @patch("tick.process_timers")
    def test_runs_all_jobs(self, mock_timers, mock_loc, mock_exercise,
                           mock_fitbit, mock_txn, mock_nudge):
        # C6: Mock the advisory lock transaction
        mc = MagicMock()
        mock_txn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_txn.return_value.__exit__ = MagicMock(return_value=False)
        # Lock acquired, no previous nudge check
        mc.execute.return_value.fetchone.side_effect = [
            {"locked": True},  # pg_try_advisory_xact_lock
            None,  # no last_nudge_check row
        ]

        with patch("tick.config") as mock_cfg:
            mock_cfg.NUDGE_INTERVAL_MIN = 0
            tick.main()

        mock_timers.assert_called_once()
        mock_loc.assert_called_once()
        mock_exercise.assert_called_once()
        mock_fitbit.assert_called_once()
        mock_nudge.assert_called_once()

    @patch("tick.process_timers", side_effect=Exception("Timer crash"))
    @patch("tick.check_location_reminders")
    @patch("tick.process_exercise_tick")
    @patch("tick.process_fitbit_poll")
    @patch("tick.run_nudge_evaluation")
    @patch("tick.db.get_transaction")
    def test_job_isolation(self, mock_txn, mock_nudge,
                           mock_fitbit, mock_exercise, mock_loc, mock_timers):
        """One job failing should not prevent others from running."""
        mc = MagicMock()
        mock_txn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_txn.return_value.__exit__ = MagicMock(return_value=False)
        mc.execute.return_value.fetchone.side_effect = [
            {"locked": True},
            None,
        ]

        with patch("tick.config") as mock_cfg:
            mock_cfg.NUDGE_INTERVAL_MIN = 30
            tick.main()

        # Location reminders should still run despite timer crash
        mock_loc.assert_called_once()
        mock_exercise.assert_called_once()
        mock_fitbit.assert_called_once()

    @patch("tick.run_nudge_evaluation")
    @patch("tick.db.get_transaction")
    @patch("tick.process_fitbit_poll")
    @patch("tick.process_exercise_tick")
    @patch("tick.check_location_reminders")
    @patch("tick.process_timers")
    def test_advisory_lock_prevents_concurrent(self, mock_timers, mock_loc,
                                                 mock_exercise, mock_fitbit,
                                                 mock_txn, mock_nudge):
        """C6: If advisory lock is held by another instance, skip nudge eval."""
        mc = MagicMock()
        mock_txn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_txn.return_value.__exit__ = MagicMock(return_value=False)
        # Lock NOT acquired
        mc.execute.return_value.fetchone.return_value = {"locked": False}

        tick.main()
        mock_nudge.assert_not_called()

    @patch("tick.run_nudge_evaluation")
    @patch("tick.db.get_transaction")
    @patch("tick.process_fitbit_poll")
    @patch("tick.process_exercise_tick")
    @patch("tick.check_location_reminders")
    @patch("tick.process_timers")
    def test_interval_not_reached_skips_nudge(self, mock_timers, mock_loc,
                                                mock_exercise, mock_fitbit,
                                                mock_txn, mock_nudge):
        """C6: If nudge interval hasn't elapsed, skip nudge eval."""
        mc = MagicMock()
        mock_txn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_txn.return_value.__exit__ = MagicMock(return_value=False)
        # Lock acquired, but last check was recent
        mc.execute.return_value.fetchone.side_effect = [
            {"locked": True},
            {"value": datetime.now().isoformat()},  # just checked
        ]

        with patch("tick.config") as mock_cfg:
            mock_cfg.NUDGE_INTERVAL_MIN = 30
            tick.main()

        mock_nudge.assert_not_called()


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

    def test_reminder_due_cooldown_is_12h(self):
        """C2: reminder_due cooldown increased from 2 to 12 hours."""
        assert tick.NUDGE_COOLDOWNS["reminder_due"] == 12


# ---------------------------------------------------------------------------
# Nudge audit log
# ---------------------------------------------------------------------------

class TestNudgeAuditLog:
    @patch("tick.db.get_conn")
    def test_log_nudge_inserts_row(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        tick._log_nudge(["meal_reminder"], ["No meals"], "Eat something!", "sent")
        mc.execute.assert_called_once()
        sql = mc.execute.call_args[0][0]
        assert "INSERT INTO nudge_log" in sql

    @patch("tick.db.get_conn", side_effect=Exception("DB down"))
    def test_log_nudge_swallows_errors(self, mock_get_conn):
        """_log_nudge should not raise even if DB is down."""
        tick._log_nudge(["meal_reminder"], ["No meals"], "Eat!", "sent")
        # No exception raised
