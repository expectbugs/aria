"""Temporal boundary tests — every date/time-sensitive code path with frozen time.

Tests midnight race conditions, date computations, quiet hours, late-night
debrief logic, nudge evaluation timing, timer crossing midnight, and diet
start date edge cases.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
Mocks: SMS delivery, phone push, Claude sessions, external HTTP APIs,
       fitbit_store where needed to isolate nutrition from Fitbit side effects.
"""

from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from freezegun import freeze_time

import actions
import context
import nutrition_store
import timer_store
import tick
import db
import fitbit_store
import calendar_store
import health_store

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_request_log,
)


# ---------------------------------------------------------------------------
# TestMidnightRace — 8 tests
# ---------------------------------------------------------------------------

class TestMidnightRace:
    """Verify that date-sensitive queries return correct results at midnight boundary."""

    @freeze_time("2026-03-28 23:59:59.999999")
    def test_daily_totals_correct_day_at_2359(self):
        """Seed nutrition for today, call get_daily_totals(today), verify correct day."""
        today = "2026-03-28"
        seed_nutrition(today, "Chicken breast", calories=400, protein_g=40)
        seed_nutrition(today, "Rice", calories=300, protein_g=6)
        totals = nutrition_store.get_daily_totals(today)
        assert totals["calories"] == 700
        assert totals["protein_g"] == 46
        assert totals["item_count"] == 2

    @freeze_time("2026-03-28 23:59:59.999999")
    def test_net_calories_same_day_at_2359(self):
        """Seed nutrition + fitbit, verify get_net_calories uses same day for both."""
        today = "2026-03-28"
        seed_nutrition(today, "Pasta", calories=600, protein_g=20)
        seed_fitbit_snapshot(today, {
            "activity": {"caloriesOut": "2200", "steps": "8000",
                         "distances": [], "activityCalories": "500",
                         "fairlyActiveMinutes": "20", "veryActiveMinutes": "10",
                         "sedentaryMinutes": "600", "floors": "5"},
        })
        net = nutrition_store.get_net_calories(today)
        assert net["consumed"] == 600
        assert net["burned"] == 2200
        assert net["net"] == 600 - 2200

    @freeze_time("2026-03-29 00:00:00")
    @patch("context.fitbit_store")
    @patch("nutrition_store.fitbit_store")
    def test_gather_health_context_new_day_at_midnight(self, mock_nutr_fitbit,
                                                        mock_ctx_fitbit):
        """At 00:00:00, gather_health_context() 'today' should be the new date."""
        # Mock fitbit at both import sites to avoid MagicMock format errors
        for m in (mock_ctx_fitbit, mock_nutr_fitbit):
            m.get_briefing_context.return_value = ""
            m.get_exercise_state.return_value = None
            m.get_exercise_coaching_context.return_value = ""
            m.get_sleep_summary.return_value = None
            m.get_heart_summary.return_value = None
            m.get_activity_summary.return_value = None
        # Seed nutrition for the NEW day
        seed_nutrition("2026-03-29", "Eggs", calories=200, protein_g=12)
        ctx = context.gather_health_context()
        # The context should reference today = 2026-03-29
        assert "Eggs" in ctx or "200" in ctx

    @freeze_time("2026-03-29 00:00:00")
    @patch("nutrition_store.fitbit_store")
    def test_nutrition_context_correct_date_at_midnight(self, mock_fitbit):
        """nutrition_store.get_context(today) at midnight uses the new date."""
        mock_fitbit.get_activity_summary.return_value = None
        today = "2026-03-29"
        seed_nutrition(today, "Oatmeal", calories=300, protein_g=10)
        ctx = nutrition_store.get_context(today)
        assert "Oatmeal" in ctx
        assert "300" in ctx

    @freeze_time("2026-03-29 00:00:01")
    def test_briefing_delivered_yesterday_not_today(self):
        """Yesterday's briefing in request_log should NOT count as delivered today."""
        # Insert a briefing request with a timestamp in the PAST (yesterday)
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO request_log (timestamp, input, status, response, duration_s)
                   VALUES (%s, %s, %s, %s, %s)""",
                ("2026-03-28 08:00:00", "good morning", "ok", "briefing", 1.0),
            )
        result = context._briefing_delivered_today()
        assert result is False

    @freeze_time("2026-03-28 23:59:59")
    @patch("nutrition_store.fitbit_store")
    def test_get_items_returns_today_at_2359(self, mock_fitbit):
        """Seed today's nutrition, verify get_items(day=today) returns them at 23:59:59."""
        today = "2026-03-28"
        seed_nutrition(today, "Salmon", calories=350, protein_g=30)
        items = nutrition_store.get_items(day=today)
        assert len(items) == 1
        assert items[0]["food_name"] == "Salmon"

    @freeze_time("2026-03-29 00:00:01")
    @patch("nutrition_store.fitbit_store")
    def test_get_items_separates_yesterday_today(self, mock_fitbit):
        """Seed yesterday + today nutrition, verify get_items returns only the requested day."""
        yesterday = "2026-03-28"
        today = "2026-03-29"
        seed_nutrition(yesterday, "Yesterday burger", calories=500, protein_g=25)
        seed_nutrition(today, "Today eggs", calories=200, protein_g=12)
        yesterday_items = nutrition_store.get_items(day=yesterday)
        today_items = nutrition_store.get_items(day=today)
        assert len(yesterday_items) == 1
        assert yesterday_items[0]["food_name"] == "Yesterday burger"
        assert len(today_items) == 1
        assert today_items[0]["food_name"] == "Today eggs"

    @freeze_time("2026-03-29 00:00:00")
    @patch("context.timer_store")
    @patch("context.calendar_store")
    @patch("context.location_store")
    @patch("context.fitbit_store")
    @patch("context.redis_client")
    def test_gather_always_context_shows_new_day(self, mock_redis, mock_fitbit,
                                                  mock_loc, mock_cal, mock_timer):
        """gather_always_context() datetime string should show the new day at midnight."""
        mock_timer.get_active.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fitbit.get_exercise_state.return_value = None
        mock_redis.get_active_tasks.return_value = []
        mock_redis.format_task_status.return_value = ""
        ctx = context.gather_always_context()
        assert "March 29, 2026" in ctx
        assert "12:00 AM" in ctx


