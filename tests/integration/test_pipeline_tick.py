"""Tick pipeline tests against a real PostgreSQL database.

Tests process_timers(), check_location_reminders(), process_exercise_tick(),
process_fitbit_poll(), and job isolation from tick.py with real DB state.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
Mocks: sms module, httpx (daemon calls), push_audio.
"""

import json
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from freezegun import freeze_time

import tick
import timer_store
import calendar_store
import fitbit_store
import location_store
import db

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_legal, seed_vehicle, seed_request_log, seed_nudge_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_sms():
    """Mock SMS delivery for all tick tests."""
    with patch("tick.sms") as mock:
        mock.send_to_owner = MagicMock()
        mock.send_long_to_owner = MagicMock()
        yield mock


# ---------------------------------------------------------------------------
# process_timers()
# ---------------------------------------------------------------------------

class TestProcessTimers:
    def test_due_timer_fires(self, _mock_sms):
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        seed_timer(label="Take pills", fire_at=past, message="Time for meds")
        with patch("tick.is_quiet_hours", return_value=False):
            tick.process_timers()
        _mock_sms.send_to_owner.assert_called_once_with("Time for meds")

    def test_future_timer_does_not_fire(self, _mock_sms):
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        seed_timer(label="Later", fire_at=future, message="Not yet")
        with patch("tick.is_quiet_hours", return_value=False):
            tick.process_timers()
        _mock_sms.send_to_owner.assert_not_called()

    def test_multiple_due_timers_all_fire(self, _mock_sms):
        past1 = (datetime.now() - timedelta(minutes=10)).isoformat()
        past2 = (datetime.now() - timedelta(minutes=5)).isoformat()
        seed_timer(label="Timer A", fire_at=past1, message="A fired")
        seed_timer(label="Timer B", fire_at=past2, message="B fired")
        with patch("tick.is_quiet_hours", return_value=False):
            tick.process_timers()
        assert _mock_sms.send_to_owner.call_count == 2

    def test_urgent_bypasses_quiet_hours(self, _mock_sms):
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        seed_timer(label="Urgent!", fire_at=past, priority="urgent",
                   message="Wake up!")
        with patch("tick.is_quiet_hours", return_value=True):
            tick.process_timers()
        _mock_sms.send_to_owner.assert_called_once_with("Wake up!")

    def test_gentle_deferred_in_quiet_hours(self, _mock_sms):
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        seed_timer(label="Gentle", fire_at=past, priority="gentle",
                   message="Soft nudge")
        with patch("tick.is_quiet_hours", return_value=True):
            tick.process_timers()
        _mock_sms.send_to_owner.assert_not_called()
        # Timer should still be pending (not completed)
        active = timer_store.get_active()
        assert len(active) == 1

    def test_timer_marked_complete_before_delivery(self, _mock_sms):
        """Timer should be marked complete BEFORE SMS delivery (C8)."""
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        t = seed_timer(label="C8 Timer", fire_at=past, message="check")

        completion_order = []

        def capture_send(msg):
            # At the point of sending, timer should already be complete
            timer = timer_store.get_timer(t["id"])
            completion_order.append(("send", timer["status"]))

        _mock_sms.send_to_owner.side_effect = capture_send

        with patch("tick.is_quiet_hours", return_value=False):
            tick.process_timers()

        assert completion_order == [("send", "fired")]

    def test_voice_timer_with_tts(self, _mock_sms):
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        seed_timer(label="Voice Timer", fire_at=past, delivery="voice",
                   message="Voice message")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_wav_data"

        with patch("tick.is_quiet_hours", return_value=False), \
             patch("httpx.post", return_value=mock_resp), \
             patch("push_audio.push_audio", return_value=True):
            tick.process_timers()

        # SMS should NOT have been called (voice succeeded)
        _mock_sms.send_to_owner.assert_not_called()

    def test_voice_failure_falls_back_to_sms(self, _mock_sms):
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        seed_timer(label="Voice Fallback", fire_at=past, delivery="voice",
                   message="Fallback msg")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake_wav_data"

        with patch("tick.is_quiet_hours", return_value=False), \
             patch("httpx.post", return_value=mock_resp), \
             patch("push_audio.push_audio", return_value=False):
            tick.process_timers()

        # SMS fallback should have been called
        _mock_sms.send_to_owner.assert_called_once_with("Fallback msg")


