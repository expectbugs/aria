"""Tests for context.py — context building functions.

SAFETY: All store lookups and external APIs are mocked.
"""

from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock, AsyncMock, call
import logging

import pytest

import context
import system_prompt
import daemon


# --- Helper: mock all Tier 1 stores to empty (used by tests that don't care about Tier 1) ---
def _patch_tier1_empty():
    """Return a dict of patches that make gather_always_context() return just datetime."""
    return {
        "context.timer_store.get_active": MagicMock(return_value=[]),
        "context.calendar_store.get_reminders": MagicMock(return_value=[]),
        "context.location_store.get_latest": MagicMock(return_value=None),
        "context.fitbit_store.get_exercise_state": MagicMock(return_value=None),
    }


class TestGatherAlwaysContext:
    """Test the Tier 1 always-inject context function."""

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_always_returns_datetime(self, mock_ts, mock_cal, mock_loc, mock_fs):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        assert "Current date and time:" in ctx
        # Should contain day of week and year
        now = datetime.now()
        assert str(now.year) in ctx

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_includes_active_timers(self, mock_ts, mock_cal, mock_loc, mock_fs):
        mock_ts.get_active.return_value = [
            {"id": "t1", "label": "Laundry", "fire_at": "2026-03-20T15:30:00",
             "delivery": "sms"},
        ]
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        assert "Laundry" in ctx
        assert "15:30" in ctx
        assert "t1" in ctx

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_includes_reminders(self, mock_ts, mock_cal, mock_loc, mock_fs):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = [
            {"id": "r1", "text": "Buy milk", "due": "2026-03-25"},
        ]
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        assert "Buy milk" in ctx
        assert "r1" in ctx
        assert "2026-03-25" in ctx

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_includes_location_and_battery(self, mock_ts, mock_cal, mock_loc, mock_fs):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = {
            "lat": 42.58, "lon": -88.43,
            "location": "Rapids Trail, Waukesha",
            "timestamp": "2026-03-20T14:00:00",
            "battery_pct": 85,
        }
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        assert "Rapids Trail" in ctx
        assert "85%" in ctx

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_includes_exercise_state(self, mock_ts, mock_cal, mock_loc, mock_fs):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = {"active": True}
        mock_fs.get_exercise_coaching_context.return_value = "EXERCISE MODE ACTIVE: stationary_bike"

        ctx = context.gather_always_context()
        assert "EXERCISE MODE ACTIVE" in ctx

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_no_exercise_when_inactive(self, mock_ts, mock_cal, mock_loc, mock_fs):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        assert "EXERCISE" not in ctx

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_compact_when_all_empty(self, mock_ts, mock_cal, mock_loc, mock_fs):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        # Only datetime line
        lines = [l for l in ctx.strip().split("\n") if l.strip()]
        assert len(lines) == 1
        assert "Current date and time:" in lines[0]

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_multiple_timers(self, mock_ts, mock_cal, mock_loc, mock_fs):
        mock_ts.get_active.return_value = [
            {"id": "t1", "label": "Laundry", "fire_at": "2026-03-20T15:30:00",
             "delivery": "sms"},
            {"id": "t2", "label": "Oven", "fire_at": "2026-03-20T16:00:00",
             "delivery": "voice"},
        ]
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        assert "Laundry" in ctx
        assert "Oven" in ctx

    @patch("context.redis_client")
    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_includes_task_status(self, mock_ts, mock_cal, mock_loc, mock_fs, mock_rc):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None
        mock_rc.get_active_tasks.return_value = [
            {"task_id": "t1", "description": "generating image",
             "progress": 45, "status": "running",
             "message": "upscaling", "eta_seconds": 120},
        ]
        mock_rc.format_task_status.return_value = "Background task [running]: generating image — 45%"

        ctx = context.gather_always_context()
        assert "Background task" in ctx
        assert "generating image" in ctx

    @patch("context.redis_client")
    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_works_without_active_tasks(self, mock_ts, mock_cal, mock_loc, mock_fs, mock_rc):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None
        mock_rc.get_active_tasks.return_value = []
        mock_rc.format_task_status.return_value = ""

        ctx = context.gather_always_context()
        assert "Background task" not in ctx
        assert "Current date and time:" in ctx

    @patch("context.redis_client")
    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_works_when_redis_unavailable(self, mock_ts, mock_cal, mock_loc, mock_fs, mock_rc):
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None
        mock_rc.get_active_tasks.return_value = []
        mock_rc.format_task_status.return_value = ""

        ctx = context.gather_always_context()
        assert "Current date and time:" in ctx  # still works


