"""Tests for enhanced verification: completeness claim detection + context scope annotations.

Completeness detector is LOG-ONLY — does not trigger retries or visible warnings.
Context scope annotations are informational headers on injected context sections.
"""

import pytest
from unittest.mock import patch, MagicMock

from verification import (
    check_completeness_claims, ClaimCheck,
    _COMPLETENESS_CLAIMS, _SCOPE_INDICATORS,
)


# ---------------------------------------------------------------------------
# Completeness claim regex
# ---------------------------------------------------------------------------

class TestCompletenessRegex:
    def test_the_only_event(self):
        assert _COMPLETENESS_CLAIMS.search("The only event I see is your birthday")

    def test_no_other_appointments(self):
        assert _COMPLETENESS_CLAIMS.search("There are no other appointments scheduled")

    def test_thats_everything(self):
        assert _COMPLETENESS_CLAIMS.search("That's everything in your calendar")

    def test_calendar_empty(self):
        assert _COMPLETENESS_CLAIMS.search("Your calendar is empty for today")

    def test_calendar_clear(self):
        assert _COMPLETENESS_CLAIMS.search("Your calendar is clear")

    def test_nothing_else_scheduled(self):
        assert _COMPLETENESS_CLAIMS.search("Nothing else scheduled this week")

    def test_dont_have_any_events(self):
        assert _COMPLETENESS_CLAIMS.search("You don't have any other events")

    def test_no_more_appointments(self):
        assert _COMPLETENESS_CLAIMS.search("No more appointments found")

    # --- Should NOT match ---

    def test_normal_event_reference(self):
        assert not _COMPLETENESS_CLAIMS.search("Your next event is tomorrow")

    def test_specific_count(self):
        assert not _COMPLETENESS_CLAIMS.search("You have 3 events this week")

    def test_simple_acknowledgment(self):
        assert not _COMPLETENESS_CLAIMS.search("Got it, timer set for 30 minutes")

    def test_question_about_events(self):
        assert not _COMPLETENESS_CLAIMS.search("Do you have any other events?")


# ---------------------------------------------------------------------------
# check_completeness_claims()
# ---------------------------------------------------------------------------

class TestCheckCompletenessClaims:
    def test_flagged_when_context_is_scoped(self):
        response = "The only event I see in your calendar is Toni's Birthday."
        context = "Events (today only, 1 shown — use `query.py calendar` for full range): ..."
        claims = check_completeness_claims(response, context)
        assert len(claims) == 1
        assert claims[0].claim_type == "completeness"
        assert claims[0].status == "logged"  # NOT contradicted

    def test_not_flagged_when_context_not_scoped(self):
        """If context doesn't have scope annotations, claim might be correct."""
        response = "The only event I see is your birthday."
        context = "Events: [id=abc] 2026-04-02 Toni's Birthday"
        claims = check_completeness_claims(response, context)
        assert len(claims) == 0

    def test_not_flagged_without_completeness_language(self):
        response = "You have an appointment tomorrow at 3pm."
        context = "Events (today only, 1 shown — use `query.py calendar` for full range): ..."
        claims = check_completeness_claims(response, context)
        assert len(claims) == 0

    def test_scope_indicator_query_py(self):
        response = "Your calendar is empty."
        context = "use `query.py calendar` for full range"
        claims = check_completeness_claims(response, context)
        assert len(claims) == 1

    def test_scope_indicator_today_only(self):
        response = "Nothing else scheduled."
        context = "Events (today only, 0 shown)"
        claims = check_completeness_claims(response, context)
        assert len(claims) == 1

    def test_scope_indicator_unread_important(self):
        response = "There are no other entries in your inbox."
        context = "unread important only"
        claims = check_completeness_claims(response, context)
        assert len(claims) == 1

    def test_claim_status_is_logged_not_contradicted(self):
        """Completeness claims must be 'logged' status to avoid triggering retries."""
        response = "The only event is tomorrow's meeting."
        context = "today only"
        claims = check_completeness_claims(response, context)
        assert len(claims) == 1
        assert claims[0].status == "logged"
        assert claims[0].status != "contradicted"


# ---------------------------------------------------------------------------
# Context scope annotations
# ---------------------------------------------------------------------------

