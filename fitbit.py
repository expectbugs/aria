"""Fitbit Web API client with automatic token refresh.

Handles OAuth2 token management and provides typed accessors for all
health/fitness data types available from a Personal app registration.
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import httpx

import config

log = logging.getLogger("aria.fitbit")

API_BASE = "https://api.fitbit.com"


class FitbitClient:
    """Fitbit Web API client with auto-refreshing OAuth2 tokens."""

    def __init__(self):
        self._tokens: dict | None = None
        self._user_id: str = "-"  # "-" means current user in Fitbit API

    def _load_tokens(self) -> dict:
        if self._tokens:
            return self._tokens
        if not config.FITBIT_TOKEN_FILE.exists():
            raise RuntimeError(
                "No Fitbit tokens found. Run fitbit_auth.py first."
            )
        self._tokens = json.loads(config.FITBIT_TOKEN_FILE.read_text())
        self._user_id = self._tokens.get("user_id", "-")
        return self._tokens

    def _save_tokens(self, tokens: dict):
        self._tokens = tokens
        self._user_id = tokens.get("user_id", "-")
        config.FITBIT_TOKEN_FILE.write_text(json.dumps(tokens, indent=2))

    async def _refresh_tokens(self):
        """Refresh the access token using the refresh token."""
        tokens = self._load_tokens()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": config.FITBIT_CLIENT_ID,
                },
                auth=(config.FITBIT_CLIENT_ID, config.FITBIT_CLIENT_SECRET),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")
        new_tokens = resp.json()
        self._save_tokens(new_tokens)
        log.info("Fitbit tokens refreshed")

    async def _request(self, path: str, params: dict | None = None) -> dict:
        """Make an authenticated API request with auto-refresh on 401."""
        tokens = self._load_tokens()

        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            resp = await client.get(
                f"{API_BASE}{path}", headers=headers, params=params, timeout=15
            )

            if resp.status_code == 401:
                # Token expired, refresh and retry
                await self._refresh_tokens()
                tokens = self._load_tokens()
                headers = {"Authorization": f"Bearer {tokens['access_token']}"}
                resp = await client.get(
                    f"{API_BASE}{path}", headers=headers, params=params, timeout=15
                )

            if resp.status_code == 429:
                log.warning("Fitbit rate limit hit")
                raise RuntimeError("Fitbit rate limit exceeded")

            resp.raise_for_status()
            return resp.json()

    # --- Heart Rate ---

    async def get_heart_rate(self, day: str = "today") -> dict:
        """Get heart rate summary for a day (resting HR, zones)."""
        data = await self._request(
            f"/1/user/{self._user_id}/activities/heart/date/{day}/1d.json"
        )
        return data.get("activities-heart", [{}])[0]

    async def get_heart_rate_intraday(self, day: str = "today",
                                       detail: str = "1min") -> dict:
        """Get intraday heart rate (1sec or 1min resolution)."""
        data = await self._request(
            f"/1/user/{self._user_id}/activities/heart/date/{day}/1d/{detail}.json"
        )
        return data.get("activities-heart-intraday", {})

    # --- Heart Rate Variability ---

    async def get_hrv(self, day: str = "today") -> dict:
        """Get HRV summary (RMSSD, LF, HF, coverage)."""
        data = await self._request(
            f"/1/user/{self._user_id}/hrv/date/{day}.json"
        )
        entries = data.get("hrv", [])
        return entries[0] if entries else {}

    # --- Sleep ---

    async def get_sleep(self, day: str = "today") -> dict:
        """Get sleep log for a day (stages, duration, efficiency)."""
        data = await self._request(
            f"/1.2/user/{self._user_id}/sleep/date/{day}.json"
        )
        return data

    # --- SpO2 ---

    async def get_spo2(self, day: str = "today") -> dict:
        """Get blood oxygen saturation for a day."""
        data = await self._request(
            f"/1/user/{self._user_id}/spo2/date/{day}.json"
        )
        return data

    # --- Activity ---

    async def get_activity_summary(self, day: str = "today") -> dict:
        """Get daily activity summary (steps, distance, calories, AZM, etc.)."""
        data = await self._request(
            f"/1/user/{self._user_id}/activities/date/{day}.json"
        )
        return data.get("summary", {})

    async def get_activity_log(self, before_date: str | None = None,
                                limit: int = 10) -> list[dict]:
        """Get recent exercise/activity log entries."""
        params = {"limit": limit, "offset": 0, "sort": "desc"}
        if before_date:
            params["beforeDate"] = before_date
        data = await self._request(
            f"/1/user/{self._user_id}/activities/list.json", params=params
        )
        return data.get("activities", [])

    # --- Breathing Rate ---

    async def get_breathing_rate(self, day: str = "today") -> dict:
        """Get breathing rate summary."""
        data = await self._request(
            f"/1/user/{self._user_id}/br/date/{day}.json"
        )
        entries = data.get("br", [])
        return entries[0] if entries else {}

    # --- Skin Temperature ---

    async def get_temperature(self, day: str = "today") -> dict:
        """Get skin temperature variation from baseline."""
        data = await self._request(
            f"/1/user/{self._user_id}/temp/skin/date/{day}.json"
        )
        entries = data.get("tempSkin", [])
        return entries[0] if entries else {}

    # --- Cardio Fitness (VO2 Max) ---

    async def get_vo2max(self, day: str = "today") -> dict:
        """Get cardio fitness score (VO2 Max estimate)."""
        data = await self._request(
            f"/1/user/{self._user_id}/cardioscore/date/{day}.json"
        )
        entries = data.get("cardioScore", [])
        return entries[0] if entries else {}

    # --- Profile ---

    async def get_profile(self) -> dict:
        """Get user profile (for verifying connection)."""
        data = await self._request(f"/1/user/{self._user_id}/profile.json")
        return data.get("user", {})

    # --- Subscriptions ---

    async def create_subscription(self, collection: str = "") -> dict:
        """Create a webhook subscription for data change notifications.

        collection: "" for all, or "activities", "sleep", "foods", "body"
        """
        tokens = self._load_tokens()
        sub_id = "aria-1"
        path = f"/1/user/{self._user_id}"
        if collection:
            path += f"/{collection}"
        path += f"/apiSubscriptions/{sub_id}.json"

        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {tokens['access_token']}",
                "X-Fitbit-Subscriber-Id": sub_id,
            }
            resp = await client.post(f"{API_BASE}{path}", headers=headers)

            if resp.status_code == 401:
                await self._refresh_tokens()
                tokens = self._load_tokens()
                headers["Authorization"] = f"Bearer {tokens['access_token']}"
                resp = await client.post(f"{API_BASE}{path}", headers=headers)

            if resp.status_code in (200, 201):
                return resp.json()
            elif resp.status_code == 409:
                log.info("Fitbit subscription already exists")
                return {"status": "already_exists"}
            else:
                raise RuntimeError(
                    f"Subscription creation failed ({resp.status_code}): {resp.text}"
                )

    async def list_subscriptions(self) -> list[dict]:
        """List active webhook subscriptions."""
        data = await self._request(
            f"/1/user/{self._user_id}/apiSubscriptions.json"
        )
        return data.get("apiSubscriptions", [])

    # --- Lightweight Intraday (for tick polling) ---

    async def get_recent_heart_rate(self, minutes: int = 15) -> list[dict]:
        """Get the last N minutes of intraday heart rate at 1-min resolution.

        Returns list of {"time": "HH:MM:SS", "value": int} entries.
        Used by tick.py for exercise coaching without a full snapshot fetch.
        Only 1 API call.
        """
        now = datetime.now()
        start = (now - timedelta(minutes=minutes)).strftime("%H:%M")
        end = now.strftime("%H:%M")
        day = now.strftime("%Y-%m-%d")
        data = await self._request(
            f"/1/user/{self._user_id}/activities/heart/date/{day}/1d/1min/time/{start}/{end}.json"
        )
        return data.get("activities-heart-intraday", {}).get("dataset", [])

    async def get_recent_steps(self, minutes: int = 15) -> int:
        """Get step count for the last N minutes. 1 API call."""
        now = datetime.now()
        start = (now - timedelta(minutes=minutes)).strftime("%H:%M")
        end = now.strftime("%H:%M")
        day = now.strftime("%Y-%m-%d")
        data = await self._request(
            f"/1/user/{self._user_id}/activities/steps/date/{day}/1d/1min/time/{start}/{end}.json"
        )
        dataset = data.get("activities-steps-intraday", {}).get("dataset", [])
        return sum(int(d.get("value", 0)) for d in dataset)

    # --- Batch Fetch (used by webhook handler) ---

    async def fetch_daily_snapshot(self, day: str = "today") -> dict:
        """Fetch all daily data types in one batch. Returns a unified snapshot."""
        snapshot = {"date": day, "fetched_at": datetime.now().isoformat()}

        # Fetch all data types, tolerating individual failures
        fetchers = {
            "heart_rate": self.get_heart_rate(day),
            "hrv": self.get_hrv(day),
            "sleep": self.get_sleep(day),
            "spo2": self.get_spo2(day),
            "activity": self.get_activity_summary(day),
            "breathing_rate": self.get_breathing_rate(day),
            "temperature": self.get_temperature(day),
            "vo2max": self.get_vo2max(day),
        }

        for key, coro in fetchers.items():
            try:
                snapshot[key] = await coro
            except Exception as e:
                log.warning("Failed to fetch %s: %s", key, e)
                snapshot[key] = None

        return snapshot


# Module-level singleton
_client: FitbitClient | None = None


def get_client() -> FitbitClient:
    global _client
    if _client is None:
        _client = FitbitClient()
    return _client
