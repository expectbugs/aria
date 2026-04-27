"""End-to-end tests for The Beckaning — multi-user SMS auth, cross-user writes,
permission rejection, delivery engine Becky branch, session pool registry.

SAFETY: these tests mock all external I/O. No Telnyx SMS, no subprocesses,
no real DB. They verify user_key plumbing and routing logic only.
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import actions
import config
import delivery_engine
import session_pool


# ---------------------------------------------------------------------------
# Fixture: ensure TRUSTED_USERS is populated for these tests regardless of
# what the host config.py contains. We patch on module attrs so other tests
# aren't affected.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _trusted_users_fixture():
    adam_phone = "+12624759698"
    becky_phone = "+14144602552"
    patches = [
        patch.object(config, "OWNER_PHONE_NUMBER", adam_phone),
        patch.object(config, "OWNER_NAME", "Adam"),
        patch.object(config, "BECKY_PHONE_NUMBER", becky_phone),
        patch.object(config, "BECKY_NAME", "Becky"),
        patch.object(config, "TRUSTED_USERS", {
            adam_phone: {"user": "adam", "name": "Adam", "role": "owner"},
            becky_phone: {"user": "becky", "name": "Becky", "role": "trusted"},
        }),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


@pytest.fixture(autouse=True)
def _clear_pending():
    actions._pending_confirmations.clear()
    yield
    actions._pending_confirmations.clear()


@pytest.fixture(autouse=True)
def _clear_session_registry():
    session_pool._SESSION_POOLS.clear()
    yield
    session_pool._SESSION_POOLS.clear()


# ---------------------------------------------------------------------------
# Authorization gate: webhook_sms lookup
# ---------------------------------------------------------------------------

class TestAuthGate:
    def test_trusted_users_contains_both(self):
        assert "+12624759698" in config.TRUSTED_USERS
        assert "+14144602552" in config.TRUSTED_USERS
        assert config.TRUSTED_USERS["+12624759698"]["user"] == "adam"
        assert config.TRUSTED_USERS["+14144602552"]["user"] == "becky"

    def test_unknown_phone_not_in_registry(self):
        assert "+15551234567" not in config.TRUSTED_USERS


# ---------------------------------------------------------------------------
# Session pool registry isolation
# ---------------------------------------------------------------------------

class TestSessionPoolIsolation:
    def test_adam_and_becky_get_different_pools(self):
        with patch("system_prompt.build_primary_prompt", return_value="adam prompt"), \
             patch("system_prompt.build_becky_primary_prompt",
                   return_value="becky prompt"):
            p_adam = session_pool.get_session_pool("adam")
            p_becky = session_pool.get_session_pool("becky")
        assert p_adam is not p_becky
        assert p_adam.user_key == "adam"
        assert p_becky.user_key == "becky"

    def test_becky_pool_has_no_fast_session(self):
        # Becky's config uses BECKY_SESSION_FAST_EFFORT = None
        with patch("system_prompt.build_becky_primary_prompt",
                   return_value="becky prompt"), \
             patch.object(config, "BECKY_SESSION_FAST_EFFORT", None):
            p_becky = session_pool.get_session_pool("becky")
        assert p_becky._fast is None

    def test_unknown_user_raises(self):
        with pytest.raises(ValueError, match="Unknown user_key"):
            session_pool.get_session_pool("alice")


# ---------------------------------------------------------------------------
# Per-user pending confirmations
# ---------------------------------------------------------------------------

class TestPendingConfirmationsMultiuser:
    def test_adam_pending_doesnt_leak_to_becky(self):
        import time as _time
        actions._pending_confirmations["abc"] = {
            "user_key": "adam",
            "action": {"action": "delete_event", "id": "x"},
            "created": _time.time(), "description": "Delete event X",
        }
        adam_pending = actions.get_pending_confirmations(user_key="adam")
        becky_pending = actions.get_pending_confirmations(user_key="becky")
        assert len(adam_pending) == 1
        assert len(becky_pending) == 0

    def test_becky_pending_doesnt_leak_to_adam(self):
        import time as _time
        actions._pending_confirmations["xyz"] = {
            "user_key": "becky",
            "action": {"action": "delete_reminder", "id": "y"},
            "created": _time.time(), "description": "Delete reminder Y",
        }
        adam_pending = actions.get_pending_confirmations(user_key="adam")
        becky_pending = actions.get_pending_confirmations(user_key="becky")
        assert len(adam_pending) == 0
        assert len(becky_pending) == 1

    def test_clear_all_pending_scoped_by_user(self):
        import time as _time
        actions._pending_confirmations["a1"] = {
            "user_key": "adam", "action": {"action": "delete_event", "id": "e1"},
            "created": _time.time(), "description": "A1",
        }
        actions._pending_confirmations["b1"] = {
            "user_key": "becky", "action": {"action": "delete_reminder", "id": "r1"},
            "created": _time.time(), "description": "B1",
        }
        actions.clear_all_pending(user_key="adam")
        assert "a1" not in actions._pending_confirmations
        assert "b1" in actions._pending_confirmations


# ---------------------------------------------------------------------------
# Per-user cancel_destructive — symmetric to TestPendingConfirmationsMultiuser
# but exercising the new ARIA-emitted cancel path.
# ---------------------------------------------------------------------------

class TestCancelMultiuser:
    def setup_method(self):
        actions._pending_confirmations.clear()

    def teardown_method(self):
        actions._pending_confirmations.clear()

    def test_becky_cannot_cancel_adams_pending_by_id(self):
        """Defense-in-depth: Becky guessing Adam's id gets rejected and the
        pending entry is restored to the dict (not silently lost)."""
        import time as _time
        actions._pending_confirmations["adam_a1"] = {
            "user_key": "adam",
            "action": {"action": "delete_event", "id": "e1"},
            "created": _time.time(),
            "description": "Delete event: Doctor",
        }
        ok, msg = actions.cancel_pending("adam_a1", user_key="becky")
        assert ok is False
        assert "belongs to another user" in msg
        # Entry MUST be restored — losing it would let an attacker silently
        # disrupt the other user's confirmation flow.
        assert "adam_a1" in actions._pending_confirmations

    def test_cancel_all_scoped_for_becky(self):
        """cancel_all_pending(user_key='becky') leaves Adam's pending intact."""
        import time as _time
        actions._pending_confirmations["adam_a2"] = {
            "user_key": "adam",
            "action": {"action": "delete_event", "id": "e2"},
            "created": _time.time(),
            "description": "Delete event: Adam thing",
        }
        actions._pending_confirmations["becky_b2"] = {
            "user_key": "becky",
            "action": {"action": "delete_reminder", "id": "r2"},
            "created": _time.time(),
            "description": "Delete reminder: Becky thing",
        }
        result = actions.cancel_all_pending(user_key="becky")
        assert result == {
            "cancelled": ["Delete reminder: Becky thing"],
            "failed": [],
        }
        assert "adam_a2" in actions._pending_confirmations
        assert "becky_b2" not in actions._pending_confirmations

    def test_becky_can_cancel_her_own_pending(self):
        """Symmetric positive case — same user_key clears successfully."""
        import time as _time
        actions._pending_confirmations["becky_b3"] = {
            "user_key": "becky",
            "action": {"action": "delete_reminder", "id": "r3"},
            "created": _time.time(),
            "description": "Delete reminder: Becky's grocery list",
        }
        ok, msg = actions.cancel_pending("becky_b3", user_key="becky")
        assert ok is True
        assert msg == "Delete reminder: Becky's grocery list"
        assert "becky_b3" not in actions._pending_confirmations