# ---------------------------------------------------------------------------
# TestTimerMidnightRace — 6 tests
# ---------------------------------------------------------------------------

class TestTimerMidnightRace:
    """Verify timer fire_at computation across midnight boundary."""

    @freeze_time("2026-03-28 23:58:00")
    def test_relative_timer_crosses_midnight(self):
        """set_timer with minutes=5 at 23:58 should fire at 00:03 next day."""
        response = (
            'Timer set! '
            '<!--ACTION::{"action": "set_timer", "label": "Test", '
            '"minutes": 5, "delivery": "sms", "message": "Time is up"}-->'
        )
        actions.process_actions_sync(response)
        timers = timer_store.get_active()
        assert len(timers) == 1
        fire_at = timers[0]["fire_at"]
        # fire_at should be 2026-03-29 00:03:00
        assert "2026-03-29" in fire_at
        assert fire_at[11:16] == "00:03"

    @freeze_time("2026-03-28 23:59:30")
    def test_absolute_timer_wraps_to_tomorrow(self):
        """set_timer time='00:05' at 23:59 should fire TOMORROW, not today."""
        response = (
            'Timer set! '
            '<!--ACTION::{"action": "set_timer", "label": "Early timer", '
            '"time": "00:05", "delivery": "sms", "message": "Wake up check"}-->'
        )
        actions.process_actions_sync(response)
        timers = timer_store.get_active()
        assert len(timers) == 1
        fire_at = timers[0]["fire_at"]
        # Should be 2026-03-29 00:05, not 2026-03-28 00:05
        assert "2026-03-29" in fire_at

    @freeze_time("2026-03-28 23:59:59")
    def test_due_timer_fires_after_midnight(self):
        """Timer with fire_at=23:59:59 should appear in get_due() at 00:00:01."""
        seed_timer(label="Midnight timer",
                   fire_at="2026-03-28 23:59:59",
                   delivery="sms")
        # Advance time past midnight
        with freeze_time("2026-03-29 00:00:01"):
            due = timer_store.get_due()
            assert len(due) == 1
            assert due[0]["label"] == "Midnight timer"

    @freeze_time("2026-03-28 14:00:00.000000")
    def test_exact_match_time_is_past(self):
        """set_timer time='14:00' at exactly 14:00:00 should schedule for tomorrow.

        The code uses string comparison (fire_at <= now), so 14:00 == 14:00 is past.
        """
        response = (
            'Timer! '
            '<!--ACTION::{"action": "set_timer", "label": "Exact match", '
            '"time": "14:00", "delivery": "sms", "message": "Check"}-->'
        )
        actions.process_actions_sync(response)
        timers = timer_store.get_active()
        assert len(timers) == 1
        fire_at = timers[0]["fire_at"]
        # fire_at <= now triggers the "already passed" branch, so tomorrow
        assert "2026-03-29" in fire_at

    @freeze_time("2026-03-28 14:00:01")
    def test_one_second_after_is_tomorrow(self):
        """set_timer time='14:00' at 14:00:01 should be tomorrow."""
        response = (
            'Timer! '
            '<!--ACTION::{"action": "set_timer", "label": "Past match", '
            '"time": "14:00", "delivery": "sms", "message": "Check"}-->'
        )
        actions.process_actions_sync(response)
        timers = timer_store.get_active()
        assert len(timers) == 1
        fire_at = timers[0]["fire_at"]
        assert "2026-03-29" in fire_at

    @freeze_time("2026-03-28 13:59:59")
    def test_one_second_before_is_today(self):
        """set_timer time='14:00' at 13:59:59 should be today."""
        response = (
            'Timer! '
            '<!--ACTION::{"action": "set_timer", "label": "Today timer", '
            '"time": "14:00", "delivery": "sms", "message": "Check"}-->'
        )
        actions.process_actions_sync(response)
        timers = timer_store.get_active()
        assert len(timers) == 1
        fire_at = timers[0]["fire_at"]
        assert "2026-03-28" in fire_at
        assert fire_at[11:16] == "14:00"


