"""Gmail email cache and classification store — PostgreSQL-backed.

Stores email metadata + full bodies with tsvector full-text search.
Provides query functions for ARIA context injection, search, and
classification audit trail.
"""

import logging
import os
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import psycopg.types.json

import config
import db

log = logging.getLogger("aria.gmail_store")


def _safe_str(val, default: str = "") -> str:
    if val is None:
        return default
    return str(val)


def _parse_email_date(date_str: str) -> datetime | None:
    """Parse RFC2822 email date header to datetime.

    Gmail dates can include parenthetical comments like '(UTC)' that
    PostgreSQL cannot parse directly.
    """
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def _extract_header(msg: dict, name: str) -> str:
    """Extract a header value from Gmail message payload."""
    for header in msg.get("payload", {}).get("headers", []):
        if _safe_str(header.get("name")).lower() == name.lower():
            return _safe_str(header.get("value"))
    return ""


def _extract_from_parts(from_header: str) -> tuple[str, str]:
    """Split 'Name <email@example.com>' into (email, name)."""
    if "<" in from_header and ">" in from_header:
        name = from_header[:from_header.index("<")].strip().strip('"')
        addr = from_header[from_header.index("<") + 1:from_header.index(">")].strip()
        return addr, name
    return from_header.strip(), ""


def _detect_gmail_category(labels: list[str]) -> str | None:
    """Map Gmail labels to category."""
    for label in labels:
        if label == "CATEGORY_PERSONAL":
            return "Primary"
        if label == "CATEGORY_SOCIAL":
            return "Social"
        if label == "CATEGORY_PROMOTIONS":
            return "Promotions"
        if label == "CATEGORY_UPDATES":
            return "Updates"
        if label == "CATEGORY_FORUMS":
            return "Forums"
    return None


def _has_attachments(msg: dict) -> bool:
    """Check if a Gmail message has attachments."""
    parts = msg.get("payload", {}).get("parts", [])
    for part in parts:
        if part.get("filename"):
            return True
        # Recurse into nested multipart
        sub_parts = part.get("parts", [])
        for sp in sub_parts:
            if sp.get("filename"):
                return True
    return False


def _get_attachment_info(msg: dict) -> list[dict]:
    """Extract attachment metadata from a Gmail message.

    Returns list of dicts with 'filename', 'mimeType', 'attachmentId', 'size'.
    """
    attachments = []

    def _walk(parts):
        for part in parts:
            filename = part.get("filename")
            if filename:
                attachments.append({
                    "filename": filename,
                    "mimeType": part.get("mimeType", ""),
                    "attachmentId": part.get("body", {}).get("attachmentId", ""),
                    "size": int(part.get("body", {}).get("size", 0)),
                })
            _walk(part.get("parts", []))

    _walk(msg.get("payload", {}).get("parts", []))
    return attachments


# --- Email Storage ---

def save_email(msg: dict):
    """Upsert a single email into email_cache.

    Expects a Gmail message dict with format=full (includes payload with body).
    """
    message_id = _safe_str(msg.get("id"))
    if not message_id:
        return

    from_header = _extract_header(msg, "From")
    from_address, from_name = _extract_from_parts(from_header)
    to_addresses = _extract_header(msg, "To")
    subject = _extract_header(msg, "Subject")
    date_val = _parse_email_date(_extract_header(msg, "Date"))
    labels = msg.get("labelIds", [])
    if not isinstance(labels, list):
        labels = []
    body = msg.get("body", "")  # extracted by google_client._extract_body()

    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO email_cache
               (id, thread_id, timestamp, from_address, from_name, to_addresses,
                subject, snippet, body, labels, has_attachments, gmail_category)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
               subject = EXCLUDED.subject,
               snippet = EXCLUDED.snippet,
               body = COALESCE(NULLIF(EXCLUDED.body, ''), email_cache.body),
               labels = EXCLUDED.labels,
               has_attachments = EXCLUDED.has_attachments,
               gmail_category = EXCLUDED.gmail_category,
               fetched_at = NOW()""",
            (message_id,
             _safe_str(msg.get("threadId")),
             date_val or datetime.now(),
             from_address,
             from_name or None,
             to_addresses or None,
             subject,
             _safe_str(msg.get("snippet")),
             body,
             labels,
             _has_attachments(msg),
             _detect_gmail_category(labels)),
        )


def save_emails(msgs: list[dict]):
    """Batch save emails."""
    for msg in msgs:
        try:
            save_email(msg)
        except Exception as e:
            log.error("Failed to save email %s: %s", msg.get("id", "?"), e)
    if msgs:
        log.info("Saved %d emails to cache", len(msgs))


# --- Attachment Download ---

async def download_attachments(message_id: str, msg: dict) -> list[str]:
    """Download attachments from a Gmail message to local disk.

    Saves to data/email_attachments/{message_id}/filename.
    Returns list of saved file paths.
    """
    import google_client

    attachments = _get_attachment_info(msg)
    if not attachments:
        return []

    attach_dir = Path(getattr(config, "GMAIL_ATTACHMENTS_DIR",
                               config.DATA_DIR / "email_attachments"))
    msg_dir = attach_dir / message_id
    msg_dir.mkdir(parents=True, exist_ok=True)

    client = google_client.get_client()
    saved_paths = []

    for att in attachments:
        if not att["attachmentId"]:
            continue
        try:
            data = await client.gmail_get_attachment(message_id, att["attachmentId"])
            filepath = msg_dir / att["filename"]
            filepath.write_bytes(data)
            saved_paths.append(str(filepath))
        except Exception as e:
            log.error("Failed to download attachment %s from %s: %s",
                      att["filename"], message_id, e)

    if saved_paths:
        save_attachment_paths(message_id, saved_paths)
        log.info("Downloaded %d attachments for %s", len(saved_paths), message_id)

    return saved_paths


def save_attachment_paths(email_id: str, paths: list[str]):
    """Update email_cache with attachment file paths."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE email_cache SET attachment_paths = %s WHERE id = %s",
            (paths, email_id),
        )