# ---------------------------------------------------------------------------
# Permission gate: Becky can't emit Adam-exclusive writes
# ---------------------------------------------------------------------------

class TestPermissionGate:
    @pytest.mark.asyncio
    async def test_becky_cannot_log_health(self):
        response = (
            'I logged that for you.'
            '<!--ACTION::{"action":"log_health","date":"2026-04-18",'
            '"category":"meal","description":"eggs","meal_type":"breakfast"}-->'
        )
        with patch("actions.health_store") as mock_hs:
            result = await actions.process_actions(response, user_key="becky")
        assert any("becky can't emit" in f.lower() for f in result.failures)
        mock_hs.add_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_becky_cannot_log_nutrition(self):
        response = (
            '<!--ACTION::{"action":"log_nutrition","date":"2026-04-18",'
            '"food_name":"eggs","meal_type":"breakfast","nutrients":{}}-->'
        )
        with patch("actions.nutrition_store") as mock_ns:
            result = await actions.process_actions(response, user_key="becky")
        assert any("becky can't emit" in f.lower() for f in result.failures)
        mock_ns.add_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_becky_cannot_send_email(self):
        response = (
            '<!--ACTION::{"action":"send_email","to":"x@y.com",'
            '"subject":"s","body":"b"}-->'
        )
        result = await actions.process_actions(response, user_key="becky")
        assert any("becky can't emit" in f.lower() for f in result.failures)

    @pytest.mark.asyncio
    async def test_adam_can_still_log_health(self):
        response = (
            '<!--ACTION::{"action":"log_health","date":"2026-04-18",'
            '"category":"meal","description":"eggs","meal_type":"breakfast"}-->'
        )
        with patch("actions.health_store") as mock_hs:
            mock_hs.add_entry = MagicMock(return_value={})
            result = await actions.process_actions(response, user_key="adam")
        # No permission-denied failure
        assert not any("adam can't emit" in f.lower() for f in result.failures)


