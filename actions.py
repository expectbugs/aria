"""ARIA ACTION block processing — extracts and executes ACTION blocks from Claude responses."""

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import db
import calendar_store
import vehicle_store
import health_store
import legal_store
import timer_store
import nutrition_store
import fitbit_store
import redis_client

log = logging.getLogger("aria")


# ---------------------------------------------------------------------------
# Destructive action confirmation gate
# ---------------------------------------------------------------------------

# Actions that delete persistent data — require code-level confirmation.
# send_email is NOT included (trusts prompt-level draft/confirm flow).
# modify_event is NOT included (changes data but doesn't destroy it).
_DESTRUCTIVE_ACTIONS = frozenset({
    "delete_event", "delete_reminder", "delete_health_entry",
    "delete_vehicle_entry", "delete_legal_entry", "delete_nutrition_entry",
    "trash_email",  # Added in v0.8.7
})

# Module-level store for pending destructive actions awaiting user confirmation.
# {confirmation_id: {"action": dict, "created": float, "description": str}}
_pending_confirmations: dict[str, dict] = {}

_PENDING_EXPIRY_SECONDS = 600  # 10 minutes


def _cleanup_expired_pending():
    """Remove pending actions older than the expiry window."""
    now = time.time()
    expired = [k for k, v in _pending_confirmations.items()
               if now - v["created"] > _PENDING_EXPIRY_SECONDS]
    for k in expired:
        del _pending_confirmations[k]


def _describe_action(action: dict) -> str:
    """Build a human-readable description of a destructive action.

    Looks up the target record so the user can verify it's the right one.
    """
    act = action.get("action", "?")
    aid = action.get("id", "?")

    try:
        if act == "delete_event":
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT title, date, time FROM events WHERE id = %s",
                    (aid,),
                ).fetchone()
            if row:
                desc = f"Delete calendar event: {row['title']}"
                if row.get("date"):
                    desc += f" on {row['date']}"
                if row.get("time"):
                    desc += f" at {row['time']}"
                return desc
            return f"Delete calendar event (id={aid})"

        elif act == "delete_reminder":
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT text, due FROM reminders WHERE id = %s",
                    (aid,),
                ).fetchone()
            if row:
                desc = f"Delete reminder: {row['text']}"
                if row.get("due"):
                    desc += f" (due {row['due']})"
                return desc
            return f"Delete reminder (id={aid})"

        elif act == "delete_health_entry":
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT date, category, description FROM health_entries WHERE id = %s",
                    (aid,),
                ).fetchone()
            if row:
                return f"Delete health entry: {row['date']} {row['category']} — {row['description'][:60]}"
            return f"Delete health entry (id={aid})"

        elif act == "delete_vehicle_entry":
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT date, event_type, description FROM vehicle_entries WHERE id = %s",
                    (aid,),
                ).fetchone()
            if row:
                return f"Delete vehicle entry: {row['date']} {row['event_type']} — {row['description'][:60]}"
            return f"Delete vehicle entry (id={aid})"

        elif act == "delete_legal_entry":
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT date, entry_type, description FROM legal_entries WHERE id = %s",
                    (aid,),
                ).fetchone()
            if row:
                return f"Delete legal entry: {row['date']} {row['entry_type']} — {row['description'][:60]}"
            return f"Delete legal entry (id={aid})"

        elif act == "delete_nutrition_entry":
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT date, food_name, meal_type FROM nutrition_entries WHERE id = %s",
                    (aid,),
                ).fetchone()
            if row:
                return f"Delete nutrition entry: {row['date']} {row['meal_type']} — {row['food_name']}"
            return f"Delete nutrition entry (id={aid})"

        elif act == "trash_email":
            email_id = action.get("email_id", "?")
            return f"Trash email (id={email_id})"

    except Exception as e:
        log.warning("Failed to describe action %s: %s", act, e)

    return f"{act} (id={aid})"


def get_pending_confirmations() -> list[dict]:
    """Return active (non-expired) pending confirmations for context injection."""
    _cleanup_expired_pending()
    return [
        {"confirmation_id": k, "description": v["description"],
         "created": v["created"]}
        for k, v in _pending_confirmations.items()
    ]


