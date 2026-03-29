"""Tests for the destructive action confirmation gate.

Covers:
  - Destructive actions blocked without confirmed flag
  - Non-destructive actions pass through freely
  - Pending action storage, expiry, and cleanup
  - _describe_action() human-readable descriptions
  - execute_pending() executes stored action
  - confirm_destructive action type
  - to_response() includes confirmation prompt
  - _is_confirmation() and _is_cancellation() detection
  - _check_pending_confirmation() daemon shortcut
  - Context injection of pending actions

SAFETY: All store writes mocked. No real DB writes.
"""

import asyncio
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import actions
from actions import (
    process_actions, ActionResult, _DESTRUCTIVE_ACTIONS,
    _pending_confirmations, _cleanup_expired_pending,
    get_pending_confirmations, execute_pending, clear_all_pending,
    _describe_action, _PENDING_EXPIRY_SECONDS,
)
from session_pool import SessionResponse


@pytest.fixture(autouse=True)
def _clear_pending():
    """Ensure pending confirmations are clean before and after each test."""
    _pending_confirmations.clear()
    yield
    _pending_confirmations.clear()


# Standard mocks for all store operations
@pytest.fixture
def mock_stores():
    """Mock all data stores so process_actions doesn't hit real DB."""
    with (
        patch("actions.calendar_store") as cal,
        patch("actions.health_store") as health,
        patch("actions.vehicle_store") as vehicle,
        patch("actions.legal_store") as legal,
        patch("actions.nutrition_store") as nutr,
        patch("actions.timer_store") as timer,
        patch("actions.fitbit_store") as fitbit,
        patch("actions.redis_client") as redis,
        patch("actions.db") as db_mock,
    ):
        # Make calendar async methods return properly
        cal.add_event = AsyncMock()
        cal.modify_event = AsyncMock(return_value=True)
        cal.delete_event = AsyncMock(return_value=True)
        cal.add_reminder = MagicMock()
        cal.delete_reminder = MagicMock(return_value=True)
        cal.complete_reminder = MagicMock(return_value=True)
        health.add_entry = MagicMock(return_value={})
        health.delete_entry = MagicMock(return_value=True)
        vehicle.delete_entry = MagicMock(return_value=True)
        legal.delete_entry = MagicMock(return_value=True)
        nutr.add_item = MagicMock(return_value={})
        nutr.delete_item = MagicMock(return_value=True)
        timer.add_timer = MagicMock()
        timer.cancel_timer = MagicMock(return_value=True)
        fitbit.start_exercise = MagicMock()
        fitbit.end_exercise = MagicMock()
        redis.push_task = MagicMock(return_value=True)
        # db mock for _describe_action lookups
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        db_mock.get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db_mock.get_conn.return_value.__exit__ = MagicMock(return_value=False)
        yield {
            "cal": cal, "health": health, "vehicle": vehicle,
            "legal": legal, "nutr": nutr, "timer": timer,
            "fitbit": fitbit, "redis": redis, "db": db_mock,
        }


# ---------------------------------------------------------------------------
# Destructive actions are blocked
# ---------------------------------------------------------------------------

class TestDestructiveBlocking:
    @pytest.mark.asyncio
    async def test_delete_event_blocked(self, mock_stores):
        resp = 'OK <!--ACTION::{"action": "delete_event", "id": "abc123"}-->'
        result = await process_actions(resp)
        # Should NOT have called delete_event
        mock_stores["cal"].delete_event.assert_not_called()
        # Should have pending
        assert len(result.pending_destructive) == 1
        assert "abc123" in result.pending_destructive[0]["description"]

    @pytest.mark.asyncio
    async def test_delete_reminder_blocked(self, mock_stores):
        resp = 'OK <!--ACTION::{"action": "delete_reminder", "id": "rem1"}-->'
        result = await process_actions(resp)
        mock_stores["cal"].delete_reminder.assert_not_called()
        assert len(result.pending_destructive) == 1

    @pytest.mark.asyncio
    async def test_delete_health_entry_blocked(self, mock_stores):
        resp = 'OK <!--ACTION::{"action": "delete_health_entry", "id": "h1"}-->'
        result = await process_actions(resp)
        mock_stores["health"].delete_entry.assert_not_called()
        assert len(result.pending_destructive) == 1

    @pytest.mark.asyncio
    async def test_delete_nutrition_entry_blocked(self, mock_stores):
        resp = 'OK <!--ACTION::{"action": "delete_nutrition_entry", "id": "n1"}-->'
        result = await process_actions(resp)
        mock_stores["nutr"].delete_item.assert_not_called()
        assert len(result.pending_destructive) == 1


