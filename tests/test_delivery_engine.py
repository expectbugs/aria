"""Tests for delivery_engine.py — smart delivery routing.

SAFETY: All external I/O mocked at module level.
"""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import delivery_engine
from delivery_engine import (
    evaluate, get_user_state, DeliveryDecision, UserState,
    queue_deferred, get_pending_deferred, mark_deferred_delivered,
)


def _mock_location(location="Rapids Trail, Waukesha", speed=0.0, battery=85,
                   minutes_ago=5):
    ts = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "location": location, "speed_mps": speed, "battery_pct": battery,
        "timestamp": ts, "lat": 42.58, "lon": -88.43,
    }


def _mock_devices(phone=True, glasses=False, watch=False):
    """Return mock device_state rows."""
    now = datetime.now().isoformat()
    devices = {
        "phone": {"device": "phone", "connected": phone, "last_seen": now,
                  "battery_pct": 85, "capabilities": {}},
        "glasses": {"device": "glasses", "connected": glasses, "last_seen": now,
                    "battery_pct": None, "capabilities": {}},
        "watch": {"device": "watch", "connected": watch, "last_seen": now,
                  "battery_pct": None, "capabilities": {}},
        "mic": {"device": "mic", "connected": False, "last_seen": now,
                "battery_pct": None, "capabilities": {}},
    }
    return devices


class TestGetUserState:
    @patch("delivery_engine._get_device_states", return_value=_mock_devices(phone=True))
    @patch("delivery_engine.location_store")
    @patch("delivery_engine.datetime")
    def test_home_available(self, mock_dt, mock_loc, mock_devs):
        # Pin to 2pm — outside quiet hours (0-7am) so activity is "available"
        mock_dt.now.return_value = datetime(2026, 3, 28, 14, 0)
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_loc.get_latest.return_value = _mock_location("Rapids Trail, Waukesha")
        state = get_user_state()
        assert state.activity == "available"
        assert "voice" in state.channels

    @patch("delivery_engine._get_device_states", return_value=_mock_devices(phone=True))
    @patch("delivery_engine.location_store")
    def test_driving_detected(self, mock_loc, mock_devs):
        mock_loc.get_latest.return_value = _mock_location(speed=15.0)
        state = get_user_state()
        assert state.activity == "driving"

    @patch("delivery_engine._get_device_states", return_value=_mock_devices(phone=False))
    @patch("delivery_engine.location_store")
    def test_phone_disconnected_sms_only(self, mock_loc, mock_devs):
        mock_loc.get_latest.return_value = None
        state = get_user_state()
        assert "sms" in state.channels
        assert "voice" not in state.channels

    @patch("delivery_engine._get_device_states",
           return_value=_mock_devices(phone=True, glasses=True))
    @patch("delivery_engine.location_store")
    def test_glasses_connected_adds_channel(self, mock_loc, mock_devs):
        mock_loc.get_latest.return_value = _mock_location()
        state = get_user_state()
        assert "glasses" in state.channels

    @patch("delivery_engine._get_device_states", return_value=_mock_devices(phone=True))
    @patch("delivery_engine.location_store")
    def test_stale_location(self, mock_loc, mock_devs):
        mock_loc.get_latest.return_value = _mock_location(minutes_ago=60)
        state = get_user_state()
        assert state.location_fresh is False


