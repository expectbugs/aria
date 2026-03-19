"""Location tracking backed by a JSON lines file."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import DATA_DIR

LOCATION_LOG = DATA_DIR / "location.jsonl"

# In-memory cache of the latest location
_latest: dict | None = None


def record(lat: float, lon: float, accuracy: float | None = None,
           speed: float | None = None, battery: int | None = None) -> dict:
    """Record a location update. Returns the saved entry."""
    global _latest
    entry = {
        "timestamp": datetime.now().isoformat(),
        "lat": lat,
        "lon": lon,
        "accuracy_m": accuracy,
        "speed_mps": speed,
        "battery_pct": battery,
    }
    LOCATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCATION_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    _latest = entry
    return entry


def get_latest() -> dict | None:
    """Get the most recent location. Uses in-memory cache if available."""
    global _latest
    if _latest:
        return _latest
    # Fall back to reading last line of the log
    if not LOCATION_LOG.exists():
        return None
    lines = LOCATION_LOG.read_text().strip().splitlines()
    if not lines:
        return None
    _latest = json.loads(lines[-1])
    return _latest


def get_history(hours: int = 24) -> list[dict]:
    """Get location history for the last N hours, oldest first."""
    if not LOCATION_LOG.exists():
        return []
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    entries = []
    for line in LOCATION_LOG.read_text().strip().splitlines():
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("timestamp", "") >= cutoff:
                entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries
