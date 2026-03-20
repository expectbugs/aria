"""Health and physical log backed by PostgreSQL."""

import uuid
from datetime import datetime, timedelta
from typing import Optional

import db


def get_entries(days: Optional[int] = None,
                category: Optional[str] = None) -> list[dict]:
    """Get log entries, newest first. Optionally filter by recency or category."""
    clauses = []
    params = []
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        clauses.append("date >= %s")
        params.append(cutoff)
    if category:
        clauses.append("category = %s")
        params.append(category)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM health_entries {where} ORDER BY date DESC",
            params,
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_patterns(days: int = 7) -> list[str]:
    """Detect patterns in the last N days of entries.

    Returns human-readable pattern strings like:
      "back pain reported 4 of last 7 days"
      "average sleep: 5.8 hours over last 7 days"
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM health_entries WHERE date >= %s",
            (cutoff,),
        ).fetchall()

    entries = [db.serialize_row(r) for r in rows]
    patterns = []

    # Count symptom/pain occurrences by description keyword
    pain_days: dict[str, set[str]] = {}
    sleep_hours: list[float] = []
    meal_days: set[str] = set()
    fish_days: set[str] = set()

    for e in entries:
        cat = e.get("category", "")
        date = e.get("date", "")
        desc = e.get("description", "").lower()

        if cat in ("pain", "symptom"):
            key = desc.split(",")[0].strip() if desc else "unspecified"
            pain_days.setdefault(key, set()).add(date)

        if cat == "sleep" and e.get("sleep_hours") is not None:
            sleep_hours.append(e["sleep_hours"])

        if cat == "meal":
            meal_days.add(date)
            fish_keywords = ["salmon", "fish", "mackerel", "sardine", "tuna"]
            if any(kw in desc for kw in fish_keywords):
                fish_days.add(date)

    for symptom, dates in pain_days.items():
        count = len(dates)
        if count >= 3:
            patterns.append(f"{symptom} reported {count} of last {days} days")

    if sleep_hours:
        avg = sum(sleep_hours) / len(sleep_hours)
        patterns.append(f"average sleep: {avg:.1f} hours over last {days} days")
        if avg < 6:
            patterns.append("warning: sleep average below 6 hours")

    if meal_days:
        patterns.append(f"meals logged {len(meal_days)} of last {days} days")
        if fish_days:
            patterns.append(f"fish/omega-3 meals: {len(fish_days)} days this week")
        elif days >= 7:
            patterns.append("no fish logged this week — target is 2-3 servings")

    return patterns


def add_entry(entry_date: str, category: str, description: str,
              severity: Optional[int] = None,
              sleep_hours: Optional[float] = None,
              meal_type: Optional[str] = None) -> dict:
    """Add a health log entry.

    category: pain, sleep, exercise, symptom, medication, meal, nutrition, general
    severity: 1-10 (optional, for pain/symptoms)
    sleep_hours: hours slept (optional, for sleep entries)
    meal_type: breakfast, lunch, dinner, snack (optional, for meal entries)
    """
    entry_id = str(uuid.uuid4())[:8]
    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO health_entries
               (id, date, category, description, severity, sleep_hours, meal_type)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (entry_id, entry_date, category, description, severity, sleep_hours, meal_type),
        ).fetchone()
    return db.serialize_row(row)


def delete_entry(entry_id: str) -> bool:
    """Delete an entry by ID."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM health_entries WHERE id = %s", (entry_id,))
    return cur.rowcount > 0
