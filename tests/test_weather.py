"""Tests for weather.py — NWS API integration."""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import weather


class TestGetGridInfo:
    @pytest.mark.asyncio
    async def test_caches_grid_info(self):
        weather._grid_cache.clear()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "properties": {
                "forecast": "https://api.weather.gov/gridpoints/MKX/71,55/forecast",
                "observationStations": "https://api.weather.gov/stations",
                "forecastZone": "https://api.weather.gov/zones/forecast/WIZ063",
            }
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("weather.httpx.AsyncClient", return_value=mock_http):
            info = await weather._get_grid_info(42.58, -88.43)
            assert "forecast" in info

            # Second call should use cache
            info2 = await weather._get_grid_info(42.58, -88.43)
            assert info2 is info
            # Only one HTTP call made
            assert mock_http.get.call_count == 1

        weather._grid_cache.clear()


class TestGetCurrentConditions:
    @pytest.mark.asyncio
    async def test_parses_conditions(self):
        with patch("weather._get_grid_info", new_callable=AsyncMock,
                   return_value={"observationStations": "https://stations"}), \
             patch("weather._fetch_with_retry", new_callable=AsyncMock) as mock_fetch:

            mock_fetch.side_effect = [
                # stations list
                {"features": [{"id": "https://station1"}]},
                # observation
                {"properties": {
                    "textDescription": "Partly Cloudy",
                    "temperature": {"value": 10},
                    "relativeHumidity": {"value": 55},
                    "windSpeed": {"value": 16},
                    "windDirection": {"value": 270},
                }},
            ]

            result = await weather.get_current_conditions()
            assert result["description"] == "Partly Cloudy"
            assert result["temperature_f"] == 50.0
            assert result["humidity"] == 55
            assert result["wind_mph"] == pytest.approx(9.94, rel=0.01)


class TestGetForecast:
    @pytest.mark.asyncio
    async def test_returns_periods(self):
        with patch("weather._get_grid_info", new_callable=AsyncMock,
                   return_value={"forecast": "https://forecast"}), \
             patch("weather._fetch_with_retry", new_callable=AsyncMock,
                   return_value={"properties": {"periods": [
                       {"name": "Today", "temperature": 52, "temperatureUnit": "F",
                        "shortForecast": "Sunny", "detailedForecast": "...",
                        "windSpeed": "10 mph", "windDirection": "SW"},
                       {"name": "Tonight", "temperature": 35, "temperatureUnit": "F",
                        "shortForecast": "Clear", "detailedForecast": "...",
                        "windSpeed": "5 mph", "windDirection": "W"},
                   ]}}):
            result = await weather.get_forecast()
            assert len(result) == 2
            assert result[0]["name"] == "Today"
            assert result[0]["temperature"] == 52


class TestGetAlerts:
    @pytest.mark.asyncio
    async def test_returns_alerts(self):
        with patch("weather._get_grid_info", new_callable=AsyncMock,
                   return_value={"forecastZone": "https://zones/WIZ063"}), \
             patch("weather._fetch_with_retry", new_callable=AsyncMock,
                   return_value={"features": [
                       {"properties": {
                           "event": "Wind Advisory",
                           "headline": "Wind advisory in effect",
                           "severity": "Moderate",
                           "description": "Winds up to 40 mph expected" * 10,
                       }}
                   ]}):
            result = await weather.get_alerts()
            assert len(result) == 1
            assert result[0]["event"] == "Wind Advisory"
            assert "Winds up to 40 mph expected" in result[0]["description"]

    @pytest.mark.asyncio
    async def test_no_zone(self):
        with patch("weather._get_grid_info", new_callable=AsyncMock,
                   return_value={"forecastZone": ""}):
            result = await weather.get_alerts()
            assert result == []


class TestFetchWithRetry:
    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        mock_http = AsyncMock()
        mock_resp_fail = MagicMock()
        mock_resp_fail.raise_for_status.side_effect = Exception("503")
        mock_resp_ok = MagicMock()
        mock_resp_ok.raise_for_status = MagicMock()
        mock_resp_ok.json.return_value = {"ok": True}

        mock_http.get.side_effect = [mock_resp_fail, mock_resp_ok]
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("weather.httpx.AsyncClient", return_value=mock_http), \
             patch("weather.asyncio.sleep", new_callable=AsyncMock):
            result = await weather._fetch_with_retry("https://test", retries=1)
            assert result["ok"] is True
