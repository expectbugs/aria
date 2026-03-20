"""Legal case log backed by PostgreSQL."""

import uuid
from datetime import datetime
from typing import Optional

import db


def get_entries(limit: Optional[int] = None,
                entry_type: Optional[str] = None) -> list[dict]:
    """Get log entries, newest first. Optionally filter by entry_type."""
    clauses = []
    params = []
    if entry_type:
        clauses.append("entry_type = %s")
        params.append(entry_type)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    limit_clause = f"LIMIT %s" if limit else ""
    if limit:
        params.append(limit)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM legal_entries {where} ORDER BY date DESC {limit_clause}",
            params,
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_upcoming_dates() -> list[dict]:
    """Get entries with entry_type 'court_date' or 'deadline' that are today or later."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM legal_entries
               WHERE entry_type IN ('court_date', 'deadline') AND date >= %s
               ORDER BY date""",
            (today,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def add_entry(entry_date: str, entry_type: str, description: str,
              contacts: Optional[list[str]] = None) -> dict:
    """Add a legal case log entry.

    entry_type: development, filing, contact, note, court_date, deadline
    contacts: list of people/entities mentioned (optional)
    """
    entry_id = str(uuid.uuid4())[:8]
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO legal_entries (id, date, entry_type, description, contacts)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING *""",
            (entry_id, entry_date, entry_type, description, contacts or []),
        ).fetchone()
    return db.serialize_row(row)


def delete_entry(entry_id: str) -> bool:
    """Delete an entry by ID."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM legal_entries WHERE id = %s", (entry_id,))
    return cur.rowcount > 0
