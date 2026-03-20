"""Tests for fitbit.py — Fitbit Web API client."""

import json
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

import pytest

import fitbit


class TestFitbitClientTokens:
    def test_load_tokens_from_file(self):
        client = fitbit.FitbitClient()
        mock_tokens = {
            "access_token": "test_access",
            "refresh_token": "test_refresh",
            "user_id": "ABC123",
        }
        with patch("fitbit.config") as mock_config:
            mock_config.FITBIT_TOKEN_FILE = MagicMock()
            mock_config.FITBIT_TOKEN_FILE.exists.return_value = True
            mock_config.FITBIT_TOKEN_FILE.read_text.return_value = json.dumps(mock_tokens)
            tokens = client._load_tokens()
        assert tokens["access_token"] == "test_access"
        assert client._user_id == "ABC123"

    def test_load_tokens_no_file_raises(self):
        client = fitbit.FitbitClient()
        with patch("fitbit.config") as mock_config:
            mock_config.FITBIT_TOKEN_FILE = MagicMock()
            mock_config.FITBIT_TOKEN_FILE.exists.return_value = False
            with pytest.raises(RuntimeError, match="No Fitbit tokens"):
                client._load_tokens()

    def test_save_tokens(self):
        client = fitbit.FitbitClient()
        tokens = {"access_token": "new", "refresh_token": "new_r", "user_id": "XYZ"}
        with patch("fitbit.config") as mock_config:
            mock_config.FITBIT_TOKEN_FILE = MagicMock()
            client._save_tokens(tokens)
        mock_config.FITBIT_TOKEN_FILE.write_text.assert_called_once()
        assert client._user_id == "XYZ"


class TestFitbitClientRefresh:
    @pytest.mark.asyncio
    async def test_refresh_updates_tokens(self):
        client = fitbit.FitbitClient()
        old_tokens = {"access_token": "old", "refresh_token": "old_r", "user_id": "U"}

        new_tokens = {"access_token": "new", "refresh_token": "new_r", "user_id": "U"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = new_tokens

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("fitbit.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens", return_value=old_tokens), \
             patch.object(client, "_save_tokens") as mock_save, \
             patch("fitbit.config") as mock_config:
            mock_config.FITBIT_CLIENT_ID = "test_id"
            mock_config.FITBIT_CLIENT_SECRET = "test_secret"
            await client._refresh_tokens()
            mock_save.assert_called_once_with(new_tokens)

    @pytest.mark.asyncio
    async def test_refresh_failure_raises(self):
        client = fitbit.FitbitClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("fitbit.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens", return_value={
                 "access_token": "x", "refresh_token": "r", "user_id": "U"
             }), \
             patch("fitbit.config") as mock_config:
            mock_config.FITBIT_CLIENT_ID = "id"
            mock_config.FITBIT_CLIENT_SECRET = "secret"
            with pytest.raises(RuntimeError, match="Token refresh failed"):
                await client._refresh_tokens()


class TestFitbitClientRequest:
    @pytest.mark.asyncio
    async def test_successful_request(self):
        client = fitbit.FitbitClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": "test"}
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("fitbit.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens", return_value={
                 "access_token": "tok", "user_id": "U"
             }):
            result = await client._request("/test/path")
        assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_auto_refresh_on_401(self):
        client = fitbit.FitbitClient()

        # First response: 401, second: 200
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"data": "refreshed"}
        resp_200.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.side_effect = [resp_401, resp_200]
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("fitbit.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens", return_value={
                 "access_token": "tok", "user_id": "U"
             }), \
             patch.object(client, "_refresh_tokens", new_callable=AsyncMock):
            result = await client._request("/test")
        assert result["data"] == "refreshed"

    @pytest.mark.asyncio
    async def test_rate_limit_429(self):
        client = fitbit.FitbitClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 429

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("fitbit.httpx.AsyncClient", return_value=mock_http), \
             patch.object(client, "_load_tokens", return_value={
                 "access_token": "tok", "user_id": "U"
             }):
            with pytest.raises(RuntimeError, match="rate limit"):
                await client._request("/test")


class TestFitbitClientAPIMethods:
    """Verify each API method calls the right path."""

    @pytest.mark.asyncio
    async def test_get_heart_rate(self):
        client = fitbit.FitbitClient()
        client._user_id = "U"
        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value={"activities-heart": [{"value": {}}]}):
            result = await client.get_heart_rate("2026-03-20")
            client._request.assert_called_once()
            path = client._request.call_args[0][0]
            assert "heart" in path
            assert "2026-03-20" in path

    @pytest.mark.asyncio
    async def test_get_sleep(self):
        client = fitbit.FitbitClient()
        client._user_id = "U"
        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value={"sleep": []}):
            result = await client.get_sleep("2026-03-20")
            path = client._request.call_args[0][0]
            assert "sleep" in path

    @pytest.mark.asyncio
    async def test_get_activity_summary(self):
        client = fitbit.FitbitClient()
        client._user_id = "U"
        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value={"summary": {"steps": 5000}}):
            result = await client.get_activity_summary("2026-03-20")
            assert result == {"steps": 5000}

    @pytest.mark.asyncio
    async def test_fetch_daily_snapshot_parallel(self):
        client = fitbit.FitbitClient()
        client._user_id = "U"

        # Mock all individual methods
        for method_name in ["get_heart_rate", "get_hrv", "get_sleep",
                           "get_spo2", "get_activity_summary",
                           "get_breathing_rate", "get_temperature",
                           "get_vo2max"]:
            setattr(client, method_name,
                    AsyncMock(return_value={"test": method_name}))

        snapshot = await client.fetch_daily_snapshot("2026-03-20")
        assert snapshot["date"] == "2026-03-20"
        assert "fetched_at" in snapshot
        assert snapshot["heart_rate"]["test"] == "get_heart_rate"
        assert snapshot["sleep"]["test"] == "get_sleep"

    @pytest.mark.asyncio
    async def test_fetch_daily_snapshot_tolerates_failures(self):
        client = fitbit.FitbitClient()
        client._user_id = "U"
        client.get_heart_rate = AsyncMock(return_value={"rhr": 65})
        client.get_hrv = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
        client.get_sleep = AsyncMock(return_value={"sleep": []})
        client.get_spo2 = AsyncMock(return_value={})
        client.get_activity_summary = AsyncMock(return_value={})
        client.get_breathing_rate = AsyncMock(return_value={})
        client.get_temperature = AsyncMock(return_value={})
        client.get_vo2max = AsyncMock(return_value={})

        snapshot = await client.fetch_daily_snapshot("2026-03-20")
        assert "heart_rate" in snapshot
        assert "hrv" not in snapshot  # failed, so not included

    @pytest.mark.asyncio
    async def test_get_recent_heart_rate(self):
        client = fitbit.FitbitClient()
        client._user_id = "U"
        with patch.object(client, "_request", new_callable=AsyncMock,
                         return_value={
                             "activities-heart-intraday": {
                                 "dataset": [{"time": "14:00:00", "value": 75}]
                             }
                         }):
            readings = await client.get_recent_heart_rate(minutes=5)
            assert len(readings) == 1
            assert readings[0]["value"] == 75


class TestGetClient:
    def test_returns_singleton(self):
        fitbit._client = None
        c1 = fitbit.get_client()
        c2 = fitbit.get_client()
        assert c1 is c2
        fitbit._client = None