async def execute_pending(confirmation_id: str) -> tuple[bool, str]:
    """Execute a stored pending destructive action by confirmation ID.

    Returns (success, message). The STORED action is executed — not whatever
    ARIA might emit later. This prevents ARIA from modifying the action
    between the block and the confirmation.
    """
    _cleanup_expired_pending()
    pending = _pending_confirmations.pop(confirmation_id, None)
    if not pending:
        return False, "No pending action found (may have expired)."

    action = pending["action"]
    act = action.get("action")
    aid = action.get("id", "")

    try:
        if act == "delete_event":
            ok = await calendar_store.delete_event(aid)
            return (True, pending["description"]) if ok else (False, "Event not found.")

        elif act == "delete_reminder":
            ok = calendar_store.delete_reminder(aid)
            return (True, pending["description"]) if ok else (False, "Reminder not found.")

        elif act == "delete_health_entry":
            ok = health_store.delete_entry(aid)
            return (True, pending["description"]) if ok else (False, "Health entry not found.")

        elif act == "delete_vehicle_entry":
            ok = vehicle_store.delete_entry(aid)
            return (True, pending["description"]) if ok else (False, "Vehicle entry not found.")

        elif act == "delete_legal_entry":
            ok = legal_store.delete_entry(aid)
            return (True, pending["description"]) if ok else (False, "Legal entry not found.")

        elif act == "delete_nutrition_entry":
            ok = nutrition_store.delete_item(aid)
            return (True, pending["description"]) if ok else (False, "Nutrition entry not found.")

        elif act == "trash_email":
            import google_client
            import gmail_store
            client = google_client.get_client()
            email_id = action.get("email_id", "")
            await client.gmail_trash_message(email_id)
            # Update local cache
            try:
                with db.get_conn() as conn:
                    conn.execute(
                        """UPDATE email_cache SET labels = array_append(
                            array_remove(labels, 'INBOX'), 'TRASH')
                           WHERE id = %s""",
                        (email_id,),
                    )
            except Exception:
                pass
            return True, pending["description"]

        else:
            return False, f"Unknown destructive action type: {act}"

    except Exception as e:
        log.error("Failed to execute pending action %s: %s", confirmation_id, e)
        return False, f"Failed: {e}"


def clear_all_pending():
    """Clear all pending actions (used when user cancels)."""
    _pending_confirmations.clear()


@dataclass
class ActionResult:
    """Structured result from process_actions().

    Separates the clean response from execution metadata so callers can
    inspect failures, warnings, and claims independently. The to_response()
    method reconstructs the old string-return behavior for backward compat.
    """
    clean_response: str           # ACTION blocks stripped, NO failure/warning notes
    actions_found: list[dict]     # parsed action dicts that executed
    action_types: list[str]       # ["log_nutrition", "log_health", ...]
    failures: list[str]           # parse + execution failure messages
    warnings: list[str]           # validation warnings
    metadata: dict                # {"delivery": "sms", "dispatched_task_id": "abc123"}
    claims_without_actions: list[str] = field(default_factory=list)
    expect_actions_missing: list[str] = field(default_factory=list)
    pending_destructive: list[dict] = field(default_factory=list)

    def to_response(self) -> str:
        """Compose final response string with failures/warnings appended.

        Preserves the exact behavior of the old string return from
        process_actions() — callers can use this for zero-change migration.
        """
        resp = self.clean_response
        if self.expect_actions_missing:
            resp = (
                "WARNING: I expected to store data but no ACTION blocks were emitted. "
                "The data was NOT actually saved. Missing actions: "
                + ", ".join(self.expect_actions_missing) + ". Please try again."
            )
        if self.failures:
            resp += "\n\nNote: Some actions failed — " + " ".join(self.failures)
        if self.warnings:
            resp += "\n\n(" + " ".join(self.warnings) + ")"
        if self.claims_without_actions:
            resp += (
                "\n\n(System note: ARIA claimed to store data but no ACTION blocks "
                "were emitted. The data may not have been saved. Please verify or retry.)"
            )
        if self.pending_destructive:
            descs = [p["description"] for p in self.pending_destructive]
            resp += "\n\nConfirmation required: " + "; ".join(descs) + ". Say 'yes' to confirm or 'no' to cancel."
        return resp

    # Backward compat: allow ActionResult to behave like a string in tests
    # and existing code that does `"text" in result`, `result.lower()`, etc.
    def __contains__(self, item):
        return item in self.to_response()

    def __str__(self):
        return self.to_response()

    def lower(self):
        return self.to_response().lower()

    def strip(self):
        return self.to_response().strip()

    def __eq__(self, other):
        if isinstance(other, str):
            return self.to_response() == other
        return NotImplemented

    def __hash__(self):
        return hash(self.to_response())