class TestBuildRequestContext:
    """Test keyword-triggered context injection."""

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.gather_health_context", return_value="")
    @patch("context.weather")
    async def test_weather_keywords(self, mock_weather, mock_hc, mock_always):
        mock_weather.get_current_conditions = AsyncMock(return_value={
            "description": "Sunny", "temperature_f": 55,
            "humidity": 40, "wind_mph": 10,
        })
        mock_weather.get_forecast = AsyncMock(return_value=[
            {"name": "Today", "temperature": 55, "unit": "F", "summary": "Sunny"},
        ])
        mock_weather.get_alerts = AsyncMock(return_value=[])

        for kw in ["weather", "temperature", "rain", "umbrella"]:
            ctx = await context.build_request_context(f"What's the {kw} like?")
            assert "55°F" in ctx or "Sunny" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.calendar_store")
    async def test_calendar_keywords_expand_range(self, mock_cal, mock_always):
        mock_cal.get_events.return_value = []

        await context.build_request_context("What's my schedule this week?")
        # Should query full week, not just today
        call_args = mock_cal.get_events.call_args
        assert call_args[1]["start"] is not None
        end = call_args[1]["end"]
        start = call_args[1]["start"]
        # end should be ~7 days after start
        start_date = datetime.strptime(start, "%Y-%m-%d")
        end_date = datetime.strptime(end, "%Y-%m-%d")
        assert (end_date - start_date).days == 7

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.calendar_store")
    async def test_default_calendar_today_only(self, mock_cal, mock_always):
        mock_cal.get_events.return_value = []

        await context.build_request_context("Hello how are you?")
        call_args = mock_cal.get_events.call_args
        start = call_args[1]["start"]
        end = call_args[1]["end"]
        assert start == end  # today only

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.vehicle_store")
    async def test_vehicle_keywords(self, mock_vs, mock_always):
        mock_vs.get_entries.return_value = [
            {"id": "v1", "date": "2026-03-15", "event_type": "oil_change",
             "description": "Synthetic", "mileage": 145000},
        ]
        mock_vs.get_latest_by_type.return_value = {}

        with patch("context.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            ctx = await context.build_request_context("When was my last xterra oil change?")

        assert "oil_change" in ctx
        assert "145000" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.gather_health_context")
    async def test_health_keywords(self, mock_hc, mock_always):
        mock_hc.return_value = "Health data here"

        with patch("context.calendar_store") as mock_cal, \
             patch("context.config") as mock_cfg, \
             patch("context.fitbit_store") as mock_fs:
            mock_cal.get_events.return_value = []
            mock_cfg.DATA_DIR = MagicMock()
            mock_cfg.DATA_DIR.__truediv__ = MagicMock(
                return_value=MagicMock(exists=MagicMock(return_value=False))
            )
            mock_cfg.DIET_START_DATE = "2026-03-17"
            mock_fs.get_trend.return_value = ""

            with patch("context.health_store") as mock_hs:
                mock_hs.get_entries.return_value = []
                ctx = await context.build_request_context("How's my heart rate?")

        assert "Health data here" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="datetime here")
    @patch("context.timer_store")
    async def test_timers_always_in_context(self, mock_ts, mock_always):
        """Timers are now Tier 1 — present even without timer keywords."""
        mock_always.return_value = "Active timers: [id=t1] Laundry — fires at 15:30 (sms)"

        with patch("context.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            # Query with NO timer keywords
            ctx = await context.build_request_context("Hello how are you?")

        assert "Laundry" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context")
    @patch("context.location_store")
    async def test_location_always_in_context(self, mock_loc, mock_always):
        """Basic location is now Tier 1 — present even without location keywords."""
        mock_always.return_value = "Location: Rapids Trail, Waukesha (as of 14:00)\nPhone battery: 85%"
        mock_loc.get_history.return_value = []

        with patch("context.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            ctx = await context.build_request_context("Hello how are you?")

        assert "Rapids Trail" in ctx
        assert "85%" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.location_store")
    async def test_location_history_keyword_gated(self, mock_loc, mock_always):
        """Movement trail requires location keywords, basic location is Tier 1."""
        mock_loc.get_history.return_value = [
            {"timestamp": "2026-03-20T13:00:00", "location": "Home"},
            {"timestamp": "2026-03-20T14:00:00", "location": "Work"},
        ]

        with patch("context.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []

            # Without location keywords — no movement trail
            ctx = await context.build_request_context("Hello there")
            assert "Recent movement" not in ctx

            # With location keywords — movement trail present
            ctx = await context.build_request_context("Where am I?")
            assert "Recent movement" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.gather_health_context", return_value="")
    @patch("context.legal_store")
    async def test_legal_keywords(self, mock_ls, mock_hc, mock_always):
        mock_ls.get_entries.return_value = [
            {"id": "l1", "date": "2026-03-18", "entry_type": "court_date",
             "description": "Hearing"},
        ]
        mock_ls.get_upcoming_dates.return_value = []

        with patch("context.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            ctx = await context.build_request_context("What's my next court date?")

        assert "court_date" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.projects")
    async def test_project_keywords(self, mock_proj, mock_always):
        mock_proj.list_projects.return_value = ["aria"]
        mock_proj.find_project.return_value = ("aria", "# ARIA status")

        with patch("context.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            ctx = await context.build_request_context("Project status for aria")

        assert "ARIA status" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    async def test_is_image_triggers_health_context(self, mock_always):
        with patch("context.gather_health_context", return_value="image health ctx"), \
             patch("context.calendar_store") as mock_cal, \
             patch("context.config") as mock_cfg, \
             patch("context.fitbit_store") as mock_fs, \
             patch("context.health_store") as mock_hs:
            mock_cal.get_events.return_value = []
            mock_cfg.DATA_DIR = MagicMock()
            mock_cfg.DATA_DIR.__truediv__ = MagicMock(
                return_value=MagicMock(exists=MagicMock(return_value=False))
            )
            mock_cfg.DIET_START_DATE = "2026-03-17"
            mock_fs.get_trend.return_value = ""
            mock_hs.get_entries.return_value = []

            ctx = await context.build_request_context(
                "Here's a photo", is_image=True
            )
        assert "image health ctx" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_always_context", return_value="")
    @patch("context.gather_health_context", return_value="today health data")
    async def test_14_day_dump_removed(self, mock_hc, mock_always):
        """The raw 14-day health entry dump was removed in v0.4.14."""
        with patch("context.calendar_store") as mock_cal, \
             patch("context.config") as mock_cfg, \
             patch("context.fitbit_store") as mock_fs, \
             patch("context.health_store") as mock_hs:
            mock_cal.get_events.return_value = []
            mock_cfg.DATA_DIR = MagicMock()
            mock_cfg.DATA_DIR.__truediv__ = MagicMock(
                return_value=MagicMock(exists=MagicMock(return_value=False))
            )
            mock_cfg.DIET_START_DATE = "2026-03-17"
            mock_fs.get_trend.return_value = ""
            mock_hs.get_entries.return_value = [
                {"id": "h1", "date": "2026-03-10", "category": "pain",
                 "description": "back pain", "severity": 5, "sleep_hours": None},
            ]

            ctx = await context.build_request_context("How's my health?")

        assert "today health data" in ctx
        assert "Health log (last 14 days)" not in ctx
        # health_store.get_entries should NOT be called for 14-day dump
        mock_hs.get_entries.assert_not_called()


class TestGetContextForText:
    """Test the routing function that detects briefings/debriefs."""

    @pytest.mark.asyncio
    @patch("context.gather_briefing_context", new_callable=AsyncMock)
    @patch("context._briefing_delivered_today")
    @patch("context.gather_always_context")
    async def test_morning_briefing_trigger(self, mock_always, mock_delivered, mock_brief):
        mock_always.return_value = "Tier 1 datetime"
        mock_delivered.return_value = False
        mock_brief.return_value = "Briefing context"

        ctx = await context._get_context_for_text("Good morning!")
        assert "Tier 1 datetime" in ctx
        assert "Briefing context" in ctx

    @pytest.mark.asyncio
    @patch("context.build_request_context", new_callable=AsyncMock)
    @patch("context._briefing_delivered_today")
    async def test_briefing_already_delivered(self, mock_delivered, mock_build):
        mock_delivered.return_value = True
        mock_build.return_value = "Normal context"

        ctx = await context._get_context_for_text("Good morning!")
        assert ctx == "Normal context"

    @pytest.mark.asyncio
    @patch("context.gather_briefing_context", new_callable=AsyncMock)
    @patch("context._briefing_delivered_today")
    @patch("context.gather_always_context")
    async def test_briefing_repeat_request(self, mock_always, mock_delivered, mock_brief):
        mock_always.return_value = "Tier 1"
        mock_delivered.return_value = True
        mock_brief.return_value = "Briefing again"

        ctx = await context._get_context_for_text("Good morning again")
        assert "Briefing again" in ctx

    @pytest.mark.asyncio
    @patch("context.gather_debrief_context", new_callable=AsyncMock)
    @patch("context.gather_always_context")
    async def test_debrief_trigger(self, mock_always, mock_debrief):
        mock_always.return_value = "Tier 1 datetime"
        mock_debrief.return_value = "Debrief context"

        for phrase in ["Good night", "End my day", "Nightly debrief",
                       "Evening debrief", "Wrap up my day"]:
            ctx = await context._get_context_for_text(phrase)
            assert "Tier 1 datetime" in ctx
            assert "Debrief context" in ctx

    @pytest.mark.asyncio
    @patch("context.build_request_context", new_callable=AsyncMock)
    async def test_context_size_logged(self, mock_build, caplog):
        mock_build.return_value = "x" * 500

        with caplog.at_level(logging.INFO, logger="aria"):
            await context._get_context_for_text("Hello")

        assert any("Context:" in r.message and "path=regular" in r.message
                    for r in caplog.records)

    @pytest.mark.asyncio
    @patch("context.gather_briefing_context", new_callable=AsyncMock)
    @patch("context._briefing_delivered_today")
    @patch("context.gather_always_context")
    async def test_briefing_context_size_logged(self, mock_always, mock_delivered,
                                                  mock_brief, caplog):
        mock_always.return_value = "datetime"
        mock_delivered.return_value = False
        mock_brief.return_value = "briefing data"

        with caplog.at_level(logging.INFO, logger="aria"):
            await context._get_context_for_text("Good morning!")

        assert any("Context:" in r.message and "path=briefing" in r.message
                    for r in caplog.records)


class TestBriefingDeliveredToday:
    @patch("context.db.get_conn")
    def test_returns_true_when_found(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mc.execute.return_value.fetchone.return_value = {"1": 1}
        assert context._briefing_delivered_today() is True

    @patch("context.db.get_conn")
    def test_returns_false_when_not_found(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mc.execute.return_value.fetchone.return_value = None
        assert context._briefing_delivered_today() is False


class TestGatherHealthContext:

    def _mock_yesterday_empty(self, mock_ns, mock_fs):
        """Set up mocks so yesterday returns no data."""
        mock_ns.get_daily_totals.return_value = {"item_count": 0, "calories": 0,
            "protein_g": 0, "dietary_fiber_g": 0}
        mock_fs.get_sleep_summary.return_value = None
        mock_fs.get_heart_summary.return_value = None
        mock_fs.get_activity_summary.return_value = None

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_builds_health_context(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2026-03-17"

        mock_hs.get_entries.return_value = [
            {"date": datetime.now().strftime("%Y-%m-%d"),
             "meal_type": "lunch", "description": "chicken"},
        ]
        mock_hs.get_patterns.return_value = ["sleep avg: 6.5h"]

        mock_ns.get_context.return_value = "Nutrition: 1200 cal"
        mock_ns.get_items.return_value = [{"notes": ""}]
        mock_ns.get_net_calories.return_value = {
            "consumed": 1200, "burned": 2000, "net": -800,
        }

        mock_fs.get_briefing_context.return_value = "Fitbit: 65 bpm"
        mock_fs.get_exercise_state.return_value = None
        self._mock_yesterday_empty(mock_ns, mock_fs)

        ctx = context.gather_health_context()
        assert "Meals consumed today" in ctx
        assert "Nutrition: 1200 cal" in ctx
        assert "Fitbit: 65 bpm" in ctx
        assert "Diet day" in ctx

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_empty_context(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2099-01-01"  # future, so diet_day <= 0
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None
        self._mock_yesterday_empty(mock_ns, mock_fs)

        assert context.gather_health_context() == ""

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_yesterday_nutrition_totals_present(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2026-03-17"
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None

        # Yesterday has nutrition data
        mock_ns.get_daily_totals.return_value = {
            "item_count": 3, "calories": 1847, "protein_g": 112, "dietary_fiber_g": 28,
        }
        mock_fs.get_sleep_summary.return_value = None
        mock_fs.get_heart_summary.return_value = None
        mock_fs.get_activity_summary.return_value = None

        ctx = context.gather_health_context()
        assert "Yesterday's nutrition:" in ctx
        assert "1847" in ctx
        assert "112" in ctx

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_yesterday_nutrition_omitted_when_empty(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2026-03-17"
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None
        self._mock_yesterday_empty(mock_ns, mock_fs)

        ctx = context.gather_health_context()
        assert "Yesterday's" not in ctx

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_yesterday_calorie_balance_present(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2026-03-17"
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None
        mock_fs.get_sleep_summary.return_value = None
        mock_fs.get_heart_summary.return_value = None
        mock_fs.get_activity_summary.return_value = None

        # get_net_calories returns different values for today vs yesterday
        def net_side_effect(day=None):
            if day and day != datetime.now().strftime("%Y-%m-%d"):
                return {"consumed": 1800, "burned": 2400, "net": -600}
            return {"consumed": 0, "burned": 0, "net": 0}
        mock_ns.get_net_calories.side_effect = net_side_effect
        mock_ns.get_daily_totals.return_value = {"item_count": 0, "calories": 0,
            "protein_g": 0, "dietary_fiber_g": 0}

        ctx = context.gather_health_context()
        assert "Yesterday's calorie balance:" in ctx
        assert "1800" in ctx
        assert "2400" in ctx

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_yesterday_calorie_balance_omitted_no_burn(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2026-03-17"
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None
        self._mock_yesterday_empty(mock_ns, mock_fs)

        ctx = context.gather_health_context()
        assert "Yesterday's calorie balance" not in ctx

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_yesterday_fitbit_compact(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2026-03-17"
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None
        mock_ns.get_daily_totals.return_value = {"item_count": 0, "calories": 0,
            "protein_g": 0, "dietary_fiber_g": 0}

        mock_fs.get_sleep_summary.return_value = {"duration_hours": 7.2}
        mock_fs.get_heart_summary.return_value = {"resting_hr": 65}
        mock_fs.get_activity_summary.return_value = {"steps": 8432}

        ctx = context.gather_health_context()
        assert "Yesterday's Fitbit:" in ctx
        assert "Sleep 7.2h" in ctx
        assert "65 bpm" in ctx
        assert "8,432 steps" in ctx

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_yesterday_fitbit_partial(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        """Only sleep data, no HR/activity — shows only sleep."""
        mock_cfg.DIET_START_DATE = "2026-03-17"
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None
        mock_ns.get_daily_totals.return_value = {"item_count": 0, "calories": 0,
            "protein_g": 0, "dietary_fiber_g": 0}

        mock_fs.get_sleep_summary.return_value = {"duration_hours": 6.5}
        mock_fs.get_heart_summary.return_value = None
        mock_fs.get_activity_summary.return_value = None

        ctx = context.gather_health_context()
        assert "Yesterday's Fitbit: Sleep 6.5h" in ctx
        assert "bpm" not in ctx.split("Yesterday's Fitbit:")[1] if "Yesterday's Fitbit:" in ctx else True

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_yesterday_fitbit_omitted_when_no_data(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2026-03-17"
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None
        self._mock_yesterday_empty(mock_ns, mock_fs)

        ctx = context.gather_health_context()
        assert "Yesterday's Fitbit" not in ctx

    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.config")
    def test_today_data_unchanged(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        """Regression guard: all today sections still present after Step 2 changes."""
        mock_cfg.DIET_START_DATE = "2026-03-17"
        mock_hs.get_entries.return_value = [
            {"date": datetime.now().strftime("%Y-%m-%d"),
             "meal_type": "dinner", "description": "salmon"},
        ]
        mock_hs.get_patterns.return_value = ["fish meals: 2 days"]
        mock_ns.get_context.return_value = "Nutrition today (3 items logged):"
        mock_ns.get_items.return_value = [{"notes": ""}]
        mock_ns.get_net_calories.return_value = {
            "consumed": 1500, "burned": 2200, "net": -700,
        }
        mock_fs.get_briefing_context.return_value = "Fitbit health data:\n  - Resting heart rate: 64 bpm"
        mock_fs.get_exercise_state.return_value = None
        self._mock_yesterday_empty(mock_ns, mock_fs)

        ctx = context.gather_health_context()
        assert "Meals consumed today" in ctx
        assert "salmon" in ctx
        assert "Nutrition today" in ctx
        assert "Calorie balance:" in ctx
        assert "Health patterns (7d):" in ctx
        assert "Diet day" in ctx


class TestBuildFileContent:
    def test_image_creates_image_block(self):
        blocks = daemon.build_file_content(b"fake jpeg", "photo.jpg", "image/jpeg")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["media_type"] == "image/jpeg"

    def test_pdf_creates_document_block(self):
        blocks = daemon.build_file_content(b"fake pdf", "doc.pdf", "application/pdf")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "document"

    def test_text_file_creates_text_block(self):
        blocks = daemon.build_file_content(b"hello world", "notes.txt", "text/plain")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert "[File: notes.txt]" in blocks[0]["text"]
        assert "hello world" in blocks[0]["text"]

    def test_text_by_extension(self):
        blocks = daemon.build_file_content(b"import os", "script.py", None)
        assert blocks[0]["type"] == "text"

    def test_unknown_type(self):
        blocks = daemon.build_file_content(b"\x00\x01", "data.bin", "application/octet-stream")
        assert blocks[0]["type"] == "text"
        assert "cannot be read" in blocks[0]["text"]

    def test_image_types(self):
        for mime, ext in [("image/jpeg", "jpg"), ("image/png", "png"),
                          ("image/gif", "gif"), ("image/webp", "webp")]:
            blocks = daemon.build_file_content(b"data", f"img.{ext}", mime)
            assert blocks[0]["type"] == "image"


class TestBuildSystemPrompt:
    def test_contains_key_sections(self):
        prompt = system_prompt.build_system_prompt()
        assert "ARIA" in prompt
        assert "ACTION" in prompt
        assert "INTEGRITY" in prompt
        assert "set_delivery" in prompt
        assert "log_nutrition" in prompt
        assert "set_timer" in prompt
        assert "add_event" in prompt
        assert "add_reminder" in prompt

    def test_contains_owner_info(self):
        prompt = system_prompt.build_system_prompt()
        assert "Adam" in prompt

    def test_contains_known_places(self):
        prompt = system_prompt.build_system_prompt()
        assert "home" in prompt.lower()
        assert "work" in prompt.lower()