# ---------------------------------------------------------------------------
# TestDietStartDateHandling — 6 tests
# ---------------------------------------------------------------------------

class TestDietStartDateHandling:
    """Verify _get_diet_day() handles all config edge cases without crashing."""

    @freeze_time("2026-03-28 12:00:00")
    def test_normal_diet_day(self):
        """Normal: DIET_START_DATE 10 days ago -> day = 10."""
        with patch.object(context.config, "DIET_START_DATE", "2026-03-19"):
            result = context._get_diet_day()
            assert result == 10

    @freeze_time("2026-03-28 12:00:00")
    def test_empty_string(self):
        """Empty string DIET_START_DATE -> None."""
        with patch.object(context.config, "DIET_START_DATE", ""):
            result = context._get_diet_day()
            assert result is None

    @freeze_time("2026-03-28 12:00:00")
    def test_missing_attribute(self):
        """Missing DIET_START_DATE attribute -> None (not AttributeError)."""
        with patch("context.config", spec=[]):
            result = context._get_diet_day()
            assert result is None

    @freeze_time("2026-03-28 12:00:00")
    def test_invalid_format(self):
        """Invalid date format -> None (not ValueError crash)."""
        with patch.object(context.config, "DIET_START_DATE", "not-a-date"):
            result = context._get_diet_day()
            assert result is None

    @freeze_time("2026-03-28 12:00:00")
    def test_future_date(self):
        """Future DIET_START_DATE -> None (day would be <= 0)."""
        with patch.object(context.config, "DIET_START_DATE", "2026-04-01"):
            result = context._get_diet_day()
            assert result is None

    @freeze_time("2026-03-28 12:00:00")
    def test_first_day(self):
        """DIET_START_DATE = today -> day = 1."""
        with patch.object(context.config, "DIET_START_DATE", "2026-03-28"):
            result = context._get_diet_day()
            assert result == 1


# ---------------------------------------------------------------------------
# TestNutritionDateValidation — 8 tests
# ---------------------------------------------------------------------------

