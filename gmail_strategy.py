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
from datetime import datetime, timedelta, timezone
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
    category: str | None = None  # organizational tag (Financial, Physical Mail, etc.)
    priority: int = 3     # P1=time-critical, P2=high, P3=normal, P4=low


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
    if not result:
        # Tier 2: Pattern scoring
        result = _classify_tier2(email)
    if not result:
        # Tier 3: AI judgment
        result = _classify_tier3(email)

    # Assign priority after classification
    result.priority = _assign_priority(email, result)
    return result


def _assign_priority(email: dict, result: ClassificationResult) -> int:
    """Assign priority P1-P4 based on classification and content signals.

    P1: Time-critical (verification codes, 2FA, delivery today, urgent)
    P2: High (financial transactions, watched emails)
    P3: Normal (regular important)
    P4: Low (routine, junk)
    """
    if result.classification == "urgent":
        return 1

    subject = (email.get("subject") or "").lower()

    # P1: Time-critical content
    if re.search(
        r'verification code|one.time|OTP|password reset|2fa|two.factor|'
        r'security code|sign.in code|out for delivery|arriving today|'
        r'delivery today',
        subject, re.IGNORECASE,
    ):
        return 1

    # P2: Financial transactions or watched emails
    if result.classification in ("important", "actionable"):
        if result.category == "Watched":
            return 2
        if re.search(
            r'payment.{0,10}(processed|confirmed|received)|'
            r'transaction|shipped|tracking|\$\d',
            subject, re.IGNORECASE,
        ):
            return 2

    # P3: Regular important
    if result.classification in ("important", "actionable", "conversation"):
        return 3

    # P4: Routine/junk
    return 4


def classify_batch(emails: list[dict]) -> list[ClassificationResult]:
    """Classify multiple emails. Tier 3 calls are sequential."""
    return [classify_email(e) for e in emails]


# --- Tier 1: Hard Rules ---

def _classify_tier1(email: dict) -> ClassificationResult | None:
    """Deterministic rules from gmail_rules.yaml.

    Check order (first match wins):
      1. Global content overrides (verification codes, OTPs — time-aware)
      2. Active email watches (user-requested alerts for expected emails)
      3. Per-sender content overrides
      4. Always-important senders/domains
      5. Always-junk senders/domains
      6. Conversation threads
    """
    rules = load_rules()
    from_addr = (email.get("from_address") or "").lower()
    from_domain = from_addr.split("@")[-1] if "@" in from_addr else ""
    subject = email.get("subject") or ""
    body = email.get("body") or ""
    content = f"{subject} {body}"

    # 1. Global content overrides (checked BEFORE any sender rules)
    result = _check_global_content_overrides(email, rules, content)
    if result:
        return result

    # 2. Active email watches
    watch_result = _check_email_watches(email, from_addr, content)
    if watch_result:
        return watch_result

    # 3. Per-sender content overrides (content_pattern is optional)
    for override in rules.get("content_overrides", []):
        sender_pat = override.get("sender_pattern", "")
        if not sender_pat:
            continue
        sender_str = from_addr + " " + (email.get("from_name") or "")
        if not re.search(sender_pat, sender_str, re.IGNORECASE):
            continue
        content_pat = override.get("content_pattern", "")
        if content_pat:
            # check_subject_only: match against subject only (prevents body
            # footer/FAQ text from triggering — same flag as global overrides)
            search_text = subject if override.get("check_subject_only") else content
            if not re.search(content_pat, search_text, re.IGNORECASE):
                continue
        classification = override.get("classification", ROUTINE)
        cat = override.get("category")
        reason_parts = [f"Content override: {sender_pat}"]
        if content_pat:
            reason_parts.append(f"+ {content_pat}")
        return ClassificationResult(
            classification, "tier1_hard", 0.9,
            " ".join(reason_parts), category=cat)

    # 4. Always important senders/domains
    important = rules.get("always_important", {})
    if from_addr in [s.lower() for s in important.get("senders", [])]:
        return ClassificationResult(IMPORTANT, "tier1_hard", 1.0,
                                    f"Sender {from_addr} in always_important")
    if from_domain in [d.lower() for d in important.get("domains", [])]:
        return ClassificationResult(IMPORTANT, "tier1_hard", 1.0,
                                    f"Domain {from_domain} in always_important")

    # 5. Always junk senders/domains
    junk = rules.get("always_junk", {})
    if from_addr in [s.lower() for s in junk.get("senders", [])]:
        return ClassificationResult(JUNK, "tier1_hard", 1.0,
                                    f"Sender {from_addr} in always_junk")
    if from_domain in [d.lower() for d in junk.get("domains", [])]:
        return ClassificationResult(JUNK, "tier1_hard", 1.0,
                                    f"Domain {from_domain} in always_junk")

    # 6. Conversation threads the user has replied to
    thread_id = email.get("thread_id")
    if thread_id:
        if thread_id in rules.get("conversation_threads", []):
            return ClassificationResult(CONVERSATION, "tier1_hard", 0.95,
                                        "Active conversation thread (manual)")
        if _user_participated_in_thread(thread_id):
            return ClassificationResult(CONVERSATION, "tier1_hard", 0.95,
                                        "Active conversation thread (user replied)")

    return None


