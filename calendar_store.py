"""Local calendar and reminders store backed by PostgreSQL.

Calendar events sync bidirectionally with Google Calendar:
- Writes go to Google first, then local (offline resilience: store locally if Google fails)
- Reads always from local cache (fast)
- Periodic sync pulls changes from Google (incremental via syncToken)

Google Calendar is the source of truth. Conflicts → Google wins.
Reminders are local-only (ARIA-specific, no Google equivalent).
"""

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

import db

log = logging.getLogger("aria.calendar")


# --- Google Calendar Integration Helpers ---

async def _google_create_event(title: str, event_date: str,
                                time: Optional[str] = None,
                                notes: Optional[str] = None) -> tuple[str | None, str | None]:
    """Create an event on Google Calendar. Returns (google_id, etag) or (None, None)."""
    try:
        import google_client
        client = google_client.get_client()

        if time:
            start = f"{event_date}T{time}:00"
            # Default 1-hour duration
            h, m = int(time.split(":")[0]), int(time.split(":")[1])
            end_h = h + 1
            end = f"{event_date}T{end_h:02d}:{m:02d}:00"
        else:
            start = event_date
            # All-day: end is next day
            end_date = date.fromisoformat(event_date) + timedelta(days=1)
            end = end_date.isoformat()

        result = await client.calendar_create_event(
            summary=title, start=start, end=end, description=notes)
        google_id = result.get("id")
        etag = result.get("etag")
        log.info("Created Google Calendar event: %s", google_id)
        return google_id, etag
    except Exception as e:
        log.warning("Google Calendar create failed (will sync later): %s", e)
        return None, None


async def _google_update_event(google_id: str, **fields) -> bool:
    """Update an event on Google Calendar. Returns True on success."""
    try:
        import google_client
        client = google_client.get_client()

        # Map local field names to Google Calendar API fields
        update = {}
        if "title" in fields:
            update["summary"] = fields["title"]
        if "notes" in fields:
            update["description"] = fields["notes"]
        if "date" in fields or "time" in fields:
            event_date = fields.get("date")
            event_time = fields.get("time")
            if event_date and event_time:
                update["start"] = {"dateTime": f"{event_date}T{event_time}:00"}
                h, m = int(event_time.split(":")[0]), int(event_time.split(":")[1])
                update["end"] = {"dateTime": f"{event_date}T{h+1:02d}:{m:02d}:00"}
            elif event_date:
                update["start"] = {"date": event_date}
                end_date = date.fromisoformat(event_date) + timedelta(days=1)
                update["end"] = {"date": end_date.isoformat()}

        if update:
            await client.calendar_update_event(google_id, **update)
            log.info("Updated Google Calendar event: %s", google_id)
        return True
    except Exception as e:
        log.warning("Google Calendar update failed: %s", e)
        return False


async def _google_delete_event(google_id: str) -> bool:
    """Delete an event from Google Calendar. Returns True on success."""
    try:
        import google_client
        client = google_client.get_client()
        await client.calendar_delete_event(google_id)
        log.info("Deleted Google Calendar event: %s", google_id)
        return True
    except Exception as e:
        log.warning("Google Calendar delete failed: %s", e)
        return False


# --- Calendar Events ---

async def add_event(title: str, event_date: str, time: Optional[str] = None,
                    notes: Optional[str] = None, owner: str = "adam") -> dict:
    """Add a calendar event. Writes to Google first (for Adam only), then local.

    owner: "adam" (syncs to his Google Calendar) or "becky" (local-only, no Google
    account for her in v0.9.5). If Google fails for Adam, stores locally with
    google_id=NULL (synced on next cycle).
    """
    event_id = str(uuid.uuid4())[:8]

    # Write to Google first — but only for Adam (he's the Google account owner).
    # Becky's events are local-only; she has no Google OAuth in v0.9.5.
    if owner == "adam":
        google_id, etag = await _google_create_event(title, event_date, time, notes)
    else:
        google_id, etag = None, None

    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO events (id, title, date, time, notes, google_id, google_etag, last_synced, owner)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (event_id, title, event_date, time, notes,
             google_id, etag,
             datetime.now().isoformat() if google_id else None,
             owner),
        ).fetchone()
    return db.serialize_row(row)


async def modify_event(event_id: str, **updates) -> Optional[dict]:
    """Modify an existing event. Updates Google first if synced."""
    allowed = {"title", "date", "time", "notes"}
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        return None

    # Check if this event has a google_id
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT google_id FROM events WHERE id = %s", (event_id,)
        ).fetchone()

    if existing and existing.get("google_id"):
        await _google_update_event(existing["google_id"], **updates)

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    params = list(updates.values()) + [event_id]
    with db.get_conn() as conn:
        row = conn.execute(
            f"UPDATE events SET {set_clause} WHERE id = %s RETURNING *",
            params,
        ).fetchone()
    return db.serialize_row(row) if row else None


async def delete_event(event_id: str) -> bool:
    """Delete an event. Deletes from Google first if synced."""
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT google_id FROM events WHERE id = %s", (event_id,)
        ).fetchone()

    if existing and existing.get("google_id"):
        await _google_delete_event(existing["google_id"])

    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM events WHERE id = %s", (event_id,))
    return cur.rowcount > 0


