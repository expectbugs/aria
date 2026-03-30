#!/usr/bin/env python3
"""One-time script: Archive all junk emails from Gmail INBOX.

Searches Gmail directly for always_junk domains/senders from gmail_rules.yaml,
then batch-removes the INBOX label. Emails stay in All Mail (reversible).

Usage:
    ./venv/bin/python archive_junk.py              # dry run (show counts only)
    ./venv/bin/python archive_junk.py --execute     # actually archive
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
import db
import gmail_store
from gmail_strategy import load_rules
from google_client import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("archive_junk")


def _get_rescued_domains(rules: dict) -> dict[str, list[str]]:
    """Find always_junk DOMAINS that have content_overrides rescuing some emails.

    Returns {domain: [content_keywords]} where content_keywords are words
    from the rescue override's content_pattern. If empty list, the rescue
    has no content_pattern (all emails from that sender are rescued) and
    the domain must be fully excluded from bulk archival.

    Domains with content_patterns can still be bulk-archived using Gmail
    search exclusions (e.g., 'from:@glassdoor.com -banker -wire in:inbox').
    """
    import re
    rescued: dict[str, list[str]] = {}
    junk_domains = set(d.lower() for d in rules.get("always_junk", {}).get("domains", []))

    for override in rules.get("content_overrides", []):
        if override.get("classification", "junk") == "junk":
            continue
        pat = override.get("sender_pattern") or ""
        if not pat:
            continue
        content_pat = override.get("content_pattern") or ""
        for domain in junk_domains:
            test_addr = f"someone@{domain}"
            try:
                if re.search(pat, test_addr, re.IGNORECASE):
                    if domain not in rescued:
                        rescued[domain] = []
                    if content_pat:
                        # Extract simple words from the content regex for Gmail -word exclusions
                        words = re.findall(r'[a-zA-Z]{3,}', content_pat)
                        rescued[domain].extend(words)
                    # else: no content_pattern = blanket rescue, leave empty list
            except re.error:
                pass
    return rescued


def build_search_queries(rules: dict) -> list[str]:
    """Build Gmail search queries from always_junk rules.

    Excludes domains that have content_overrides rescuing some emails
    (e.g., betterhelp therapy messages, mercurycards transactions).
    Those are handled by the ongoing classifier-based auto-archive.

    Returns list of query strings, each under ~1500 chars to stay
    within Gmail search limits. All include 'in:inbox' filter.
    """
    rescued = _get_rescued_domains(rules)
    queries = []
    terms = []
    # Domains with content-pattern rescues get their own queries with -word exclusions
    rescued_queries = []

    # always_junk domains
    for domain in rules.get("always_junk", {}).get("domains", []):
        dl = domain.lower()
        if dl in rescued:
            if rescued[dl]:
                # Has content keywords — can archive with exclusions
                excl = " ".join(f"-{w}" for w in set(rescued[dl]))
                rescued_queries.append(f"from:@{domain} {excl} in:inbox")
            # else: blanket rescue, skip entirely
        else:
            terms.append(f"from:@{domain}")

    # always_junk senders
    for sender in rules.get("always_junk", {}).get("senders", []):
        terms.append(f"from:{sender}")

    # content_overrides with classification: junk (and no content_pattern —
    # meaning ALL emails from that sender are junk).
    # ONLY include simple single-word patterns. Multi-word patterns like
    # "AI with ALLIE" break Gmail search (bare words match everything).
    for override in rules.get("content_overrides", []):
        if override.get("classification") == "junk" and not override.get("content_pattern"):
            pat = override.get("sender_pattern", "")
            if not pat:
                continue
            # Split on | and only use single-word, domain-like patterns
            for part in pat.split("|"):
                clean = part.replace("\\.", ".").strip()
                if clean and " " not in clean:
                    terms.append(f"from:{clean}")

    # Split into queries of ~40 terms each (~1500 chars)
    chunk_size = 40
    for i in range(0, len(terms), chunk_size):
        chunk = terms[i:i + chunk_size]
        query = "{" + " ".join(chunk) + "} in:inbox"
        queries.append(query)

    # Add rescued-domain queries with exclusion words
    queries.extend(rescued_queries)

    return queries


async def collect_message_ids(client, queries: list[str]) -> list[str]:
    """Search Gmail with each query and collect all matching message IDs."""
    all_ids = set()
    for i, query in enumerate(queries):
        log.info("Search query %d/%d (length %d chars)...",
                 i + 1, len(queries), len(query))
        try:
            messages = await client.gmail_list_messages(query=query, max_results=5000)
            ids = [m["id"] for m in messages]
            log.info("  Found %d messages", len(ids))
            all_ids.update(ids)
        except Exception as e:
            log.error("  Search failed: %s", e)
    return list(all_ids)


async def run(execute: bool = False):
    rules = load_rules()
    rescued = _get_rescued_domains(rules)
    blanket = [d for d, words in rescued.items() if not words]
    partial = {d: words for d, words in rescued.items() if words}
    if blanket:
        log.info("Fully excluded %d domains (blanket rescue, no content filter): %s",
                 len(blanket), ", ".join(sorted(blanket)))
    if partial:
        log.info("Partial rescue %d domains (archive with keyword exclusions): %s",
                 len(partial), ", ".join(f"{d} (-{' -'.join(set(w))})" for d, w in sorted(partial.items())))
    queries = build_search_queries(rules)
    total_domains = len(rules.get("always_junk", {}).get("domains", []))
    log.info("Built %d search queries from %d junk domains (%d excluded) + %d junk senders",
             len(queries), total_domains, len(rescued),
             len(rules.get("always_junk", {}).get("senders", [])))

    client = get_client()
    message_ids = await collect_message_ids(client, queries)
    log.info("Total unique junk messages in INBOX: %d", len(message_ids))

    if not message_ids:
        log.info("Nothing to archive.")
        return

    if not execute:
        log.info("DRY RUN — pass --execute to actually archive these %d messages",
                 len(message_ids))
        return

    # Archive in batches
    log.info("Archiving %d messages (removing INBOX label)...", len(message_ids))
    total = await client.gmail_batch_modify(
        message_ids, remove_labels=["INBOX"])
    log.info("Archived %d messages via Gmail API", total)

    # Update local cache for any we have cached
    cached = gmail_store.archive_emails(message_ids)
    log.info("Updated %d local cache entries", cached)


def main():
    parser = argparse.ArgumentParser(description="Archive junk emails from Gmail INBOX")
    parser.add_argument("--execute", action="store_true",
                        help="Actually archive (default is dry run)")
    args = parser.parse_args()
    asyncio.run(run(execute=args.execute))


if __name__ == "__main__":
    main()