# ---------------------------------------------------------------------------
# check_location_reminders()
# ---------------------------------------------------------------------------

class TestCheckLocationReminders:
    def test_arrive_trigger_fires_at_matching_location(self, _mock_sms):
        seed_location(location_name="Walmart, Waukesha")
        seed_reminder(text="Buy milk", location="walmart", location_trigger="arrive")
        with patch("tick.is_quiet_hours", return_value=False), \
             patch.object(tick.config, "KNOWN_PLACES", {}, create=True):
            tick.check_location_reminders()
        assert _mock_sms.send_to_owner.call_count == 1
        assert "Buy milk" in _mock_sms.send_to_owner.call_args[0][0]

    def test_arrive_no_fire_at_wrong_location(self, _mock_sms):
        seed_location(location_name="Home, Milwaukee")
        seed_reminder(text="Buy tires", location="walmart", location_trigger="arrive")
        with patch("tick.is_quiet_hours", return_value=False), \
             patch.object(tick.config, "KNOWN_PLACES", {}, create=True):
            tick.check_location_reminders()
        _mock_sms.send_to_owner.assert_not_called()

    def test_leave_trigger_fires_on_departure(self, _mock_sms):
        """Leave trigger fires when user was at location and leaves."""
        reminder = seed_reminder(text="Check stove", location="home",
                                 location_trigger="leave")

        # First, simulate being at home to set presence state
        seed_location(location_name="Home, Milwaukee")
        with patch("tick.is_quiet_hours", return_value=False), \
             patch.object(tick.config, "KNOWN_PLACES", {"home": "home"}, create=True):
            tick.check_location_reminders()

        # Now move away
        with db.get_conn() as conn:
            conn.execute("TRUNCATE locations CASCADE")
        seed_location(location_name="Walmart, Waukesha")
        with patch("tick.is_quiet_hours", return_value=False), \
             patch.object(tick.config, "KNOWN_PLACES", {"home": "home"}, create=True):
            tick.check_location_reminders()

        assert _mock_sms.send_to_owner.call_count == 1
        assert "Check stove" in _mock_sms.send_to_owner.call_args[0][0]

    def test_leave_needs_prior_presence_state(self, _mock_sms):
        """Leave trigger does NOT fire if user was never detected at the location."""
        seed_location(location_name="Walmart, Waukesha")
        seed_reminder(text="Check stove", location="home",
                      location_trigger="leave")
        with patch("tick.is_quiet_hours", return_value=False), \
             patch.object(tick.config, "KNOWN_PLACES", {"home": "home"}, create=True):
            tick.check_location_reminders()
        _mock_sms.send_to_owner.assert_not_called()

    def test_quiet_hours_skips_not_completes(self, _mock_sms):
        """During quiet hours, location reminders are skipped but NOT completed."""
        seed_location(location_name="Walmart, Waukesha")
        seed_reminder(text="Buy milk", location="walmart", location_trigger="arrive")
        with patch("tick.is_quiet_hours", return_value=True), \
             patch.object(tick.config, "KNOWN_PLACES", {}, create=True):
            tick.check_location_reminders()
        _mock_sms.send_to_owner.assert_not_called()
        # Reminder should still be active
        reminders = calendar_store.get_reminders()
        assert len(reminders) == 1
        assert not reminders[0]["done"]

    def test_presence_state_tracked_in_tick_state(self, _mock_sms):
        """Presence state for leave detection is persisted in tick_state."""
        reminder = seed_reminder(text="Leave check", location="home",
                                 location_trigger="leave")
        seed_location(location_name="Home, Milwaukee")
        with patch("tick.is_quiet_hours", return_value=False), \
             patch.object(tick.config, "KNOWN_PLACES", {"home": "home"}, create=True):
            tick.check_location_reminders()

        state = tick.load_state()
        key = f"loc_reminder:{reminder['id']}"
        assert state.get(key) == "at"