# Regex to strip fenced code blocks before ACTION extraction (S14 fix)
_CODE_FENCE = re.compile(r'```.*?```', re.DOTALL)


def _extract_action_blocks(text: str) -> list[tuple[str, int, int]]:
    """Extract ACTION blocks with span positions using balanced-brace matching.

    Returns list of (json_str, span_start, span_end) tuples where span_start
    and span_end are positions in the code-fence-stripped text. These spans
    are used for both JSON extraction AND stripping — guaranteeing the same
    boundaries are applied to both operations.

    Fixes two bugs:
    - S14: Strips fenced code blocks first so ACTION blocks inside
      code examples are not executed.
    - S15: Uses balanced-brace counting instead of non-greedy .*? regex
      so that JSON values containing '-->' do not truncate the outer block.
    """
    # Strip code blocks before extraction (S14)
    stripped = _CODE_FENCE.sub('', text)

    results = []
    marker = '<!--ACTION::{'
    pos = 0
    while True:
        start = stripped.find(marker, pos)
        if start == -1:
            break
        # Start of JSON is at the '{'
        json_start = start + len('<!--ACTION::')
        # Find matching closing brace via balanced counting
        depth = 0
        i = json_start
        while i < len(stripped):
            ch = stripped[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    # Found the matching close brace
                    json_str = stripped[json_start:i + 1]
                    # Verify the --> closing marker follows
                    rest = stripped[i + 1:i + 4]
                    if rest == '-->':
                        # span covers the full <!--ACTION::...-->
                        results.append((json_str, start, i + 4))
                    break
            elif ch == '"':
                # Skip string contents (handle escaped quotes)
                i += 1
                while i < len(stripped) and stripped[i] != '"':
                    if stripped[i] == '\\':
                        i += 1  # skip escaped char
                    i += 1
            i += 1
        pos = i + 1 if i < len(stripped) else len(stripped)
    return results


def _strip_action_blocks(text: str, spans: list[tuple[int, int]]) -> str:
    """Remove ACTION blocks from text using pre-computed span positions.

    Spans are (start, end) pairs in the code-fence-stripped text.
    Uses the same boundaries as extraction — no regex, no possible mismatch.
    Also strips any trailing partial/truncated ACTION markers.
    """
    if not spans:
        # Still need to strip partial markers (truncated ACTION blocks)
        return re.sub(r'<!--ACTION::.*', '', text, flags=re.DOTALL).strip()

    # Build result by copying text between spans
    parts = []
    prev_end = 0
    for start, end in sorted(spans):
        parts.append(text[prev_end:start])
        prev_end = end
    parts.append(text[prev_end:])
    result = ''.join(parts)
    # Strip any trailing partial markers (incomplete ACTION blocks)
    result = re.sub(r'<!--ACTION::.*', '', result, flags=re.DOTALL)
    return result.strip()


_FISH_KEYWORDS = re.compile(
    r'\b(salmon|fish|tuna|sardine|mackerel|trout|cod|tilapia|halibut)\b', re.IGNORECASE
)
_EGG_KEYWORDS = re.compile(
    r'\b(eggs?|omelet|omelette|frittata|quiche|scramble)\b', re.IGNORECASE
)
_EGG_FALSE_POSITIVES = re.compile(r'\b(eggplant)\b', re.IGNORECASE)

_CHICKEN_KEYWORDS = re.compile(
    r'\b(chicken|poultry|hen|wing|thigh|breast|drumstick)\b', re.IGNORECASE
)
_CHICKEN_FALSE_POSITIVES = re.compile(r'\b(chickpea)\b', re.IGNORECASE)
_MAGNESIUM_KEYWORDS = re.compile(
    r'\b(chicken|pork|beef|rice|pasta|bean|lentil|grain)\b', re.IGNORECASE
)
_MAGNESIUM_FALSE_POSITIVES = re.compile(r'\b(rice paper|rice vinegar)\b', re.IGNORECASE)

_CORE_NUTRIENTS = [
    "calories", "total_fat_g", "saturated_fat_g", "sodium_mg",
    "total_carb_g", "dietary_fiber_g", "total_sugars_g", "protein_g",
]


def _validate_nutrition(nutrition_actions: list[dict],
                        health_meal_types: list[str]) -> list[str]:
    """Validate nutrition ACTION data quality. Returns list of warning strings."""
    warnings = []

    for action in nutrition_actions:
        food = action.get("food_name", "")
        nutrients = action.get("nutrients", {})
        source = action.get("source", "")

        # 1. Calories present and > 0 (skip for supplements — genuinely 0 cal)
        cal = nutrients.get("calories")
        food_lower = food.lower()
        is_supplement = any(kw in food_lower for kw in
                           ("supplement", "vitamin", "multivitamin", "magnesium",
                            "pill", "capsule", "tablet"))
        if (cal is None or cal == 0) and not is_supplement:
            warnings.append(f"No calories on '{food}' — verify entry.")

        # 2. Fish/salmon must have omega-3
        if _FISH_KEYWORDS.search(food):
            omega3 = nutrients.get("omega3_mg")
            if omega3 is None:
                warnings.append(
                    f"Omega-3 missing on '{food}' — estimate ~920mg per 3oz for canned salmon."
                )

        # 3. Egg dishes must have realistic cholesterol
        if _EGG_KEYWORDS.search(food) and not _EGG_FALSE_POSITIVES.search(food):
            chol = nutrients.get("cholesterol_mg")
            if chol is not None and chol < 100:
                warnings.append(
                    f"Cholesterol only {chol}mg on '{food}' — eggs are 186mg each, verify."
                )

        # 3b. Egg dishes should also have choline (147mg per egg, critical for NAFLD)
        if _EGG_KEYWORDS.search(food) and not _EGG_FALSE_POSITIVES.search(food):
            choline = nutrients.get("choline_mg")
            if choline is None:
                warnings.append(
                    f"Choline missing on '{food}' — eggs have ~147mg choline each "
                    f"(critical for NAFLD, target 550mg/day)."
                )

        # 3c. Chicken dishes should have choline (~85mg per 4oz)
        if (_CHICKEN_KEYWORDS.search(food)
                and not _CHICKEN_FALSE_POSITIVES.search(food)):
            choline = nutrients.get("choline_mg")
            if choline is None:
                warnings.append(
                    f"Choline missing on '{food}' — chicken has ~85mg choline "
                    f"per 4oz (critical for NAFLD, target 550mg/day)."
                )

        # 3d. Meat/grain/legume dishes should have magnesium
        if (_MAGNESIUM_KEYWORDS.search(food)
                and not _MAGNESIUM_FALSE_POSITIVES.search(food)):
            mag = nutrients.get("magnesium_mg")
            if mag is None:
                warnings.append(
                    f"Magnesium missing on '{food}' — estimate from USDA "
                    f"(critical for NAFLD, target 400-420mg/day)."
                )

        # 4. Label photos should have most core nutrients
        if source == "label_photo":
            present = sum(1 for f in _CORE_NUTRIENTS
                          if nutrients.get(f) is not None)
            if present < 8:
                missing = [f for f in _CORE_NUTRIENTS
                           if nutrients.get(f) is None]
                warnings.append(
                    f"Label photo '{food}' only has {present}/8 core nutrients — "
                    f"missing: {', '.join(missing)}."
                )

    # 5. Meal type consistency between log_health and log_nutrition
    if health_meal_types:
        nutr_meal_types = [a.get("meal_type", "snack") for a in nutrition_actions]
        health_set = set(health_meal_types)
        nutr_set = set(nutr_meal_types)
        if health_set != nutr_set:
            warnings.append(
                f"Meal type mismatch — diary has {health_set} but nutrition has {nutr_set}."
            )

    return warnings


def _push_data_quality_alert(reason: str, context: str = ""):
    """Push a LOUD data quality alert via Tasker (automated trigger — no Telnyx cost)."""
    try:
        from sms import _render_sms_image
        import push_image
        import os
        alert = f"DATA QUALITY ALERT\n\n{reason}"
        if context:
            alert += f"\n\nContext: {context[:200]}"
        img_path = _render_sms_image(alert, header="ARIA DATA")
        try:
            push_image.push_image(img_path, caption="Data Quality Alert")
        finally:
            os.unlink(img_path)
    except Exception:
        pass  # Don't crash action processing over alert delivery


def _prevalidate_nutrition_actions(parsed_actions: list[dict]) -> tuple[list[dict], list[str]]:
    """Pre-validate nutrition actions before any DB writes.

    Returns (cleaned_actions, hard_errors).
    - Removes intra-response duplicates (same food_name + date + meal_type)
    - Cross-checks dates between log_health and log_nutrition for same meal
    - Returns hard_errors if any critical issue found (caller should abort)
    """
    hard_errors = []
    cleaned = []
    seen_nutrition = set()  # (food_name_lower, date, meal_type)
    health_dates_by_meal = {}  # meal_type -> date

    # First pass: collect health dates for cross-checking
    for action in parsed_actions:
        act = action.get("action")
        if act == "log_health" and action.get("category") == "meal":
            mt = action.get("meal_type")
            if mt:
                health_dates_by_meal[mt] = action.get("date")

    # Second pass: validate and dedup nutrition actions
    for action in parsed_actions:
        act = action.get("action")
        if act == "log_nutrition":
            food = action.get("food_name", "").lower().strip()
            d = action.get("date", "")
            mt = action.get("meal_type", "snack")
            key = (food, d, mt)

            # Intra-response duplicate
            if key in seen_nutrition:
                log.warning("Intra-response duplicate blocked: %s (%s) on %s", food, mt, d)
                continue
            seen_nutrition.add(key)

            # Date cross-check with log_health
            if mt in health_dates_by_meal and d and health_dates_by_meal[mt] != d:
                hard_errors.append(
                    f"Date mismatch for {mt}: log_health has {health_dates_by_meal[mt]} "
                    f"but log_nutrition has {d} for '{food}'"
                )

        cleaned.append(action)

    return cleaned, hard_errors


async def process_actions(response_text: str, expect_actions: list[str] | None = None,
                          metadata: dict | None = None,
                          log_fn=None) -> ActionResult:
    """Extract and execute ACTION blocks from Claude's response.

    Three-phase pipeline:
    1. Parse — extract and parse all ACTION block JSON
    2. Validate — pre-validate nutrition entries (dedup, date cross-check)
    3. Execute — run each action against the database

    Returns ActionResult with structured data. Use result.to_response() for
    the composed string (identical to old behavior).

    Async because calendar_store write operations and send_email call
    the Google API. All callers are already async (daemon.py, verification.py,
    completion_listener.py).

    expect_actions: optional list of action types that SHOULD be present
                    (e.g. ["log_nutrition"] for nutrition label photos).
    metadata: optional mutable dict to receive extracted metadata like
              delivery routing preferences ({"delivery": "voice"}).
    log_fn: optional callable with signature (text, status, **kwargs) for
            audit logging to the request_log table. If None, audit logging
            is skipped (useful in tests).
    """
    action_blocks = _extract_action_blocks(response_text)
    action_jsons = [json_str for json_str, _, _ in action_blocks]
    action_spans = [(start, end) for _, start, end in action_blocks]

    # Detect ACTION markers that the balanced-brace parser couldn't extract
    # (malformed JSON, unterminated strings, etc.) — report as parse failures
    _code_stripped = _CODE_FENCE.sub('', response_text)
    _marker_count = _code_stripped.count('<!--ACTION::')

    # --- Phase 1: Parse ---
    parsed_actions = []
    parse_failures = []
    for action_json in action_jsons:
        try:
            parsed_actions.append(json.loads(action_json))
        except json.JSONDecodeError as e:
            parse_failures.append(f"Invalid ACTION JSON: {e}")

    # Detect malformed ACTION markers that the parser couldn't extract
    if _marker_count > len(action_jsons):
        parse_failures.append(
            f"Found {_marker_count} ACTION markers but only extracted "
            f"{len(action_jsons)} — {_marker_count - len(action_jsons)} "
            f"had malformed JSON"
        )

    # --- Phase 2: Pre-validate nutrition/health ---
    if parsed_actions:
        parsed_actions, hard_errors = _prevalidate_nutrition_actions(parsed_actions)
        if hard_errors:
            error_msg = "; ".join(hard_errors)
            log.error("Data quality validation FAILED: %s", error_msg)
            _push_data_quality_alert(error_msg, response_text[:300])
            if log_fn:
                log_fn("DATA_QUALITY", "error", error=error_msg)
            clean_response = re.sub(r'<!--ACTION::.*?-->', '', response_text, flags=re.DOTALL).strip()
            return ActionResult(
                clean_response=clean_response,
                actions_found=[], action_types=[], metadata=metadata or {},
                failures=[f"DATA QUALITY ERROR — actions aborted: {error_msg}"],
                warnings=[], claims_without_actions=[], expect_actions_missing=[],
            )

    # Run nutrition validation BEFORE executing
    pre_nutrition_actions = [a for a in parsed_actions if a.get("action") == "log_nutrition"]
    pre_health_meal_types = [
        a.get("meal_type") for a in parsed_actions
        if a.get("action") == "log_health" and a.get("category") == "meal" and a.get("meal_type")
    ]
    if pre_nutrition_actions:
        validation_warnings = _validate_nutrition(pre_nutrition_actions, pre_health_meal_types)
    else:
        validation_warnings = []

    # --- Phase 3: Execute ---
    failures = list(parse_failures)
    action_types_found = []
    _nutrition_actions = []  # collected for post-execution tracking
    _health_meal_types = []  # collected for tracking
    pending_destructive = []

    for action in parsed_actions:
        try:
            act = action.get("action")
            action_types_found.append(act)

            # --- Destructive action confirmation gate ---
            if act in _DESTRUCTIVE_ACTIONS:
                _cleanup_expired_pending()
                conf_id = str(uuid.uuid4())[:8]
                desc = _describe_action(action)
                _pending_confirmations[conf_id] = {
                    "action": action,
                    "created": time.time(),
                    "description": desc,
                }
                pending_destructive.append(
                    {"confirmation_id": conf_id, "description": desc})
                log.info("[GATE] Destructive action blocked: %s → pending %s",
                         act, conf_id)
                if log_fn:
                    log_fn("DESTRUCTIVE_GATE", "blocked",
                           error=f"{act} blocked, pending={conf_id}: {desc}")
                continue  # skip execution — user must confirm

            # --- confirm_destructive: user confirmed via ARIA ---
            if act == "confirm_destructive":
                conf_id = action.get("confirmation_id", "")
                ok, msg = await execute_pending(conf_id)
                if ok:
                    log.info("[GATE] Confirmed and executed: %s — %s",
                             conf_id, msg)
                else:
                    failures.append(msg)
                continue

            if act == "add_event":
                await calendar_store.add_event(
                    title=action["title"],
                    event_date=action["date"],
                    time=action.get("time"),
                    notes=action.get("notes"),
                )
            elif act == "add_reminder":
                calendar_store.add_reminder(
                    text=action["text"],
                    due=action.get("due"),
                    recurring=action.get("recurring"),
                    location=action.get("location"),
                    location_trigger=action.get("location_trigger"),
                )
            elif act == "complete_reminder":
                if not calendar_store.complete_reminder(action["id"]):
                    failures.append(f"Couldn't complete reminder — no reminder found with that ID.")
            elif act == "modify_event":
                updates = {k: v for k, v in action.items()
                           if k not in ("action", "id")}
                if not await calendar_store.modify_event(action["id"], **updates):
                    failures.append("Couldn't modify event — no event found with that ID.")
            elif act == "delete_event":
                if not await calendar_store.delete_event(action["id"]):
                    failures.append(f"Couldn't delete event — no event found with that ID.")
            elif act == "delete_reminder":
                if not calendar_store.delete_reminder(action["id"]):
                    failures.append(f"Couldn't delete reminder — no reminder found with that ID.")
            elif act == "log_vehicle":
                vehicle_store.add_entry(
                    event_date=action["date"],
                    event_type=action["event_type"],
                    description=action["description"],
                    mileage=action.get("mileage"),
                    cost=action.get("cost"),
                )
            elif act == "delete_vehicle_entry":
                if not vehicle_store.delete_entry(action["id"]):
                    failures.append("Couldn't delete vehicle entry — no entry found with that ID.")
            elif act == "log_health":
                result = health_store.add_entry(
                    entry_date=action["date"],
                    category=action["category"],
                    description=action["description"],
                    severity=action.get("severity"),
                    sleep_hours=action.get("sleep_hours"),
                    meal_type=action.get("meal_type"),
                )
                if result.get("duplicate"):
                    log.warning("Duplicate health entry blocked: %s", action.get("description", "")[:50])
                if action.get("category") == "meal" and action.get("meal_type"):
                    _health_meal_types.append(action["meal_type"])
            elif act == "delete_health_entry":
                if not health_store.delete_entry(action["id"]):
                    failures.append("Couldn't delete health entry — no entry found with that ID.")
            elif act == "log_legal":
                legal_store.add_entry(
                    entry_date=action["date"],
                    entry_type=action["entry_type"],
                    description=action["description"],
                    contacts=action.get("contacts"),
                )
            elif act == "delete_legal_entry":
                if not legal_store.delete_entry(action["id"]):
                    failures.append("Couldn't delete legal entry — no entry found with that ID.")
            elif act == "set_timer":
                # Compute fire_at from minutes (relative) or time (absolute)
                if "minutes" in action:
                    fire_at = (datetime.now() + timedelta(minutes=action["minutes"])).isoformat()
                elif "time" in action:
                    t = datetime.strptime(action["time"], "%H:%M").time()
                    fire_at = datetime.combine(datetime.now().date(), t).isoformat()
                    # If the time has already passed today, set for tomorrow
                    if fire_at <= datetime.now().isoformat():
                        fire_at = datetime.combine(
                            datetime.now().date() + timedelta(days=1), t
                        ).isoformat()
                else:
                    failures.append("Timer needs 'minutes' or 'time' field.")
                    continue
                timer_store.add_timer(
                    label=action.get("label", "Timer"),
                    fire_at=fire_at,
                    delivery=action.get("delivery", "sms"),
                    priority=action.get("priority", "gentle"),
                    message=action.get("message", ""),
                )
            elif act == "cancel_timer":
                if not timer_store.cancel_timer(action["id"]):
                    failures.append("Couldn't cancel timer — no active timer found with that ID.")
            elif act == "log_nutrition":
                # Date is required — fall back to today with warning if missing
                nutr_date = action.get("date")
                if not nutr_date:
                    nutr_date = datetime.now().strftime("%Y-%m-%d")
                    log.warning("log_nutrition missing date field for '%s', defaulting to today (%s)",
                                action.get("food_name", "?"), nutr_date)
                result = nutrition_store.add_item(
                    food_name=action["food_name"],
                    meal_type=action.get("meal_type", "snack"),
                    nutrients=action.get("nutrients", {}),
                    servings=action.get("servings", 1.0),
                    serving_size=action.get("serving_size", ""),
                    source=action.get("source", "label_photo"),
                    notes=action.get("notes", ""),
                    entry_date=nutr_date,
                    entry_time=action.get("time"),
                )
                if result.get("duplicate"):
                    log.warning("Duplicate nutrition entry blocked: %s", action.get("food_name", ""))
                _nutrition_actions.append(action)
            elif act == "delete_nutrition_entry":
                if not nutrition_store.delete_item(action["id"]):
                    failures.append("Couldn't delete nutrition entry — no entry found with that ID.")
            elif act == "start_exercise":
                exercise_type = action.get("exercise_type", "general")
                fitbit_store.start_exercise(exercise_type)
            elif act == "end_exercise":
                fitbit_store.end_exercise("user ended")
            elif act == "set_delivery":
                if metadata is not None:
                    metadata["delivery"] = action.get("method", "default")
            elif act == "dispatch_action":
                task_id = str(uuid.uuid4())[:8]
                task = {
                    "task_id": task_id,
                    "mode": action.get("mode", "shell"),
                    "command": action.get("command", ""),
                    "task": action.get("task", ""),
                    "context": action.get("context", ""),
                    "notify": action.get("notify", True),
                    "channel": metadata.get("channel", "voice") if metadata else "voice",
                }
                pushed = redis_client.push_task(task)
                if pushed:
                    log.info("Dispatched task %s (mode=%s)", task_id, task["mode"])
                    if metadata is not None:
                        metadata["dispatched_task_id"] = task_id
                else:
                    failures.append("Failed to dispatch task — Redis unavailable")
            elif act == "watch_email":
                import gmail_store
                watch_id = gmail_store.add_watch(
                    sender_pattern=action.get("sender_pattern"),
                    content_pattern=action.get("content_pattern"),
                    classification=action.get("classification", "important"),
                    description=action.get("description", ""),
                    expires_days=int(action.get("expires_days", 30)),
                )
                log.info("Email watch created: id=%d, desc='%s'",
                         watch_id, action.get("description", ""))
            elif act == "cancel_watch":
                import gmail_store
                watch_id = action.get("id")
                desc = action.get("description", "")
                if watch_id:
                    if not gmail_store.cancel_watch(int(watch_id)):
                        failures.append(f"No active watch found with id {watch_id}.")
                elif desc:
                    if not gmail_store.cancel_watch_by_description(desc):
                        failures.append(f"No active watch matching '{desc}'.")
                else:
                    failures.append("cancel_watch needs 'id' or 'description'.")
            elif act == "send_email":
                try:
                    import google_client
                    client = google_client.get_client()
                    result = await client.gmail_send_message(
                        to=action["to"],
                        subject=action["subject"],
                        body=action["body"],
                        in_reply_to=action.get("in_reply_to"),
                        thread_id=action.get("thread_id"),
                    )
                    log.info("Email sent to %s (id=%s)", action["to"],
                             result.get("id", "?"))
                except Exception as e:
                    failures.append(f"Failed to send email: {e}")
            else:
                log.warning("Unknown ACTION type ignored: %s", act)
        except Exception as e:
            failures.append(f"Action failed: {e}")
            if log_fn:
                log_fn("ACTION", "error", error=str(e))

    # Strip action blocks from spoken response using the same balanced-brace
    # spans as extraction — no regex mismatch possible (C2 fix).
    # Code fences are already stripped by _extract_action_blocks, and the spans
    # are relative to the code-stripped text.
    clean_response = _CODE_FENCE.sub('', response_text)
    clean_response = _strip_action_blocks(clean_response, action_spans)

    # Log failures
    if failures:
        if log_fn:
            log_fn("ACTION", "error", error="; ".join(failures))

    # Nutrition validation warnings (computed before execution)
    # Wrap in "Nutrition check:" prefix so to_response() output matches old format
    if validation_warnings:
        validation_warnings = ["Nutrition check: " + " ".join(validation_warnings)]
        if log_fn:
            log_fn("NUTRITION_VALIDATION", "warning",
                   error="; ".join(validation_warnings))

    # Check expected actions
    expect_missing = []
    if expect_actions:
        expect_missing = [a for a in expect_actions if a not in action_types_found]
        if expect_missing:
            if log_fn:
                log_fn("ACTION_MISSING", "error",
                       error=f"Expected {expect_missing}, got {action_types_found}")

    # Detect claim-without-action: response says data was stored but no actions found.
    claims_without = []
    if not action_jsons:
        claim_phrases = re.findall(
            r"(?:I've |I have |I )"
            r"(?:logged|stored|saved|recorded|tracked|captured|added|noted)"
            r"|"
            r"(?:logged|stored|saved|recorded|tracked|added to|captured) your\b"
            r"|"
            r"\bnoted and logged\b",
            clean_response, re.IGNORECASE
        )
        nutrient_terms = re.findall(
            r'\b(calories|protein|carb|fat|sodium|fiber|sugar|cholesterol|potassium)\b',
            clean_response, re.IGNORECASE
        )
        if claim_phrases and len(set(t.lower() for t in nutrient_terms)) >= 3:
            claim_phrases.append("nutrition_data_extracted")
        if claim_phrases:
            claims_without = claim_phrases
            if log_fn:
                log_fn("CLAIM_WITHOUT_ACTION", "warning",
                       error=f"Response claims '{claim_phrases}' but 0 actions found")

    return ActionResult(
        clean_response=clean_response,
        actions_found=parsed_actions,
        action_types=action_types_found,
        failures=failures,
        warnings=validation_warnings,
        metadata=metadata or {},
        claims_without_actions=claims_without,
        expect_actions_missing=expect_missing,
        pending_destructive=pending_destructive,
    )


def process_actions_sync(response_text: str, expect_actions: list[str] | None = None,
                         metadata: dict | None = None,
                         log_fn=None) -> ActionResult:
    """Sync wrapper for process_actions — for use in tests and sync contexts.

    Runs the async process_actions in a new event loop. Calendar and email
    handlers that call Google APIs will work but are slower than the async path.
    """
    import asyncio
    return asyncio.run(process_actions(response_text, expect_actions, metadata, log_fn))