def get_events(start: Optional[str] = None, end: Optional[str] = None,
               owner: Optional[str] = None) -> list[dict]:
    """Get calendar events, optionally filtered by date range (YYYY-MM-DD).

    owner: filter to a specific user ("adam" or "becky"). None returns all users.
    """
    clauses = []
    params = []
    if start:
        clauses.append("date >= %s")
        params.append(start)
    if end:
        clauses.append("date <= %s")
        params.append(end)
    if owner:
        clauses.append("owner = %s")
        params.append(owner)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY date, time",
            params,
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


# --- Google Calendar Sync ---

def sync_from_google(events: list[dict], sync_token: str | None = None):
    """Sync events from Google Calendar into local cache.

    Called with results from calendar_list_events_incremental().
    Inserts new events, updates existing, handles deletions.
    """
    for event in events:
        google_id = event.get("id")
        if not google_id:
            continue

        status = event.get("status", "confirmed")
        summary = event.get("summary", "")
        start_raw = event.get("start", {})
        end_raw = event.get("end", {})
        etag = event.get("etag")

        # Extract date and time
        if start_raw.get("dateTime"):
            dt = datetime.fromisoformat(start_raw["dateTime"])
            event_date = dt.strftime("%Y-%m-%d")
            event_time = dt.strftime("%H:%M")
        elif start_raw.get("date"):
            event_date = start_raw["date"]
            event_time = None
        else:
            continue

        notes = event.get("description")
        location = event.get("location")
        if location and notes:
            notes = f"{notes}\nLocation: {location}"
        elif location:
            notes = f"Location: {location}"

        with db.get_conn() as conn:
            # Check if we already have this event
            existing = conn.execute(
                "SELECT id FROM events WHERE google_id = %s", (google_id,)
            ).fetchone()

            if status == "cancelled":
                # Deleted on Google → delete locally
                if existing:
                    conn.execute("DELETE FROM events WHERE id = %s",
                                (existing["id"],))
                    log.info("Deleted local event (Google deleted): %s", google_id)
                continue

            if existing:
                # Update existing — Google wins
                conn.execute(
                    """UPDATE events SET title = %s, date = %s, time = %s,
                       notes = %s, google_etag = %s, last_synced = NOW()
                       WHERE id = %s""",
                    (summary, event_date, event_time, notes, etag,
                     existing["id"]),
                )
            else:
                # New event from Google
                local_id = str(uuid.uuid4())[:8]
                conn.execute(
                    """INSERT INTO events (id, title, date, time, notes,
                       google_id, google_etag, last_synced)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())""",
                    (local_id, summary, event_date, event_time, notes,
                     google_id, etag),
                )
                log.info("Imported Google Calendar event: %s → %s",
                         google_id, local_id)

    # Save sync token
    if sync_token:
        _save_sync_token(sync_token)


def _save_sync_token(token: str):
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO calendar_sync_state (id, sync_token, last_incremental_sync)
               VALUES (1, %s, NOW())
               ON CONFLICT (id) DO UPDATE SET
               sync_token = EXCLUDED.sync_token,
               last_incremental_sync = NOW()""",
            (token,),
        )


def get_sync_token() -> str | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT sync_token FROM calendar_sync_state WHERE id = 1"
        ).fetchone()
    return row["sync_token"] if row else None


# --- Reminders (local-only, no Google sync) ---

def get_reminders(include_done: bool = False,
                  owner: Optional[str] = None) -> list[dict]:
    """Get all reminders. By default only returns active ones.

    owner: filter to a specific user ("adam" or "becky"). None returns all users.
    """
    clauses = []
    params = []
    if not include_done:
        clauses.append("NOT done")
    if owner:
        clauses.append("owner = %s")
        params.append(owner)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM reminders {where} ORDER BY COALESCE(due, '9999-12-31')",
            params,
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def add_reminder(text: str, due: Optional[str] = None,
                 recurring: Optional[str] = None,
                 location: Optional[str] = None,
                 location_trigger: Optional[str] = None,
                 owner: str = "adam") -> dict:
    """Add a reminder. due format: YYYY-MM-DD or YYYY-MM-DD HH:MM.

    owner: "adam" (default) or "becky". Becky's reminders never have
    location triggers (she has no location tracking in v0.9.5).
    """
    reminder_id = str(uuid.uuid4())[:8]
    trigger = location_trigger or ("arrive" if location else None)
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO reminders (id, text, due, recurring, location, location_trigger, owner)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (reminder_id, text, due, recurring, location, trigger, owner),
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


def auto_expire_stale_reminders(max_overdue_days: int = 3,
                                owner: Optional[str] = None) -> list[dict]:
    """Auto-expire non-location reminders overdue by > max_overdue_days.

    owner: if provided, scope expiry to a single user. None = all users.
    """
    cutoff = (date.today() - timedelta(days=max_overdue_days)).isoformat()
    clauses = ["due < %s", "NOT done", "location IS NULL"]
    params = [cutoff]
    if owner:
        clauses.append("owner = %s")
        params.append(owner)
    where = " AND ".join(clauses)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"""UPDATE reminders
                SET done = TRUE, completed_at = NOW(), auto_expired_at = NOW()
                WHERE {where}
                RETURNING *""",
            params,
        ).fetchall()
    expired = [db.serialize_row(r) for r in rows]
    if expired:
        log.info("Auto-expired %d stale reminders (overdue > %d days%s)",
                 len(expired), max_overdue_days,
                 f", owner={owner}" if owner else "")
    return expired
