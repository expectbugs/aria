"""Timer/scheduler store backed by PostgreSQL.

Supports relative (delay) and absolute timers with SMS or voice delivery.
The tick.py cron script checks for due timers every minute.
"""

import uuid
from datetime import datetime

import db


def add_timer(label: str, fire_at: str, delivery: str = "sms",
              priority: str = "gentle", message: str = "",
              source: str = "user", owner: str = "adam") -> dict:
    """Create a new timer.

    fire_at: ISO datetime string for when the timer should fire.
    delivery: "sms" or "voice"
    priority: "gentle" or "urgent" (urgent bypasses quiet hours)
    message: pre-composed message to deliver when the timer fires.
    source: "user" (via ACTION block) or "system" (nudge-generated)
    owner: "adam" (default) or "becky" — routes delivery to the right phone.
    """
    timer_id = str(uuid.uuid4())[:8]
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO timers (id, label, fire_at, delivery, priority, message, source, owner)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (timer_id, label, fire_at, delivery, priority, message, source, owner),
        ).fetchone()
    return db.serialize_row(row)


def cancel_timer(timer_id: str) -> bool:
    """Cancel a pending timer by ID."""
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE timers SET status = 'cancelled', cancelled_at = NOW()
               WHERE id = %s AND status = 'pending'""",
            (timer_id,),
        )
    return cur.rowcount > 0


def complete_timer(timer_id: str) -> bool:
    """Mark a timer as fired."""
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE timers SET status = 'fired', fired_at = NOW()
               WHERE id = %s""",
            (timer_id,),
        )
    return cur.rowcount > 0


def get_due(now: datetime | None = None,
            owner: str | None = None) -> list[dict]:
    """Return all pending timers whose fire_at is at or before now.

    owner: filter to a single user ("adam" or "becky"). None = all users.
    """
    if now is None:
        now = datetime.now()
    if owner:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM timers
                   WHERE status = 'pending' AND fire_at <= %s AND owner = %s""",
                (now, owner),
            ).fetchall()
    else:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM timers
                   WHERE status = 'pending' AND fire_at <= %s""",
                (now,),
            ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_active(owner: str | None = None) -> list[dict]:
    """Return all pending timers, sorted by fire_at.

    owner: filter to a single user ("adam" or "becky"). None = all users.
    """
    if owner:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM timers
                   WHERE status = 'pending' AND owner = %s ORDER BY fire_at""",
                (owner,),
            ).fetchall()
    else:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM timers
                   WHERE status = 'pending' ORDER BY fire_at""",
            ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_timer(timer_id: str) -> dict | None:
    """Look up a single timer by ID."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM timers WHERE id = %s", (timer_id,),
        ).fetchone()
    return db.serialize_row(row) if row else None
