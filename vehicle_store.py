"""Vehicle maintenance log backed by PostgreSQL."""

import uuid
from datetime import datetime
from typing import Optional

import db


def get_entries(limit: Optional[int] = None,
                event_type: Optional[str] = None) -> list[dict]:
    """Get log entries, newest first. Optionally filter by event_type."""
    clauses = []
    params = []
    if event_type:
        clauses.append("event_type = %s")
        params.append(event_type)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    limit_clause = f"LIMIT %s" if limit else ""
    if limit:
        params.append(limit)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM vehicle_entries {where} ORDER BY date DESC {limit_clause}",
            params,
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_latest_by_type() -> dict[str, dict]:
    """Return the most recent entry for each event_type."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT ON (event_type) *
               FROM vehicle_entries
               ORDER BY event_type, date DESC""",
        ).fetchall()
    return {r["event_type"]: db.serialize_row(r) for r in rows}


def add_entry(event_date: str, event_type: str, description: str,
              mileage: Optional[int] = None,
              cost: Optional[float] = None) -> dict:
    """Add a maintenance log entry."""
    entry_id = str(uuid.uuid4())[:8]
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO vehicle_entries (id, date, event_type, description, mileage, cost)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (entry_id, event_date, event_type, description, mileage, cost),
        ).fetchone()
    return db.serialize_row(row)


def delete_entry(entry_id: str) -> bool:
    """Delete an entry by ID."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM vehicle_entries WHERE id = %s", (entry_id,))
    return cur.rowcount > 0
