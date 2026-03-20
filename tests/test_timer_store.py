"""Tests for timer_store.py — timers/alarms."""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import timer_store
from helpers import make_timer_row


def _patch_db():
    mock_conn = MagicMock()
    patcher = patch("timer_store.db.get_conn")
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


class TestAddTimer:
    def test_creates_timer(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_timer_row()
        try:
            result = timer_store.add_timer(
                label="Laundry",
                fire_at="2026-03-20T15:30:00",
                delivery="sms",
                message="Laundry done",
            )
            assert result["label"] == "Laundry"
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO timers" in sql
        finally:
            p.stop()

    def test_voice_delivery(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_timer_row(delivery="voice")
        try:
            result = timer_store.add_timer(
                label="Alarm", fire_at="2026-03-20T07:00:00",
                delivery="voice", priority="urgent",
            )
            assert result["delivery"] == "voice"
        finally:
            p.stop()

    def test_system_source(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_timer_row(source="system")
        try:
            timer_store.add_timer(
                label="Nudge", fire_at="2026-03-20T15:30:00",
                source="system",
            )
            params = mc.execute.call_args[0][1]
            assert params[6] == "system"  # source field
        finally:
            p.stop()


class TestCancelTimer:
    def test_cancels_pending_timer(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 1
        try:
            assert timer_store.cancel_timer("tmr12345") is True
            sql = mc.execute.call_args[0][0]
            assert "status = 'cancelled'" in sql
            assert "status = 'pending'" in sql
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 0
        try:
            assert timer_store.cancel_timer("bad") is False
        finally:
            p.stop()


class TestCompleteTimer:
    def test_marks_fired(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 1
        try:
            assert timer_store.complete_timer("tmr12345") is True
            sql = mc.execute.call_args[0][0]
            assert "status = 'fired'" in sql
        finally:
            p.stop()


class TestGetDue:
    def test_returns_due_timers(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [make_timer_row()]
        try:
            due = timer_store.get_due()
            assert len(due) == 1
            sql = mc.execute.call_args[0][0]
            assert "status = 'pending'" in sql
            assert "fire_at <= %s" in sql
        finally:
            p.stop()

    def test_with_specific_time(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        specific = datetime(2026, 3, 20, 16, 0)
        try:
            timer_store.get_due(now=specific)
            params = mc.execute.call_args[0][1]
            assert params[0] == specific
        finally:
            p.stop()


class TestGetActive:
    def test_returns_pending_ordered_by_fire_at(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            make_timer_row(id="t1"),
            make_timer_row(id="t2"),
        ]
        try:
            active = timer_store.get_active()
            assert len(active) == 2
            sql = mc.execute.call_args[0][0]
            assert "ORDER BY fire_at" in sql
        finally:
            p.stop()


class TestGetTimer:
    def test_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_timer_row()
        try:
            result = timer_store.get_timer("tmr12345")
            assert result["id"] == "tmr12345"
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = None
        try:
            assert timer_store.get_timer("bad") is None
        finally:
            p.stop()
