"""Legal case log backed by a JSON file."""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import LEGAL_DB


def _load() -> list[dict]:
    if not LEGAL_DB.exists():
        return []
    return json.loads(LEGAL_DB.read_text())


def _save(data: list[dict]) -> None:
    LEGAL_DB.parent.mkdir(parents=True, exist_ok=True)
    LEGAL_DB.write_text(json.dumps(data, indent=2, default=str))


def get_entries(limit: Optional[int] = None,
                entry_type: Optional[str] = None) -> list[dict]:
    """Get log entries, newest first. Optionally filter by entry_type."""
    entries = _load()
    if entry_type:
        entries = [e for e in entries if e.get("entry_type") == entry_type]
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    if limit:
        entries = entries[:limit]
    return entries


def get_upcoming_dates() -> list[dict]:
    """Get entries with entry_type 'court_date' or 'deadline' that are today or later."""
    today = datetime.now().strftime("%Y-%m-%d")
    entries = _load()
    upcoming = [
        e for e in entries
        if e.get("entry_type") in ("court_date", "deadline")
        and e.get("date", "") >= today
    ]
    return sorted(upcoming, key=lambda e: e.get("date", ""))


def add_entry(entry_date: str, entry_type: str, description: str,
              contacts: Optional[list[str]] = None) -> dict:
    """Add a legal case log entry.

    entry_type: development, filing, contact, note, court_date, deadline
    contacts: list of people/entities mentioned (optional)
    """
    entries = _load()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "date": entry_date,
        "entry_type": entry_type,
        "description": description,
        "contacts": contacts or [],
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