# ---------------------------------------------------------------------------
# Cross-user writes trigger consolidated notification
# ---------------------------------------------------------------------------

class TestCrossUserNotification:
    @pytest.mark.asyncio
    async def test_becky_adds_to_adam_reminders_notifies_him(self):
        response = (
            '<!--ACTION::{"action":"add_reminder","text":"pick up milk",'
            '"due":"2026-04-19","owner":"adam"}-->'
        )
        with patch("actions.calendar_store") as mock_cs, \
             patch("sms.send_long_sms") as mock_sms:
            mock_cs.add_reminder = MagicMock(return_value={"id": "abc"})
            await actions.process_actions(response, user_key="becky")

        # calendar write happened with owner=adam
        mock_cs.add_reminder.assert_called_once()
        assert mock_cs.add_reminder.call_args.kwargs["owner"] == "adam"
        # And Adam got notified
        mock_sms.assert_called_once()
        notify_phone, body = mock_sms.call_args[0][:2]
        assert notify_phone == config.OWNER_PHONE_NUMBER
        assert "Becky added" in body
        assert "pick up milk" in body

    @pytest.mark.asyncio
    async def test_becky_solo_write_doesnt_notify_adam(self):
        response = (
            '<!--ACTION::{"action":"add_reminder","text":"my own thing",'
            '"due":"2026-04-19"}-->'
        )
        with patch("actions.calendar_store") as mock_cs, \
             patch("sms.send_long_sms") as mock_sms:
            mock_cs.add_reminder = MagicMock(return_value={"id": "abc"})
            await actions.process_actions(response, user_key="becky")
        # Owner defaulted to becky, so no cross-user notification
        assert mock_cs.add_reminder.call_args.kwargs["owner"] == "becky"
        mock_sms.assert_not_called()

    @pytest.mark.asyncio
    async def test_consolidated_multiple_writes(self):
        response = (
            '<!--ACTION::{"action":"add_reminder","text":"thing 1","owner":"adam"}-->'
            '<!--ACTION::{"action":"add_reminder","text":"thing 2","owner":"adam"}-->'
        )
        with patch("actions.calendar_store") as mock_cs, \
             patch("sms.send_long_sms") as mock_sms:
            mock_cs.add_reminder = MagicMock(return_value={"id": "abc"})
            await actions.process_actions(response, user_key="becky")
        # One consolidated SMS for both writes
        assert mock_sms.call_count == 1
        body = mock_sms.call_args[0][1]
        assert "Becky added 2 things" in body
        assert "thing 1" in body
        assert "thing 2" in body


# ---------------------------------------------------------------------------
# Relay action handlers
# ---------------------------------------------------------------------------

class TestRelayActions:
    @pytest.mark.asyncio
    async def test_relay_to_adam_sms(self):
        response = (
            '<!--ACTION::{"action":"relay_to_adam","method":"sms",'
            '"body":"She will be late, maybe 7:30"}-->'
        )
        with patch("sms.send_long_sms") as mock_sms:
            result = await actions.process_actions(response, user_key="becky")
        # Exactly one SMS (the relay itself — no cross-user writes list for plain SMS)
        mock_sms.assert_called_once()
        target, body = mock_sms.call_args[0][:2]
        assert target == config.OWNER_PHONE_NUMBER
        assert "Becky:" in body or "Becky " in body
        assert "7:30" in body

    @pytest.mark.asyncio
    async def test_relay_to_adam_rejected_for_adam_requester(self):
        response = (
            '<!--ACTION::{"action":"relay_to_adam","method":"sms","body":"loop"}-->'
        )
        result = await actions.process_actions(response, user_key="adam")
        assert any("requires requester=becky" in f for f in result.failures)

    @pytest.mark.asyncio
    async def test_relay_to_adam_reminder_consolidates(self):
        response = (
            '<!--ACTION::{"action":"relay_to_adam","method":"reminder",'
            '"body":"grab stuff","due":"2026-04-20"}-->'
        )
        with patch("actions.calendar_store") as mock_cs, \
             patch("sms.send_long_sms") as mock_sms:
            mock_cs.add_reminder = MagicMock(return_value={"id": "rem1"})
            await actions.process_actions(response, user_key="becky")
        mock_cs.add_reminder.assert_called_once()
        assert mock_cs.add_reminder.call_args.kwargs["owner"] == "adam"
        # Notification consolidation: exactly one SMS with the summary
        assert mock_sms.call_count == 1
        assert "Reminder: grab stuff" in mock_sms.call_args[0][1]


