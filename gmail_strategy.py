"""Gmail three-tier classification engine.

Tier 1: Hard rules from data/gmail_rules.yaml (instant, deterministic)
Tier 2: Pattern scoring with weighted signals (instant, heuristic)
Tier 3: AI judgment via Haiku (only for uncertain emails, ~200ms)

Each email gets a ClassificationResult with classification, tier,
confidence, and human-readable reason.
"""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

import config

log = logging.getLogger("aria.gmail_strategy")

# Classification types
IMPORTANT = "important"
ROUTINE = "routine"
JUNK = "junk"
CONVERSATION = "conversation"
ACTIONABLE = "actionable"
URGENT = "urgent"

# Freemail domains (low-weight signal, not decisive)
_FREEMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "mail.com", "protonmail.com",
    "live.com", "msn.com",
}

# Promotional subject patterns
_PROMO_PATTERNS = re.compile(
    r'\b(\d+%\s*off|free shipping|sale|clearance|deal of|limited time|'
    r'order now|shop now|buy now|exclusive offer|unsubscribe|'
    r'coupon|promo code|flash sale|save \$|earn \$)\b',
    re.IGNORECASE,
)


@dataclass
class ClassificationResult:
    classification: str   # important, routine, junk, conversation, actionable, urgent
    tier: str             # tier1_hard, tier2_pattern, tier3_ai
    confidence: float     # 0.0-1.0
    reason: str           # human-readable explanation


# --- Rules Loading ---

_rules_cache: dict | None = None
_rules_mtime: float = 0


def load_rules() -> dict:
    """Load and cache gmail_rules.yaml with mtime check for hot-reload."""
    global _rules_cache, _rules_mtime

    rules_path = getattr(config, "GMAIL_RULES_FILE",
                         config.DATA_DIR / "gmail_rules.yaml")

    if not Path(rules_path).exists():
        return _default_rules()

    mtime = os.path.getmtime(rules_path)
    if _rules_cache is not None and mtime == _rules_mtime:
        return _rules_cache

    try:
        with open(rules_path) as f:
            _rules_cache = yaml.safe_load(f) or _default_rules()
        _rules_mtime = mtime
        return _rules_cache
    except Exception as e:
        log.error("Failed to load gmail_rules.yaml: %s", e)
        return _default_rules()


def _default_rules() -> dict:
    return {
        "always_important": {"senders": [], "domains": []},
        "always_junk": {"senders": [], "domains": []},
        "content_overrides": [],
        "conversation_threads": [],
    }


# --- Main Entry Point ---

def classify_email(email: dict) -> ClassificationResult:
    """Classify a single email through the 3-tier pipeline.

    email: dict from email_cache (must have from_address, subject, body, etc.)
    """
    # Tier 1: Hard rules
    result = _classify_tier1(email)
    if result:
        return result

    # Tier 2: Pattern scoring
    result = _classify_tier2(email)
    if result:
        return result

    # Tier 3: AI judgment
    return _classify_tier3(email)


def classify_batch(emails: list[dict]) -> list[ClassificationResult]:
    """Classify multiple emails. Tier 3 calls are sequential."""
    return [classify_email(e) for e in emails]


# --- Tier 1: Hard Rules ---

