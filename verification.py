"""ARIA response verification pipeline — structurally prevent hallucination.

Verifies factual claims in ARIA's response against data stores before delivery.
Three claim types:
  - Action claims: "I logged your meal" → check ACTION blocks were emitted
  - Date claims: "your appointment is on March 28th" → check calendar/timer stores
  - Numeric claims: "you ate 1,450 calories" → check nutrition totals

Action claim violations trigger the retry loop (re-prompt ARIA to emit ACTION blocks).
Date/numeric violations are logged for LoRA training but do not trigger retries.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import db

log = logging.getLogger("aria.verify")


@dataclass
class ClaimCheck:
    """A single verified claim."""
    claim_text: str
    claim_type: str      # action, date, numeric
    status: str          # verified, contradicted, unverifiable
    evidence: str = ""


@dataclass
class VerificationResult:
    """Result of verifying all claims in a response."""
    ok: bool                        # True if no contradictions found
    claims: list[ClaimCheck] = field(default_factory=list)
    needs_retry: bool = False       # True if action claims need retry
    correction_prompt: str | None = None  # prompt for retry if needed


def needs_verification(text: str, response: str, action_result=None) -> bool:
    """Gate: decide whether a response needs claim verification.

    Skip verification for:
    - Responses that are pure questions
    - Responses without factual claim patterns AND no claims_without_actions

    Always verify if claims_without_actions detected (any length).
    """
    # If claim detection already found something, always verify regardless of length
    if action_result and action_result.claims_without_actions:
        return True

    # Very short responses without pre-detected claims are safe to skip
    if len(response) < 30:
        return False

    # Pure question responses don't need verification
    stripped = response.rstrip()
    if stripped.endswith("?") and "\n" not in stripped:
        return False

    # Check for factual claim patterns
    return bool(re.search(
        r"(?:I've |I have |I )"
        r"(?:logged|stored|saved|recorded|tracked|captured|added|noted)"
        r"|your (?:appointment|event|timer) (?:is|was) (?:on|at|for) "
        r"|you (?:ate|consumed|had) (?:about |approximately )?\d",
        response, re.IGNORECASE
    ))


def verify_response(response_text: str, action_result,
                    injected_context: str = "") -> VerificationResult:
    """Main verification: check action claims, date claims, numeric claims.

    action_result: ActionResult from process_actions()
    injected_context: the context that was injected into the request
    """
    claims = []
    needs_retry = False
    correction_prompt = None

    # --- Action claim verification (primary — triggers retry) ---
    if action_result.claims_without_actions:
        claim_text = ", ".join(action_result.claims_without_actions[:3])
        claims.append(ClaimCheck(
            claim_text=claim_text,
            claim_type="action",
            status="contradicted",
            evidence="No ACTION blocks found in response",
        ))
        needs_retry = True

        # Determine what type of action was claimed
        claim_summary = claim_text[:100]
        correction_prompt = (
            f"[INTERNAL CORRECTION — do not acknowledge] "
            f"Your previous response claimed to store data ({claim_summary}) "
            f"but contained no ACTION blocks. The data was NOT saved. "
            f"Regenerate your response with the correct ACTION blocks. "
            f"Do not apologize or reference this correction."
        )

    # --- Numeric claim verification (secondary — log only) ---
    calorie_claims = re.findall(
        r'(\d[\d,]*)\s*(?:calories|cal(?:ories)?)\b(?!\s*(?:burned|burn))',
        response_text, re.IGNORECASE,
    )
    if calorie_claims:
        try:
            import nutrition_store
            today = datetime.now().strftime("%Y-%m-%d")
            totals = nutrition_store.get_daily_totals(today)
            actual_cal = totals.get("calories", 0)

            for claimed_str in calorie_claims:
                claimed = int(claimed_str.replace(",", ""))
                if actual_cal > 0 and abs(claimed - actual_cal) > 200:
                    claims.append(ClaimCheck(
                        claim_text=f"{claimed} calories",
                        claim_type="numeric",
                        status="contradicted",
                        evidence=f"Actual daily total: {actual_cal:.0f} cal "
                                 f"(delta: {claimed - actual_cal:.0f})",
                    ))
        except Exception as e:
            log.warning("[VERIFY] Calorie check failed: %s", e)

    # --- Date claim verification (secondary — log only) ---
    date_claims = re.findall(
        r'your (?:appointment|event|timer|reminder) '
        r'(?:is|was|has been) (?:set |scheduled )?'
        r'(?:on|at|for) (.+?)(?:\.|,|$)',
        response_text, re.IGNORECASE,
    )
    if date_claims:
        # These are logged for training data but don't trigger retries
        for claimed_date in date_claims[:3]:
            claims.append(ClaimCheck(
                claim_text=f"event/timer claim: {claimed_date.strip()[:60]}",
                claim_type="date",
                status="unverifiable",  # would need NLU to parse natural dates
                evidence="Date claims logged for training data",
            ))

    # --- Completeness claim detection (log-only — training data) ---
    completeness_claims = check_completeness_claims(response_text, injected_context)
    claims.extend(completeness_claims)

    # Determine overall status — completeness claims are "logged" status,
    # they do NOT affect ok/needs_retry (training data only)
    contradicted = [c for c in claims
                    if c.status == "contradicted" and c.claim_type != "completeness"]
    ok = len(contradicted) == 0

    return VerificationResult(
        ok=ok,
        claims=claims,
        needs_retry=needs_retry,
        correction_prompt=correction_prompt,
    )


# --- Completeness claim detection ---

# Patterns that imply the response has exhaustive/complete knowledge of a dataset,
# when the injected context was actually scoped (e.g. today-only calendar).
_COMPLETENESS_CLAIMS = re.compile(
    r"\b(?:the only (?:event|appointment|entry|email|item|reminder))"
    r"|\b(?:there (?:are|is) no other)"
    r"|\b(?:that'?s (?:all|everything) (?:I see|in your|you have))"
    r"|\b(?:nothing else (?:scheduled|planned|logged|in your))"
    r"|\b(?:your calendar is (?:empty|clear|free))"
    r"|\b(?:you (?:don'?t|do not) have any (?:other |more )?(?:events?|appointments?))"
    r"|\b(?:no (?:other |more )?(?:events?|appointments?|entries) (?:scheduled|planned|found|logged))",
    re.IGNORECASE,
)

# Context annotations that indicate scoped/limited data was injected
_SCOPE_INDICATORS = [
    "today only",
    "shown — use",
    "use `query.py",
    "unread important only",
    "today + yesterday",
]


def check_completeness_claims(response: str, context: str) -> list[ClaimCheck]:
    """Detect responses that claim exhaustive knowledge of scoped data.

    Log-only — does NOT trigger retries or visible warnings. Stored in
    verification_log for training data collection.

    Only flags when:
    1. Response uses absolute completeness language (not scope-qualified)
    2. The injected context had scope limitations (scope annotations present)
    """
    claims = []
    match = _COMPLETENESS_CLAIMS.search(response)
    if not match:
        return claims

    # Only flag if context was actually scoped
    context_is_scoped = any(indicator in context for indicator in _SCOPE_INDICATORS)
    if not context_is_scoped:
        return claims  # Context wasn't scoped — claim might be correct

    claims.append(ClaimCheck(
        claim_text=f"Completeness claim on scoped data: '{match.group()[:60]}'",
        claim_type="completeness",
        status="logged",  # NOT "contradicted" — doesn't affect ok/needs_retry
        evidence="Context was scoped (scope annotations present)",
    ))
    return claims


def log_verification(result: VerificationResult, retry_attempt: int = 0,
                     request_text: str = "", response_text: str = ""):
    """Log verification results to verification_log table."""
    if not result.claims:
        return  # nothing to log

    try:
        with db.get_conn() as conn:
            for claim in result.claims:
                conn.execute(
                    """INSERT INTO verification_log
                       (request_text, response_text, claim_text, claim_type,
                        verification_status, evidence, retry_attempt,
                        original_response)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (request_text[:500] if request_text else None,
                     response_text[:2000] if response_text else None,
                     claim.claim_text, claim.claim_type,
                     claim.status, claim.evidence,
                     retry_attempt,
                     response_text[:2000] if retry_attempt > 0 else None),
                )
    except Exception as e:
        log.error("[VERIFY] Failed to log verification: %s", e)


