"""Vehicle maintenance log backed by a JSON file."""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import VEHICLE_DB


def _load() -> list[dict]:
    if not VEHICLE_DB.exists():
        return []
    return json.loads(VEHICLE_DB.read_text())


def _save(data: list[dict]) -> None:
    VEHICLE_DB.parent.mkdir(parents=True, exist_ok=True)
    VEHICLE_DB.write_text(json.dumps(data, indent=2, default=str))


def get_entries(limit: Optional[int] = None,
                event_type: Optional[str] = None) -> list[dict]:
    """Get log entries, newest first. Optionally filter by event_type."""
    entries = _load()
    if event_type:
        entries = [e for e in entries if e.get("event_type") == event_type]
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    if limit:
        entries = entries[:limit]
    return entries


def get_latest_by_type() -> dict[str, dict]:
    """Return the most recent entry for each event_type."""
    entries = _load()
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    latest: dict[str, dict] = {}
    for e in entries:
        t = e.get("event_type", "general")
        if t not in latest:
            latest[t] = e
    return latest


def add_entry(event_date: str, event_type: str, description: str,
              mileage: Optional[int] = None,
              cost: Optional[float] = None) -> dict:
    """Add a maintenance log entry."""
    entries = _load()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "date": event_date,
        "event_type": event_type,
        "description": description,
        "mileage": mileage,
        "cost": cost,
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
