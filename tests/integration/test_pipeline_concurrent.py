"""Concurrency and race condition tests using real PostgreSQL.

Tests advisory locks, content hash deduplication, webhook idempotency,
and state transitions with the real aria_test database.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

from datetime import datetime, date, timedelta

import pytest

import db
import timer_store
import calendar_store
import nutrition_store
import health_store

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_legal, seed_vehicle, seed_request_log, seed_nudge_log,
)


# ---------------------------------------------------------------------------
# Advisory locks
# ---------------------------------------------------------------------------

class TestAdvisoryLock:
    def test_first_connection_acquires_lock(self, test_pool):
        """First connection should successfully acquire advisory lock 42."""
        with test_pool.connection() as conn:
            conn.autocommit = False
            row = conn.execute(
                "SELECT pg_try_advisory_xact_lock(42) AS locked"
            ).fetchone()
            assert row["locked"] is True
            conn.rollback()

    def test_second_connection_fails_while_held(self, test_pool):
        """Second connection should fail to acquire lock held by first."""
        conn1 = test_pool.getconn()
        conn1.autocommit = False
        try:
            row1 = conn1.execute(
                "SELECT pg_try_advisory_xact_lock(42) AS locked"
            ).fetchone()
            assert row1["locked"] is True

            # Second connection tries same lock
            with test_pool.connection() as conn2:
                conn2.autocommit = False
                row2 = conn2.execute(
                    "SELECT pg_try_advisory_xact_lock(42) AS locked"
                ).fetchone()
                assert row2["locked"] is False
                conn2.rollback()
        finally:
            conn1.rollback()
            conn1.autocommit = True
            test_pool.putconn(conn1)

    def test_lock_released_on_commit_then_succeeds(self, test_pool):
        """After first connection commits (releasing lock), second should succeed."""
        conn1 = test_pool.getconn()
        conn1.autocommit = False
        try:
            row1 = conn1.execute(
                "SELECT pg_try_advisory_xact_lock(42) AS locked"
            ).fetchone()
            assert row1["locked"] is True
            conn1.commit()
        finally:
            conn1.autocommit = True
            test_pool.putconn(conn1)

        # Now second connection should succeed
        with test_pool.connection() as conn2:
            conn2.autocommit = False
            row2 = conn2.execute(
                "SELECT pg_try_advisory_xact_lock(42) AS locked"
            ).fetchone()
            assert row2["locked"] is True
            conn2.rollback()


# ---------------------------------------------------------------------------
# Content hash deduplication
# ---------------------------------------------------------------------------

class TestContentHashDedup:
    def test_nutrition_same_entry_exactly_1_row(self):
        """Two inserts of identical nutrition entry produce exactly 1 row."""
        today = date.today().isoformat()
        r1 = seed_nutrition(day=today, food_name="Apple", meal_type="snack",
                            calories=95)
        r2 = seed_nutrition(day=today, food_name="Apple", meal_type="snack",
                            calories=95)
        items = nutrition_store.get_items(day=today)
        assert len(items) == 1
        assert r2["duplicate"] is True

    def test_health_same_entry_exactly_1_row(self):
        """Two inserts of identical health entry produce exactly 1 row."""
        today = date.today().isoformat()
        r1 = seed_health(day=today, category="pain", description="headache",
                          severity=3)
        r2 = seed_health(day=today, category="pain", description="headache",
                          severity=3)
        entries = health_store.get_entries(days=1, category="pain")
        # Filter to today only
        today_entries = [e for e in entries if e["date"] == today]
        assert len(today_entries) == 1
        assert r2["duplicate"] is True

    def test_different_servings_different_hash(self):
        """Different servings = different hash = both inserted."""
        today = date.today().isoformat()
        r1 = nutrition_store.add_item(
            food_name="Protein Bar", meal_type="snack",
            nutrients={"calories": 200}, servings=1.0, entry_date=today,
        )
        r2 = nutrition_store.add_item(
            food_name="Protein Bar", meal_type="snack",
            nutrients={"calories": 200}, servings=2.0, entry_date=today,
        )
        assert r1["inserted"] is True
        assert r2["inserted"] is True
        items = nutrition_store.get_items(day=today)
        assert len(items) == 2


# ---------------------------------------------------------------------------
# Webhook idempotency
# ---------------------------------------------------------------------------

class TestWebhookIdempotency:
    def test_same_message_sid_exactly_1_row(self):
        """Two inserts of same MessageSid produce exactly 1 row."""
        sid = "SM_test_12345"
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO processed_webhooks (message_sid) VALUES (%s) "
                "ON CONFLICT (message_sid) DO NOTHING",
                (sid,),
            )
            conn.execute(
                "INSERT INTO processed_webhooks (message_sid) VALUES (%s) "
                "ON CONFLICT (message_sid) DO NOTHING",
                (sid,),
            )
            rows = conn.execute(
                "SELECT COUNT(*) AS cnt FROM processed_webhooks WHERE message_sid = %s",
                (sid,),
            ).fetchone()
        assert rows["cnt"] == 1


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

class TestStateTransitions:
    def test_timer_create_complete_no_longer_due(self):
        """After completing a timer, get_due() no longer returns it."""
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        t = seed_timer(label="Transition Test", fire_at=past)
        due_before = timer_store.get_due()
        assert any(d["id"] == t["id"] for d in due_before)

        timer_store.complete_timer(t["id"])

        due_after = timer_store.get_due()
        assert not any(d["id"] == t["id"] for d in due_after)

    def test_reminder_create_complete_not_in_active(self):
        """After completing a reminder, it's not in the active list."""
        r = seed_reminder(text="Finish task", due=date.today().isoformat())
        active_before = calendar_store.get_reminders()
        assert any(a["id"] == r["id"] for a in active_before)

        calendar_store.complete_reminder(r["id"])

        active_after = calendar_store.get_reminders()
        assert not any(a["id"] == r["id"] for a in active_after)


# ===========================================================================
# Total: 10 tests
# ===========================================================================
