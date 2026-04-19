"""News digest via RSS feeds."""

import asyncio
import logging

import feedparser
import httpx
from config import NEWS_FEEDS

log = logging.getLogger("aria")


async def fetch_feed(name: str, url: str, max_items: int = 3) -> list[dict]:
    """Fetch headlines from a single RSS feed."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        items = []
        for entry in feed.entries[:max_items]:
            items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", "") or "",
            })
        return items
    except Exception as e:
        log.warning("Failed to fetch %s feed: %s", name, e)
        return []


async def get_news_digest(max_per_feed: int = 3,
                          feeds: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """Fetch headlines from all configured feeds in parallel.

    feeds: optional dict {name: url} — defaults to config.NEWS_FEEDS (Adam's
    feeds). Pass config.BECKY_NEWS_FEEDS to build a per-user digest.
    """
    if feeds is None:
        feeds = NEWS_FEEDS
    names = list(feeds.keys())
    urls = list(feeds.values())
    results = await asyncio.gather(
        *[fetch_feed(n, u, max_per_feed) for n, u in zip(names, urls)]
    )
    digest = {}
    for name, items in zip(names, results):
        if items:
            digest[name] = items
    return digest