class TestNutritionDateValidation:
    """Verify nutrition_store._validate_entry date boundary checks."""

    @freeze_time("2026-03-28 12:00:00")
    def test_today_passes(self):
        """entry_date = today -> passes validation."""
        errors = nutrition_store._validate_entry("Food", "2026-03-28", 1.0, {"calories": 100})
        assert errors == []

    @freeze_time("2026-03-28 12:00:00")
    def test_yesterday_passes(self):
        """entry_date = yesterday -> passes validation."""
        errors = nutrition_store._validate_entry("Food", "2026-03-27", 1.0, {"calories": 100})
        assert errors == []

    @freeze_time("2026-03-28 12:00:00")
    def test_exactly_7_days_ago_passes(self):
        """entry_date = exactly 7 days ago -> passes (boundary)."""
        errors = nutrition_store._validate_entry("Food", "2026-03-21", 1.0, {"calories": 100})
        assert errors == []

    @freeze_time("2026-03-28 12:00:00")
    def test_8_days_ago_fails(self):
        """entry_date = 8 days ago -> fails."""
        errors = nutrition_store._validate_entry("Food", "2026-03-20", 1.0, {"calories": 100})
        assert any("more than 7 days" in e for e in errors)

    @freeze_time("2026-03-28 12:00:00")
    def test_tomorrow_fails(self):
        """entry_date = tomorrow -> fails (future)."""
        errors = nutrition_store._validate_entry("Food", "2026-03-29", 1.0, {"calories": 100})
        assert any("future" in e for e in errors)

    @freeze_time("2026-03-28 12:00:00")
    def test_invalid_string_fails(self):
        """entry_date = invalid string -> fails."""
        errors = nutrition_store._validate_entry("Food", "not-a-date", 1.0, {"calories": 100})
        assert any("not a valid date" in e for e in errors)

    @freeze_time("2026-03-28 12:00:00")
    def test_none_fails(self):
        """entry_date = None -> fails."""
        errors = nutrition_store._validate_entry("Food", None, 1.0, {"calories": 100})
        assert any("not a valid date" in e for e in errors)

    @freeze_time("2026-03-28 12:00:00")
    def test_empty_string_fails(self):
        """entry_date = '' -> add_item raises ValueError (required field)."""
        with pytest.raises(ValueError, match="entry_date is required"):
            nutrition_store.add_item(food_name="Food", entry_date="",
                                     nutrients={"calories": 100})


# ---------------------------------------------------------------------------
# TestQuietHours — 8 tests
# ---------------------------------------------------------------------------

class TestQuietHours:
    """Verify is_quiet_hours() for all boundary conditions."""

    @freeze_time("2026-03-28 03:00:00")
    def test_simple_range_inside(self):
        """START=0, END=7, 3am -> quiet (True)."""
        with patch.object(tick.config, "QUIET_HOURS_START", 0), \
             patch.object(tick.config, "QUIET_HOURS_END", 7):
            assert tick.is_quiet_hours() is True

    @freeze_time("2026-03-28 08:00:00")
    def test_simple_range_outside(self):
        """START=0, END=7, 8am -> not quiet (False)."""
        with patch.object(tick.config, "QUIET_HOURS_START", 0), \
             patch.object(tick.config, "QUIET_HOURS_END", 7):
            assert tick.is_quiet_hours() is False

    @freeze_time("2026-03-28 23:00:00")
    def test_wrap_midnight_late_night(self):
        """START=22, END=7 (wraps midnight), 23:00 -> quiet (True)."""
        with patch.object(tick.config, "QUIET_HOURS_START", 22), \
             patch.object(tick.config, "QUIET_HOURS_END", 7):
            assert tick.is_quiet_hours() is True

    @freeze_time("2026-03-29 06:00:00")
    def test_wrap_midnight_early_morning(self):
        """START=22, END=7 (wraps), 6:00 -> quiet (True)."""
        with patch.object(tick.config, "QUIET_HOURS_START", 22), \
             patch.object(tick.config, "QUIET_HOURS_END", 7):
            assert tick.is_quiet_hours() is True

    @freeze_time("2026-03-28 14:00:00")
    def test_wrap_midnight_afternoon(self):
        """START=22, END=7 (wraps), 14:00 -> not quiet (False)."""
        with patch.object(tick.config, "QUIET_HOURS_START", 22), \
             patch.object(tick.config, "QUIET_HOURS_END", 7):
            assert tick.is_quiet_hours() is False

    @freeze_time("2026-03-28 22:00:00")
    def test_wrap_midnight_exact_start_boundary(self):
        """START=22, END=7 (wraps), 22:00 -> quiet (True, start is inclusive)."""
        with patch.object(tick.config, "QUIET_HOURS_START", 22), \
             patch.object(tick.config, "QUIET_HOURS_END", 7):
            assert tick.is_quiet_hours() is True

    @freeze_time("2026-03-28 00:00:00")
    def test_simple_range_midnight_start(self):
        """START=0, END=7, hour=0 (midnight) -> quiet (True)."""
        with patch.object(tick.config, "QUIET_HOURS_START", 0), \
             patch.object(tick.config, "QUIET_HOURS_END", 7):
            assert tick.is_quiet_hours() is True

    @freeze_time("2026-03-28 07:00:00")
    def test_simple_range_exclusive_end(self):
        """START=0, END=7, hour=7 -> not quiet (False, end is exclusive)."""
        with patch.object(tick.config, "QUIET_HOURS_START", 0), \
             patch.object(tick.config, "QUIET_HOURS_END", 7):
            assert tick.is_quiet_hours() is False