# ---------------------------------------------------------------------------
# Non-destructive actions pass through
# ---------------------------------------------------------------------------

class TestNonDestructivePassThrough:
    @pytest.mark.asyncio
    async def test_add_event_not_blocked(self, mock_stores):
        resp = '<!--ACTION::{"action": "add_event", "title": "Meeting", "date": "2026-04-01"}-->'
        result = await process_actions(resp)
        mock_stores["cal"].add_event.assert_called_once()
        assert len(result.pending_destructive) == 0

    @pytest.mark.asyncio
    async def test_log_health_not_blocked(self, mock_stores):
        resp = '<!--ACTION::{"action": "log_health", "date": "2026-03-29", "category": "pain", "description": "headache"}-->'
        result = await process_actions(resp)
        mock_stores["health"].add_entry.assert_called_once()
        assert len(result.pending_destructive) == 0

    @pytest.mark.asyncio
    async def test_set_timer_not_blocked(self, mock_stores):
        resp = '<!--ACTION::{"action": "set_timer", "label": "Laundry", "minutes": 30, "message": "Check laundry"}-->'
        result = await process_actions(resp)
        mock_stores["timer"].add_timer.assert_called_once()
        assert len(result.pending_destructive) == 0

    @pytest.mark.asyncio
    async def test_cancel_timer_not_blocked(self, mock_stores):
        resp = '<!--ACTION::{"action": "cancel_timer", "id": "t1"}-->'
        result = await process_actions(resp)
        mock_stores["timer"].cancel_timer.assert_called_once()
        assert len(result.pending_destructive) == 0

    @pytest.mark.asyncio
    async def test_modify_event_not_blocked(self, mock_stores):
        resp = '<!--ACTION::{"action": "modify_event", "id": "e1", "title": "New title"}-->'
        result = await process_actions(resp)
        mock_stores["cal"].modify_event.assert_called_once()
        assert len(result.pending_destructive) == 0

    @pytest.mark.asyncio
    async def test_send_email_not_blocked(self, mock_stores):
        """send_email is NOT gated — trusts prompt-level draft/confirm flow."""
        mock_client = MagicMock()
        mock_client.gmail_send_message = AsyncMock(return_value={"id": "msg1"})
        with patch.dict("sys.modules", {"google_client": MagicMock(get_client=MagicMock(return_value=mock_client))}):
            resp = '<!--ACTION::{"action": "send_email", "to": "x@y.com", "subject": "Hi", "body": "Hello"}-->'
            result = await process_actions(resp)
        assert len(result.pending_destructive) == 0


# ---------------------------------------------------------------------------
# Mixed actions: safe executed, destructive blocked
# ---------------------------------------------------------------------------

class TestMixedActions:
    @pytest.mark.asyncio
    async def test_safe_and_destructive_mixed(self, mock_stores):
        resp = (
            'Logging meal and deleting old entry '
            '<!--ACTION::{"action": "log_health", "date": "2026-03-29", "category": "meal", "description": "lunch"}-->'
            '<!--ACTION::{"action": "delete_health_entry", "id": "old1"}-->'
        )
        result = await process_actions(resp)
        # Safe action executed
        mock_stores["health"].add_entry.assert_called_once()
        # Destructive action blocked
        mock_stores["health"].delete_entry.assert_not_called()
        assert len(result.pending_destructive) == 1


# ---------------------------------------------------------------------------
# Pending action lifecycle
# ---------------------------------------------------------------------------