# ---------------------------------------------------------------------------
# Delivery engine: Becky branch
# ---------------------------------------------------------------------------

class TestDeliveryEngineBecky:
    @pytest.mark.asyncio
    async def test_becky_sms_only(self):
        with patch("sms.send_long_sms") as mock_sms:
            result = await delivery_engine.execute_delivery(
                "hi becky", source="sms", user_key="becky",
            )
        mock_sms.assert_called_once()
        target = mock_sms.call_args[0][0]
        assert target == config.BECKY_PHONE_NUMBER
        assert result["method"] == "sms"

    @pytest.mark.asyncio
    async def test_becky_image_hint_respected(self):
        with patch("sms.send_long_sms") as mock_sms:
            result = await delivery_engine.execute_delivery(
                "here's the image", source="sms", hint="image",
                user_key="becky",
            )
        assert result["method"] == "image"

    @pytest.mark.asyncio
    async def test_becky_voice_hint_collapsed_to_sms(self):
        with patch("sms.send_long_sms") as mock_sms:
            result = await delivery_engine.execute_delivery(
                "voice plz", source="sms", hint="voice",
                user_key="becky",
            )
        # Voice is not respected for Becky — SMS delivered
        assert result["method"] == "sms"
        mock_sms.assert_called_once()


# ---------------------------------------------------------------------------
# conversation_history per-user filtering
# ---------------------------------------------------------------------------

class TestConversationHistoryPerUser:
    def test_adam_filter_excludes_becky_sms(self):
        import conversation_history
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch("conversation_history.db.get_conn") as mock_gc:
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            conversation_history.get_recent_turns(n=5, user_key="adam")
        sql = mock_conn.execute.call_args[0][0]
        assert "NOT LIKE" in sql

    def test_becky_filter_only_her_sms(self):
        import conversation_history
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        with patch("conversation_history.db.get_conn") as mock_gc:
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            conversation_history.get_recent_turns(n=5, user_key="becky")
        sql = mock_conn.execute.call_args[0][0]
        assert "input LIKE" in sql


# ---------------------------------------------------------------------------
# Store owner-awareness
# ---------------------------------------------------------------------------

class TestStoreOwnerAwareness:
    def test_add_reminder_defaults_adam(self):
        import calendar_store
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {
            "id": "x", "text": "t", "due": None, "recurring": None,
            "location": None, "location_trigger": None, "done": False,
            "completed_at": None, "auto_expired_at": None, "owner": "adam",
            "created": None,
        }
        with patch("calendar_store.db.get_conn") as mock_gc:
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            calendar_store.add_reminder(text="t")
        # SQL should include owner column
        sql = mock_conn.execute.call_args[0][0]
        params = mock_conn.execute.call_args[0][1]
        assert "owner" in sql
        assert "adam" in params  # inserted as owner

    def test_add_timer_with_owner_becky(self):
        import timer_store
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {
            "id": "x", "label": "t", "fire_at": None, "delivery": "sms",
            "priority": "gentle", "message": "", "source": "user",
            "status": "pending", "owner": "becky", "created": None,
            "fired_at": None, "cancelled_at": None,
        }
        with patch("timer_store.db.get_conn") as mock_gc:
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            timer_store.add_timer(label="t", fire_at="x", owner="becky")
        params = mock_conn.execute.call_args[0][1]
        assert "becky" in params


# ---------------------------------------------------------------------------
# v0.9.6 — Multi-confirmation system
# ---------------------------------------------------------------------------

def _seed_pending(user_key: str, action: dict, description: str,
                  conf_id: str | None = None) -> str:
    """Helper: add a pending destructive entry and return its confirmation_id."""
    import time as _time
    import uuid as _uuid
    conf_id = conf_id or str(_uuid.uuid4())[:8]
    actions._pending_confirmations[conf_id] = {
        "user_key": user_key,
        "action": action,
        "created": _time.time(),
        "description": description,
    }
    return conf_id


