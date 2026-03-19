"""Timer/scheduler store backed by a JSON file.

Supports relative (delay) and absolute timers with SMS or voice delivery.
The tick.py cron script checks for due timers every minute.
"""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import TIMER_DB


def _load() -> list[dict]:
    if not TIMER_DB.exists():
        return []
    return json.loads(TIMER_DB.read_text())


def _save(data: list[dict]) -> None:
    TIMER_DB.parent.mkdir(parents=True, exist_ok=True)
    TIMER_DB.write_text(json.dumps(data, indent=2, default=str))


def add_timer(label: str, fire_at: str, delivery: str = "sms",
              priority: str = "gentle", message: str = "",
              source: str = "user") -> dict:
    """Create a new timer.

    fire_at: ISO datetime string for when the timer should fire.
    delivery: "sms" or "voice"
    priority: "gentle" or "urgent" (urgent bypasses quiet hours)
    message: pre-composed message to deliver when the timer fires.
    source: "user" (via ACTION block) or "system" (nudge-generated)
    """
    timers = _load()
    timer = {
        "id": str(uuid.uuid4())[:8],
        "label": label,
        "fire_at": fire_at,
        "delivery": delivery,
        "priority": priority,
        "message": message,
        "source": source,
        "status": "pending",
        "created": datetime.now().isoformat(),
        "fired_at": None,
        "cancelled_at": None,
    }
    timers.append(timer)
    _save(timers)
    return timer


def cancel_timer(timer_id: str) -> bool:
    """Cancel a pending timer by ID."""
    timers = _load()
    for t in timers:
        if t["id"] == timer_id and t["status"] == "pending":
            t["status"] = "cancelled"
            t["cancelled_at"] = datetime.now().isoformat()
            _save(timers)
            return True
    return False


def complete_timer(timer_id: str) -> bool:
    """Mark a timer as fired."""
    timers = _load()
    for t in timers:
        if t["id"] == timer_id:
            t["status"] = "fired"
            t["fired_at"] = datetime.now().isoformat()
            _save(timers)
            return True
    return False


def get_due(now: datetime | None = None) -> list[dict]:
    """Return all pending timers whose fire_at is at or before now."""
    if now is None:
        now = datetime.now()
    now_str = now.isoformat()
    return [t for t in _load()
            if t["status"] == "pending" and t.get("fire_at", "9999") <= now_str]


def get_active() -> list[dict]:
    """Return all pending timers, sorted by fire_at."""
    timers = [t for t in _load() if t["status"] == "pending"]
    return sorted(timers, key=lambda t: t.get("fire_at", ""))


def get_timer(timer_id: str) -> dict | None:
    """Look up a single timer by ID."""
    for t in _load():
        if t["id"] == timer_id:
            return t
    return None
