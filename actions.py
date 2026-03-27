"""ARIA ACTION block processing — extracts and executes ACTION blocks from Claude responses."""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta

import calendar_store
import vehicle_store
import health_store
import legal_store
import timer_store
import nutrition_store
import fitbit_store
import redis_client

log = logging.getLogger("aria")


_FISH_KEYWORDS = re.compile(
    r'\b(salmon|fish|tuna|sardine|mackerel|trout|cod|tilapia|halibut)\b', re.IGNORECASE
)
_EGG_KEYWORDS = re.compile(
    r'\b(eggs?|omelet|omelette|frittata|quiche|scramble)\b', re.IGNORECASE
)
_EGG_FALSE_POSITIVES = re.compile(r'\b(eggplant)\b', re.IGNORECASE)

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

        # 1. Calories present and > 0
        cal = nutrients.get("calories")
        if cal is None or cal == 0:
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
    """Push a LOUD data quality alert to the phone."""
    try:
        from sms import _render_sms_image
        import push_image
        import os
        alert = f"DATA QUALITY ALERT\n\n{reason}"
        if context:
            alert += f"\n\nContext: {context[:200]}"
        img_path = _render_sms_image(alert, header="ARIA DATA")
        push_image.push_image(img_path, caption="Data Quality Alert")
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


def process_actions(response_text: str, expect_actions: list[str] | None = None,
                    metadata: dict | None = None,
                    log_fn=None) -> str:
    """Extract and execute ACTION blocks from Claude's response.

    Three-phase pipeline:
    1. Parse — extract and parse all ACTION block JSON
    2. Validate — pre-validate nutrition entries (dedup, date cross-check)
    3. Execute — run each action against the database

    Returns the cleaned response, replacing it with an error message
    if any actions failed so the user isn't told something worked when it didn't.

    expect_actions: optional list of action types that SHOULD be present
                    (e.g. ["log_nutrition"] for nutrition label photos).
                    If expected actions are missing, a warning is appended.
    metadata: optional mutable dict to receive extracted metadata like
              delivery routing preferences ({"delivery": "voice"}).
    log_fn: optional callable with signature (text, status, **kwargs) for
            audit logging to the request_log table. If None, audit logging
            is skipped (useful in tests).
    """
    action_jsons = re.findall(r'<!--ACTION::(\{.*?\})-->', response_text, re.DOTALL)

    # --- Phase 1: Parse ---
    parsed_actions = []
    parse_failures = []
    for action_json in action_jsons:
        try:
            parsed_actions.append(json.loads(action_json))
        except json.JSONDecodeError as e:
            parse_failures.append(f"Invalid ACTION JSON: {e}")

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
            return clean_response + f"\n\nDATA QUALITY ERROR — actions aborted: {error_msg}"

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

    for action in parsed_actions:
        try:
            act = action.get("action")
            action_types_found.append(act)

            if act == "add_event":
                calendar_store.add_event(
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
                if not calendar_store.modify_event(action["id"], **updates):
                    failures.append("Couldn't modify event — no event found with that ID.")
            elif act == "delete_event":
                if not calendar_store.delete_event(action["id"]):
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
            else:
                log.warning("Unknown ACTION type ignored: %s", act)
        except Exception as e:
            failures.append(f"Action failed: {e}")
            if log_fn:
                log_fn("ACTION", "error", error=str(e))

    # Strip action blocks from spoken response
    clean_response = re.sub(r'<!--ACTION::.*?-->', '', response_text, flags=re.DOTALL).strip()

    if failures:
        if log_fn:
            log_fn("ACTION", "error", error="; ".join(failures))
        clean_response += "\n\nNote: Some actions failed — " + " ".join(failures)

    # Nutrition validation warnings (computed in Phase 2, before execution)
    if validation_warnings:
        clean_response += "\n\n(Nutrition check: " + " ".join(validation_warnings) + ")"
        if log_fn:
            log_fn("NUTRITION_VALIDATION", "warning",
                   error="; ".join(validation_warnings))

    # Validate: if specific actions were expected but not found, warn
    if expect_actions:
        missing = [a for a in expect_actions if a not in action_types_found]
        if missing:
            warning = (
                "WARNING: I expected to store data but no ACTION blocks were emitted. "
                "The data was NOT actually saved. Missing actions: "
                + ", ".join(missing) + ". Please try again."
            )
            if log_fn:
                log_fn("ACTION_MISSING", "error",
                       error=f"Expected {missing}, got {action_types_found}")
            clean_response = warning

    # Detect claim-without-action: response says data was stored but no actions found.
    # Uses phrase patterns (not single words) to distinguish real claims like
    # "I've logged your meal" from descriptive text like "meals logged 3 of 7 days".
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
        # Also detect nutrition-specific claims: ARIA-phrased claim + 3+ nutrient terms
        # without a log_nutrition action (Claude extracted data but didn't store it)
        nutrient_terms = re.findall(
            r'\b(calories|protein|carb|fat|sodium|fiber|sugar|cholesterol|potassium)\b',
            clean_response, re.IGNORECASE
        )
        if claim_phrases and len(set(t.lower() for t in nutrient_terms)) >= 3:
            claim_phrases.append("nutrition_data_extracted")
        if claim_phrases:
            clean_response += (
                "\n\n(System note: ARIA claimed to store data but no ACTION blocks "
                "were emitted. The data may not have been saved. Please verify or retry.)"
            )
            if log_fn:
                log_fn("CLAIM_WITHOUT_ACTION", "warning",
                       error=f"Response claims '{claim_phrases}' but 0 actions found")

    return clean_response