# --- Tool Use Validation ---

_CONVERSATIONAL_EXACT = frozenset({
    "ok", "okay", "got it", "thanks", "thank you", "sure", "yep", "yeah",
    "no", "nope", "nah", "yes", "lol", "ha", "haha", "nice", "cool",
    "hmm", "hm", "right", "alright", "sounds good",
})

_CONVERSATIONAL_PATTERNS = re.compile(
    r"^(?:good (?:morning|night|evening|afternoon))\b"
    r"|^(?:hey|hi|hello|yo|sup|what'?s up)\b"
    r"|^(?:how are you|how'?s it going)"
    r"|^(?:bye|later|see you|talk (?:later|soon))\b",
    re.IGNORECASE,
)

_FACTUAL_CLAIM_PATTERNS = re.compile(
    r"your (?:appointment|event|timer|reminder|next|last|calendar) "
    r"|you (?:have|had|ate|consumed|logged|took|slept|burned|walked) \d"
    r"|\b(?:on|for|at|until) (?:January|February|March|April|May|June|"
    r"July|August|September|October|November|December) \d"
    r"|(?:^|\. )(?:There (?:are|is) \d|You have \d|Your \w+ (?:is|was|are) )"
    r"|\d[\d,]+ (?:calories|cal|steps|mg|mcg|grams?|hours?|minutes?)\b"
    r"|(?:the only|no other|that'?s all|nothing else|your calendar is (?:empty|clear))",
    re.IGNORECASE | re.MULTILINE,
)


def _is_conversational(text: str) -> bool:
    """Detect banter, greetings, acknowledgments that don't need tool verification."""
    stripped = text.strip()
    # Very short responses are likely conversational
    if len(stripped) < 40 and not any(c.isdigit() for c in stripped):
        return True
    # Exact match on common conversational responses
    if stripped.rstrip("!.?").lower() in _CONVERSATIONAL_EXACT:
        return True
    # Single-line question responses
    if stripped.endswith("?") and "\n" not in stripped and len(stripped) < 200:
        return True
    # Greeting/farewell patterns
    if _CONVERSATIONAL_PATTERNS.match(stripped):
        return True
    return False


def _has_factual_claims(text: str) -> bool:
    """Detect date assertions, numeric claims, state claims, event references."""
    return bool(_FACTUAL_CLAIM_PATTERNS.search(text))


def validate_tool_use(response_text: str,
                      tool_calls: list[str]) -> tuple[bool, str]:
    """Check whether a response with factual claims was backed by tool use.

    Returns (ok, reason):
      - ok=True: response is fine (conversational, or tools were used, or no claims)
      - ok=False: factual claims detected without any tool calls
    """
    if _is_conversational(response_text):
        return True, "conversational"
    if tool_calls:
        return True, f"tools_used: {', '.join(tool_calls[:5])}"
    if _has_factual_claims(response_text):
        return False, "factual_claims_without_tool_use"
    return True, "no_factual_claims"