class TestPendingLifecycle:
    @pytest.mark.asyncio
    async def test_pending_stored_with_description(self, mock_stores):
        resp = '<!--ACTION::{"action": "delete_event", "id": "e1"}-->'
        await process_actions(resp)
        pending = get_pending_confirmations()
        assert len(pending) == 1
        assert "confirmation_id" in pending[0]
        assert "description" in pending[0]

    def test_pending_expires(self):
        _pending_confirmations["old"] = {
            "action": {"action": "delete_event", "id": "e1"},
            "created": time.time() - _PENDING_EXPIRY_SECONDS - 1,
            "description": "test",
        }
        pending = get_pending_confirmations()
        assert len(pending) == 0  # expired

    def test_clear_all_pending(self):
        _pending_confirmations["a"] = {"action": {}, "created": time.time(), "description": "x"}
        _pending_confirmations["b"] = {"action": {}, "created": time.time(), "description": "y"}
        clear_all_pending()
        assert len(_pending_confirmations) == 0


# ---------------------------------------------------------------------------
# execute_pending
# ---------------------------------------------------------------------------

class TestExecutePending:
    @pytest.mark.asyncio
    async def test_execute_delete_event(self):
        _pending_confirmations["c1"] = {
            "action": {"action": "delete_event", "id": "e1"},
            "created": time.time(),
            "description": "Delete calendar event: Test",
        }
        with patch("actions.calendar_store.delete_event",
                   new_callable=AsyncMock, return_value=True):
            ok, msg = await execute_pending("c1")
        assert ok is True
        assert "c1" not in _pending_confirmations  # consumed

    @pytest.mark.asyncio
    async def test_execute_expired(self):
        _pending_confirmations["c2"] = {
            "action": {"action": "delete_event", "id": "e1"},
            "created": time.time() - _PENDING_EXPIRY_SECONDS - 1,
            "description": "old",
        }
        ok, msg = await execute_pending("c2")
        assert ok is False
        assert "expired" in msg.lower()

    @pytest.mark.asyncio
    async def test_execute_missing_id(self):
        ok, msg = await execute_pending("nonexistent")
        assert ok is False

    @pytest.mark.asyncio
    async def test_execute_delete_health(self):
        _pending_confirmations["c3"] = {
            "action": {"action": "delete_health_entry", "id": "h1"},
            "created": time.time(),
            "description": "Delete health entry",
        }
        with patch("actions.health_store.delete_entry", return_value=True):
            ok, msg = await execute_pending("c3")
        assert ok is True

    @pytest.mark.asyncio
    async def test_execute_delete_nutrition(self):
        _pending_confirmations["c4"] = {
            "action": {"action": "delete_nutrition_entry", "id": "n1"},
            "created": time.time(),
            "description": "Delete nutrition entry",
        }
        with patch("actions.nutrition_store.delete_item", return_value=True):
            ok, msg = await execute_pending("c4")
        assert ok is True


# ---------------------------------------------------------------------------
# confirm_destructive action type
# ---------------------------------------------------------------------------

class TestConfirmDestructive:
    @pytest.mark.asyncio
    async def test_confirm_via_action_block(self, mock_stores):
        """ARIA emits confirm_destructive with a valid confirmation_id."""
        _pending_confirmations["c5"] = {
            "action": {"action": "delete_event", "id": "e1"},
            "created": time.time(),
            "description": "Delete calendar event: Meeting",
        }
        resp = '<!--ACTION::{"action": "confirm_destructive", "confirmation_id": "c5"}-->'
        result = await process_actions(resp)
        mock_stores["cal"].delete_event.assert_called_once_with("e1")
        assert len(result.failures) == 0

    @pytest.mark.asyncio
    async def test_confirm_invalid_id(self, mock_stores):
        resp = '<!--ACTION::{"action": "confirm_destructive", "confirmation_id": "bogus"}-->'
        result = await process_actions(resp)
        assert any("expired" in f.lower() or "not found" in f.lower()
                    for f in result.failures)


# ---------------------------------------------------------------------------
# to_response() includes confirmation prompt
# ---------------------------------------------------------------------------

class TestToResponseConfirmation:
    def test_pending_shows_confirmation(self):
        result = ActionResult(
            clean_response="Sure thing.",
            actions_found=[], action_types=[], failures=[], warnings=[],
            metadata={}, pending_destructive=[
                {"confirmation_id": "c1", "description": "Delete event: Birthday"}
            ],
        )
        resp = result.to_response()
        assert "Confirmation required" in resp
        assert "Birthday" in resp
        assert "yes" in resp.lower()

    def test_no_pending_no_confirmation(self):
        result = ActionResult(
            clean_response="Sure thing.",
            actions_found=[], action_types=[], failures=[], warnings=[],
            metadata={}, pending_destructive=[],
        )
        resp = result.to_response()
        assert "Confirmation required" not in resp


