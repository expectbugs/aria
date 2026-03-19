"""Health and physical log backed by a JSON file."""

import json
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import HEALTH_DB


def _load() -> list[dict]:
    if not HEALTH_DB.exists():
        return []
    return json.loads(HEALTH_DB.read_text())


def _save(data: list[dict]) -> None:
    HEALTH_DB.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_DB.write_text(json.dumps(data, indent=2, default=str))


def get_entries(days: Optional[int] = None,
                category: Optional[str] = None) -> list[dict]:
    """Get log entries, newest first. Optionally filter by recency or category."""
    entries = _load()
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        entries = [e for e in entries if e.get("date", "") >= cutoff]
    if category:
        entries = [e for e in entries if e.get("category") == category]
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    return entries


def get_patterns(days: int = 7) -> list[str]:
    """Detect patterns in the last N days of entries.

    Returns human-readable pattern strings like:
      "back pain reported 4 of last 7 days"
      "average sleep: 5.8 hours over last 7 days"
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    entries = [e for e in _load() if e.get("date", "") >= cutoff]
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
            # Group by the first meaningful word(s) of the description
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
    entries = _load()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "date": entry_date,
        "category": category,
        "description": description,
        "severity": severity,
        "sleep_hours": sleep_hours,
        "meal_type": meal_type,
        "created": datetime.now().isoformat(),
    }
    entries.append(entry)
    _save(entries)
    return entry


def delete_entry(entry_id: str) -> bool:
    """Delete an entry by ID."""
    entries = _load()
    new_entries = [e for e in entries if e["id"] != entry_id]
    if len(new_entries) == len(entries):
        return False
    _save(new_entries)
    return True