class TestContextScopeAnnotations:
    """Verify that context builders include scope annotations."""

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_timers_include_count(self, mock_ts, mock_cal, mock_loc, mock_fs):
        import context
        from actions import _pending_confirmations
        _pending_confirmations.clear()
        mock_ts.get_active.return_value = [
            {"id": "t1", "label": "Laundry", "fire_at": "2026-03-29T14:30:00",
             "delivery": "sms"},
        ]
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        assert "1 total" in ctx
        assert "Active timers" in ctx

    @patch("context.fitbit_store")
    @patch("context.location_store")
    @patch("context.calendar_store")
    @patch("context.timer_store")
    def test_reminders_include_count(self, mock_ts, mock_cal, mock_loc, mock_fs):
        import context
        from actions import _pending_confirmations
        _pending_confirmations.clear()
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = [
            {"id": "r1", "text": "Buy milk", "due": "2026-03-30"},
            {"id": "r2", "text": "Call dentist", "due": None},
        ]
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = context.gather_always_context()
        assert "2 total" in ctx
        assert "Active reminders" in ctx

    @pytest.mark.asyncio
    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.calendar_store")
    @patch("context.location_store")
    @patch("context.timer_store")
    async def test_calendar_scope_today_only(self, mock_ts, mock_loc, mock_cal,
                                              mock_hs, mock_ns, mock_fs):
        import context
        from actions import _pending_confirmations
        _pending_confirmations.clear()
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_cal.get_events.return_value = [
            {"id": "e1", "date": "2026-03-29", "title": "Meeting", "time": "14:00"},
        ]
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        ctx = await context.build_request_context("what time is my meeting?")
        assert "today only" in ctx
        assert "1 shown" in ctx
        assert "query.py calendar" in ctx

    @pytest.mark.asyncio
    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.calendar_store")
    @patch("context.location_store")
    @patch("context.timer_store")
    async def test_calendar_scope_week(self, mock_ts, mock_loc, mock_cal,
                                        mock_hs, mock_ns, mock_fs):
        import context
        from actions import _pending_confirmations
        _pending_confirmations.clear()
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_cal.get_events.return_value = [
            {"id": "e1", "date": "2026-03-29", "title": "Meeting", "time": "14:00"},
            {"id": "e2", "date": "2026-04-02", "title": "Dentist", "time": "10:00"},
        ]
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        # "schedule" triggers calendar expansion
        ctx = await context.build_request_context("what's my schedule this week?")
        assert "next 7 days" in ctx
        assert "2 shown" in ctx

    @pytest.mark.asyncio
    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.calendar_store")
    @patch("context.location_store")
    @patch("context.timer_store")
    async def test_email_scope_annotation(self, mock_ts, mock_loc, mock_cal,
                                           mock_hs, mock_ns, mock_fs):
        import context
        from actions import _pending_confirmations
        _pending_confirmations.clear()
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_cal.get_events.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None

        mock_gmail = MagicMock()
        mock_gmail.get_email_context.return_value = "Unread important: 2"
        with patch.dict("sys.modules", {"gmail_store": mock_gmail}):
            ctx = await context.build_request_context("any new emails?")
        assert "unread important only" in ctx
        assert "query.py email" in ctx

    @pytest.mark.asyncio
    @patch("context.fitbit_store")
    @patch("context.nutrition_store")
    @patch("context.health_store")
    @patch("context.calendar_store")
    @patch("context.location_store")
    @patch("context.timer_store")
    async def test_health_scope_annotation(self, mock_ts, mock_loc, mock_cal,
                                            mock_hs, mock_ns, mock_fs):
        import context
        from actions import _pending_confirmations
        _pending_confirmations.clear()
        mock_ts.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_cal.get_events.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fs.get_exercise_state.return_value = None
        mock_fs.get_trend.return_value = ""
        mock_hs.get_entries.return_value = [
            {"date": "2026-03-29", "meal_type": "lunch", "description": "Chicken"}
        ]
        mock_ns.get_context.return_value = "Calories: 500"
        mock_ns.get_items.return_value = [{"notes": ""}]
        mock_ns.get_net_calories.return_value = {"consumed": 0, "burned": 0, "net": 0}
        mock_ns.get_daily_totals.return_value = {"item_count": 0}
        mock_hs.get_patterns.return_value = []
        mock_fs.get_briefing_context.return_value = ""
        mock_fs.get_exercise_state.return_value = None
        mock_fs.get_sleep_summary.return_value = None
        mock_fs.get_heart_summary.return_value = None
        mock_fs.get_activity_summary.return_value = None

        ctx = await context.build_request_context("how many calories today?")
        assert "today + yesterday" in ctx
        assert "query.py health" in ctx