# ---------------------------------------------------------------------------
# _is_confirmation and _is_cancellation
# ---------------------------------------------------------------------------

class TestConfirmationDetection:
    def test_simple_confirmations(self):
        from daemon import _is_confirmation
        for phrase in ["yes", "Yeah", "do it", "go ahead", "confirm", "OK"]:
            assert _is_confirmation(phrase), f"'{phrase}' should be confirmation"

    def test_simple_confirmations_with_punctuation(self):
        from daemon import _is_confirmation
        assert _is_confirmation("yes!") is True
        assert _is_confirmation("go ahead.") is True

    def test_long_text_not_confirmation(self):
        from daemon import _is_confirmation
        assert _is_confirmation("yes and also set a timer for 30 minutes") is False

    def test_questions_not_confirmation(self):
        from daemon import _is_confirmation
        assert _is_confirmation("yes but what about the other one?") is False

    def test_cancellation_phrases(self):
        from daemon import _is_cancellation
        for phrase in ["no", "cancel", "don't", "never mind", "nope"]:
            assert _is_cancellation(phrase), f"'{phrase}' should be cancellation"

    def test_long_text_not_cancellation(self):
        from daemon import _is_cancellation
        assert _is_cancellation("no I meant delete the other event instead") is False


# ---------------------------------------------------------------------------
# _check_pending_confirmation daemon shortcut
# ---------------------------------------------------------------------------

class TestCheckPendingConfirmation:
    @pytest.mark.asyncio
    async def test_no_pending_returns_none(self):
        from daemon import _check_pending_confirmation
        result = await _check_pending_confirmation("yes")
        assert result is None

    @pytest.mark.asyncio
    async def test_confirmation_executes_pending(self):
        from daemon import _check_pending_confirmation
        _pending_confirmations["c6"] = {
            "action": {"action": "delete_reminder", "id": "r1"},
            "created": time.time(),
            "description": "Delete reminder: Buy groceries",
        }
        with patch("actions.calendar_store.delete_reminder", return_value=True):
            result = await _check_pending_confirmation("yes")
        assert result is not None
        assert "Done" in result.text
        assert "Buy groceries" in result.text

    @pytest.mark.asyncio
    async def test_cancellation_clears_pending(self):
        from daemon import _check_pending_confirmation
        _pending_confirmations["c7"] = {
            "action": {"action": "delete_event", "id": "e1"},
            "created": time.time(),
            "description": "Delete event: Test",
        }
        result = await _check_pending_confirmation("cancel")
        assert result is not None
        assert "Cancelled" in result.text
        assert len(_pending_confirmations) == 0

    @pytest.mark.asyncio
    async def test_non_confirmation_returns_none(self):
        from daemon import _check_pending_confirmation
        _pending_confirmations["c8"] = {
            "action": {"action": "delete_event", "id": "e1"},
            "created": time.time(),
            "description": "Delete event: Test",
        }
        result = await _check_pending_confirmation("what time is my appointment?")
        assert result is None  # Not a confirmation, let ARIA handle it
        assert len(_pending_confirmations) == 1  # Still pending


# ---------------------------------------------------------------------------
# _DESTRUCTIVE_ACTIONS coverage check
# ---------------------------------------------------------------------------

class TestDestructiveActionsSet:
    def test_expected_actions_in_set(self):
        expected = {
            "delete_event", "delete_reminder", "delete_health_entry",
            "delete_vehicle_entry", "delete_legal_entry",
            "delete_nutrition_entry", "trash_email",
        }
        assert expected == _DESTRUCTIVE_ACTIONS

    def test_send_email_not_in_set(self):
        assert "send_email" not in _DESTRUCTIVE_ACTIONS

    def test_modify_event_not_in_set(self):
        assert "modify_event" not in _DESTRUCTIVE_ACTIONS

    def test_cancel_timer_not_in_set(self):
        assert "cancel_timer" not in _DESTRUCTIVE_ACTIONS

    def test_complete_reminder_not_in_set(self):
        assert "complete_reminder" not in _DESTRUCTIVE_ACTIONS