def _classify_tier1(email: dict) -> ClassificationResult | None:
    """Deterministic rules from gmail_rules.yaml."""
    rules = load_rules()
    from_addr = (email.get("from_address") or "").lower()
    from_domain = from_addr.split("@")[-1] if "@" in from_addr else ""
    subject = email.get("subject") or ""
    body = email.get("body") or ""
    content = f"{subject} {body}"

    # Always important senders/domains
    important = rules.get("always_important", {})
    if from_addr in [s.lower() for s in important.get("senders", [])]:
        return ClassificationResult(IMPORTANT, "tier1_hard", 1.0,
                                    f"Sender {from_addr} in always_important")
    if from_domain in [d.lower() for d in important.get("domains", [])]:
        return ClassificationResult(IMPORTANT, "tier1_hard", 1.0,
                                    f"Domain {from_domain} in always_important")

    # Always junk senders/domains
    junk = rules.get("always_junk", {})
    if from_addr in [s.lower() for s in junk.get("senders", [])]:
        return ClassificationResult(JUNK, "tier1_hard", 1.0,
                                    f"Sender {from_addr} in always_junk")
    if from_domain in [d.lower() for d in junk.get("domains", [])]:
        return ClassificationResult(JUNK, "tier1_hard", 1.0,
                                    f"Domain {from_domain} in always_junk")

    # Content overrides (per-sender content patterns)
    for override in rules.get("content_overrides", []):
        sender_pat = override.get("sender_pattern", "")
        content_pat = override.get("content_pattern", "")
        classification = override.get("classification", ROUTINE)
        if sender_pat and content_pat:
            if re.search(sender_pat, from_addr + " " + (email.get("from_name") or ""),
                        re.IGNORECASE):
                if re.search(content_pat, content, re.IGNORECASE):
                    return ClassificationResult(
                        classification, "tier1_hard", 0.9,
                        f"Content override: {sender_pat} + {content_pat}")

    # Conversation threads the user has replied to
    thread_id = email.get("thread_id")
    if thread_id and thread_id in rules.get("conversation_threads", []):
        return ClassificationResult(CONVERSATION, "tier1_hard", 0.95,
                                    "Active conversation thread")

    return None


# --- Tier 2: Pattern Scoring ---

def _classify_tier2(email: dict) -> ClassificationResult | None:
    """Weighted scoring from multiple signals."""
    score = 0
    signals = []
    from_addr = (email.get("from_address") or "").lower()
    from_domain = from_addr.split("@")[-1] if "@" in from_addr else ""
    subject = email.get("subject") or ""
    body = email.get("body") or ""
    labels = email.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]

    # Gmail category signals
    category = email.get("gmail_category") or ""
    if category in ("Promotions", "Forums"):
        score -= 2
        signals.append(f"Gmail category={category} (-2)")
    elif category == "Social":
        score -= 1
        signals.append(f"Gmail category=Social (-1)")

    # Domain reputation
    if from_domain in _FREEMAIL_DOMAINS:
        score -= 1
        signals.append(f"Freemail domain (-1)")
    elif from_domain and "." in from_domain:
        score += 1
        signals.append(f"Corporate domain (+1)")

    # Promotional subject patterns
    if _PROMO_PATTERNS.search(subject):
        score -= 1
        signals.append("Promotional subject (-1)")

    # List-Unsubscribe header presence
    has_unsub = bool(_extract_header_from_email(email, "List-Unsubscribe"))
    if has_unsub:
        score -= 1
        signals.append("Has List-Unsubscribe (-1)")

    # Addressed directly to user (not BCC)
    to = (email.get("to_addresses") or "").lower()
    owner_email = getattr(config, "OWNER_EMAIL", "").lower()
    if owner_email and owner_email in to:
        score += 1
        signals.append("Addressed to user directly (+1)")

    # Contains user's name in body
    owner_name = getattr(config, "OWNER_NAME", "").split()[0] if getattr(config, "OWNER_NAME", "") else ""
    if owner_name and len(owner_name) > 2 and owner_name.lower() in body.lower():
        score += 2
        signals.append(f"Contains user name '{owner_name}' (+2)")

    # Check if user has ever replied to this sender (check for SENT messages to this domain)
    try:
        import db as _db
        with _db.get_conn() as conn:
            row = conn.execute(
                """SELECT 1 FROM email_cache
                   WHERE 'SENT' = ANY(labels)
                   AND to_addresses ILIKE %s
                   LIMIT 1""",
                (f"%{from_domain}%",),
            ).fetchone()
            if row:
                score += 2
                signals.append("User has replied to this sender (+2)")
    except Exception:
        pass  # DB unavailable, skip this signal

    # Check entity_mentions for known contacts
    try:
        import db as _db
        with _db.get_conn() as conn:
            row = conn.execute(
                """SELECT 1 FROM entity_mentions
                   WHERE entity_type = 'person'
                   AND entity_value ILIKE %s
                   LIMIT 1""",
                (f"%{from_addr.split('@')[0]}%",),
            ).fetchone()
            if row:
                score += 3
                signals.append("Sender in entity_mentions (+3)")
    except Exception:
        pass

    # Decide based on score
    reason = "; ".join(signals) if signals else "no signals"

    if score >= 3:
        return ClassificationResult(IMPORTANT, "tier2_pattern",
                                    min(0.5 + score * 0.1, 0.9),
                                    f"Score {score}: {reason}")
    if score <= -2:
        return ClassificationResult(JUNK, "tier2_pattern",
                                    min(0.5 + abs(score) * 0.1, 0.9),
                                    f"Score {score}: {reason}")
    if -1 <= score <= 2:
        return ClassificationResult(ROUTINE, "tier2_pattern",
                                    0.5,
                                    f"Score {score}: {reason}")

    # Truly uncertain — fall through to Tier 3
    return None