def _user_participated_in_thread(thread_id: str) -> bool:
    """Check if user has sent any emails in this thread (DB lookup)."""
    try:
        import db as _db
        with _db.get_conn() as conn:
            row = conn.execute(
                """SELECT 1 FROM email_cache
                   WHERE thread_id = %s AND 'SENT' = ANY(labels)
                   LIMIT 1""",
                (thread_id,),
            ).fetchone()
            return row is not None
    except Exception:
        return False


def _check_email_watches(email: dict, from_addr: str,
                          content: str) -> ClassificationResult | None:
    """Check active email watches — user-requested alerts for expected emails.

    Both sender_pattern and content_pattern are optional (but at least one
    must be present). A watch matches if all present patterns match.
    Matching watches are fulfilled (one-shot).
    """
    try:
        import gmail_store
        watches = gmail_store.get_active_watches()
    except Exception:
        return None

    email_id = email.get("id") or ""
    from_name = email.get("from_name") or ""
    sender_str = f"{from_addr} {from_name}"

    for watch in watches:
        sender_pat = watch.get("sender_pattern") or ""
        content_pat = watch.get("content_pattern") or ""

        if not sender_pat and not content_pat:
            continue

        if sender_pat and not re.search(sender_pat, sender_str, re.IGNORECASE):
            continue
        if content_pat and not re.search(content_pat, content, re.IGNORECASE):
            continue

        # Match — fulfill the watch
        classification = watch.get("classification", IMPORTANT)
        description = watch.get("description", "")
        try:
            gmail_store.fulfill_watch(int(watch["id"]), email_id)
            log.info("Email watch fulfilled: %s (email %s)", description, email_id)
        except Exception as e:
            log.warning("Failed to fulfill watch %s: %s", watch.get("id"), e)

        return ClassificationResult(
            classification, "tier1_hard", 1.0,
            f"Email watch matched: {description}",
            category="Watched")

    return None


def _check_global_content_overrides(email: dict, rules: dict,
                                     content: str) -> ClassificationResult | None:
    """Check global content overrides — patterns that override ALL sender rules.

    Supports time-aware rules: classification_within applies if the email
    is newer than max_age_hours, classification_after applies otherwise.
    """
    subject = email.get("subject") or ""
    for override in rules.get("global_content_overrides", []):
        content_pat = override.get("content_pattern", "")
        if not content_pat:
            continue
        # check_subject_only: only match against subject, not full body
        # (prevents FAQ/footer text in body from triggering verification patterns)
        search_text = subject if override.get("check_subject_only") else content
        if not re.search(content_pat, search_text, re.IGNORECASE):
            continue

        cat = override.get("category")
        max_age = override.get("max_age_hours")
        if max_age is not None:
            email_age_hours = _email_age_hours(email)
            if email_age_hours is not None and email_age_hours > float(max_age):
                cls_after = override.get("classification_after", JUNK)
                return ClassificationResult(
                    cls_after, "tier1_hard", 0.95,
                    f"Global content override (expired, {email_age_hours:.1f}h old): {content_pat}",
                    category=cat)
            cls_within = override.get("classification_within", IMPORTANT)
            return ClassificationResult(
                cls_within, "tier1_hard", 0.95,
                f"Global content override (fresh, {email_age_hours:.1f}h old): {content_pat}",
                category=cat)

        # No time constraint — simple global override
        classification = override.get("classification", IMPORTANT)
        return ClassificationResult(
            classification, "tier1_hard", 0.95,
            f"Global content override: {content_pat}", category=cat)

    return None