class TestEvaluate:
    # --- Proactive sources: respect activity overrides ---

    @patch("delivery_engine.get_user_state")
    def test_sleeping_defers_proactive(self, mock_state):
        mock_state.return_value = UserState(
            location="home", activity="sleeping",
            channels=["sms"], battery=85, location_fresh=True,
        )
        d = evaluate("timer", "normal", "timer")
        assert d.method == "defer"

    @patch("delivery_engine.get_user_state")
    def test_sleeping_urgent_image_proactive(self, mock_state):
        mock_state.return_value = UserState(
            location="home", activity="sleeping",
            channels=["sms", "image"], battery=85, location_fresh=True,
        )
        d = evaluate("timer", "urgent", "timer")
        assert d.method == "image"

    @patch("delivery_engine.get_user_state")
    def test_working_blocks_voice_proactive(self, mock_state):
        mock_state.return_value = UserState(
            location="work", activity="working",
            channels=["sms", "image"], battery=85, location_fresh=True,
        )
        d = evaluate("nudge", "normal", "nudge", hint="voice")
        assert d.method == "image"  # voice blocked at work for proactive

    @patch("delivery_engine.get_user_state")
    def test_working_uses_glasses_proactive(self, mock_state):
        mock_state.return_value = UserState(
            location="work", activity="working",
            channels=["sms", "image", "glasses"], battery=85, location_fresh=True,
        )
        d = evaluate("nudge", "normal", "nudge")
        assert d.method == "glasses"

    @patch("delivery_engine.get_user_state")
    def test_driving_forces_voice_proactive(self, mock_state):
        mock_state.return_value = UserState(
            location="driving", activity="driving",
            channels=["voice", "sms", "image"], battery=85, location_fresh=True,
        )
        d = evaluate("timer", "normal", "timer")
        assert d.method == "voice"

    @patch("delivery_engine.get_user_state")
    def test_exercising_voice_proactive(self, mock_state):
        mock_state.return_value = UserState(
            location="home", activity="exercising",
            channels=["voice", "sms", "image"], battery=85, location_fresh=True,
        )
        d = evaluate("nudge", "normal", "nudge")
        assert d.method == "voice"

    @patch("delivery_engine.get_user_state")
    def test_court_defers_proactive(self, mock_state):
        mock_state.return_value = UserState(
            location="court", activity="available",
            channels=["image"], battery=85, location_fresh=True,
        )
        d = evaluate("nudge", "normal", "nudge")
        assert d.method == "defer"

    @patch("delivery_engine.get_user_state")
    def test_court_urgent_image_proactive(self, mock_state):
        mock_state.return_value = UserState(
            location="court", activity="available",
            channels=["image"], battery=85, location_fresh=True,
        )
        d = evaluate("timer", "urgent", "timer")
        assert d.method == "image"

    # --- User-initiated sources: never defer ---

    @patch("delivery_engine.get_user_state")
    def test_sleeping_voice_request_not_deferred(self, mock_state):
        """User sends voice request during quiet hours — respond, don't defer."""
        mock_state.return_value = UserState(
            location="home", activity="sleeping",
            channels=["voice", "sms"], battery=85, location_fresh=True,
        )
        d = evaluate("response", "normal", "voice")
        assert d.method == "voice"

    @patch("delivery_engine.get_user_state")
    def test_sleeping_file_request_not_deferred(self, mock_state):
        """User sends photo during quiet hours — respond, don't defer."""
        mock_state.return_value = UserState(
            location="home", activity="sleeping",
            channels=["voice", "sms"], battery=85, location_fresh=True,
        )
        d = evaluate("response", "normal", "file")
        assert d.method == "voice"

    @patch("delivery_engine.get_user_state")
    def test_sleeping_sms_request_not_deferred(self, mock_state):
        mock_state.return_value = UserState(
            location="home", activity="sleeping",
            channels=["sms"], battery=85, location_fresh=True,
        )
        d = evaluate("response", "normal", "sms")
        assert d.method == "sms"

    @patch("delivery_engine.get_user_state")
    def test_court_voice_request_not_deferred(self, mock_state):
        mock_state.return_value = UserState(
            location="court", activity="available",
            channels=["voice", "image"], battery=85, location_fresh=True,
        )
        d = evaluate("response", "normal", "voice")
        assert d.method == "voice"

    # --- General routing (activity=available) ---

    @patch("delivery_engine.get_user_state")
    def test_available_respects_sms_hint(self, mock_state):
        mock_state.return_value = UserState(
            location="home", activity="available",
            channels=["voice", "sms", "image"], battery=85, location_fresh=True,
        )
        d = evaluate("response", "normal", "voice", hint="sms")
        assert d.method == "sms"

    @patch("delivery_engine.get_user_state")
    def test_engine_disabled_returns_default(self, mock_state):
        with patch.object(delivery_engine.config, "DELIVERY_ENGINE_ENABLED",
                          False, create=True):
            d = evaluate("response", "normal", "voice")
            assert d.method == "voice"

    @patch("delivery_engine.get_user_state")
    def test_sms_source_defaults_to_sms(self, mock_state):
        mock_state.return_value = UserState(
            location="home", activity="available",
            channels=["voice", "sms", "image"], battery=85, location_fresh=True,
        )
        d = evaluate("response", "normal", "sms")
        assert d.method == "sms"
