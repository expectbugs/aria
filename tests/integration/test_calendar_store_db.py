"""Integration tests for calendar_store — real SQL against PostgreSQL."""

import asyncio
from unittest.mock import AsyncMock, patch

import calendar_store


def _run(coro):
    """Run an async function synchronously in tests."""
    return asyncio.run(coro)


def _add_event(**kwargs):
    """Add event with Google API mocked out."""
    with patch("calendar_store._google_create_event",
               new_callable=AsyncMock, return_value=(None, None)):
        return _run(calendar_store.add_event(**kwargs))


def _modify_event(event_id, **kwargs):
    with patch("calendar_store._google_update_event",
               new_callable=AsyncMock, return_value=True):
        return _run(calendar_store.modify_event(event_id, **kwargs))


def _delete_event(event_id):
    with patch("calendar_store._google_delete_event",
               new_callable=AsyncMock, return_value=True):
        return _run(calendar_store.delete_event(event_id))


class TestEventsRoundtrip:
    def test_add_and_retrieve(self):
        event = _add_event(
            title="Dentist", event_date="2026-03-25", time="14:30", notes="Cleaning",
        )
        assert event["title"] == "Dentist"
        assert event["date"] == "2026-03-25"
        assert event["time"] == "14:30"
        assert len(event["id"]) == 8

        events = calendar_store.get_events(start="2026-03-25", end="2026-03-25")
        assert len(events) == 1
        assert events[0]["id"] == event["id"]

    def test_add_event_without_time(self):
        event = _add_event(title="All day", event_date="2026-03-26")
        assert event["time"] is None

    def test_date_range_filter(self):
        _add_event(title="A", event_date="2026-03-20")
        _add_event(title="B", event_date="2026-03-22")
        _add_event(title="C", event_date="2026-03-25")

        events = calendar_store.get_events(start="2026-03-21", end="2026-03-24")
        assert len(events) == 1
        assert events[0]["title"] == "B"

    def test_events_ordered_by_date_and_time(self):
        _add_event(title="Late", event_date="2026-03-20", time="16:00")
        _add_event(title="Early", event_date="2026-03-20", time="09:00")
        _add_event(title="NoTime", event_date="2026-03-20")

        events = calendar_store.get_events(start="2026-03-20", end="2026-03-20")
        titles = [e["title"] for e in events]
        assert titles.index("Early") < titles.index("Late")

    def test_modify_event(self):
        event = _add_event(title="Old", event_date="2026-03-20")
        updated = _modify_event(event["id"], title="New", time="10:00")
        assert updated["title"] == "New"
        assert updated["time"] == "10:00"
        assert updated["date"] == "2026-03-20"

    def test_modify_nonexistent_returns_none(self):
        assert _modify_event("nonexist", title="X") is None

    def test_delete_event(self):
        event = _add_event(title="Delete me", event_date="2026-03-20")
        assert _delete_event(event["id"]) is True
        assert calendar_store.get_events() == []

    def test_delete_nonexistent_returns_false(self):
        assert _delete_event("nonexist") is False


class TestRemindersRoundtrip:
    def test_add_and_retrieve(self):
        reminder = calendar_store.add_reminder(text="Buy milk", due="2026-03-22")
        assert reminder["text"] == "Buy milk"
        assert reminder["done"] is False

        reminders = calendar_store.get_reminders()
        assert len(reminders) == 1

    def test_active_only_by_default(self):
        calendar_store.add_reminder(text="Active")
        r = calendar_store.add_reminder(text="Done")
        calendar_store.complete_reminder(r["id"])

        active = calendar_store.get_reminders()
        assert len(active) == 1
        assert active[0]["text"] == "Active"

        all_r = calendar_store.get_reminders(include_done=True)
        assert len(all_r) == 2

    def test_complete_reminder(self):
        r = calendar_store.add_reminder(text="Finish")
        result = calendar_store.complete_reminder(r["id"])
        assert result["done"] is True
        assert result["completed_at"] is not None

    def test_location_trigger_defaults_to_arrive(self):
        r = calendar_store.add_reminder(text="Check mail", location="home")
        assert r["location"] == "home"
        assert r["location_trigger"] == "arrive"

    def test_explicit_leave_trigger(self):
        r = calendar_store.add_reminder(
            text="Lock up", location="home", location_trigger="leave",
        )
        assert r["location_trigger"] == "leave"

    def test_recurring_reminder(self):
        r = calendar_store.add_reminder(text="Daily meds", recurring="daily")
        assert r["recurring"] == "daily"

    def test_ordered_by_due_date(self):
        calendar_store.add_reminder(text="Later", due="2026-04-01")
        calendar_store.add_reminder(text="Sooner", due="2026-03-22")
        calendar_store.add_reminder(text="No due")

        reminders = calendar_store.get_reminders()
        texts = [r["text"] for r in reminders]
        assert texts.index("Sooner") < texts.index("Later")
        assert texts[-1] == "No due"

    def test_delete_reminder(self):
        r = calendar_store.add_reminder(text="Delete me")
        assert calendar_store.delete_reminder(r["id"]) is True
        assert calendar_store.get_reminders() == []
