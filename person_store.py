"""Person profile store, backed by PostgreSQL.

Auto-builds contact profiles from ambient conversation mentions.
Tracks who appears in conversations, their relationship, and frequency.
"""

import logging
from datetime import datetime

import db

log = logging.getLogger("aria.persons")


def upsert(name: str, relationship: str | None = None,
           organization: str | None = None,
           notes: str | None = None,
           aliases: list[str] | None = None) -> dict:
    """Create or update a person profile.

    Only non-None fields are updated on conflict (preserves existing data).
    """
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO person_profiles (name, relationship, organization, notes, aliases)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET
                   relationship = COALESCE(EXCLUDED.relationship, person_profiles.relationship),
                   organization = COALESCE(EXCLUDED.organization, person_profiles.organization),
                   notes = COALESCE(EXCLUDED.notes, person_profiles.notes),
                   aliases = CASE
                       WHEN EXCLUDED.aliases IS NOT NULL AND EXCLUDED.aliases != '{}'
                       THEN EXCLUDED.aliases
                       ELSE person_profiles.aliases
                   END
               RETURNING *""",
            (name, relationship, organization, notes, aliases or []),
        ).fetchone()
    return db.serialize_row(row)


def get(name: str) -> dict | None:
    """Get a person profile by exact name."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM person_profiles WHERE name = %s",
            (name,),
        ).fetchone()
    return db.serialize_row(row) if row else None


def get_by_id(person_id: int) -> dict | None:
    """Get a person profile by ID."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM person_profiles WHERE id = %s",
            (person_id,),
        ).fetchone()
    return db.serialize_row(row) if row else None


def search(query: str, limit: int = 20) -> list[dict]:
    """Search person profiles by name or alias (case-insensitive)."""
    pattern = f"%{query}%"
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM person_profiles
               WHERE name ILIKE %s
                  OR EXISTS (
                      SELECT 1 FROM unnest(aliases) AS alias
                      WHERE alias ILIKE %s
                  )
               ORDER BY mention_count DESC, name
               LIMIT %s""",
            (pattern, pattern, limit),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_all(limit: int = 100) -> list[dict]:
    """Get all person profiles, most mentioned first."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM person_profiles
               ORDER BY mention_count DESC, name
               LIMIT %s""",
            (limit,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def record_mention(name: str) -> bool:
    """Increment mention count and update last_mentioned timestamp.

    Returns True if the person exists, False otherwise.
    """
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE person_profiles
               SET mention_count = mention_count + 1,
                   last_mentioned = NOW()
               WHERE name = %s""",
            (name,),
        )
    return cur.rowcount > 0


def get_names() -> list[str]:
    """Get all known person names (for keyword matching in context injection)."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM person_profiles ORDER BY name",
        ).fetchall()
    return [r["name"] for r in rows]


def delete(name: str) -> bool:
    """Delete a person profile by name."""
    with db.get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM person_profiles WHERE name = %s",
            (name,),
        )
    return cur.rowcount > 0