# ---------------------------------------------------------------------------
# process_exercise_tick()
# ---------------------------------------------------------------------------

class TestProcessExerciseTick:
    def test_no_active_session_returns(self, _mock_sms):
        """When no exercise is active, should return immediately."""
        with patch("httpx.post") as mock_post:
            tick.process_exercise_tick()
        mock_post.assert_not_called()

    def test_active_session_fetches_hr(self, _mock_sms):
        with patch("fitbit_store.config.OWNER_BIRTH_DATE", "1990-01-01"):
            fitbit_store.start_exercise("running")

        hr_resp = MagicMock()
        hr_resp.status_code = 200
        hr_resp.json.return_value = {"readings": [{"time": "10:00:00", "value": 130}]}

        with patch("httpx.post", return_value=hr_resp):
            tick.process_exercise_tick()

        # HR should have been recorded
        state = fitbit_store.get_exercise_state()
        assert state is not None
        assert len(state.get("hr_readings", [])) >= 1

    def test_rate_limiting_c9(self, _mock_sms):
        """C9: If avg interval < 3 minutes and nudge_count > 0, skip."""
        with patch("fitbit_store.config.OWNER_BIRTH_DATE", "1990-01-01"):
            fitbit_store.start_exercise("running")

        # Set started_at to 5 minutes ago and nudge_count to 10
        # avg_interval = 5/10 = 0.5 < 3 => rate limited
        five_min_ago = (datetime.now() - timedelta(minutes=5)).isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE fitbit_exercise SET nudge_count = 10, started_at = %s "
                "WHERE active = TRUE",
                (five_min_ago,),
            )

        with patch("httpx.post") as mock_post:
            tick.process_exercise_tick()

        # Should not have fetched HR due to rate limiting
        # (elapsed_min=5, nudge_count=10, avg_interval=0.5 < 3)
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Job isolation
# ---------------------------------------------------------------------------

class TestJobIsolation:
    def test_one_job_failure_doesnt_block_others(self, _mock_sms):
        """Each tick job is isolated — one failure doesn't block the rest."""
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        seed_timer(label="Isolated", fire_at=past, message="Should fire")

        with patch("tick.is_quiet_hours", return_value=False), \
             patch("tick.check_location_reminders", side_effect=Exception("boom")), \
             patch("tick.process_exercise_tick", side_effect=Exception("crash")), \
             patch("tick.process_fitbit_poll", side_effect=Exception("fail")), \
             patch.object(tick.config, "NUDGE_INTERVAL_MIN", 999):
            tick.main()

        # Timer should still have fired despite other jobs failing
        _mock_sms.send_to_owner.assert_called_once_with("Should fire")


# ---------------------------------------------------------------------------
# Fitbit poll
# ---------------------------------------------------------------------------

class TestFitbitPoll:
    def test_respects_15_min_interval(self, _mock_sms):
        """Fitbit poll should skip if less than 15 minutes since last sync."""
        recent = datetime.now().isoformat()
        tick.save_state({"last_fitbit_sync": recent})

        with patch("tick.is_quiet_hours", return_value=False), \
             patch("tick.fetch_fitbit_snapshot") as mock_fetch:
            tick.process_fitbit_poll()

        mock_fetch.assert_not_called()

    def test_first_run_triggers(self, _mock_sms):
        """First run with no state should trigger a sync."""
        with patch("tick.is_quiet_hours", return_value=False), \
             patch("tick.fetch_fitbit_snapshot") as mock_fetch:
            tick.process_fitbit_poll()

        mock_fetch.assert_called_once()

    def test_quiet_hours_skip(self, _mock_sms):
        """Fitbit poll should skip during quiet hours."""
        with patch("tick.is_quiet_hours", return_value=True), \
             patch("tick.fetch_fitbit_snapshot") as mock_fetch:
            tick.process_fitbit_poll()

        mock_fetch.assert_not_called()


# ===========================================================================
# Total: 22 tests
# ===========================================================================
