"""Tests for google_client.py — Google Calendar + Gmail API client."""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import google_client


class TestGoogleClientTokens:
    def test_load_tokens_from_file(self):
        client = google_client.GoogleClient()
        mock_tokens = {
            "access_token": "test_access",
            "refresh_token": "test_refresh",
            "scope": "calendar.readonly gmail.readonly",
        }
        with patch("google_client.config") as mock_config:
            mock_config.GOOGLE_TOKEN_FILE = MagicMock()
            mock_config.GOOGLE_TOKEN_FILE.exists.return_value = True
            mock_config.GOOGLE_TOKEN_FILE.read_text.return_value = json.dumps(mock_tokens)
            tokens = client._load_tokens()
        assert tokens["access_token"] == "test_access"
        assert tokens["refresh_token"] == "test_refresh"

    def test_load_tokens_no_file_raises(self):
        client = google_client.GoogleClient()
        with patch("google_client.config") as mock_config:
            mock_config.GOOGLE_TOKEN_FILE = MagicMock()
            mock_config.GOOGLE_TOKEN_FILE.exists.return_value = False
            with pytest.raises(RuntimeError, match="No Google tokens"):
                client._load_tokens()

    def test_save_tokens_preserves_refresh_token(self):
        """Google doesn't return refresh_token on refresh — must preserve it."""
        client = google_client.GoogleClient()
        existing = {"access_token": "old", "refresh_token": "keep_me", "expires_in": 3599}
        new_response = {"access_token": "new", "expires_in": 3599}  # no refresh_token

        with patch("google_client.config") as mock_config:
            mock_config.GOOGLE_TOKEN_FILE = MagicMock()
            mock_config.GOOGLE_TOKEN_FILE.exists.return_value = True
            mock_config.GOOGLE_TOKEN_FILE.read_text.return_value = json.dumps(existing)
            client._save_tokens(new_response)

        # Verify refresh_token was preserved in the saved data
        saved = json.loads(mock_config.GOOGLE_TOKEN_FILE.write_text.call_args[0][0])
        assert saved["refresh_token"] == "keep_me"
        assert saved["access_token"] == "new"

    def test_save_tokens_uses_new_refresh_token_if_provided(self):
        """If Google does provide a new refresh_token, use it."""
        client = google_client.GoogleClient()
        existing = {"access_token": "old", "refresh_token": "old_refresh"}
        new_response = {"access_token": "new", "refresh_token": "new_refresh"}

        with patch("google_client.config") as mock_config:
            mock_config.GOOGLE_TOKEN_FILE = MagicMock()
            mock_config.GOOGLE_TOKEN_FILE.exists.return_value = True
            mock_config.GOOGLE_TOKEN_FILE.read_text.return_value = json.dumps(existing)
            client._save_tokens(new_response)

        saved = json.loads(mock_config.GOOGLE_TOKEN_FILE.write_text.call_args[0][0])
        assert saved["refresh_token"] == "new_refresh"


class TestGoogleClientRefresh:
    @pytest.mark.asyncio
    async def test_refresh_updates_access_token(self):
        client = google_client.GoogleClient()
        old_tokens = {"access_token": "old", "refresh_token": "old_r"}

        new_tokens = {"access_token": "new", "expires_in": 3599}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = new_tokens

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("google_client.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens", return_value=old_tokens), \
             patch.object(client, "_save_tokens") as mock_save, \
             patch("google_client.config") as mock_config:
            mock_config.GOOGLE_CLIENT_ID = "test_id"
            mock_config.GOOGLE_CLIENT_SECRET = "test_secret"
            await client._refresh_tokens()
            mock_save.assert_called_once_with(new_tokens)

    @pytest.mark.asyncio
    async def test_refresh_failure_raises(self):
        client = google_client.GoogleClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("google_client.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens",
                         return_value={"access_token": "x", "refresh_token": "y"}), \
             patch("google_client.config") as mock_config:
            mock_config.GOOGLE_CLIENT_ID = "test_id"
            mock_config.GOOGLE_CLIENT_SECRET = "test_secret"
            with pytest.raises(RuntimeError, match="token refresh failed"):
                await client._refresh_tokens()

    @pytest.mark.asyncio
    async def test_refresh_no_refresh_token_raises(self):
        client = google_client.GoogleClient()
        with patch.object(client, "_load_tokens",
                         return_value={"access_token": "x"}):
            with pytest.raises(RuntimeError, match="No refresh_token"):
                await client._refresh_tokens()

    @pytest.mark.asyncio
    async def test_refresh_skips_if_already_refreshed(self):
        client = google_client.GoogleClient()
        # Simulate another coroutine already refreshed
        with patch.object(client, "_load_tokens",
                         return_value={"access_token": "new_token", "refresh_token": "r"}):
            # Pass the OLD token — _load_tokens returns a different one
            await client._refresh_tokens(expired_access_token="old_token")
            # Should return without making any HTTP request


