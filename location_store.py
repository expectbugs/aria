"""Location tracking with reverse geocoding, backed by PostgreSQL."""

import logging
import httpx
from datetime import datetime, timedelta
from typing import Optional

import db

log = logging.getLogger("aria.location")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "ARIA/1.0 (personal assistant)"

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

                parts = []
                house = address.get("house_number", "")
                road = address.get("road", "")
                if road:
                    parts.append(f"{house} {road}".strip())

                city = (address.get("city") or address.get("town")
                        or address.get("village") or address.get("hamlet", ""))
                if city:
                    parts.append(city)

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
    location_name = await _reverse_geocode(lat, lon)

    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO locations (lat, lon, location, accuracy_m, speed_mps, battery_pct)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (lat, lon, location_name, accuracy, speed, battery),
        ).fetchone()
    return db.serialize_row(row)


def get_latest() -> dict | None:
    """Get the most recent location."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM locations ORDER BY timestamp DESC LIMIT 1",
        ).fetchone()
    return db.serialize_row(row) if row else None


def get_history(hours: int = 24) -> list[dict]:
    """Get location history for the last N hours, oldest first."""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM locations WHERE timestamp >= %s ORDER BY timestamp",
            (cutoff,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]
