"""ARIA training data collection — tool traces, entity mentions, interaction quality.

Collects data for future LoRA training and Neo4j knowledge graph population.
All log functions are non-fatal: exceptions are caught and logged, never
propagated to callers. Training data collection must never break the
production request pipeline.
"""

import json
import logging
import re

import config
import db

log = logging.getLogger("aria.training")

# ---------------------------------------------------------------------------
# Pre-compiled regex for entity extraction
# ---------------------------------------------------------------------------

# Capitalized multi-word names (e.g., "John Smith", "Dr. Draper")
_NAME_PATTERN = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b')

# Common false positives for name extraction — full phrases
_NAME_EXCLUSIONS = {
    "Good Morning", "Good Night", "Good Afternoon", "Good Evening",
    "New Year", "United States", "Action ARIA", "ARIA Primary",
}

# Individual words that disqualify a name match (days, months, etc.)
_NAME_EXCLUDED_WORDS = {
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
}

# Known topic categories with word-boundary patterns
_TOPIC_PATTERNS = [
    ("health", re.compile(
        r'\b(pain|sleep|exercise|symptom|headache|medication|workout|'
        r'nafld|liver|blood pressure|heart rate)\b', re.IGNORECASE)),
    ("nutrition", re.compile(
        r'\b(calories|protein|fiber|sodium|cholesterol|omega[- ]?3|'
        r'choline|magnesium|vitamin|supplement|diet|deficit)\b', re.IGNORECASE)),
    ("legal", re.compile(
        r'\b(court|lawyer|attorney|hearing|filing|deposition|subpoena)\b',
        re.IGNORECASE)),
    ("vehicle", re.compile(
        r'\b(oil change|tire|brake|maintenance|mileage|xterra)\b',
        re.IGNORECASE)),
]


# ---------------------------------------------------------------------------
# Log functions (all non-fatal)
# ---------------------------------------------------------------------------

def log_tool_trace(request_input: str, tool_name: str,
                   tool_input: str, tool_output: str) -> dict | None:
    """Log a tool usage trace for LoRA training data."""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                """INSERT INTO tool_traces
                   (request_input, tool_name, tool_input, tool_output)
                   VALUES (%s, %s, %s, %s)
                   RETURNING *""",
                (request_input, tool_name, tool_input, tool_output),
            ).fetchone()
        return db.serialize_row(row) if row else None
    except Exception as e:
        log.error("Failed to log tool trace: %s", e)
        return None


def log_entity_mention(source: str, entity_type: str, entity_value: str,
                       context_snippet: str | None = None,
                       source_id: str | None = None) -> dict | None:
    """Log an entity mention for future knowledge graph population."""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                """INSERT INTO entity_mentions
                   (source, entity_type, entity_value, context_snippet, source_id)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING *""",
                (source, entity_type, entity_value, context_snippet, source_id),
            ).fetchone()
        return db.serialize_row(row) if row else None
    except Exception as e:
        log.error("Failed to log entity mention: %s", e)
        return None


def log_interaction_quality(request_id: int | None, quality_signal: str,
                            details: str | None = None) -> dict | None:
    """Log an interaction quality signal for LoRA preference training."""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                """INSERT INTO interaction_quality
                   (request_id, quality_signal, details)
                   VALUES (%s, %s, %s)
                   RETURNING *""",
                (request_id, quality_signal, details),
            ).fetchone()
        return db.serialize_row(row) if row else None
    except Exception as e:
        log.error("Failed to log interaction quality: %s", e)
        return None


# ---------------------------------------------------------------------------
# Query functions (for future LoRA training export)
# ---------------------------------------------------------------------------

def get_tool_traces(days: int | None = None,
                    tool_name: str | None = None) -> list[dict]:
    """Retrieve tool traces, optionally filtered by recency and tool name."""
    try:
        with db.get_conn() as conn:
            conditions = []
            params = []
            if days is not None:
                conditions.append("timestamp >= NOW() - INTERVAL '%s days'")
                params.append(days)
            if tool_name is not None:
                conditions.append("tool_name = %s")
                params.append(tool_name)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            rows = conn.execute(
                f"SELECT * FROM tool_traces{where} ORDER BY timestamp DESC",
                params,
            ).fetchall()
        return [db.serialize_row(r) for r in rows]
    except Exception as e:
        log.error("Failed to query tool traces: %s", e)
        return []


def get_entity_mentions(entity_type: str | None = None,
                        limit: int = 100) -> list[dict]:
    """Retrieve entity mentions, optionally filtered by type."""
    try:
        with db.get_conn() as conn:
            if entity_type is not None:
                rows = conn.execute(
                    """SELECT * FROM entity_mentions
                       WHERE entity_type = %s
                       ORDER BY timestamp DESC LIMIT %s""",
                    (entity_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM entity_mentions
                       ORDER BY timestamp DESC LIMIT %s""",
                    (limit,),
                ).fetchall()
        return [db.serialize_row(r) for r in rows]
    except Exception as e:
        log.error("Failed to query entity mentions: %s", e)
        return []


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def extract_entities(text: str, source: str,
                     source_id: str | None = None
                     ) -> list[tuple[str, str, str]]:
    """Extract entities from text using regex patterns.

    Returns list of (entity_type, entity_value, context_snippet) tuples.
    Designed to be fast (<10ms) — all regex pre-compiled at module level.
    """
    if not text:
        return []

    results = []
    seen = set()  # deduplicate within a single extraction

    # 1. Known places from config
    known_places = getattr(config, "KNOWN_PLACES", {})
    text_lower = text.lower()
    for place_key in known_places:
        if place_key.lower() in text_lower:
            if ("place", place_key) not in seen:
                seen.add(("place", place_key))
                # Extract context snippet around the match
                idx = text_lower.index(place_key.lower())
                start = max(0, idx - 30)
                end = min(len(text), idx + len(place_key) + 30)
                snippet = text[start:end]
                results.append(("place", place_key, snippet))

    # 2. Known topics via word-boundary regex
    for topic, pattern in _TOPIC_PATTERNS:
        match = pattern.search(text)
        if match and ("topic", topic) not in seen:
            seen.add(("topic", topic))
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            snippet = text[start:end]
            results.append(("topic", topic, snippet))

    # 3. Capitalized multi-word names (person detection)
    for match in _NAME_PATTERN.finditer(text):
        name = match.group(1)
        if name in _NAME_EXCLUSIONS:
            continue
        # Reject if any word is a day/month name
        if any(word in _NAME_EXCLUDED_WORDS for word in name.split()):
            continue
        if ("person", name) not in seen:
            seen.add(("person", name))
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            snippet = text[start:end]
            results.append(("person", name, snippet))

    return results
