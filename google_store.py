"""Google Calendar + Gmail data store — PostgreSQL-backed cache.

Stores synced events and messages with JSONB raw data plus extracted
key fields for efficient queries. Data is synced periodically from
Google APIs via google_client.py.
"""

import logging
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import psycopg.types.json

import db

log = logging.getLogger("aria.google_store")


def _safe_str(val, default: str = "") -> str:
    """Safely convert to string, handling None from API."""
    if val is None:
        return default
    return str(val)


def _parse_email_date(date_str: str) -> datetime | None:
    """Parse RFC2822 email date header to datetime.

    Gmail dates can include parenthetical comments like '(UTC)' that
    PostgreSQL cannot parse. Uses Python's email.utils parser which
    handles all RFC2822 variants.
    """
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


# --- Calendar Events ---

def save_calendar_events(events: list[dict]):
    """Upsert calendar events from the Google Calendar API.

    Extracts key fields for indexing and stores the full raw event as JSONB.
    Handles both timed events (start.dateTime) and all-day events (start.date).
    """
    with db.get_conn() as conn:
        for event in events:
            event_id = _safe_str(event.get("id"))
            if not event_id:
                continue
            start = event.get("start", {})
            end = event.get("end", {})
            # Timed events use dateTime, all-day events use date
            start_time = start.get("dateTime") or start.get("date")
            end_time = end.get("dateTime") or end.get("date")
            conn.execute(
                """INSERT INTO google_calendar_events
                   (event_id, calendar_id, summary, start_time, end_time,
                    location, status, data)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (event_id) DO UPDATE SET
                   summary = EXCLUDED.summary,
                   start_time = EXCLUDED.start_time,
                   end_time = EXCLUDED.end_time,
                   location = EXCLUDED.location,
                   status = EXCLUDED.status,
                   data = EXCLUDED.data,
                   synced_at = NOW()""",
                (event_id, "primary",
                 _safe_str(event.get("summary")),
                 start_time,
                 end_time,
                 _safe_str(event.get("location")) or None,
                 _safe_str(event.get("status")),
                 psycopg.types.json.Jsonb(event)),
            )
    if events:
        log.info("Saved %d Google Calendar events", len(events))


def get_upcoming_events(hours: int = 48) -> list[dict]:
    """Get cached calendar events from now to now + hours."""
    now = datetime.now()
    future = now + timedelta(hours=hours)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM google_calendar_events
               WHERE start_time >= %s AND start_time <= %s
               ORDER BY start_time""",
            (now, future),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_events_for_date(day: str) -> list[dict]:
    """Get cached calendar events for a specific date (YYYY-MM-DD)."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM google_calendar_events
               WHERE start_time::date = %s
               ORDER BY start_time""",
            (day,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


# --- Gmail Messages ---

def _extract_header(msg: dict, name: str) -> str:
    """Extract a header value from Gmail message metadata."""
    for header in msg.get("payload", {}).get("headers", []):
        if _safe_str(header.get("name")).lower() == name.lower():
            return _safe_str(header.get("value"))
    return ""


def save_gmail_messages(messages: list[dict]):
    """Upsert Gmail messages from the API.

    Extracts key headers for indexing and stores the full raw message as JSONB.
    """
    with db.get_conn() as conn:
        for msg in messages:
            message_id = _safe_str(msg.get("id"))
            if not message_id:
                continue
            subject = _extract_header(msg, "Subject")
            sender = _extract_header(msg, "From")
            date_val = _parse_email_date(_extract_header(msg, "Date"))
            label_ids = msg.get("labelIds", [])
            if not isinstance(label_ids, list):
                label_ids = []
            conn.execute(
                """INSERT INTO google_gmail_messages
                   (message_id, thread_id, subject, sender, date, snippet,
                    label_ids, data)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (message_id) DO UPDATE SET
                   subject = EXCLUDED.subject,
                   sender = EXCLUDED.sender,
                   date = EXCLUDED.date,
                   snippet = EXCLUDED.snippet,
                   label_ids = EXCLUDED.label_ids,
                   data = EXCLUDED.data,
                   synced_at = NOW()""",
                (message_id,
                 _safe_str(msg.get("threadId")),
                 subject,
                 sender,
                 date_val,
                 _safe_str(msg.get("snippet")),
                 label_ids,
                 psycopg.types.json.Jsonb(msg)),
            )
    if messages:
        log.info("Saved %d Gmail messages", len(messages))


def get_recent_messages(hours: int = 24, limit: int = 50) -> list[dict]:
    """Get cached Gmail messages from the last N hours."""
    cutoff = datetime.now() - timedelta(hours=hours)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM google_gmail_messages
               WHERE date >= %s
               ORDER BY date DESC
               LIMIT %s""",
            (cutoff, limit),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_unread_count() -> int:
    """Count messages with UNREAD label in the last 24 hours."""
    cutoff = datetime.now() - timedelta(hours=24)
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM google_gmail_messages
               WHERE date >= %s AND 'UNREAD' = ANY(label_ids)""",
            (cutoff,),
        ).fetchone()
    return int(row["cnt"]) if row else 0