class TestMultiConfirmation:
    @pytest.mark.asyncio
    async def test_all_succeed(self):
        """Single 'yes' confirms 3 pending, all succeed → summary lists all three."""
        _seed_pending("adam", {"action": "delete_event", "id": "e1"},
                      "Delete event 'Dentist' on 2026-04-20")
        _seed_pending("adam", {"action": "delete_reminder", "id": "r1"},
                      "Delete reminder 'milk'")
        _seed_pending("adam", {"action": "delete_event", "id": "e2"},
                      "Delete event 'Lunch' on 2026-04-21")

        with patch("calendar_store.delete_event",
                   new_callable=AsyncMock, return_value=True), \
             patch("calendar_store.delete_reminder", return_value=True):
            result = await actions.execute_all_pending(user_key="adam")

        assert len(result["executed"]) == 3
        assert len(result["failed"]) == 0
        assert actions._pending_confirmations == {}

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        """One delete target not found; others succeed. Summary separates."""
        _seed_pending("adam", {"action": "delete_event", "id": "e_ok"},
                      "Delete event OK")
        _seed_pending("adam", {"action": "delete_event", "id": "e_gone"},
                      "Delete event GONE")

        async def _mock_delete(aid):
            return aid == "e_ok"

        with patch("calendar_store.delete_event", side_effect=_mock_delete):
            result = await actions.execute_all_pending(user_key="adam")

        assert len(result["executed"]) == 1
        assert "Delete event OK" in result["executed"][0]
        assert len(result["failed"]) == 1
        assert "GONE" in result["failed"][0][0]

    @pytest.mark.asyncio
    async def test_cancel_all_via_daemon(self):
        """Daemon _check_pending_confirmation on 'no' clears ALL this user's pending."""
        from daemon import _check_pending_confirmation
        _seed_pending("adam", {"action": "delete_event", "id": "e1"}, "D1")
        _seed_pending("adam", {"action": "delete_event", "id": "e2"}, "D2")
        _seed_pending("becky", {"action": "delete_reminder", "id": "r1"}, "B1")

        response = await _check_pending_confirmation("no", user_key="adam")

        assert response is not None
        assert "2 pending actions" in response.text
        # Adam's cleared; Becky's untouched
        remaining_keys = {v.get("user_key") for v in
                          actions._pending_confirmations.values()}
        assert "adam" not in remaining_keys
        assert "becky" in remaining_keys

    @pytest.mark.asyncio
    async def test_confirm_all_via_daemon_single_item(self):
        """Backward compat: single pending, single 'yes' → single-item summary."""
        from daemon import _check_pending_confirmation
        _seed_pending("adam", {"action": "delete_event", "id": "e1"},
                      "Delete event X")
        with patch("calendar_store.delete_event",
                   new_callable=AsyncMock, return_value=True):
            response = await _check_pending_confirmation("yes", user_key="adam")
        assert response is not None
        assert "Done — Delete event X." == response.text

    @pytest.mark.asyncio
    async def test_confirm_all_via_daemon_multi_item(self):
        """3 pending, single 'yes' → multi-item summary."""
        from daemon import _check_pending_confirmation
        _seed_pending("adam", {"action": "delete_event", "id": "e1"}, "D1")
        _seed_pending("adam", {"action": "delete_event", "id": "e2"}, "D2")
        _seed_pending("adam", {"action": "delete_event", "id": "e3"}, "D3")
        with patch("calendar_store.delete_event",
                   new_callable=AsyncMock, return_value=True):
            response = await _check_pending_confirmation("yes", user_key="adam")
        assert response is not None
        assert "3 things" in response.text
        assert "D1" in response.text and "D2" in response.text and "D3" in response.text

    @pytest.mark.asyncio
    async def test_empty_pending_returns_none(self):
        """'yes' with no pending → None (let ARIA handle as normal message)."""
        from daemon import _check_pending_confirmation
        response = await _check_pending_confirmation("yes", user_key="adam")
        assert response is None

    @pytest.mark.asyncio
    async def test_cross_user_delete_notification(self):
        """Becky confirms deletes of 2 Adam-owned events → Adam gets ONE SMS."""
        _seed_pending("becky", {"action": "delete_event", "id": "e1"},
                      "Delete event 'Dentist'")
        _seed_pending("becky", {"action": "delete_event", "id": "e2"},
                      "Delete event 'Lunch'")

        # _get_target_owner queries the DB. Mock to return adam for both.
        async def _mock_owner(action):
            return "adam"

        with patch("actions._get_target_owner", side_effect=_mock_owner), \
             patch("calendar_store.delete_event",
                   new_callable=AsyncMock, return_value=True), \
             patch("sms.send_long_sms") as mock_sms:
            result = await actions.execute_all_pending(user_key="becky")

        assert len(result["executed"]) == 2
        assert "adam" in result["cross_user"]
        assert len(result["cross_user"]["adam"]) == 2
        # ONE SMS, summarizing both
        mock_sms.assert_called_once()
        target, body = mock_sms.call_args[0][:2]
        assert target == config.OWNER_PHONE_NUMBER
        assert "Becky removed 2 things" in body
        assert "Dentist" in body and "Lunch" in body

    @pytest.mark.asyncio
    async def test_cross_user_delete_not_triggered_for_own(self):
        """Becky deletes her OWN reminder → no SMS to Adam."""
        _seed_pending("becky", {"action": "delete_reminder", "id": "r1"},
                      "Delete reminder 'call mom'")

        async def _mock_owner(action):
            return "becky"

        with patch("actions._get_target_owner", side_effect=_mock_owner), \
             patch("calendar_store.delete_reminder", return_value=True), \
             patch("sms.send_long_sms") as mock_sms:
            result = await actions.execute_all_pending(user_key="becky")

        assert len(result["executed"]) == 1
        assert result["cross_user"] == {}
        mock_sms.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_pending_user_key_mismatch_rejects(self):
        """execute_pending with user_key defense-in-depth: refuses if mismatch."""
        conf_id = _seed_pending("adam", {"action": "delete_event", "id": "e1"},
                                 "Delete X")
        ok, msg = await actions.execute_pending(conf_id, user_key="becky")
        assert ok is False
        assert "another user" in msg
        # Entry should NOT be consumed
        assert conf_id in actions._pending_confirmations

    @pytest.mark.asyncio
    async def test_confirm_destructive_all_action(self):
        """confirm_destructive with confirmation_id='all' triggers batch."""
        _seed_pending("adam", {"action": "delete_event", "id": "e1"}, "D1")
        _seed_pending("adam", {"action": "delete_event", "id": "e2"}, "D2")
        response = (
            '<!--ACTION::{"action":"confirm_destructive","confirmation_id":"all"}-->'
        )
        with patch("calendar_store.delete_event",
                   new_callable=AsyncMock, return_value=True):
            result = await actions.process_actions(response, user_key="adam")
        # Both pending should be gone
        assert len(actions._pending_confirmations) == 0
        assert not result.failures


