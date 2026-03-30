"""Google Calendar + Gmail API client with automatic token refresh.

Handles OAuth2 token management and provides async accessors for
Calendar events (read-write) and Gmail messages (read + send).
Uses raw httpx — no google-api-python-client dependency.
"""

import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText

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

    # --- HTTP methods with auto-refresh ---

    async def _request(self, url: str, params: dict | None = None) -> dict:
        """GET with auto-refresh on 401."""
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

    async def _post(self, url: str, json_body: dict | None = None,
                    params: dict | None = None) -> dict:
        """POST with auto-refresh on 401."""
        tokens = self._load_tokens()

        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            }
            resp = await client.post(
                url, headers=headers, json=json_body, params=params, timeout=30
            )

            if resp.status_code == 401:
                await self._refresh_tokens(expired_access_token=tokens["access_token"])
                tokens = self._load_tokens()
                headers["Authorization"] = f"Bearer {tokens['access_token']}"
                resp = await client.post(
                    url, headers=headers, json=json_body, params=params, timeout=30
                )

            if resp.status_code == 429:
                log.warning("Google API rate limit hit")
                raise RuntimeError("Google API rate limit exceeded")

            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def _patch(self, url: str, json_body: dict) -> dict:
        """PATCH with auto-refresh on 401."""
        tokens = self._load_tokens()

        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            }
            resp = await client.patch(
                url, headers=headers, json=json_body, timeout=15
            )

            if resp.status_code == 401:
                await self._refresh_tokens(expired_access_token=tokens["access_token"])
                tokens = self._load_tokens()
                headers["Authorization"] = f"Bearer {tokens['access_token']}"
                resp = await client.patch(
                    url, headers=headers, json=json_body, timeout=15
                )

            if resp.status_code == 429:
                raise RuntimeError("Google API rate limit exceeded")

            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def _delete(self, url: str) -> bool:
        """DELETE with auto-refresh on 401. Returns True on success."""
        tokens = self._load_tokens()

        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            resp = await client.delete(url, headers=headers, timeout=15)

            if resp.status_code == 401:
                await self._refresh_tokens(expired_access_token=tokens["access_token"])
                tokens = self._load_tokens()
                headers = {"Authorization": f"Bearer {tokens['access_token']}"}
                resp = await client.delete(url, headers=headers, timeout=15)

            if resp.status_code == 429:
                raise RuntimeError("Google API rate limit exceeded")

            if resp.status_code in (200, 204):
                return True
            resp.raise_for_status()
            return False

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

    # --- Google Calendar (read-write) ---

    async def calendar_list_events(self, time_min: str, time_max: str,
                                    calendar_id: str = "primary") -> list[dict]:
        """List calendar events in a time range.

        time_min/time_max: RFC3339 timestamps (e.g. datetime.isoformat()).
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

    async def calendar_list_events_incremental(
            self, sync_token: str | None = None,
            calendar_id: str = "primary") -> tuple[list[dict], str | None]:
        """Incremental sync — returns (events, new_sync_token).

        First call (sync_token=None): fetches all events, returns syncToken.
        Subsequent calls: returns only changed events since last sync.
        On 410 Gone (expired syncToken): returns ([], None) — caller should
        do a full sync.
        """
        params: dict = {"maxResults": 250, "showDeleted": "true"}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            # First sync: fetch next 90 days
            now = datetime.now().astimezone().isoformat()
            future = (datetime.now() + timedelta(days=90)).astimezone().isoformat()
            params["timeMin"] = now
            params["timeMax"] = future
            params["singleEvents"] = "true"

        events = []
        new_token = None
        try:
            url = f"{CALENDAR_BASE}/calendars/{calendar_id}/events"
            while True:
                data = await self._request(url, params)
                events.extend(data.get("items", []))
                npt = data.get("nextPageToken")
                if npt:
                    params["pageToken"] = npt
                    continue
                new_token = data.get("nextSyncToken")
                break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 410:
                log.warning("Google Calendar syncToken expired, need full sync")
                return [], None
            raise

        return events, new_token

    async def calendar_get_event(self, event_id: str,
                                  calendar_id: str = "primary") -> dict:
        """Get a single calendar event by ID."""
        return await self._request(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
        )

    async def calendar_create_event(
            self, summary: str, start: str, end: str,
            location: str | None = None,
            description: str | None = None,
            calendar_id: str = "primary") -> dict:
        """Create a calendar event. Returns the created event dict.

        start/end: RFC3339 timestamps for timed events, or YYYY-MM-DD for all-day.
        """
        body: dict = {"summary": summary}
        # All-day events use 'date', timed events use 'dateTime'
        if "T" in start:
            body["start"] = {"dateTime": start}
            body["end"] = {"dateTime": end}
        else:
            body["start"] = {"date": start}
            body["end"] = {"date": end}
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        return await self._post(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events", json_body=body
        )

    async def calendar_update_event(
            self, event_id: str, calendar_id: str = "primary",
            **fields) -> dict:
        """Update a calendar event (partial update via PATCH)."""
        return await self._patch(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
            json_body=fields,
        )

    async def calendar_delete_event(
            self, event_id: str,
            calendar_id: str = "primary") -> bool:
        """Delete a calendar event. Returns True on success."""
        return await self._delete(
            f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
        )

    # --- Gmail (read + send) ---

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

    async def gmail_get_full_message(self, message_id: str) -> dict:
        """Get a Gmail message with full body extracted.

        Fetches with format=full, extracts plain text body from MIME parts,
        and adds a 'body' key to the returned dict.
        """
        msg = await self.gmail_get_message(message_id, fmt="full")
        msg["body"] = _extract_body(msg.get("payload", {}))
        return msg

    async def gmail_fetch_recent(self, hours: int = 24,
                                  max_results: int = 50,
                                  full_body: bool = False) -> list[dict]:
        """Fetch recent messages — list + batched get.

        Uses a semaphore to limit concurrency to 5 to avoid rate limits.
        If full_body=True, fetches format=full and extracts body text.
        """
        stubs = await self.gmail_list_messages(
            query=f"newer_than:{hours}h",
            max_results=max_results,
        )
        if not stubs:
            return []

        sem = asyncio.Semaphore(5)
        fmt = "full" if full_body else "metadata"

        async def _fetch(stub):
            async with sem:
                msg = await self.gmail_get_message(stub["id"], fmt=fmt)
                if full_body:
                    msg["body"] = _extract_body(msg.get("payload", {}))
                return msg

        coros = [_fetch(stub) for stub in stubs]
        results = await asyncio.gather(*coros, return_exceptions=True)

        messages = []
        for result in results:
            if isinstance(result, Exception):
                log.warning("Failed to fetch Gmail message: %s", result)
            else:
                messages.append(result)

        return messages

    async def gmail_send_message(
            self, to: str, subject: str, body: str,
            in_reply_to: str | None = None,
            thread_id: str | None = None) -> dict:
        """Send an email via Gmail API.

        Constructs an RFC2822 message, base64url encodes it, and sends
        via the Gmail messages.send endpoint.

        in_reply_to: Message-ID header of the email being replied to.
        thread_id: Gmail thread ID to keep the reply in the same thread.
        """
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to

        # base64url encode (no padding)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        send_body: dict = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id

        return await self._post(
            f"{GMAIL_BASE}/users/me/messages/send",
            json_body=send_body,
        )

    async def gmail_trash_message(self, message_id: str) -> dict:
        """Move a message to trash. Returns the updated message."""
        return await self._post(
            f"{GMAIL_BASE}/users/me/messages/{message_id}/trash"
        )

    async def gmail_modify_labels(self, message_id: str,
                                   add_labels: list[str] | None = None,
                                   remove_labels: list[str] | None = None) -> dict:
        """Modify labels on a single message. Returns updated message."""
        body: dict = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels
        return await self._post(
            f"{GMAIL_BASE}/users/me/messages/{message_id}/modify",
            json_body=body,
        )

    async def gmail_batch_modify(self, message_ids: list[str],
                                  add_labels: list[str] | None = None,
                                  remove_labels: list[str] | None = None) -> int:
        """Modify labels on multiple messages. Returns count modified.

        Gmail API limit: 1000 IDs per call. Automatically splits larger
        batches into multiple calls.
        """
        if not message_ids:
            return 0
        total = 0
        for i in range(0, len(message_ids), 1000):
            chunk = message_ids[i:i + 1000]
            body: dict = {"ids": chunk}
            if add_labels:
                body["addLabelIds"] = add_labels
            if remove_labels:
                body["removeLabelIds"] = remove_labels
            await self._post(
                f"{GMAIL_BASE}/users/me/messages/batchModify",
                json_body=body,
            )
            total += len(chunk)
        return total

    async def gmail_get_attachment(self, message_id: str,
                                    attachment_id: str) -> bytes:
        """Download a Gmail attachment. Returns raw bytes."""
        data = await self._request(
            f"{GMAIL_BASE}/users/me/messages/{message_id}/attachments/{attachment_id}"
        )
        # Gmail returns base64url-encoded data
        return base64.urlsafe_b64decode(data.get("data", ""))

    async def gmail_get_labels(self) -> list[dict]:
        """List all Gmail labels."""
        data = await self._request(f"{GMAIL_BASE}/users/me/labels")
        return data.get("labels", [])


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload.

    Gmail MIME structure varies:
    - Simple: payload.body.data (base64url encoded)
    - Multipart: payload.parts[] — prefer text/plain, fallback text/html
    - Nested multipart: recursive walk
    """
    mime_type = payload.get("mimeType", "")

    # Simple message — body directly in payload
    body_data = payload.get("body", {}).get("data", "")
    if body_data and mime_type == "text/plain":
        return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")

    # Multipart — walk parts
    parts = payload.get("parts", [])
    if parts:
        # First try: find text/plain
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Recurse into nested multipart
        for part in parts:
            if part.get("mimeType", "").startswith("multipart/"):
                result = _extract_body(part)
                if result:
                    return result

        # Fallback: text/html stripped (better than nothing)
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    # Basic HTML stripping — good enough for search indexing
                    import re
                    text = re.sub(r'<[^>]+>', ' ', html)
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text

    # Simple non-text/plain (e.g., text/html at top level)
    if body_data:
        decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        if mime_type == "text/html":
            import re
            decoded = re.sub(r'<[^>]+>', ' ', decoded)
            decoded = re.sub(r'\s+', ' ', decoded).strip()
        return decoded

    return ""


# Module-level singleton
_client: GoogleClient | None = None


def get_client() -> GoogleClient:
    global _client
    if _client is None:
        _client = GoogleClient()
    return _client
