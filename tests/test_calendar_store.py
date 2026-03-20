"""Tests for calendar_store.py — events and reminders CRUD."""

from datetime import date, time, datetime
from unittest.mock import patch, MagicMock

import calendar_store
from helpers import make_event_row, make_reminder_row


def _patch_db():
    """Patch calendar_store.db.get_conn and return (mock_conn, patcher)."""
    mock_conn = MagicMock()
    patcher = patch("calendar_store.db.get_conn")
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


# === Events ===

class TestGetEvents:
    def test_returns_serialized_events(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            make_event_row(),
        ]
        try:
            events = calendar_store.get_events(start="2026-03-20", end="2026-03-20")
            assert len(events) == 1
            assert events[0]["id"] == "abc12345"
            assert events[0]["title"] == "Dentist"
            assert events[0]["date"] == "2026-03-20"
            assert events[0]["time"] == "14:30"
        finally:
            p.stop()

    def test_no_filters_queries_all(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            calendar_store.get_events()
            sql = mc.execute.call_args[0][0]
            assert "WHERE" not in sql
            assert "ORDER BY date, time" in sql
        finally:
            p.stop()

    def test_start_only_filter(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            calendar_store.get_events(start="2026-03-20")
            sql = mc.execute.call_args[0][0]
            assert "date >= %s" in sql
            assert "date <= %s" not in sql
        finally:
            p.stop()

    def test_both_filters(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            calendar_store.get_events(start="2026-03-20", end="2026-03-27")
            sql = mc.execute.call_args[0][0]
            params = mc.execute.call_args[0][1]
            assert "date >= %s" in sql
            assert "date <= %s" in sql
            assert params == ["2026-03-20", "2026-03-27"]
        finally:
            p.stop()

    def test_empty_result(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert calendar_store.get_events() == []
        finally:
            p.stop()


class TestAddEvent:
    def test_creates_event_with_all_fields(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_event_row()
        try:
            result = calendar_store.add_event(
                title="Dentist", event_date="2026-03-20",
                time="14:30", notes="Cleaning"
            )
            assert result["id"] == "abc12345"
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO events" in sql
            assert "RETURNING *" in sql
        finally:
            p.stop()

    def test_creates_event_without_optional_fields(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_event_row(t=None, notes=None)
        try:
            result = calendar_store.add_event(title="Meeting", event_date="2026-03-21")
            assert result["time"] is None
        finally:
            p.stop()

    def test_generates_uuid_id(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_event_row()
        try:
            calendar_store.add_event(title="Test", event_date="2026-03-20")
            params = mc.execute.call_args[0][1]
            event_id = params[0]
            assert len(event_id) == 8  # uuid[:8]
        finally:
            p.stop()


class TestModifyEvent:
    def test_updates_allowed_fields(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_event_row(title="Updated")
        try:
            result = calendar_store.modify_event("abc12345", title="Updated")
            assert result["title"] == "Updated"
            sql = mc.execute.call_args[0][0]
            assert "UPDATE events" in sql
            assert "title = %s" in sql
        finally:
            p.stop()

    def test_filters_disallowed_fields(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = None
        try:
            result = calendar_store.modify_event("abc12345", id="new_id", action="hack")
            assert result is None  # no allowed updates = None
        finally:
            p.stop()

    def test_returns_none_if_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = None
        try:
            result = calendar_store.modify_event("nonexistent", title="X")
            assert result is None
        finally:
            p.stop()


class TestDeleteEvent:
    def test_returns_true_on_success(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 1
        try:
            assert calendar_store.delete_event("abc12345") is True
        finally:
            p.stop()

    def test_returns_false_if_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 0
        try:
            assert calendar_store.delete_event("nonexistent") is False
        finally:
            p.stop()


# === Reminders ===

class TestGetReminders:
    def test_active_only_by_default(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [make_reminder_row()]
        try:
            reminders = calendar_store.get_reminders()
            sql = mc.execute.call_args[0][0]
            assert "WHERE NOT done" in sql
            assert len(reminders) == 1
        finally:
            p.stop()

    def test_include_done(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            calendar_store.get_reminders(include_done=True)
            sql = mc.execute.call_args[0][0]
            assert "WHERE" not in sql
        finally:
            p.stop()


class TestAddReminder:
    def test_basic_reminder(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_reminder_row()
        try:
            result = calendar_store.add_reminder(text="Buy milk", due="2026-03-21")
            assert result["text"] == "Buy milk"
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO reminders" in sql
        finally:
            p.stop()

    def test_location_reminder_defaults_to_arrive(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_reminder_row(
            location="home", location_trigger="arrive"
        )
        try:
            calendar_store.add_reminder(text="Check mail", location="home")
            params = mc.execute.call_args[0][1]
            # location_trigger should default to "arrive"
            assert params[5] == "arrive"
        finally:
            p.stop()

    def test_explicit_leave_trigger(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_reminder_row(
            location="work", location_trigger="leave"
        )
        try:
            calendar_store.add_reminder(
                text="Clock out", location="work", location_trigger="leave"
            )
            params = mc.execute.call_args[0][1]
            assert params[5] == "leave"
        finally:
            p.stop()

    def test_recurring_reminder(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_reminder_row(recurring="daily")
        try:
            calendar_store.add_reminder(text="Take meds", recurring="daily")
            params = mc.execute.call_args[0][1]
            assert params[3] == "daily"
        finally:
            p.stop()


class TestCompleteReminder:
    def test_marks_done(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_reminder_row(done=True)
        try:
            result = calendar_store.complete_reminder("rem12345")
            sql = mc.execute.call_args[0][0]
            assert "SET done = TRUE" in sql
            assert result is not None
        finally:
            p.stop()

    def test_returns_none_if_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = None
        try:
            assert calendar_store.complete_reminder("bad_id") is None
        finally:
            p.stop()


class TestDeleteReminder:
    def test_returns_true_on_success(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 1
        try:
            assert calendar_store.delete_reminder("rem12345") is True
        finally:
            p.stop()

    def test_returns_false_if_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 0
        try:
            assert calendar_store.delete_reminder("bad") is False
        finally:
            p.stop()
