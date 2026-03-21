"""ARIA ACTION block processing — extracts and executes ACTION blocks from Claude responses."""

import json
import logging
import re
from datetime import datetime, timedelta

import calendar_store
import vehicle_store
import health_store
import legal_store
import timer_store
import nutrition_store
import fitbit_store

log = logging.getLogger("aria")


def process_actions(response_text: str, expect_actions: list[str] | None = None,
                    metadata: dict | None = None,
                    log_fn=None) -> str:
    """Extract and execute ACTION blocks from Claude's response.

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
    actions = re.findall(r'<!--ACTION::(\{.*?\})-->', response_text, re.DOTALL)
    failures = []
    action_types_found = []

    for action_json in actions:
        try:
            action = json.loads(action_json)
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
                health_store.add_entry(
                    entry_date=action["date"],
                    category=action["category"],
                    description=action["description"],
                    severity=action.get("severity"),
                    sleep_hours=action.get("sleep_hours"),
                    meal_type=action.get("meal_type"),
                )
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
                nutrition_store.add_item(
                    food_name=action["food_name"],
                    meal_type=action.get("meal_type", "snack"),
                    nutrients=action.get("nutrients", {}),
                    servings=action.get("servings", 1.0),
                    serving_size=action.get("serving_size", ""),
                    source=action.get("source", "label_photo"),
                    notes=action.get("notes", ""),
                    entry_date=action.get("date"),
                    entry_time=action.get("time"),
                )
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
    if not actions:
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
