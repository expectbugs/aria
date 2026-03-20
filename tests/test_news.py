"""Tests for news.py — RSS feed fetching."""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import news


class TestFetchFeed:
    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<?xml version="1.0"?>
        <rss version="2.0">
          <channel>
            <item><title>Article 1</title><description>Summary 1</description></item>
            <item><title>Article 2</title><description>Summary 2</description></item>
          </channel>
        </rss>"""
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("news.httpx.AsyncClient", return_value=mock_http):
            items = await news.fetch_feed("tech", "https://example.com/rss", max_items=2)

        assert len(items) == 2
        assert items[0]["title"] == "Article 1"

    @pytest.mark.asyncio
    async def test_max_items_limit(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<?xml version="1.0"?>
        <rss version="2.0">
          <channel>
            <item><title>A1</title></item>
            <item><title>A2</title></item>
            <item><title>A3</title></item>
          </channel>
        </rss>"""
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("news.httpx.AsyncClient", return_value=mock_http):
            items = await news.fetch_feed("tech", "https://test", max_items=1)
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self):
        mock_http = AsyncMock()
        mock_http.get.side_effect = Exception("Network error")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("news.httpx.AsyncClient", return_value=mock_http):
            items = await news.fetch_feed("tech", "https://down.example.com")
        assert items == []


class TestGetNewsDigest:
    @pytest.mark.asyncio
    async def test_aggregates_feeds(self):
        with patch("news.fetch_feed", new_callable=AsyncMock,
                   side_effect=[
                       [{"title": "Tech 1", "summary": ""}],
                       [{"title": "WI 1", "summary": ""}],
                       [],  # manufacturing feed empty
                   ]):
            digest = await news.get_news_digest(max_per_feed=3)
        assert "tech" in digest
        assert "wisconsin" in digest
        assert "manufacturing" not in digest  # empty result excluded

    @pytest.mark.asyncio
    async def test_all_empty(self):
        with patch("news.fetch_feed", new_callable=AsyncMock, return_value=[]):
            digest = await news.get_news_digest()
        assert digest == {}