# ---------------------------------------------------------------------------
# TestLateNightDebrief — 4 tests
# ---------------------------------------------------------------------------

class TestLateNightDebrief:
    """Verify that 'good night' hits the debrief path at various hours.

    The late-night rule (12am-6am: treat previous date as 'today') is in
    the system prompt, not in code logic. The code's _get_context_for_text()
    always routes 'good night' to the debrief path regardless of hour.
    These tests verify the routing, not the date-shifting (which is LLM behavior).
    """

    @pytest.mark.asyncio
    @freeze_time("2026-03-29 01:00:00")
    @patch("context.gather_debrief_context", new_callable=AsyncMock)
    @patch("context.gather_always_context")
    async def test_good_night_at_1am_is_debrief(self, mock_always, mock_debrief):
        """1am: 'good night' should route to debrief."""
        mock_always.return_value = "datetime: 2026-03-29 01:00"
        mock_debrief.return_value = "Debrief context"
        ctx = await context._get_context_for_text("good night")
        mock_debrief.assert_called_once()

    @pytest.mark.asyncio
    @freeze_time("2026-03-29 05:59:00")
    @patch("context.gather_debrief_context", new_callable=AsyncMock)
    @patch("context.gather_always_context")
    async def test_good_night_at_559am_is_debrief(self, mock_always, mock_debrief):
        """5:59am: 'good night' should still route to debrief."""
        mock_always.return_value = "datetime: 2026-03-29 05:59"
        mock_debrief.return_value = "Debrief context"
        ctx = await context._get_context_for_text("good night")
        mock_debrief.assert_called_once()

    @pytest.mark.asyncio
    @freeze_time("2026-03-28 23:00:00")
    @patch("context.gather_debrief_context", new_callable=AsyncMock)
    @patch("context.gather_always_context")
    async def test_good_night_at_11pm_is_debrief(self, mock_always, mock_debrief):
        """11pm: 'good night' is a normal debrief (not late-night special case)."""
        mock_always.return_value = "datetime: 2026-03-28 23:00"
        mock_debrief.return_value = "Debrief context"
        ctx = await context._get_context_for_text("good night")
        mock_debrief.assert_called_once()

    @pytest.mark.asyncio
    @freeze_time("2026-03-28 07:00:00")
    @patch("context.gather_debrief_context", new_callable=AsyncMock)
    @patch("context.gather_always_context")
    async def test_good_night_at_7am_is_debrief(self, mock_always, mock_debrief):
        """7am: 'good night' still routes to debrief path (code does not gate by hour)."""
        mock_always.return_value = "datetime: 2026-03-28 07:00"
        mock_debrief.return_value = "Debrief context"
        ctx = await context._get_context_for_text("good night")
        mock_debrief.assert_called_once()