def _extract_header_from_email(email: dict, name: str) -> str:
    """Extract a header from the raw data stored in email_cache.

    Checks both the payload.headers structure (if raw data is available)
    and falls back to checking common field names.
    """
    data = email.get("data")
    if isinstance(data, dict):
        for header in data.get("payload", {}).get("headers", []):
            if str(header.get("name", "")).lower() == name.lower():
                return str(header.get("value", ""))
    return ""


# --- Tier 3: AI Judgment ---

def _classify_tier3(email: dict) -> ClassificationResult:
    """AI judgment via Haiku for uncertain emails."""
    from_addr = email.get("from_address") or "?"
    from_name = email.get("from_name") or ""
    subject = email.get("subject") or "(no subject)"
    snippet = (email.get("snippet") or "")[:150]

    sender_display = f"{from_name} <{from_addr}>" if from_name else from_addr

    prompt = (
        "Classify this email as exactly ONE of: important, routine, junk, actionable\n\n"
        f"From: {sender_display}\n"
        f"Subject: {subject}\n"
        f"Preview: {snippet}\n\n"
        "Reply with ONLY the classification word."
    )

    context = (
        "User priorities: Legal/court emails are urgent. Health/disability/insurance emails "
        "are important. Work emails (Banker Wire) are important. Emails from family/friends "
        "are important. Food delivery promos are junk. Payment confirmations are routine. "
        "Fraud alerts are urgent. Shipping notifications are routine but keep. "
        "Marketing emails with unsubscribe are usually junk."
    )

    try:
        import asyncio
        from aria_api import ask_haiku

        # ask_haiku is async — run it in the event loop if available,
        # otherwise use asyncio.run for sync context
        try:
            loop = asyncio.get_running_loop()
            # Already in async context — can't use asyncio.run
            # Return a default classification; the monitor will handle this
            # via its own async context
            log.warning("Tier 3 called from async context — deferring to routine")
            return ClassificationResult(ROUTINE, "tier3_ai", 0.3,
                                        "Deferred (async context)")
        except RuntimeError:
            # No running loop — safe to use asyncio.run
            response = asyncio.run(ask_haiku(prompt, context))

        response = response.strip().lower()
        if response in (IMPORTANT, ROUTINE, JUNK, ACTIONABLE, URGENT):
            return ClassificationResult(response, "tier3_ai", 0.7,
                                        f"Haiku classified as {response}")
        # Haiku returned something unexpected — default to routine
        log.warning("Haiku returned unexpected classification: %s", response)
        return ClassificationResult(ROUTINE, "tier3_ai", 0.3,
                                    f"Haiku response unparseable: {response[:50]}")
    except Exception as e:
        log.error("Tier 3 classification failed: %s", e)
        return ClassificationResult(ROUTINE, "tier3_ai", 0.2,
                                    f"AI classification failed: {e}")
