"""Google Calendar + Gmail API client with automatic token refresh.

Handles OAuth2 token management and provides async accessors for
Calendar events and Gmail message metadata. Uses raw httpx —
no google-api-python-client dependency.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

import httpx

import config

log = logging.getLogger("aria.google")

CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"


class GoogleClient:
    """Google Calendar + Gmail API client with auto-refreshing OAuth2 tokens."""

    def __init__(self):
        self._tokens: dict | None = None
        self._refresh_lock = asyncio.Lock()

    def _load_tokens(self) -> dict:
        if self._tokens:
            return self._tokens
        if not config.GOOGLE_TOKEN_FILE.exists():
            raise RuntimeError(
                "No Google tokens found. Run google_auth.py first."
            )
        self._tokens = json.loads(config.GOOGLE_TOKEN_FILE.read_text())
        return self._tokens

    def _save_tokens(self, new_tokens: dict):
        """Save tokens, preserving refresh_token if not in new response.

        Google does NOT return a new refresh_token on refresh — only
        access_token and expires_in are updated. The original refresh_token
        from the initial authorization must be preserved.
        """
        existing = {}
        if config.GOOGLE_TOKEN_FILE.exists():
            try:
                existing = json.loads(config.GOOGLE_TOKEN_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        # Preserve refresh_token if not in new response
        if "refresh_token" not in new_tokens and "refresh_token" in existing:
            new_tokens["refresh_token"] = existing["refresh_token"]
        self._tokens = new_tokens
        config.GOOGLE_TOKEN_FILE.write_text(json.dumps(new_tokens, indent=2))

    async def _refresh_tokens(self, expired_access_token: str = ""):
        """Refresh the access token using the refresh token.

        Uses a lock to prevent concurrent refresh stampedes when multiple
        parallel requests all hit 401 at the same time.
        """
        async with self._refresh_lock:
            # Re-check: another coroutine may have already refreshed
            self._tokens = None  # force reload from disk
            tokens = self._load_tokens()
            if expired_access_token and tokens["access_token"] != expired_access_token:
                log.info("Google tokens already refreshed by another request")
                return

            if "refresh_token" not in tokens:
                raise RuntimeError(
                    "No refresh_token in stored tokens. Re-run google_auth.py."
                )

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                        "client_id": config.GOOGLE_CLIENT_ID,
                        "client_secret": config.GOOGLE_CLIENT_SECRET,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Google token refresh failed ({resp.status_code}): {resp.text}"
                )
            new_tokens = resp.json()
            self._save_tokens(new_tokens)
            log.info("Google tokens refreshed")

    async def _request(self, url: str, params: dict | None = None) -> dict:
        """Make an authenticated API request with auto-refresh on 401."""
        tokens = self._load_tokens()

        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            resp = await client.get(
                url, headers=headers, params=params, timeout=15
            )

            if resp.status_code == 401:
                await self._refresh_tokens(expired_access_token=tokens["access_token"])
                tokens = self._load_tokens()
                headers = {"Authorization": f"Bearer {tokens['access_token']}"}
                resp = await client.get(
                    url, headers=headers, params=params, timeout=15
                )

            if resp.status_code == 429:
                log.warning("Google API rate limit hit")
                raise RuntimeError("Google API rate limit exceeded")

            resp.raise_for_status()
            return resp.json()

    async def _request_paginated(self, url: str,
                                  params: dict | None = None,
                                  key: str = "items",
                                  max_pages: int = 10) -> list:
        """Fetch all pages following nextPageToken."""
        results = []
        params = dict(params or {})
        for _ in range(max_pages):
            data = await self._request(url, params)
            results.extend(data.get(key, []))
            npt = data.get("nextPageToken")
            if not npt:
                break
            params["pageToken"] = npt
        return results

    # --- Google Calendar ---

    async def calendar_list_events(self, time_min: str, time_max: str,
                                    calendar_id: str = "primary") -> list[dict]:
        """List calendar events in a time range.

        time_min/time_max: RFC3339 timestamps (e.g. datetime.isoformat()).
        Returns raw event dicts from the API.
        """
        return await self._request_paginated(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events",
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": 250,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )

    async def calendar_get_event(self, event_id: str,
                                  calendar_id: str = "primary") -> dict:
        """Get a single calendar event by ID."""
        return await self._request(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
        )

    # --- Gmail ---

    async def gmail_list_messages(self, query: str = "",
                                   max_results: int = 50) -> list[dict]:
        """List Gmail message stubs (id + threadId only).

        query: Gmail search query (e.g. "is:unread", "newer_than:1d").
        """
        params = {"maxResults": max_results}
        if query:
            params["q"] = query
        return await self._request_paginated(
            f"{GMAIL_BASE}/users/me/messages",
            params=params,
            key="messages",
        )

    async def gmail_get_message(self, message_id: str,
                                 fmt: str = "metadata") -> dict:
        """Get a single Gmail message.

        fmt: "metadata" (headers+snippet), "full" (includes body), "minimal".
        """
        return await self._request(
            f"{GMAIL_BASE}/users/me/messages/{message_id}",
            params={"format": fmt},
        )

    async def gmail_fetch_recent(self, hours: int = 24,
                                  max_results: int = 50) -> list[dict]:
        """Fetch recent messages with metadata — list + batched get.

        Convenience method: lists message IDs, then fetches each with
        metadata format. Uses a semaphore to limit concurrency to 5
        to avoid hitting Google's rate limits.
        """
        stubs = await self.gmail_list_messages(
            query=f"newer_than:{hours}h",
            max_results=max_results,
        )
        if not stubs:
            return []

        # Fetch messages with limited concurrency to avoid 429s
        sem = asyncio.Semaphore(5)

        async def _fetch(stub):
            async with sem:
                return await self.gmail_get_message(stub["id"], fmt="metadata")

        coros = [_fetch(stub) for stub in stubs]
        results = await asyncio.gather(*coros, return_exceptions=True)

        messages = []
        for result in results:
            if isinstance(result, Exception):
                log.warning("Failed to fetch Gmail message: %s", result)
            else:
                messages.append(result)

        return messages


# Module-level singleton
_client: GoogleClient | None = None


def get_client() -> GoogleClient:
    global _client
    if _client is None:
        _client = GoogleClient()
    return _client
