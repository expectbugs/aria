"""Location tracking backed by a JSON lines file with reverse geocoding."""

import json
import logging
import httpx
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import DATA_DIR

log = logging.getLogger("aria.location")

LOCATION_LOG = DATA_DIR / "location.jsonl"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "ARIA/1.0 (personal assistant)"

# In-memory cache of the latest location
_latest: dict | None = None

# Cache reverse geocode results by rounded coords to avoid redundant lookups
# Key: (rounded_lat, rounded_lon) at ~100m precision
_geocode_cache: dict[tuple[float, float], str] = {}


def _round_coords(lat: float, lon: float) -> tuple[float, float]:
    """Round coords to ~100m precision for cache keys."""
    return (round(lat, 3), round(lon, 3))


async def _reverse_geocode(lat: float, lon: float) -> str:
    """Resolve GPS coordinates to a human-readable address via Nominatim."""
    cache_key = _round_coords(lat, lon)
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(NOMINATIM_URL, params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
                "zoom": 18,
            }, headers={"User-Agent": USER_AGENT}, timeout=5.0)

            if resp.status_code == 200:
                data = resp.json()
                address = data.get("address", {})

                # Build a concise location string
                parts = []
                # Street address if available
                house = address.get("house_number", "")
                road = address.get("road", "")
                if road:
                    parts.append(f"{house} {road}".strip())

                # City/town/village
                city = (address.get("city") or address.get("town")
                        or address.get("village") or address.get("hamlet", ""))
                if city:
                    parts.append(city)

                # State
                state = address.get("state", "")
                if state:
                    parts.append(state)

                location_str = ", ".join(parts) if parts else data.get("display_name", "Unknown")
                _geocode_cache[cache_key] = location_str
                return location_str
    except Exception as e:
        log.debug("Reverse geocode failed: %s", e)

    return f"{lat:.4f}, {lon:.4f}"


async def record(lat: float, lon: float, accuracy: float | None = None,
                 speed: float | None = None, battery: int | None = None) -> dict:
    """Record a location update with reverse geocoding. Returns the saved entry."""
    global _latest

    location_name = await _reverse_geocode(lat, lon)

    entry = {
        "timestamp": datetime.now().isoformat(),
        "lat": lat,
        "lon": lon,
        "location": location_name,
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
