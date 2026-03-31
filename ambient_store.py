"""Ambient transcript and conversation store, backed by PostgreSQL.

Stores individual utterances from the DJI Mic 3 ambient capture pipeline,
groups them into conversations, and provides full-text search via tsvector.
"""

import logging
from datetime import datetime, timedelta

import db

log = logging.getLogger("aria.ambient")


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------

def insert_transcript(source: str, text: str, started_at: str,
                      ended_at: str | None = None,
                      duration_s: float | None = None,
                      confidence: float | None = None,
                      audio_path: str | None = None,
                      speaker: str | None = None,
                      has_wake_word: bool = False) -> dict:
    """Insert an ambient transcript segment. Returns the new row."""
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO ambient_transcripts
               (source, text, started_at, ended_at, duration_s,
                confidence, audio_path, speaker, has_wake_word)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (source, text, started_at, ended_at, duration_s,
             confidence, audio_path, speaker, has_wake_word),
        ).fetchone()
    return db.serialize_row(row)


def get_recent(hours: int = 4, limit: int = 200) -> list[dict]:
    """Get recent transcripts, newest first."""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM ambient_transcripts
               WHERE started_at >= %s
               ORDER BY started_at DESC
               LIMIT %s""",
            (cutoff, limit),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_by_id(transcript_id: int) -> dict | None:
    """Get a single transcript by ID."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ambient_transcripts WHERE id = %s",
            (transcript_id,),
        ).fetchone()
    return db.serialize_row(row) if row else None


def search(query: str, days: int = 7, limit: int = 50) -> list[dict]:
    """Full-text search on transcripts using tsvector index."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT *, ts_rank(text_search, websearch_to_tsquery('english', %s)) AS rank
               FROM ambient_transcripts
               WHERE text_search @@ websearch_to_tsquery('english', %s)
                 AND started_at >= %s
               ORDER BY rank DESC, started_at DESC
               LIMIT %s""",
            (query, query, cutoff, limit),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_pending_quality(limit: int = 50) -> list[dict]:
    """Get transcripts awaiting quality pass (WhisperX refinement)."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM ambient_transcripts
               WHERE quality_pass = 'pending' AND audio_path IS NOT NULL
               ORDER BY started_at
               LIMIT %s""",
            (limit,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def mark_quality_done(transcript_id: int, quality_text: str | None = None,
                      quality_speaker: str | None = None) -> bool:
    """Mark a transcript's quality pass as done."""
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE ambient_transcripts
               SET quality_pass = 'done', quality_text = %s, quality_speaker = %s
               WHERE id = %s""",
            (quality_text, quality_speaker, transcript_id),
        )
    return cur.rowcount > 0


def get_unextracted(limit: int = 100) -> list[dict]:
    """Get transcripts not yet processed by the extraction engine."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM ambient_transcripts
               WHERE extracted = FALSE
               ORDER BY started_at
               LIMIT %s""",
            (limit,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def mark_extracted(transcript_ids: list[int]) -> int:
    """Mark transcripts as extracted. Returns count updated."""
    if not transcript_ids:
        return 0
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE ambient_transcripts SET extracted = TRUE WHERE id = ANY(%s)",
            (transcript_ids,),
        )
    return cur.rowcount


def cleanup_audio(retention_hours: int = 72) -> int:
    """Clear audio_path on transcripts older than retention period.

    Actual file deletion is handled by the caller (ambient_audio.py).
    Returns count of rows cleared.
    """
    cutoff = (datetime.now() - timedelta(hours=retention_hours)).isoformat()
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE ambient_transcripts
               SET audio_path = NULL
               WHERE audio_path IS NOT NULL AND started_at < %s""",
            (cutoff,),
        )
    return cur.rowcount


def get_today_count() -> int:
    """Count today's transcript segments."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM ambient_transcripts
               WHERE started_at >= %s""",
            (today,),
        ).fetchone()
    return row["cnt"] if row else 0