# --- Email Queries ---

def get_email(email_id: str) -> dict | None:
    """Get a single email by ID."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM email_cache WHERE id = %s", (email_id,)
        ).fetchone()
    return db.serialize_row(row) if row else None


def get_thread(thread_id: str) -> list[dict]:
    """Get all emails in a thread, ordered by timestamp."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM email_cache
               WHERE thread_id = %s
               ORDER BY timestamp""",
            (thread_id,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def search_emails(query: str, limit: int = 20) -> list[dict]:
    """Full-text search across email bodies and subjects.

    Uses PostgreSQL tsvector for body+subject search, with ILIKE
    fallback for exact substring matches in subject.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT id, from_address, from_name, subject, snippet, timestamp
               FROM email_cache
               WHERE body_search @@ plainto_tsquery('english', %s)
                  OR subject ILIKE %s
               ORDER BY timestamp DESC
               LIMIT %s""",
            (query, f"%{query}%", limit),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_recent(hours: int = 24, limit: int = 50) -> list[dict]:
    """Get recent emails by timestamp."""
    cutoff = datetime.now() - timedelta(hours=hours)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM email_cache
               WHERE timestamp >= %s
               ORDER BY timestamp DESC
               LIMIT %s""",
            (cutoff, limit),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_unread_important(limit: int = 10) -> list[dict]:
    """Get emails with UNREAD label classified as important/urgent/actionable."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT e.id, e.from_address, e.from_name, e.subject, e.snippet,
                      e.timestamp, c.classification
               FROM email_cache e
               JOIN email_classifications c ON c.email_id = e.id
               WHERE 'UNREAD' = ANY(e.labels)
               AND c.classification IN ('important', 'urgent', 'actionable')
               ORDER BY e.timestamp DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_unclassified(limit: int = 50) -> list[dict]:
    """Get emails that haven't been classified yet."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT e.* FROM email_cache e
               LEFT JOIN email_classifications c ON c.email_id = e.id
               WHERE c.id IS NULL
               ORDER BY e.timestamp DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_email_count(hours: int = 24) -> dict:
    """Get counts of emails by classification in the last N hours."""
    cutoff = datetime.now() - timedelta(hours=hours)
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT c.classification, COUNT(*) as cnt
               FROM email_cache e
               JOIN email_classifications c ON c.email_id = e.id
               WHERE e.timestamp >= %s
               GROUP BY c.classification""",
            (cutoff,),
        ).fetchall()
    return {r["classification"]: int(r["cnt"]) for r in rows}


# --- Classification Storage ---

def save_classification(email_id: str, tier: str, classification: str,
                        confidence: float, reason: str,
                        category: str | None = None):
    """Insert a classification decision."""
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO email_classifications
               (email_id, tier, classification, confidence, reason, category)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (email_id, tier, classification, confidence, reason, category),
        )


def get_classification(email_id: str) -> dict | None:
    """Get the latest classification for an email."""
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM email_classifications
               WHERE email_id = %s
               ORDER BY timestamp DESC LIMIT 1""",
            (email_id,),
        ).fetchone()
    return db.serialize_row(row) if row else None


