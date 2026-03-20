"""Tests for daemon.py — context building functions.

SAFETY: All store lookups and external APIs are mocked.
"""

from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import daemon


class TestBuildRequestContext:
    """Test keyword-triggered context injection."""

    @pytest.mark.asyncio
    @patch("daemon.gather_health_context", return_value="")
    @patch("daemon.weather")
    async def test_weather_keywords(self, mock_weather, mock_hc):
        mock_weather.get_current_conditions = AsyncMock(return_value={
            "description": "Sunny", "temperature_f": 55,
            "humidity": 40, "wind_mph": 10,
        })
        mock_weather.get_forecast = AsyncMock(return_value=[
            {"name": "Today", "temperature": 55, "unit": "F", "summary": "Sunny"},
        ])
        mock_weather.get_alerts = AsyncMock(return_value=[])

        for kw in ["weather", "temperature", "rain", "umbrella"]:
            ctx = await daemon.build_request_context(f"What's the {kw} like?")
            assert "55°F" in ctx or "Sunny" in ctx

    @pytest.mark.asyncio
    @patch("daemon.calendar_store")
    async def test_calendar_keywords_expand_range(self, mock_cal):
        mock_cal.get_events.return_value = []
        mock_cal.get_reminders.return_value = []

        await daemon.build_request_context("What's my schedule this week?")
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
    @patch("daemon.calendar_store")
    async def test_default_calendar_today_only(self, mock_cal):
        mock_cal.get_events.return_value = []
        mock_cal.get_reminders.return_value = []

        await daemon.build_request_context("Hello how are you?")
        call_args = mock_cal.get_events.call_args
        start = call_args[1]["start"]
        end = call_args[1]["end"]
        assert start == end  # today only

    @pytest.mark.asyncio
    @patch("daemon.vehicle_store")
    async def test_vehicle_keywords(self, mock_vs):
        mock_vs.get_entries.return_value = [
            {"id": "v1", "date": "2026-03-15", "event_type": "oil_change",
             "description": "Synthetic", "mileage": 145000},
        ]
        mock_vs.get_latest_by_type.return_value = {}

        with patch("daemon.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            mock_cal.get_reminders.return_value = []
            ctx = await daemon.build_request_context("When was my last xterra oil change?")

        assert "oil_change" in ctx
        assert "145000" in ctx

    @pytest.mark.asyncio
    @patch("daemon.gather_health_context")
    async def test_health_keywords(self, mock_hc):
        mock_hc.return_value = "Health data here"

        with patch("daemon.calendar_store") as mock_cal, \
             patch("daemon.config") as mock_cfg, \
             patch("daemon.fitbit_store") as mock_fs:
            mock_cal.get_events.return_value = []
            mock_cal.get_reminders.return_value = []
            mock_cfg.DATA_DIR = MagicMock()
            mock_cfg.DATA_DIR.__truediv__ = MagicMock(
                return_value=MagicMock(exists=MagicMock(return_value=False))
            )
            mock_cfg.DIET_START_DATE = "2026-03-17"
            mock_fs.get_trend.return_value = ""

            with patch("daemon.health_store") as mock_hs:
                mock_hs.get_entries.return_value = []
                ctx = await daemon.build_request_context("How's my heart rate?")

        assert "Health data here" in ctx

    @pytest.mark.asyncio
    @patch("daemon.timer_store")
    async def test_timer_keywords(self, mock_ts):
        mock_ts.get_active.return_value = [
            {"id": "t1", "label": "Laundry", "fire_at": "2026-03-20T15:30:00",
             "delivery": "sms"},
        ]

        with patch("daemon.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            mock_cal.get_reminders.return_value = []
            ctx = await daemon.build_request_context("Set a timer for 30 minutes")

        assert "Laundry" in ctx

    @pytest.mark.asyncio
    @patch("daemon.location_store")
    async def test_location_keywords(self, mock_loc):
        mock_loc.get_latest.return_value = {
            "lat": 42.58, "lon": -88.43,
            "location": "Rapids Trail, Waukesha",
            "timestamp": "2026-03-20T14:00:00",
            "battery_pct": 85,
        }
        mock_loc.get_history.return_value = []

        with patch("daemon.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            mock_cal.get_reminders.return_value = []
            ctx = await daemon.build_request_context("Where am I?")

        assert "Rapids Trail" in ctx
        assert "85%" in ctx

    @pytest.mark.asyncio
    @patch("daemon.gather_health_context", return_value="")
    @patch("daemon.legal_store")
    async def test_legal_keywords(self, mock_ls, mock_hc):
        mock_ls.get_entries.return_value = [
            {"id": "l1", "date": "2026-03-18", "entry_type": "court_date",
             "description": "Hearing"},
        ]
        mock_ls.get_upcoming_dates.return_value = []

        with patch("daemon.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            mock_cal.get_reminders.return_value = []
            ctx = await daemon.build_request_context("What's my next court date?")

        assert "court_date" in ctx

    @pytest.mark.asyncio
    @patch("daemon.projects")
    async def test_project_keywords(self, mock_proj):
        mock_proj.list_projects.return_value = ["aria"]
        mock_proj.find_project.return_value = ("aria", "# ARIA status")

        with patch("daemon.calendar_store") as mock_cal:
            mock_cal.get_events.return_value = []
            mock_cal.get_reminders.return_value = []
            ctx = await daemon.build_request_context("Project status for aria")

        assert "ARIA status" in ctx

    @pytest.mark.asyncio
    async def test_is_image_triggers_health_context(self):
        with patch("daemon.gather_health_context", return_value="image health ctx"), \
             patch("daemon.calendar_store") as mock_cal, \
             patch("daemon.config") as mock_cfg, \
             patch("daemon.fitbit_store") as mock_fs, \
             patch("daemon.health_store") as mock_hs:
            mock_cal.get_events.return_value = []
            mock_cal.get_reminders.return_value = []
            mock_cfg.DATA_DIR = MagicMock()
            mock_cfg.DATA_DIR.__truediv__ = MagicMock(
                return_value=MagicMock(exists=MagicMock(return_value=False))
            )
            mock_cfg.DIET_START_DATE = "2026-03-17"
            mock_fs.get_trend.return_value = ""
            mock_hs.get_entries.return_value = []

            ctx = await daemon.build_request_context(
                "Here's a photo", is_image=True
            )
        assert "image health ctx" in ctx


class TestGetContextForText:
    """Test the routing function that detects briefings/debriefs."""

    @pytest.mark.asyncio
    @patch("daemon.gather_briefing_context", new_callable=AsyncMock)
    @patch("daemon._briefing_delivered_today")
    async def test_morning_briefing_trigger(self, mock_delivered, mock_brief):
        mock_delivered.return_value = False
        mock_brief.return_value = "Briefing context"

        ctx = await daemon._get_context_for_text("Good morning!")
        assert ctx == "Briefing context"

    @pytest.mark.asyncio
    @patch("daemon.build_request_context", new_callable=AsyncMock)
    @patch("daemon._briefing_delivered_today")
    async def test_briefing_already_delivered(self, mock_delivered, mock_build):
        mock_delivered.return_value = True
        mock_build.return_value = "Normal context"

        ctx = await daemon._get_context_for_text("Good morning!")
        assert ctx == "Normal context"

    @pytest.mark.asyncio
    @patch("daemon.gather_briefing_context", new_callable=AsyncMock)
    @patch("daemon._briefing_delivered_today")
    async def test_briefing_repeat_request(self, mock_delivered, mock_brief):
        mock_delivered.return_value = True
        mock_brief.return_value = "Briefing again"

        ctx = await daemon._get_context_for_text("Good morning again")
        assert ctx == "Briefing again"

    @pytest.mark.asyncio
    @patch("daemon.gather_debrief_context", new_callable=AsyncMock)
    async def test_debrief_trigger(self, mock_debrief):
        mock_debrief.return_value = "Debrief context"

        for phrase in ["Good night", "End my day", "Nightly debrief",
                       "Evening debrief", "Wrap up my day"]:
            ctx = await daemon._get_context_for_text(phrase)
            assert ctx == "Debrief context"


class TestBriefingDeliveredToday:
    @patch("daemon.db.get_conn")
    def test_returns_true_when_found(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mc.execute.return_value.fetchone.return_value = {"1": 1}
        assert daemon._briefing_delivered_today() is True

    @patch("daemon.db.get_conn")
    def test_returns_false_when_not_found(self, mock_get_conn):
        mc = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mc.execute.return_value.fetchone.return_value = None
        assert daemon._briefing_delivered_today() is False


class TestGatherHealthContext:
    @patch("daemon.fitbit_store")
    @patch("daemon.nutrition_store")
    @patch("daemon.health_store")
    @patch("daemon.config")
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

        ctx = daemon.gather_health_context()
        assert "Meals consumed today" in ctx
        assert "Nutrition: 1200 cal" in ctx
        assert "Fitbit: 65 bpm" in ctx
        assert "Diet day" in ctx

    @patch("daemon.fitbit_store")
    @patch("daemon.nutrition_store")
    @patch("daemon.health_store")
    @patch("daemon.config")
    def test_empty_context(self, mock_cfg, mock_hs, mock_ns, mock_fs):
        mock_cfg.DIET_START_DATE = "2099-01-01"  # future, so diet_day <= 0
        mock_hs.get_entries.return_value = []
        mock_hs.get_patterns.return_value = []
        mock_ns.get_context.return_value = ""
        mock_ns.get_items.return_value = []
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None

        assert daemon.gather_health_context() == ""


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
        prompt = daemon.build_system_prompt()
        assert "ARIA" in prompt
        assert "ACTION" in prompt
        assert "INTEGRITY" in prompt
        assert "set_delivery" in prompt
        assert "log_nutrition" in prompt
        assert "set_timer" in prompt
        assert "add_event" in prompt
        assert "add_reminder" in prompt

    def test_contains_owner_info(self):
        prompt = daemon.build_system_prompt()
        assert "Adam" in prompt

    def test_contains_known_places(self):
        prompt = daemon.build_system_prompt()
        assert "home" in prompt.lower()
        assert "work" in prompt.lower()
