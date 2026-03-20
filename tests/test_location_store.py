"""Tests for location_store.py — GPS tracking and reverse geocoding."""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import location_store
from helpers import make_location_row


def _patch_db():
    mock_conn = MagicMock()
    patcher = patch("location_store.db.get_conn")
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


class TestReverseGeocode:
    """Test the internal geocoding function."""

    @pytest.mark.asyncio
    @patch("location_store.httpx.AsyncClient")
    async def test_successful_geocode(self, mock_client_cls):
        location_store._geocode_cache.clear()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "address": {
                "house_number": "3549",
                "road": "Rapids Trail",
                "city": "Waukesha",
                "state": "Wisconsin",
            },
            "display_name": "3549 Rapids Trail, Waukesha, WI",
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await location_store._reverse_geocode(42.58, -88.43)
        assert "Rapids Trail" in result
        assert "Waukesha" in result

    @pytest.mark.asyncio
    async def test_cached_result(self):
        location_store._geocode_cache.clear()
        key = location_store._round_coords(42.58, -88.43)
        location_store._geocode_cache[key] = "Cached Location"
        result = await location_store._reverse_geocode(42.58, -88.43)
        assert result == "Cached Location"
        location_store._geocode_cache.clear()

    @pytest.mark.asyncio
    @patch("location_store.httpx.AsyncClient")
    async def test_geocode_failure_returns_coords(self, mock_client_cls):
        location_store._geocode_cache.clear()
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Network error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await location_store._reverse_geocode(42.58, -88.43)
        assert "42.5800" in result
        assert "-88.4300" in result

    def test_round_coords_precision(self):
        lat, lon = location_store._round_coords(42.58123, -88.43456)
        assert lat == 42.581
        assert lon == -88.435


class TestRecord:
    @pytest.mark.asyncio
    @patch("location_store._reverse_geocode", new_callable=AsyncMock)
    async def test_records_location(self, mock_geocode):
        mock_geocode.return_value = "Rapids Trail, Waukesha"
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_location_row()
        try:
            result = await location_store.record(
                lat=42.58, lon=-88.43, accuracy=10.0,
                speed=0.0, battery=85,
            )
            assert result["location"] == "Rapids Trail, Waukesha, Wisconsin"
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO locations" in sql
        finally:
            p.stop()


class TestGetLatest:
    def test_returns_latest(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_location_row()
        try:
            result = location_store.get_latest()
            assert result["lat"] == 42.58
            sql = mc.execute.call_args[0][0]
            assert "ORDER BY timestamp DESC LIMIT 1" in sql
        finally:
            p.stop()

    def test_returns_none_when_empty(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = None
        try:
            assert location_store.get_latest() is None
        finally:
            p.stop()


class TestGetHistory:
    def test_returns_sorted_history(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            make_location_row(id=1),
            make_location_row(id=2, lat=42.59),
        ]
        try:
            history = location_store.get_history(hours=4)
            assert len(history) == 2
            sql = mc.execute.call_args[0][0]
            assert "ORDER BY timestamp" in sql
            assert "timestamp >= %s" in sql
        finally:
            p.stop()

    def test_empty_history(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert location_store.get_history() == []
        finally:
            p.stop()