def record_user_override(email_id: str, override_text: str):
    """Record a user correction to a classification."""
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE email_classifications
               SET user_override = %s
               WHERE email_id = %s AND user_override IS NULL
               ORDER BY timestamp DESC LIMIT 1""",
            (override_text, email_id),
        )


# --- Email Watches ---

def add_watch(sender_pattern: str | None, content_pattern: str | None,
              classification: str = "important", description: str = "",
              expires_days: int = 30) -> int:
    """Create an email watch. Returns the watch ID."""
    expires_at = datetime.now() + timedelta(days=expires_days)
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO email_watches
               (sender_pattern, content_pattern, classification, description, expires_at)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (sender_pattern, content_pattern, classification, description, expires_at),
        ).fetchone()
    return row["id"]


def cancel_watch(watch_id: int) -> bool:
    """Cancel an active watch. Returns True if found and cancelled."""
    with db.get_conn() as conn:
        row = conn.execute(
            "UPDATE email_watches SET active = FALSE WHERE id = %s AND active = TRUE RETURNING id",
            (watch_id,),
        ).fetchone()
    return row is not None


def cancel_watch_by_description(description: str) -> bool:
    """Cancel active watches matching a description pattern. Returns True if any cancelled."""
    with db.get_conn() as conn:
        row = conn.execute(
            """UPDATE email_watches SET active = FALSE
               WHERE active = TRUE AND description ILIKE %s RETURNING id""",
            (f"%{description}%",),
        ).fetchone()
    return row is not None


def fulfill_watch(watch_id: int, email_id: str):
    """Mark a watch as fulfilled by a specific email."""
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE email_watches
               SET fulfilled_at = NOW(), fulfilled_email_id = %s, active = FALSE
               WHERE id = %s""",
            (email_id, watch_id),
        )


def get_active_watches() -> list[dict]:
    """Get all active, non-expired watches."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM email_watches
               WHERE active = TRUE
               AND (expires_at IS NULL OR expires_at > NOW())
               ORDER BY created_at DESC""",
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def expire_watches():
    """Deactivate watches that have passed their expiry."""
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE email_watches SET active = FALSE
               WHERE active = TRUE AND expires_at IS NOT NULL AND expires_at <= NOW()""",
        )


# --- Context Builders ---

def get_email_context(today: str) -> str:
    """Build email context string for ARIA context injection.

    Returns unread important count + summaries of today's important emails.
    """
    parts = []

    # Unread important emails
    unread = get_unread_important(limit=10)
    if unread:
        parts.append(f"Unread important emails ({len(unread)}):")
        for e in unread[:5]:
            sender = e.get("from_name") or e.get("from_address", "?")
            parts.append(f"  - {sender}: {e.get('subject', '(no subject)')}")
        if len(unread) > 5:
            parts.append(f"  ... and {len(unread) - 5} more")

    # Active email watches
    watches = get_active_watches()
    if watches:
        parts.append(f"Active email watches ({len(watches)}):")
        for w in watches:
            desc = w.get("description") or f"sender={w.get('sender_pattern', '?')}"
            parts.append(f"  - {desc}")

    # Today's email summary
    counts = get_email_count(hours=24)
    if counts:
        summary_parts = []
        for cls in ["important", "urgent", "actionable", "routine", "junk"]:
            if counts.get(cls, 0) > 0:
                summary_parts.append(f"{counts[cls]} {cls}")
        if summary_parts:
            parts.append("Today's email: " + ", ".join(summary_parts))

    return "\n".join(parts) if parts else ""


def get_briefing_context() -> str:
    """Build overnight email summary for morning briefings."""
    parts = []
    counts = get_email_count(hours=12)
    if not counts:
        parts.append("No new emails overnight.")
        return "\n".join(parts)

    total = sum(counts.values())
    important = counts.get("important", 0) + counts.get("urgent", 0) + counts.get("actionable", 0)
    routine = counts.get("routine", 0)
    junk = counts.get("junk", 0)

    parts.append(f"Overnight email: {total} new ({important} important, "
                 f"{routine} routine, {junk} filtered)")

    # List important ones
    unread = get_unread_important(limit=5)
    if unread:
        parts.append("Important emails:")
        for e in unread:
            sender = e.get("from_name") or e.get("from_address", "?")
            parts.append(f"  - {sender}: {e.get('subject', '(no subject)')}")

    return "\n".join(parts)