# ---------------------------------------------------------------------------
# TestNudgeEvaluationTiming — 6 tests
# ---------------------------------------------------------------------------

class TestNudgeEvaluationTiming:
    """Verify nudge triggers respect time-of-day guards."""

    @freeze_time("2026-03-28 14:00:00")
    @patch("tick.fitbit_store")
    @patch("tick.legal_store")
    @patch("tick.health_store")
    @patch("tick.location_store")
    @patch("tick.calendar_store")
    def test_meal_reminder_triggers_after_5h_gap(self, mock_cal, mock_loc,
                                                  mock_health, mock_legal,
                                                  mock_fitbit):
        """14:00 with nutrition 5+ hours ago -> meal_reminder triggers."""
        mock_cal.auto_expire_stale_reminders.return_value = []
        mock_cal.get_events.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_health.get_patterns.return_value = []
        mock_legal.get_upcoming_dates.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fitbit.get_sleep_summary.return_value = None
        mock_fitbit.get_heart_summary.return_value = None
        mock_fitbit.get_resting_hr_history.return_value = []
        mock_fitbit.get_activity_summary.return_value = None
        # Seed a nutrition entry from 5+ hours ago
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO nutrition_entries
                   (id, date, time, meal_type, food_name, source, servings,
                    nutrients, content_hash, created)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                ("meal1", "2026-03-28", "08:30", "breakfast", "Eggs",
                 "manual", 1.0, '{"calories": 200}', "hash_meal1",
                 "2026-03-28 08:30:00"),
            )
        triggers = tick.evaluate_nudges()
        nudge_types = [t for t, _ in triggers]
        assert "meal_reminder" in nudge_types

    @freeze_time("2026-03-28 07:00:00")
    @patch("tick.fitbit_store")
    @patch("tick.legal_store")
    @patch("tick.health_store")
    @patch("tick.location_store")
    @patch("tick.calendar_store")
    def test_no_meal_reminder_before_8am(self, mock_cal, mock_loc,
                                          mock_health, mock_legal,
                                          mock_fitbit):
        """7:00 with no meals -> NO meal_reminder (hour < 8)."""
        mock_cal.auto_expire_stale_reminders.return_value = []
        mock_cal.get_events.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_health.get_patterns.return_value = []
        mock_legal.get_upcoming_dates.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fitbit.get_sleep_summary.return_value = None
        mock_fitbit.get_heart_summary.return_value = None
        mock_fitbit.get_resting_hr_history.return_value = []
        mock_fitbit.get_activity_summary.return_value = None
        triggers = tick.evaluate_nudges()
        nudge_types = [t for t, _ in triggers]
        assert "meal_reminder" not in nudge_types

    @freeze_time("2026-03-28 20:30:00")
    @patch("tick.fitbit_store")
    @patch("tick.legal_store")
    @patch("tick.health_store")
    @patch("tick.location_store")
    @patch("tick.calendar_store")
    def test_diet_check_triggers_in_evening(self, mock_cal, mock_loc,
                                             mock_health, mock_legal,
                                             mock_fitbit):
        """20:30 with only 1 nutrition entry and DIET_START_DATE -> diet_check triggers."""
        mock_cal.auto_expire_stale_reminders.return_value = []
        mock_cal.get_events.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_health.get_patterns.return_value = []
        mock_legal.get_upcoming_dates.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fitbit.get_sleep_summary.return_value = None
        mock_fitbit.get_heart_summary.return_value = None
        mock_fitbit.get_resting_hr_history.return_value = []
        mock_fitbit.get_activity_summary.return_value = None
        # Seed 1 nutrition entry today
        seed_nutrition("2026-03-28", "Eggs", calories=200, protein_g=12)
        with patch.object(tick.config, "DIET_START_DATE", "2026-03-18"):
            triggers = tick.evaluate_nudges()
        nudge_types = [t for t, _ in triggers]
        assert "diet_check" in nudge_types

    @freeze_time("2026-03-28 14:00:00")
    @patch("tick.fitbit_store")
    @patch("tick.legal_store")
    @patch("tick.health_store")
    @patch("tick.location_store")
    @patch("tick.calendar_store")
    def test_no_diet_check_in_afternoon(self, mock_cal, mock_loc,
                                         mock_health, mock_legal,
                                         mock_fitbit):
        """14:00 -> NO diet_check (not between 20-21)."""
        mock_cal.auto_expire_stale_reminders.return_value = []
        mock_cal.get_events.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_health.get_patterns.return_value = []
        mock_legal.get_upcoming_dates.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fitbit.get_sleep_summary.return_value = None
        mock_fitbit.get_heart_summary.return_value = None
        mock_fitbit.get_resting_hr_history.return_value = []
        mock_fitbit.get_activity_summary.return_value = None
        seed_nutrition("2026-03-28", "Eggs", calories=200, protein_g=12)
        with patch.object(tick.config, "DIET_START_DATE", "2026-03-18"):
            triggers = tick.evaluate_nudges()
        nudge_types = [t for t, _ in triggers]
        assert "diet_check" not in nudge_types

    @freeze_time("2026-03-28 18:59:00")
    @patch("tick.fitbit_store")
    @patch("tick.legal_store")
    @patch("tick.health_store")
    @patch("tick.location_store")
    @patch("tick.calendar_store")
    def test_no_calorie_surplus_before_7pm(self, mock_cal, mock_loc,
                                            mock_health, mock_legal,
                                            mock_fitbit):
        """18:59 -> NO calorie_surplus nudge (hour < 19)."""
        mock_cal.auto_expire_stale_reminders.return_value = []
        mock_cal.get_events.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_health.get_patterns.return_value = []
        mock_legal.get_upcoming_dates.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fitbit.get_sleep_summary.return_value = None
        mock_fitbit.get_heart_summary.return_value = None
        mock_fitbit.get_resting_hr_history.return_value = []
        mock_fitbit.get_activity_summary.return_value = None
        # Seed calorie surplus scenario
        seed_nutrition("2026-03-28", "Big meal", calories=3000, protein_g=50)
        seed_fitbit_snapshot("2026-03-28", {
            "activity": {"caloriesOut": "2000", "steps": "5000",
                         "distances": [], "activityCalories": "300",
                         "fairlyActiveMinutes": "10", "veryActiveMinutes": "5",
                         "sedentaryMinutes": "700", "floors": "3"},
        })
        triggers = tick.evaluate_nudges()
        nudge_types = [t for t, _ in triggers]
        assert "nutrition_calorie_surplus" not in nudge_types

    @freeze_time("2026-03-28 19:00:00")
    @patch("tick.fitbit_store")
    @patch("tick.legal_store")
    @patch("tick.health_store")
    @patch("tick.location_store")
    @patch("tick.calendar_store")
    def test_calorie_surplus_triggers_at_7pm(self, mock_cal, mock_loc,
                                              mock_health, mock_legal,
                                              mock_fitbit):
        """19:00 with calorie surplus -> nutrition_calorie_surplus triggers."""
        mock_cal.auto_expire_stale_reminders.return_value = []
        mock_cal.get_events.return_value = []
        mock_cal.get_reminders.return_value = []
        mock_health.get_patterns.return_value = []
        mock_legal.get_upcoming_dates.return_value = []
        mock_loc.get_latest.return_value = None
        mock_fitbit.get_sleep_summary.return_value = None
        mock_fitbit.get_heart_summary.return_value = None
        mock_fitbit.get_resting_hr_history.return_value = []
        mock_fitbit.get_activity_summary.return_value = None
        # Seed calorie surplus scenario: consumed 3000, burned 2000 -> +1000 net
        seed_nutrition("2026-03-28", "Big meal", calories=3000, protein_g=50)
        seed_fitbit_snapshot("2026-03-28", {
            "activity": {"caloriesOut": "2000", "steps": "5000",
                         "distances": [], "activityCalories": "300",
                         "fairlyActiveMinutes": "10", "veryActiveMinutes": "5",
                         "sedentaryMinutes": "700", "floors": "3"},
        })
        triggers = tick.evaluate_nudges()
        nudge_types = [t for t, _ in triggers]
        assert "nutrition_calorie_surplus" in nudge_types