# ---------------------------------------------------------------------------
# B3 fix: symmetric cross-user write notification (Adam → Becky direction)
# ---------------------------------------------------------------------------

class TestSymmetricCrossUserWrites:
    @pytest.mark.asyncio
    async def test_adam_adds_to_becky_reminder_notifies_her(self):
        response = (
            '<!--ACTION::{"action":"add_reminder","text":"call her mom",'
            '"due":"2026-04-25","owner":"becky"}-->'
        )
        with patch("actions.calendar_store") as mock_cs, \
             patch("sms.send_long_sms") as mock_sms:
            mock_cs.add_reminder = MagicMock(return_value={"id": "abc"})
            await actions.process_actions(response, user_key="adam")

        # Becky notified
        mock_sms.assert_called_once()
        target, body = mock_sms.call_args[0][:2]
        assert target == config.BECKY_PHONE_NUMBER
        assert "Adam added" in body
        assert "call her mom" in body

    @pytest.mark.asyncio
    async def test_adam_adds_to_becky_event_notifies_her(self):
        response = (
            '<!--ACTION::{"action":"add_event","title":"Dinner at Odd Duck",'
            '"date":"2026-04-25","time":"19:00","owner":"becky"}-->'
        )
        with patch("actions.calendar_store") as mock_cs, \
             patch("sms.send_long_sms") as mock_sms:
            mock_cs.add_event = AsyncMock(return_value={"id": "abc"})
            await actions.process_actions(response, user_key="adam")

        mock_sms.assert_called_once()
        target, body = mock_sms.call_args[0][:2]
        assert target == config.BECKY_PHONE_NUMBER
        assert "Adam added" in body
        assert "Dinner at Odd Duck" in body


# ---------------------------------------------------------------------------
# Parameterized: all 12 Adam-exclusive actions rejected for Becky
# ---------------------------------------------------------------------------

