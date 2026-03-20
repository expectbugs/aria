"""Integration tests for location_store — real SQL with mocked geocoding."""

from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock

import pytest

import location_store


class TestLocationRoundtrip:
    @pytest.mark.asyncio
    @patch("location_store._reverse_geocode", new_callable=AsyncMock,
           return_value="Rapids Trail, Waukesha, Wisconsin")
    async def test_record_and_retrieve(self, mock_geocode):
        entry = await location_store.record(
            lat=42.58, lon=-88.43, accuracy=10.0, speed=0.0, battery=85,
        )
        assert entry["lat"] == 42.58
        assert entry["lon"] == -88.43
        assert entry["location"] == "Rapids Trail, Waukesha, Wisconsin"
        assert entry["battery_pct"] == 85

        latest = location_store.get_latest()
        assert latest is not None
        assert latest["lat"] == 42.58

    @pytest.mark.asyncio
    @patch("location_store._reverse_geocode", new_callable=AsyncMock,
           return_value="Test Location")
    async def test_serial_id_auto_increment(self, mock_geocode):
        e1 = await location_store.record(lat=42.58, lon=-88.43)
        e2 = await location_store.record(lat=42.59, lon=-88.44)
        assert e2["id"] > e1["id"]

    @pytest.mark.asyncio
    @patch("location_store._reverse_geocode", new_callable=AsyncMock,
           return_value="Location")
    async def test_get_latest_returns_most_recent(self, mock_geocode):
        await location_store.record(lat=10.0, lon=20.0)
        await location_store.record(lat=30.0, lon=40.0)

        latest = location_store.get_latest()
        assert latest["lat"] == 30.0

    @pytest.mark.asyncio
    @patch("location_store._reverse_geocode", new_callable=AsyncMock,
           return_value="Location")
    async def test_get_history_ordered_oldest_first(self, mock_geocode):
        await location_store.record(lat=1.0, lon=1.0)
        await location_store.record(lat=2.0, lon=2.0)

        history = location_store.get_history(hours=1)
        assert len(history) == 2
        assert history[0]["lat"] == 1.0  # oldest first

    @pytest.mark.asyncio
    @patch("location_store._reverse_geocode", new_callable=AsyncMock,
           return_value="Location")
    async def test_history_filters_by_hours(self, mock_geocode):
        await location_store.record(lat=1.0, lon=1.0)
        # The entry just recorded should be within the last hour
        history = location_store.get_history(hours=1)
        assert len(history) == 1

    def test_get_latest_empty(self):
        assert location_store.get_latest() is None

    def test_get_history_empty(self):
        assert location_store.get_history(hours=24) == []

    @pytest.mark.asyncio
    @patch("location_store._reverse_geocode", new_callable=AsyncMock,
           return_value="Location")
    async def test_null_optional_fields(self, mock_geocode):
        entry = await location_store.record(lat=42.58, lon=-88.43)
        assert entry["accuracy_m"] is None
        assert entry["speed_mps"] is None
        assert entry["battery_pct"] is None
