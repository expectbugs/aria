"""Forward-looking contract tests for ARIA Project Phases 1-3.

Validates that the API surface future phases depend on works correctly TODAY,
so we catch breaking changes before they happen.

Phase 1: Agent System (monitors inject into context, stores provide summaries)
Phase 2: Delivery Intelligence (location/exercise state for routing)
Phase 3: Verification Pipeline (process_actions correctness guarantees)

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

from datetime import date, timedelta, datetime
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import context
import nutrition_store
import health_store
import fitbit_store
import location_store
import legal_store
import calendar_store
import timer_store
import actions
import db
from aria_api import _is_simple_query

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot, seed_location,
    seed_timer, seed_reminder, seed_event, seed_legal,
)


# ---------------------------------------------------------------------------
# Phase 1 contracts: Agent System — monitors inject findings into context
# ---------------------------------------------------------------------------

class TestPhase1GatherAlwaysContextContract:
    """gather_always_context() returns str — monitors inject findings here."""

    def test_returns_str_with_seeded_data(self):
        """Always-context returns a non-empty string when data is present."""
        seed_location(location_name="Home", battery_pct=80)
        seed_timer(label="Laundry")
        seed_reminder(text="Buy milk")
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()

        assert isinstance(result, str)
        assert len(result) > 0
        # Monitors will append to this string; verify it contains injected data
        assert "Laundry" in result
        assert "Buy milk" in result
        assert "Home" in result

    def test_returns_str_empty_db(self):
        """Always-context returns a string even with empty database."""
        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()

        assert isinstance(result, str)
        assert "Current date and time:" in result


class TestPhase1HealthStorePatterns:
    """health_store.get_patterns(days=7) returns list[str]."""

    def test_returns_list_of_strings(self):
        """Pain entries on 3+ days produce pattern strings."""
        today = date.today()
        for i in range(3):
            d = (today - timedelta(days=i)).isoformat()
            seed_health(d, category="pain", description="back pain, lower",
                        severity=5)

        patterns = health_store.get_patterns(days=7)
        assert isinstance(patterns, list)
        assert len(patterns) >= 1
        for p in patterns:
            assert isinstance(p, str)
        # Should detect recurring back pain
        assert any("back pain" in p for p in patterns)

    def test_empty_returns_empty_list(self):
        """No entries returns empty list, not None."""
        patterns = health_store.get_patterns(days=7)
        assert isinstance(patterns, list)
        assert len(patterns) == 0

    def test_meal_patterns_detected(self):
        """Meal logging patterns are detected across days."""
        today = date.today()
        for i in range(5):
            d = (today - timedelta(days=i)).isoformat()
            seed_health(d, category="meal", description="test meal",
                        meal_type="lunch")

        patterns = health_store.get_patterns(days=7)
        assert any("meals logged" in p for p in patterns)


class TestPhase1NutritionDailyTotals:
    """nutrition_store.get_daily_totals() has ALL NUTRIENT_FIELDS as keys."""

    def test_all_33_fields_present(self):
        """Daily totals contain all 33 tracked nutrient fields."""
        today = date.today().isoformat()
        seed_nutrition(today, "Test chicken breast", meal_type="lunch",
                       calories=300, protein_g=40, dietary_fiber_g=0,
                       sodium_mg=200)

        totals = nutrition_store.get_daily_totals(today)

        assert isinstance(totals, dict)
        assert "item_count" in totals
        assert totals["item_count"] == 1

        for field in nutrition_store.NUTRIENT_FIELDS:
            assert field in totals, (
                f"NUTRIENT_FIELDS member '{field}' missing from get_daily_totals()"
            )

    def test_all_33_fields_count(self):
        """NUTRIENT_FIELDS has exactly 33 entries."""
        assert len(nutrition_store.NUTRIENT_FIELDS) == 33


class TestPhase1FitbitRestingHRHistory:
    """fitbit_store.get_resting_hr_history(days=7) returns list[int]."""

    def test_returns_list_of_ints(self):
        """Seeded Fitbit snapshots with HR data produce int list."""
        today = date.today()
        for i in range(1, 6):
            d = (today - timedelta(days=i)).isoformat()
            seed_fitbit_snapshot(d, {
                "heart_rate": {
                    "value": {
                        "restingHeartRate": 68 + i,
                        "heartRateZones": [],
                    },
                    "dateTime": d,
                },
            })

        hrs = fitbit_store.get_resting_hr_history(days=7)
        assert isinstance(hrs, list)
        assert len(hrs) == 5
        for hr in hrs:
            assert isinstance(hr, int), f"Expected int, got {type(hr)}: {hr}"

    def test_empty_returns_empty_list(self):
        """No snapshots returns empty list."""
        hrs = fitbit_store.get_resting_hr_history(days=7)
        assert isinstance(hrs, list)
        assert len(hrs) == 0


class TestPhase1FitbitActivitySummary:
    """fitbit_store.get_activity_summary() returns dict with expected keys."""

    def test_has_steps_and_calories_total(self):
        """Activity summary includes 'steps' and 'calories_total'."""
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {
            "activity": {
                "steps": 5000,
                "floors": 10,
                "distances": [
                    {"activity": "total", "distance": 3.5},
                ],
                "caloriesOut": 2100,
                "activityCalories": 500,
                "sedentaryMinutes": 600,
                "fairlyActiveMinutes": 20,
                "veryActiveMinutes": 10,
            },
        })

        summary = fitbit_store.get_activity_summary(today)
        assert isinstance(summary, dict)
        assert "steps" in summary
        assert "calories_total" in summary
        assert summary["steps"] == 5000
        assert summary["calories_total"] == 2100

    def test_returns_none_when_no_snapshot(self):
        """Returns None when no snapshot exists."""
        summary = fitbit_store.get_activity_summary("2020-01-01")
        assert summary is None


class TestPhase1LegalUpcomingDates:
    """legal_store.get_upcoming_dates() returns list[dict] with date, description."""

    def test_returns_list_with_required_keys(self):
        """Upcoming dates include 'date' and 'description' keys."""
        future_date = (date.today() + timedelta(days=30)).isoformat()
        seed_legal(entry_date=future_date, entry_type="court_date",
                   description="Hearing on motion to dismiss")

        upcoming = legal_store.get_upcoming_dates()
        assert isinstance(upcoming, list)
        assert len(upcoming) >= 1
        for entry in upcoming:
            assert isinstance(entry, dict)
            assert "date" in entry
            assert "description" in entry

    def test_filters_past_dates(self):
        """Past dates are excluded from upcoming."""
        past = (date.today() - timedelta(days=30)).isoformat()
        seed_legal(entry_date=past, entry_type="court_date",
                   description="Old hearing")

        upcoming = legal_store.get_upcoming_dates()
        assert len(upcoming) == 0

    def test_filters_non_date_types(self):
        """Only court_date and deadline types appear in upcoming."""
        future = (date.today() + timedelta(days=10)).isoformat()
        seed_legal(entry_date=future, entry_type="note",
                   description="Just a note about the case")

        upcoming = legal_store.get_upcoming_dates()
        assert len(upcoming) == 0


# ---------------------------------------------------------------------------
# Phase 2 contracts: Delivery Intelligence
# ---------------------------------------------------------------------------

class TestPhase2LocationLatest:
    """location_store.get_latest() returns dict or None."""

    def test_returns_dict_with_required_keys(self):
        """Latest location has 'location' and 'battery_pct' keys."""
        seed_location(location_name="Office", battery_pct=55)

        latest = location_store.get_latest()
        assert isinstance(latest, dict)
        assert "location" in latest
        assert "battery_pct" in latest
        assert latest["location"] == "Office"
        assert latest["battery_pct"] == 55

    def test_returns_none_empty_table(self):
        """Empty locations table returns None."""
        latest = location_store.get_latest()
        assert latest is None


class TestPhase2ExerciseState:
    """fitbit_store.get_exercise_state() returns dict or None."""

    def test_returns_none_when_no_exercise(self):
        """No active exercise session returns None."""
        state = fitbit_store.get_exercise_state()
        assert state is None

    @patch.object(fitbit_store.config, "OWNER_BIRTH_DATE", "1984-01-01")
    def test_returns_dict_when_active(self):
        """Active exercise session returns dict with expected shape."""
        fitbit_store.start_exercise("walking")
        state = fitbit_store.get_exercise_state()

        assert isinstance(state, dict)
        assert state.get("exercise_type") == "walking"
        assert "started_at" in state
        assert "target_zones" in state
        assert "resting_hr" in state
        assert "max_hr" in state

        # Clean up
        fitbit_store.end_exercise("test cleanup")


class TestPhase2DeliveryMetadata:
    """set_delivery ACTION sets metadata['delivery'] correctly."""

    def test_voice_delivery_sets_metadata(self):
        """set_delivery with method='voice' updates metadata dict."""
        resp = (
            'Sure, I\'ll use voice. '
            '<!--ACTION::{"action":"set_delivery","method":"voice"}-->'
        )
        metadata = {}
        actions.process_actions_sync(resp, metadata=metadata)
        assert metadata.get("delivery") == "voice"

    def test_sms_delivery_sets_metadata(self):
        """set_delivery with method='sms' updates metadata dict."""
        resp = (
            'Switching to text. '
            '<!--ACTION::{"action":"set_delivery","method":"sms"}-->'
        )
        metadata = {}
        actions.process_actions_sync(resp, metadata=metadata)
        assert metadata.get("delivery") == "sms"

    def test_default_delivery_sets_metadata(self):
        """set_delivery with method='default' also works."""
        resp = '<!--ACTION::{"action":"set_delivery","method":"default"}-->'
        metadata = {}
        actions.process_actions_sync(resp, metadata=metadata)
        assert metadata.get("delivery") == "default"


# ---------------------------------------------------------------------------
# Phase 3 contracts: Verification Pipeline
# ---------------------------------------------------------------------------

class TestPhase3ProcessActionsReturnType:
    """process_actions() returns ActionResult (Phase 3 implemented)."""

    def test_returns_action_result_no_actions(self):
        """Plain text with no actions returns ActionResult."""
        result = actions.process_actions_sync("Hello, I can help with that.")
        assert isinstance(result, actions.ActionResult)
        assert result.clean_response == "Hello, I can help with that."
        assert result.actions_found == []
        assert result.failures == []

    def test_returns_action_result_with_actions(self):
        """Text with valid actions returns ActionResult."""
        today = date.today().isoformat()
        resp = (
            'Done! '
            '<!--ACTION::{"action":"log_health","date":"' + today + '",'
            '"category":"meal","description":"test meal","meal_type":"lunch"}-->'
        )
        result = actions.process_actions_sync(resp)
        assert isinstance(result, actions.ActionResult)
        assert "log_health" in result.action_types

    def test_to_response_returns_str(self):
        """to_response() always returns str."""
        result = actions.process_actions_sync("Hello")
        assert isinstance(result.to_response(), str)

    def test_str_contains_compat(self):
        """ActionResult supports 'in' operator via __contains__."""
        result = actions.process_actions_sync("Hello, I can help!")
        assert "Hello" in result
        assert "ACTION" not in result


class TestPhase3ActionBlockStripping:
    """ACTION blocks are always fully stripped from returned text."""

    def test_single_action_stripped(self):
        """Single ACTION block is removed."""
        today = date.today().isoformat()
        resp = (
            'Got it! '
            '<!--ACTION::{"action":"log_health","date":"' + today + '",'
            '"category":"meal","description":"test","meal_type":"lunch"}-->'
            ' All done.'
        )
        result = actions.process_actions_sync(resp)
        assert "<!--ACTION" not in result
        assert "Got it!" in result

    def test_multiline_action_stripped(self):
        """Multiline ACTION block is removed."""
        today = date.today().isoformat()
        resp = (
            'Logged.\n'
            '<!--ACTION::{\n'
            '  "action": "log_health",\n'
            '  "date": "' + today + '",\n'
            '  "category": "meal",\n'
            '  "description": "test meal",\n'
            '  "meal_type": "lunch"\n'
            '}-->'
        )
        result = actions.process_actions_sync(resp)
        assert "<!--ACTION" not in result

    def test_partial_action_stripped(self):
        """Partial/truncated ACTION block is stripped."""
        resp = 'Logging <!--ACTION::{"action":"log_health","date":"2026-01-01"'
        result = actions.process_actions_sync(resp)
        assert "<!--ACTION" not in result

    def test_multiple_actions_stripped(self):
        """Multiple ACTION blocks are all stripped."""
        today = date.today().isoformat()
        resp = (
            'Done! '
            '<!--ACTION::{"action":"log_health","date":"' + today + '",'
            '"category":"meal","description":"egg","meal_type":"breakfast"}-->'
            ' And also '
            '<!--ACTION::{"action":"add_reminder","text":"buy eggs"}-->'
            ' All set.'
        )
        result = actions.process_actions_sync(resp)
        assert "<!--ACTION" not in result
        assert "Done!" in result
        assert "All set." in result


class TestPhase3ClaimWithoutAction:
    """Claim-without-action detection catches false claims."""

    def test_claim_logged_no_action(self):
        """'I've logged your meal' with no actions triggers system note."""
        resp = "I've logged your meal and tracked the nutrition data with calories, protein, and carbs."
        result = actions.process_actions_sync(resp)
        assert "System note" in result or "system note" in result.lower()
        assert "ACTION blocks" in result or "action" in result.lower()

    def test_no_claim_no_note(self):
        """Normal text without claims does not trigger system note."""
        resp = "The weather looks nice today. Here are some joke ideas for you."
        result = actions.process_actions_sync(resp)
        assert "System note" not in result


