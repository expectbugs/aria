#!/usr/bin/env python3
"""Fetch web page text content with JavaScript rendering via Playwright.

Launches headless Chromium to fully render the page (including JS-loaded
content like supplement facts panels, dynamic tables, SPAs), then extracts
the visible text content.

Usage:
    python fetch_page.py "https://example.com"
    python fetch_page.py "https://example.com" --selector ".product-details"
    python fetch_page.py "https://example.com" --timeout 30000

Options:
    --selector CSS   Extract text only from elements matching this CSS selector
    --timeout MS     Page load timeout in milliseconds (default: 15000)
    --wait MS        Extra wait after load for lazy content (default: 0)

Usable by both ARIA (via shell commands) and Claude Code (via Bash tool).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def fetch(url: str, selector: str = "", timeout: int = 15000,
          wait: int = 0, idle_timeout: int = 5000) -> str:
    """Fetch and return visible text from a fully-rendered web page.

    Loads the DOM first (fast), then gives JS up to idle_timeout ms to
    finish rendering. Ad-heavy sites that never reach network idle are
    cut off after idle_timeout rather than blocking for the full timeout.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        except Exception as e:
            browser.close()
            raise RuntimeError(f"Failed to load {url}: {e}")

        # Give JS time to render dynamic content, but don't wait forever
        # for ad networks and trackers to finish
        try:
            page.wait_for_load_state("networkidle", timeout=idle_timeout)
        except Exception:
            pass  # DOM content is already loaded — proceed with what we have

        if wait > 0:
            page.wait_for_timeout(wait)

        if selector:
            elements = page.query_selector_all(selector)
            text = "\n\n".join(el.inner_text() for el in elements if el)
        else:
            text = page.inner_text("body")

        browser.close()
        return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch web page text with JS rendering")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--selector", default="",
                        help="CSS selector to extract specific content")
    parser.add_argument("--timeout", type=int, default=15000,
                        help="Page load timeout in ms (default: 15000)")
    parser.add_argument("--wait", type=int, default=0,
                        help="Extra wait after load in ms (default: 0)")
    args = parser.parse_args()

    try:
        text = fetch(args.url, args.selector, args.timeout, args.wait)
        print(text)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