def _email_age_hours(email: dict) -> float | None:
    """Calculate email age in hours from its timestamp."""
    ts = email.get("timestamp")
    if not ts:
        return None
    try:
        if isinstance(ts, str):
            # Handle both naive and aware datetime strings
            from dateutil.parser import parse as parse_dt
            ts = parse_dt(ts)
        if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()
        delta = now - ts
        return delta.total_seconds() / 3600
    except Exception:
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

    # "Unsubscribe" text in body (marketing signal even without header)
    if re.search(r'\bunsubscribe\b', body, re.IGNORECASE):
        score -= 1
        signals.append("Body contains 'unsubscribe' (-1)")

    # Addressed directly to user (not BCC)
    to = (email.get("to_addresses") or "").lower()
    owner_email = getattr(config, "OWNER_EMAIL", "").lower()
    if owner_email and owner_email in to:
        score += 1
        signals.append("Addressed to user directly (+1)")

    # Contains user's name in body
    owner_name = getattr(config, "OWNER_NAME", "").split()[0] if getattr(config, "OWNER_NAME", "") else ""
    if owner_name and len(owner_name) > 2 and owner_name.lower() in body.lower():
        score += 1
        signals.append(f"Contains user name '{owner_name}' (+1)")

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
    """AI judgment for uncertain emails (configurable model, default Sonnet)."""
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

    tier3_model = getattr(config, "TIER3_EMAIL_MODEL", "claude-sonnet-4-6")

    try:
        import asyncio
        from aria_api import ask_model

        try:
            loop = asyncio.get_running_loop()
            log.warning("Tier 3 called from async context — deferring to routine")
            return ClassificationResult(ROUTINE, "tier3_ai", 0.3,
                                        "Deferred (async context)")
        except RuntimeError:
            response = asyncio.run(ask_model(
                prompt, context, model=tier3_model, max_tokens=50,
                system_prompt="Classify the email. Reply with exactly one word.",
            ))

        response = response.strip().lower()
        if response in (IMPORTANT, ROUTINE, JUNK, ACTIONABLE, URGENT):
            return ClassificationResult(response, "tier3_ai", 0.7,
                                        f"AI ({tier3_model}) classified as {response}")
        log.warning("Tier 3 returned unexpected classification: %s", response)
        return ClassificationResult(ROUTINE, "tier3_ai", 0.3,
                                    f"AI response unparseable: {response[:50]}")
    except Exception as e:
        log.error("Tier 3 classification failed: %s", e)
        return ClassificationResult(ROUTINE, "tier3_ai", 0.2,
                                    f"AI classification failed: {e}")


# --- Auto-Cleanup ---

def get_auto_cleanup_candidates() -> list[dict]:
    """Find emails matching auto_cleanup rules that have expired.

    Returns list of dicts with 'email_id' and 'rule' for tick.py to trash.
    """
    rules = load_rules()
    cleanup_rules = rules.get("auto_cleanup", [])
    if not cleanup_rules:
        return []

    candidates = []
    try:
        import db as _db
        with _db.get_conn() as conn:
            for rule in cleanup_rules:
                sender_pat = rule.get("sender_pattern", "")
                max_age = rule.get("max_age_hours")
                if not sender_pat or not max_age:
                    continue

                cutoff = datetime.now(timezone.utc) - timedelta(
                    hours=float(max_age))
                rows = conn.execute(
                    """SELECT id, from_address, subject FROM email_cache
                       WHERE from_address ILIKE %s
                       AND timestamp < %s
                       AND NOT ('TRASH' = ANY(labels))""",
                    (f"%{sender_pat}%", cutoff),
                ).fetchall()

                for row in rows:
                    candidates.append({
                        "email_id": row["id"],
                        "from": row["from_address"],
                        "subject": row["subject"],
                        "rule": sender_pat,
                        "action": rule.get("action", "trash"),
                    })
    except Exception as e:
        log.error("Auto-cleanup candidate lookup failed: %s", e)

    return candidates