class TestPhase3ProcessActionsLogFn:
    """process_actions with log_fn: verify log_fn called on failures."""

    def test_log_fn_called_on_failure(self):
        """log_fn is called when an action fails."""
        log_calls = []

        def capture_log(text, status, **kwargs):
            log_calls.append({"text": text, "status": status, **kwargs})

        # Intentionally bad action — complete a nonexistent reminder
        resp = '<!--ACTION::{"action":"complete_reminder","id":"nonexistent"}-->'
        actions.process_actions_sync(resp, log_fn=capture_log)

        assert len(log_calls) >= 1
        assert any(c["status"] == "error" for c in log_calls)

    def test_log_fn_called_on_claim_without_action(self):
        """log_fn is called when claim-without-action is detected."""
        log_calls = []

        def capture_log(text, status, **kwargs):
            log_calls.append({"text": text, "status": status, **kwargs})

        resp = "I've saved your meal data. It has 500 calories, 30g protein, 20g fat, and 40g carbs."
        actions.process_actions_sync(resp, log_fn=capture_log)

        assert any(c["text"] == "CLAIM_WITHOUT_ACTION" for c in log_calls)

    def test_log_fn_not_called_on_success(self):
        """log_fn is NOT called when all actions succeed cleanly."""
        log_calls = []

        def capture_log(text, status, **kwargs):
            log_calls.append({"text": text, "status": status, **kwargs})

        resp = '<!--ACTION::{"action":"add_reminder","text":"test"}-->'
        actions.process_actions_sync(resp, log_fn=capture_log)

        # No errors, no claim-without-action — log_fn should not be called
        assert len(log_calls) == 0