class TestAdamExclusiveRejection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("action_json,mock_target", [
        ('{"action":"log_health","date":"2026-04-18","category":"meal",'
         '"description":"eggs","meal_type":"breakfast"}',
         "actions.health_store"),
        ('{"action":"log_nutrition","date":"2026-04-18","food_name":"eggs",'
         '"meal_type":"breakfast","nutrients":{}}',
         "actions.nutrition_store"),
        ('{"action":"log_vehicle","date":"2026-04-18","event_type":"oil_change",'
         '"description":"oil"}',
         "actions.vehicle_store"),
        ('{"action":"log_legal","date":"2026-04-18","entry_type":"note",'
         '"description":"call lawyer"}',
         "actions.legal_store"),
        ('{"action":"start_exercise","exercise_type":"walking"}',
         "actions.fitbit_store"),
        ('{"action":"end_exercise"}',
         "actions.fitbit_store"),
        ('{"action":"send_email","to":"x@y.com","subject":"s","body":"b"}',
         None),
        ('{"action":"trash_email","email_id":"abc123"}',
         None),
        ('{"action":"watch_email","sender_pattern":"foo"}',
         None),
        ('{"action":"cancel_watch","id":"1"}',
         None),
        ('{"action":"delete_health_entry","id":"h1"}',
         "actions.health_store"),
        ('{"action":"delete_nutrition_entry","id":"n1"}',
         "actions.nutrition_store"),
        ('{"action":"delete_vehicle_entry","id":"v1"}',
         "actions.vehicle_store"),
        ('{"action":"delete_legal_entry","id":"l1"}',
         "actions.legal_store"),
    ])
    async def test_becky_rejected(self, action_json, mock_target):
        response = f'<!--ACTION::{action_json}-->'
        if mock_target:
            with patch(mock_target) as mock_store:
                result = await actions.process_actions(response, user_key="becky")
                # Verify no writes happened
                for name in ("add_entry", "add_item", "delete_entry",
                             "delete_item", "start_exercise", "end_exercise"):
                    method = getattr(mock_store, name, None)
                    if method is not None and hasattr(method, "assert_not_called"):
                        method.assert_not_called()
        else:
            result = await actions.process_actions(response, user_key="becky")
        # Permission denied surfaced as failure
        assert any("becky can't emit" in f.lower() for f in result.failures), \
            f"expected permission-denied for {action_json}; failures: {result.failures}"


# ---------------------------------------------------------------------------
# B1 fix: Becky image hint renders MMS
# ---------------------------------------------------------------------------

class TestBeckyImageHintRendersMMS:
    @pytest.mark.asyncio
    async def test_image_hint_triggers_render_and_mms(self):
        with patch("sms._render_sms_image", return_value="/tmp/fake.png") as mock_render, \
             patch("sms.send_image_mms") as mock_mms, \
             patch("os.unlink"):
            result = await delivery_engine.execute_delivery(
                "here's your chart", source="sms", hint="image",
                user_key="becky",
            )
        mock_render.assert_called_once_with("here's your chart", header="ARIA")
        mock_mms.assert_called_once()
        assert mock_mms.call_args[0][0] == config.BECKY_PHONE_NUMBER
        assert result["method"] == "image"

    @pytest.mark.asyncio
    async def test_image_hint_falls_back_to_sms_on_error(self):
        with patch("sms._render_sms_image", side_effect=Exception("render fail")), \
             patch("sms.send_long_sms") as mock_sms:
            result = await delivery_engine.execute_delivery(
                "text here", source="sms", hint="image", user_key="becky",
            )
        mock_sms.assert_called_once()
        assert result["method"] == "sms"


# ---------------------------------------------------------------------------
# B4 fix: --user flag errors on Adam-only subcommands
# ---------------------------------------------------------------------------

class TestQueryUserFlagRemoval:
    def test_user_flag_rejected_on_health(self):
        import query
        with pytest.raises(SystemExit):
            query.main(["health", "--user", "becky"])

    def test_user_flag_rejected_on_email(self):
        import query
        with pytest.raises(SystemExit):
            query.main(["email", "--user", "becky", "--search", "x"])

    def test_user_flag_works_on_calendar(self):
        import query
        with patch("query.calendar_store.get_events", return_value=[]):
            with patch("query._log_trace"):
                # Should not raise
                query.main(["calendar", "--user", "becky",
                            "--start", "2026-04-01", "--end", "2026-04-30"])

    def test_user_flag_works_on_reminders(self):
        import query
        with patch("query.calendar_store.get_reminders", return_value=[]):
            with patch("query._log_trace"):
                query.main(["reminders", "--user", "becky"])


# ---------------------------------------------------------------------------
# B2 fix: unknown-owner reminder is NOT marked done
# ---------------------------------------------------------------------------

