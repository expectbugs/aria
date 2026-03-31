"""Commitment/promise tracker, backed by PostgreSQL.

Tracks commitments made by the user or others, extracted from ambient
conversations, direct ARIA interactions, or email. Supports lifecycle
management (open → done/cancelled/expired).
"""

import logging
from datetime import datetime, date, timedelta

import db

log = logging.getLogger("aria.commitments")


def add(who: str, what: str, to_whom: str | None = None,
        due_date: str | None = None, source: str = "ambient",
        source_id: int | None = None,
        conversation_id: int | None = None) -> dict:
    """Add a new commitment. Returns the new row."""
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO commitments
               (who, what, to_whom, due_date, source, source_id, conversation_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (who, what, to_whom, due_date, source, source_id, conversation_id),
        ).fetchone()
    log.info("Commitment added: %s → %s (due: %s)", who, what, due_date)
    return db.serialize_row(row)


def get_by_id(commitment_id: int) -> dict | None:
    """Get a single commitment by ID."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM commitments WHERE id = %s",
            (commitment_id,),
        ).fetchone()
    return db.serialize_row(row) if row else None


def get_open(limit: int = 50) -> list[dict]:
    """Get all open commitments, ordered by due date (soonest first, NULLs last)."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM commitments
               WHERE status = 'open'
               ORDER BY due_date ASC NULLS LAST, created_at DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_overdue() -> list[dict]:
    """Get open commitments past their due date."""
    today = date.today().isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM commitments
               WHERE status = 'open' AND due_date IS NOT NULL AND due_date < %s
               ORDER BY due_date""",
            (today,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_due_today() -> list[dict]:
    """Get open commitments due today."""
    today = date.today().isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM commitments
               WHERE status = 'open' AND due_date = %s
               ORDER BY created_at""",
            (today,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_by_person(name: str, status: str | None = None,
                  limit: int = 50) -> list[dict]:
    """Get commitments involving a person (as who or to_whom)."""
    clauses = ["(who ILIKE %s OR to_whom ILIKE %s)"]
    params = [f"%{name}%", f"%{name}%"]
    if status:
        clauses.append("status = %s")
        params.append(status)
    params.append(limit)
    where = " AND ".join(clauses)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM commitments
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %s""",
            params,
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_recent(days: int = 7, limit: int = 50) -> list[dict]:
    """Get commitments created in the last N days."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM commitments
               WHERE created_at >= %s
               ORDER BY created_at DESC
               LIMIT %s""",
            (cutoff, limit),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def complete(commitment_id: int) -> bool:
    """Mark a commitment as done."""
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE commitments
               SET status = 'done', completed_at = NOW()
               WHERE id = %s AND status = 'open'""",
            (commitment_id,),
        )
    return cur.rowcount > 0


def cancel(commitment_id: int) -> bool:
    """Cancel a commitment."""
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE commitments
               SET status = 'cancelled'
               WHERE id = %s AND status = 'open'""",
            (commitment_id,),
        )
    return cur.rowcount > 0


def expire_overdue(grace_days: int = 30) -> int:
    """Mark open commitments overdue by more than grace_days as expired.

    Returns count of commitments expired.
    """
    cutoff = (date.today() - timedelta(days=grace_days)).isoformat()
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE commitments
               SET status = 'expired'
               WHERE status = 'open' AND due_date IS NOT NULL AND due_date < %s""",
            (cutoff,),
        )
    if cur.rowcount > 0:
        log.info("Expired %d overdue commitments (grace=%d days)",
                 cur.rowcount, grace_days)
    return cur.rowcount
