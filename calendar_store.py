"""Local calendar and reminders store backed by JSON files."""

import json
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from config import CALENDAR_DB, REMINDERS_DB


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _save(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


# --- Calendar Events ---

def get_events(start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    """Get calendar events, optionally filtered by date range (YYYY-MM-DD)."""
    events = _load(CALENDAR_DB)
    if start:
        events = [e for e in events if e.get("date", "") >= start]
    if end:
        events = [e for e in events if e.get("date", "") <= end]
    return sorted(events, key=lambda e: (e.get("date", ""), e.get("time", "")))


def add_event(title: str, event_date: str, time: Optional[str] = None,
              notes: Optional[str] = None) -> dict:
    """Add a calendar event. date format: YYYY-MM-DD, time format: HH:MM."""
    events = _load(CALENDAR_DB)
    event = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "date": event_date,
        "time": time,
        "notes": notes,
        "created": datetime.now().isoformat(),
    }
    events.append(event)
    _save(CALENDAR_DB, events)
    return event


def modify_event(event_id: str, **updates) -> Optional[dict]:
    """Modify an existing event by ID. Pass fields to update as kwargs."""
    events = _load(CALENDAR_DB)
    for event in events:
        if event["id"] == event_id:
            event.update(updates)
            _save(CALENDAR_DB, events)
            return event
    return None


def delete_event(event_id: str) -> bool:
    """Delete an event by ID."""
    events = _load(CALENDAR_DB)
    new_events = [e for e in events if e["id"] != event_id]
    if len(new_events) == len(events):
        return False
    _save(CALENDAR_DB, new_events)
    return True


# --- Reminders ---

def get_reminders(include_done: bool = False) -> list[dict]:
    """Get all reminders. By default only returns active ones."""
    reminders = _load(REMINDERS_DB)
    if not include_done:
        reminders = [r for r in reminders if not r.get("done")]
    return sorted(reminders, key=lambda r: r.get("due", "") or "9999")


def add_reminder(text: str, due: Optional[str] = None,
                 recurring: Optional[str] = None,
                 location: Optional[str] = None,
                 location_trigger: Optional[str] = None) -> dict:
    """Add a reminder. due format: YYYY-MM-DD or YYYY-MM-DD HH:MM.
    recurring: 'daily', 'weekly', 'monthly', or None.
    location: place name for location-triggered reminders (e.g., 'home', 'work', address).
    location_trigger: 'arrive' or 'leave' (default: 'arrive').
    """
    reminders = _load(REMINDERS_DB)
    reminder = {
        "id": str(uuid.uuid4())[:8],
        "text": text,
        "due": due,
        "recurring": recurring,
        "location": location,
        "location_trigger": location_trigger or ("arrive" if location else None),
        "done": False,
        "created": datetime.now().isoformat(),
    }
    reminders.append(reminder)
    _save(REMINDERS_DB, reminders)
    return reminder


def complete_reminder(reminder_id: str) -> Optional[dict]:
    """Mark a reminder as done."""
    reminders = _load(REMINDERS_DB)
    for r in reminders:
        if r["id"] == reminder_id:
            r["done"] = True
            r["completed_at"] = datetime.now().isoformat()
            _save(REMINDERS_DB, reminders)
            return r
    return None


def delete_reminder(reminder_id: str) -> bool:
    """Delete a reminder by ID."""
    reminders = _load(REMINDERS_DB)
    new_reminders = [r for r in reminders if r["id"] != reminder_id]
    if len(new_reminders) == len(reminders):
        return False
    _save(REMINDERS_DB, new_reminders)
    return True