def get_today_duration() -> float:
    """Total duration in seconds of today's transcripts."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(duration_s), 0) AS total
               FROM ambient_transcripts
               WHERE started_at >= %s""",
            (today,),
        ).fetchone()
    return float(row["total"]) if row else 0.0


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def create_conversation(started_at: str, ended_at: str | None = None,
                        duration_s: float | None = None,
                        speakers: list[str] | None = None,
                        location: str | None = None) -> dict:
    """Create a conversation group. Returns the new row."""
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO ambient_conversations
               (started_at, ended_at, duration_s, speakers, location)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING *""",
            (started_at, ended_at, duration_s, speakers or [], location),
        ).fetchone()
    return db.serialize_row(row)


def get_conversation(conversation_id: int) -> dict | None:
    """Get a conversation by ID, including its transcript segments."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ambient_conversations WHERE id = %s",
            (conversation_id,),
        ).fetchone()
    if not row:
        return None
    result = db.serialize_row(row)
    # Attach transcript segments
    with db.get_conn() as conn:
        segments = conn.execute(
            """SELECT * FROM ambient_transcripts
               WHERE conversation_id = %s
               ORDER BY started_at""",
            (conversation_id,),
        ).fetchall()
    result["segments"] = [db.serialize_row(s) for s in segments]
    return result


def get_conversations(days: int = 7, limit: int = 50) -> list[dict]:
    """Get recent conversations, newest first."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM ambient_conversations
               WHERE started_at >= %s
               ORDER BY started_at DESC
               LIMIT %s""",
            (cutoff, limit),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def assign_to_conversation(transcript_ids: list[int],
                           conversation_id: int) -> int:
    """Assign transcript segments to a conversation. Returns count updated."""
    if not transcript_ids:
        return 0
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE ambient_transcripts
               SET conversation_id = %s
               WHERE id = ANY(%s)""",
            (conversation_id, transcript_ids),
        )
        # Update segment count on the conversation
        conn.execute(
            """UPDATE ambient_conversations
               SET segment_count = (
                   SELECT COUNT(*) FROM ambient_transcripts
                   WHERE conversation_id = %s
               )
               WHERE id = %s""",
            (conversation_id, conversation_id),
        )
    return cur.rowcount


def update_conversation(conversation_id: int, title: str | None = None,
                        summary: str | None = None,
                        speakers: list[str] | None = None) -> bool:
    """Update conversation metadata. Only sets non-None fields."""
    sets = []
    params = []
    if title is not None:
        sets.append("title = %s")
        params.append(title)
    if summary is not None:
        sets.append("summary = %s")
        params.append(summary)
    if speakers is not None:
        sets.append("speakers = %s")
        params.append(speakers)
    if not sets:
        return False
    params.append(conversation_id)
    with db.get_conn() as conn:
        cur = conn.execute(
            f"UPDATE ambient_conversations SET {', '.join(sets)} WHERE id = %s",
            params,
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Daily summaries
# ---------------------------------------------------------------------------

def upsert_daily_summary(day: str, summary: str,
                         key_topics: list[str] | None = None,
                         people_mentioned: list[str] | None = None,
                         commitments_made: int = 0,
                         conversation_count: int = 0,
                         total_duration_s: float = 0) -> dict:
    """Create or update a daily summary."""
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO daily_summaries
               (date, summary, key_topics, people_mentioned,
                commitments_made, conversation_count, total_duration_s)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (date) DO UPDATE SET
                   summary = EXCLUDED.summary,
                   key_topics = EXCLUDED.key_topics,
                   people_mentioned = EXCLUDED.people_mentioned,
                   commitments_made = EXCLUDED.commitments_made,
                   conversation_count = EXCLUDED.conversation_count,
                   total_duration_s = EXCLUDED.total_duration_s
               RETURNING *""",
            (day, summary, key_topics or [], people_mentioned or [],
             commitments_made, conversation_count, total_duration_s),
        ).fetchone()
    return db.serialize_row(row)


def get_daily_summary(day: str) -> dict | None:
    """Get the summary for a specific day."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM daily_summaries WHERE date = %s",
            (day,),
        ).fetchone()
    return db.serialize_row(row) if row else None
