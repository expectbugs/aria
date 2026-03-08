"""News digest via RSS feeds."""

import feedparser
import httpx
from config import NEWS_FEEDS


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
                "summary": entry.get("summary", "")[:200] if entry.get("summary") else "",
            })
        return items
    except Exception:
        return []


async def get_news_digest(max_per_feed: int = 3) -> dict[str, list[dict]]:
    """Fetch headlines from all configured feeds."""
    digest = {}
    for name, url in NEWS_FEEDS.items():
        items = await fetch_feed(name, url, max_per_feed)
        if items:
            digest[name] = items
    return digest