class TestGoogleClientRequest:
    @pytest.mark.asyncio
    async def test_successful_request(self):
        client = google_client.GoogleClient()
        expected = {"items": [{"id": "1"}]}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = expected
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("google_client.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens",
                         return_value={"access_token": "tok"}):
            result = await client._request("https://example.com/api")
        assert result == expected

    @pytest.mark.asyncio
    async def test_auto_refresh_on_401(self):
        client = google_client.GoogleClient()

        # First call returns 401, second succeeds
        resp_401 = MagicMock()
        resp_401.status_code = 401

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"data": "ok"}
        resp_ok.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.side_effect = [resp_401, resp_ok]
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("google_client.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens",
                         return_value={"access_token": "tok"}), \
             patch.object(client, "_refresh_tokens", new_callable=AsyncMock):
            result = await client._request("https://example.com/api")
        assert result == {"data": "ok"}

    @pytest.mark.asyncio
    async def test_rate_limit_429(self):
        client = google_client.GoogleClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("google_client.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens",
                         return_value={"access_token": "tok"}):
            with pytest.raises(RuntimeError, match="rate limit"):
                await client._request("https://example.com/api")


class TestGoogleClientPagination:
    @pytest.mark.asyncio
    async def test_follows_next_page_token(self):
        client = google_client.GoogleClient()

        page1 = {"items": [{"id": "1"}], "nextPageToken": "page2"}
        page2 = {"items": [{"id": "2"}]}

        with patch.object(client, "_request", new_callable=AsyncMock,
                         side_effect=[page1, page2]):
            results = await client._request_paginated(
                "https://example.com/api", key="items"
            )
        assert len(results) == 2
        assert results[0]["id"] == "1"
        assert results[1]["id"] == "2"

    @pytest.mark.asyncio
    async def test_stops_without_next_page_token(self):
        client = google_client.GoogleClient()

        page = {"items": [{"id": "1"}]}  # no nextPageToken

        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value=page):
            results = await client._request_paginated(
                "https://example.com/api", key="items"
            )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_respects_max_pages(self):
        client = google_client.GoogleClient()

        # Always returns nextPageToken — should stop at max_pages
        page = {"items": [{"id": "1"}], "nextPageToken": "next"}

        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value=page) as mock_req:
            results = await client._request_paginated(
                "https://example.com/api", key="items", max_pages=3
            )
        assert mock_req.call_count == 3
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_empty_response(self):
        client = google_client.GoogleClient()

        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value={}):
            results = await client._request_paginated(
                "https://example.com/api", key="items"
            )
        assert results == []


class TestCalendarMethods:
    @pytest.mark.asyncio
    async def test_list_events_correct_url_and_params(self):
        client = google_client.GoogleClient()

        with patch.object(client, "_request_paginated",
                         new_callable=AsyncMock, return_value=[]) as mock:
            await client.calendar_list_events(
                time_min="2026-03-28T00:00:00",
                time_max="2026-04-04T00:00:00",
            )
        url, = mock.call_args[0]
        assert "/calendars/primary/events" in url
        params = mock.call_args[1]["params"]
        assert params["singleEvents"] == "true"
        assert params["orderBy"] == "startTime"

    @pytest.mark.asyncio
    async def test_get_event(self):
        client = google_client.GoogleClient()
        event = {"id": "evt1", "summary": "Test"}

        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value=event):
            result = await client.calendar_get_event("evt1")
        assert result["summary"] == "Test"


class TestGmailMethods:
    @pytest.mark.asyncio
    async def test_list_messages(self):
        client = google_client.GoogleClient()

        with patch.object(client, "_request_paginated",
                         new_callable=AsyncMock, return_value=[]) as mock:
            await client.gmail_list_messages(query="is:unread")
        _, = mock.call_args[0]
        params = mock.call_args[1]["params"]
        assert params["q"] == "is:unread"

    @pytest.mark.asyncio
    async def test_get_message(self):
        client = google_client.GoogleClient()
        msg = {"id": "msg1", "snippet": "Hello"}

        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value=msg):
            result = await client.gmail_get_message("msg1")
        assert result["snippet"] == "Hello"

    @pytest.mark.asyncio
    async def test_fetch_recent_parallel(self):
        client = google_client.GoogleClient()
        stubs = [{"id": "m1", "threadId": "t1"}, {"id": "m2", "threadId": "t2"}]
        msg1 = {"id": "m1", "snippet": "Hello"}
        msg2 = {"id": "m2", "snippet": "World"}

        with patch.object(client, "gmail_list_messages",
                         new_callable=AsyncMock, return_value=stubs), \
             patch.object(client, "gmail_get_message",
                         new_callable=AsyncMock, side_effect=[msg1, msg2]):
            results = await client.gmail_fetch_recent(hours=24)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_fetch_recent_empty(self):
        client = google_client.GoogleClient()

        with patch.object(client, "gmail_list_messages",
                         new_callable=AsyncMock, return_value=[]):
            results = await client.gmail_fetch_recent()
        assert results == []

    @pytest.mark.asyncio
    async def test_fetch_recent_handles_errors(self):
        client = google_client.GoogleClient()
        stubs = [{"id": "m1", "threadId": "t1"}, {"id": "m2", "threadId": "t2"}]
        msg1 = {"id": "m1", "snippet": "Hello"}

        with patch.object(client, "gmail_list_messages",
                         new_callable=AsyncMock, return_value=stubs), \
             patch.object(client, "gmail_get_message",
                         new_callable=AsyncMock,
                         side_effect=[msg1, RuntimeError("fetch failed")]):
            results = await client.gmail_fetch_recent()
        # One succeeded, one failed — should return the one that worked
        assert len(results) == 1
        assert results[0]["id"] == "m1"


class TestGetClient:
    def test_returns_singleton(self):
        google_client._client = None  # reset
        c1 = google_client.get_client()
        c2 = google_client.get_client()
        assert c1 is c2
        google_client._client = None  # cleanup
