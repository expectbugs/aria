"""Weather data via NWS API (free, no key needed)."""

import asyncio
import httpx
from config import WEATHER_LAT, WEATHER_LON, WEATHER_USER_AGENT

HEADERS = {"User-Agent": WEATHER_USER_AGENT, "Accept": "application/geo+json"}
BASE = "https://api.weather.gov"

# Cache grid info to avoid repeated lookups (it never changes for the same coordinates)
_grid_cache: dict[tuple, dict] = {}


async def _get_grid_info(lat: float = WEATHER_LAT, lon: float = WEATHER_LON) -> dict:
    """Get NWS grid point info for coordinates, with caching."""
    key = (lat, lon)
    if key in _grid_cache:
        return _grid_cache[key]

    async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
        resp = await client.get(f"{BASE}/points/{lat},{lon}")
        resp.raise_for_status()
        info = resp.json()["properties"]
        _grid_cache[key] = info
        return info


async def _fetch_with_retry(url: str, retries: int = 2) -> dict:
    """Fetch a URL with retries for transient NWS failures."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_error = e
            if attempt < retries:
                await asyncio.sleep(1)
    raise last_error


async def get_current_conditions(lat: float = WEATHER_LAT, lon: float = WEATHER_LON) -> dict:
    """Get current weather conditions from the nearest observation station."""
    grid = await _get_grid_info(lat, lon)

    stations_data = await _fetch_with_retry(grid["observationStations"])
    station_url = stations_data["features"][0]["id"]

    obs_data = await _fetch_with_retry(f"{station_url}/observations/latest")
    props = obs_data["properties"]

    temp_c = props["temperature"]["value"]
    temp_f = round(temp_c * 9 / 5 + 32, 1) if temp_c is not None else None
    wind_speed_kmh = props["windSpeed"]["value"]
    wind_mph = round(wind_speed_kmh * 0.621371, 1) if wind_speed_kmh is not None else None

    return {
        "description": props["textDescription"],
        "temperature_f": temp_f,
        "humidity": props["relativeHumidity"]["value"],
        "wind_mph": wind_mph,
        "wind_direction": props["windDirection"]["value"],
    }


async def get_forecast(lat: float = WEATHER_LAT, lon: float = WEATHER_LON) -> list[dict]:
    """Get the 7-day forecast."""
    grid = await _get_grid_info(lat, lon)
    data = await _fetch_with_retry(grid["forecast"])
    periods = data["properties"]["periods"]
    return [
        {
            "name": p["name"],
            "temperature": p["temperature"],
            "unit": p["temperatureUnit"],
            "summary": p["shortForecast"],
            "detail": p["detailedForecast"],
            "wind": p["windSpeed"],
            "wind_direction": p["windDirection"],
        }
        for p in periods[:6]
    ]


async def get_alerts(lat: float = WEATHER_LAT, lon: float = WEATHER_LON) -> list[dict]:
    """Get active weather alerts for the area."""
    grid = await _get_grid_info(lat, lon)
    zone = grid.get("forecastZone", "").split("/")[-1]
    if not zone:
        return []
    data = await _fetch_with_retry(f"{BASE}/alerts/active/zone/{zone}")
    features = data.get("features", [])
    return [
        {
            "event": f["properties"]["event"],
            "headline": f["properties"]["headline"],
            "severity": f["properties"]["severity"],
            "description": f["properties"]["description"],
        }
        for f in features
    ]