class TestUnknownOwnerReminder:
    def test_unknown_owner_skipped_not_completed(self):
        import tick
        # Mock the DB query to return a reminder with owner='charlie'
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            {"id": "r_charlie", "text": "something", "due": None, "owner": "charlie"},
        ]

        with patch("tick.is_quiet_hours", return_value=False), \
             patch("tick.db.get_conn") as mock_gc, \
             patch("calendar_store.complete_reminder") as mock_complete, \
             patch("tick._sync_deliver") as mock_deliver, \
             patch("tick.sms.send_long_sms") as mock_sms:
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            tick.process_reminders()

        # complete_reminder should NOT be called for unknown owner
        mock_complete.assert_not_called()
        # Neither should delivery
        mock_deliver.assert_not_called()
        mock_sms.assert_not_called()

    def test_becky_no_phone_reminder_not_completed(self):
        import tick
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            {"id": "r_becky", "text": "becky thing", "due": None, "owner": "becky"},
        ]

        with patch("tick.is_quiet_hours", return_value=False), \
             patch("tick.db.get_conn") as mock_gc, \
             patch.object(config, "BECKY_PHONE_NUMBER", None), \
             patch("calendar_store.complete_reminder") as mock_complete, \
             patch("tick.sms.send_long_sms") as mock_sms:
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            tick.process_reminders()

        mock_complete.assert_not_called()
        mock_sms.assert_not_called()


# ---------------------------------------------------------------------------
# v0.9.8 — Voice-confirmation fallthrough to ARIA. Recreates the Becky
# failure where "Yes, please clear." was wrapped as a voice transcript that
# missed the daemon shortcut. With option 3, the shortcut still bows out
# but ARIA now resolves via confirm_destructive emitted from her prompt.
# ---------------------------------------------------------------------------

class TestVoiceFallthroughToAria:
    @pytest.mark.asyncio
    async def test_yes_please_clear_voice_resolves_via_aria(self):
        from daemon import _check_pending_confirmation

        actions._pending_confirmations.clear()
        conf_id = _seed_pending(
            "becky",
            {"action": "delete_reminder", "id": "r_becky_grocery"},
            "Delete reminder: dryer sheets, case of water, 2 Glad refills",
        )

        # Step 1 — daemon shortcut bows out for wrapped voice that's longer
        # than 40 chars and not in the whitelist.
        wrapped_voice = (
            '[Voice message (4.0s, language=en): "Yes, please clear."]'
        )
        shortcut = await _check_pending_confirmation(
            wrapped_voice, user_key="becky"
        )
        assert shortcut is None

        # Step 2 — ARIA's prompt-driven response emits confirm_destructive
        # because pending was injected into her context. Drive process_actions
        # with that ACTION block and assert the actual delete fires.
        aria_response = (
            'Got it — clearing your grocery list.'
            '<!--ACTION::{"action": "confirm_destructive", '
            '"confirmation_id": "all"}-->'
        )

        async def _mock_owner(action):
            return "becky"

        with patch("actions._get_target_owner", side_effect=_mock_owner), \
             patch("calendar_store.delete_reminder", return_value=True) as mock_del:
            result = await actions.process_actions(
                aria_response, user_key="becky"
            )

        mock_del.assert_called_once_with("r_becky_grocery")
        assert conf_id not in actions._pending_confirmations
        assert len(result.failures) == 0

        actions._pending_confirmations.clear()

    @pytest.mark.asyncio
    async def test_voice_no_dont_routes_to_cancel_via_aria(self):
        """Symmetric path for cancellation: a voice 'no' that misses the
        whitelist (apostrophe-less Whisper) gets resolved through ARIA's
        cancel_destructive emit."""
        from daemon import _check_pending_confirmation

        actions._pending_confirmations.clear()
        conf_id = _seed_pending(
            "adam",
            {"action": "delete_event", "id": "e_dinner"},
            "Delete event: Dinner with parents",
        )

        # Whisper drops the apostrophe → wrapped voice phrase falls outside
        # _CANCELLATION_PHRASES.
        wrapped_voice = (
            '[Voice message (3.0s, language=en): "No, dont do that please."]'
        )
        shortcut = await _check_pending_confirmation(
            wrapped_voice, user_key="adam"
        )
        assert shortcut is None

        aria_response = (
            'Cancelled.'
            '<!--ACTION::{"action": "cancel_destructive", '
            '"confirmation_id": "all"}-->'
        )
        with patch("calendar_store.delete_event",
                   new_callable=AsyncMock) as mock_del:
            result = await actions.process_actions(
                aria_response, user_key="adam"
            )

        # Cancel must NOT execute the underlying delete.
        mock_del.assert_not_called()
        assert conf_id not in actions._pending_confirmations
        assert len(result.failures) == 0

        actions._pending_confirmations.clear()
