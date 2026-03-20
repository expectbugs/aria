"""Local calendar and reminders store backed by PostgreSQL."""

import uuid
from datetime import datetime
from typing import Optional

import db


# --- Calendar Events ---

def get_events(start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    """Get calendar events, optionally filtered by date range (YYYY-MM-DD)."""
    clauses = []
    params = []
    if start:
        clauses.append("date >= %s")
        params.append(start)
    if end:
        clauses.append("date <= %s")
        params.append(end)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY date, time",
            params,
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def add_event(title: str, event_date: str, time: Optional[str] = None,
              notes: Optional[str] = None) -> dict:
    """Add a calendar event. date format: YYYY-MM-DD, time format: HH:MM."""
    event_id = str(uuid.uuid4())[:8]
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO events (id, title, date, time, notes)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING *""",
            (event_id, title, event_date, time, notes),
        ).fetchone()
    return db.serialize_row(row)


def modify_event(event_id: str, **updates) -> Optional[dict]:
    """Modify an existing event by ID. Pass fields to update as kwargs."""
    # Filter out internal fields that shouldn't be overwritten
    allowed = {"title", "date", "time", "notes"}
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        return None
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    params = list(updates.values()) + [event_id]
    with db.get_conn() as conn:
        row = conn.execute(
            f"UPDATE events SET {set_clause} WHERE id = %s RETURNING *",
            params,
        ).fetchone()
    return db.serialize_row(row) if row else None


def delete_event(event_id: str) -> bool:
    """Delete an event by ID."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM events WHERE id = %s", (event_id,))
    return cur.rowcount > 0


# --- Reminders ---

def get_reminders(include_done: bool = False) -> list[dict]:
    """Get all reminders. By default only returns active ones."""
    if include_done:
        where = ""
    else:
        where = "WHERE NOT done"
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM reminders {where} ORDER BY COALESCE(due, '9999-12-31')",
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def add_reminder(text: str, due: Optional[str] = None,
                 recurring: Optional[str] = None,
                 location: Optional[str] = None,
                 location_trigger: Optional[str] = None) -> dict:
    """Add a reminder. due format: YYYY-MM-DD or YYYY-MM-DD HH:MM."""
    reminder_id = str(uuid.uuid4())[:8]
    trigger = location_trigger or ("arrive" if location else None)
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO reminders (id, text, due, recurring, location, location_trigger)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (reminder_id, text, due, recurring, location, trigger),
        ).fetchone()
    return db.serialize_row(row)


def complete_reminder(reminder_id: str) -> Optional[dict]:
    """Mark a reminder as done."""
    with db.get_conn() as conn:
        row = conn.execute(
            """UPDATE reminders SET done = TRUE, completed_at = NOW()
               WHERE id = %s RETURNING *""",
            (reminder_id,),
        ).fetchone()
    return db.serialize_row(row) if row else None


def delete_reminder(reminder_id: str) -> bool:
    """Delete a reminder by ID."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
    return cur.rowcount > 0
