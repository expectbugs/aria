#!/usr/bin/env python3
"""ARIA tick — runs every minute via cron.

Checks for due timers and periodically evaluates nudge conditions.
Most ticks are no-ops (<100ms). Only contacts the daemon when
Claude needs to compose a nudge message.

Cron entry:
    * * * * * /home/user/aria/venv/bin/python /home/user/aria/tick.py
"""

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Set up path so we can import project modules
sys.path.insert(0, str(Path(__file__).parent))

import config
import db
import timer_store
import calendar_store
import health_store
import vehicle_store
import legal_store
import location_store
import fitbit_store
import nutrition_store
import sms

logging.basicConfig(
    filename=str(config.LOGS_DIR / "tick.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tick")

DAEMON_URL = f"http://127.0.0.1:{config.PORT}"


def is_quiet_hours() -> bool:
    """Check if we're in quiet hours."""
    hour = datetime.now().hour
    if config.QUIET_HOURS_START <= config.QUIET_HOURS_END:
        return config.QUIET_HOURS_START <= hour < config.QUIET_HOURS_END
    else:  # wraps midnight, e.g., 22-7
        return hour >= config.QUIET_HOURS_START or hour < config.QUIET_HOURS_END


def load_state() -> dict:
    """Load tick state (last nudge check time, etc.)."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM tick_state").fetchall()
    return {r["key"]: r["value"] for r in rows}


def save_state(state: dict):
    """Save tick state."""
    with db.get_conn() as conn:
        for key, value in state.items():
            conn.execute(
                """INSERT INTO tick_state (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                (key, str(value)),
            )


def load_cooldowns() -> dict:
    """Load nudge cooldowns {nudge_type: last_fired_iso}."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT nudge_type, last_fired FROM nudge_cooldowns").fetchall()
    result = {}
    for r in rows:
        ts = r["last_fired"]
        if ts.tzinfo is not None:
            ts = ts.astimezone().replace(tzinfo=None)
        result[r["nudge_type"]] = ts.isoformat()
    return result


def save_cooldowns(cooldowns: dict):
    """Save nudge cooldowns."""
    with db.get_conn() as conn:
        for nudge_type, last_fired in cooldowns.items():
            conn.execute(
                """INSERT INTO nudge_cooldowns (nudge_type, last_fired, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (nudge_type) DO UPDATE
                   SET last_fired = EXCLUDED.last_fired, updated_at = NOW()""",
                (nudge_type, last_fired),
            )


def is_cooled_down(cooldowns: dict, nudge_type: str, hours: float) -> bool:
    """Check if enough time has passed since the last nudge of this type."""
    last = cooldowns.get(nudge_type)
    if not last:
        return True
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    return last < cutoff


# --- Nudge Audit Log ---

def _log_nudge(nudge_types: list[str], descriptions: list[str],
               message: str, status: str):
    """Insert a row into nudge_log for auditing/frequency cap queries."""
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO nudge_log (nudge_types, trigger_descriptions, message, delivery_status)
                   VALUES (%s, %s, %s, %s)""",
                (nudge_types, descriptions, message, status),
            )
    except Exception as e:
        log.error("Failed to write nudge_log: %s", e)


# --- Sync Delivery Dispatch ---

def _sync_deliver(text: str, method: str,
                  sms_target: str | None = None) -> tuple[bool, str]:
    """Synchronous delivery dispatch for cron-invoked code.

    Handles voice (via daemon /ask/audio + push_audio), SMS, and image.
    Voice falls back to SMS on push failure or TTS failure.
    Callers handle evaluate/log/defer — this only dispatches the action.

    Returns (success: bool, actual_method: str).
    actual_method may differ from method if voice fell back to SMS.
    """
    import uuid as _uuid

    if method == "voice":
        try:
            import httpx
            resp = httpx.post(
                f"{DAEMON_URL}/ask/audio",
                json={"text": text},
                headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
                timeout=60,
            )
            if resp.status_code == 200:
                wav_path = config.DATA_DIR / f"voice_{_uuid.uuid4().hex[:8]}.wav"
                wav_path.write_bytes(resp.content)
                import push_audio
                try:
                    if push_audio.push_audio(str(wav_path)):
                        return True, "voice"
                    # Voice push failed — fall back to SMS
                    log.warning("Voice push failed, falling back to SMS")
                finally:
                    try:
                        wav_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                log.error("TTS via daemon failed (HTTP %s), falling back to SMS",
                          resp.status_code)
        except Exception as e:
            log.error("Voice delivery failed: %s — falling back to SMS", e)
        # Voice failed at some point — fall back to SMS
        try:
            if sms_target:
                sms.send_long_sms(sms_target, text)
            else:
                sms.send_to_owner(text)
            return True, "sms"
        except Exception as e:
            log.error("SMS fallback also failed: %s", e)
            return False, "sms"

    elif method == "sms":
        try:
            if sms_target:
                sms.send_long_sms(sms_target, text)
            else:
                sms.send_to_owner(text)
            return True, "sms"
        except Exception as e:
            log.error("SMS delivery failed: %s", e)
            return False, "sms"

    elif method == "image":
        try:
            from sms import _render_sms_image
            import push_image
            import os
            img_path = _render_sms_image(text, header="ARIA")
            push_image.push_image(img_path, caption="ARIA")
            os.unlink(img_path)
            return True, "image"
        except Exception as e:
            log.error("Image delivery failed: %s", e)
            return False, "image"

    else:
        log.warning("Unknown delivery method for sync dispatch: %s", method)
        return False, method


# --- Timer Execution ---

def fire_timer(timer: dict):
    """Execute a due timer.

    Marks timer complete BEFORE delivery attempt. If delivery fails,
    logs loudly but does not retry (prevents infinite retry loops).
    Handles all delivery methods via _sync_deliver().
    """
    import delivery_engine as _de
    hint = timer.get("delivery", "sms")
    message = timer.get("message", timer.get("label", "Timer fired"))
    priority = timer.get("priority", "gentle")
    state = _de.get_user_state()
    decision = _de.evaluate("timer", priority, "timer", hint, _state=state)
    _de.log_decision(decision, "timer", "timer", hint, _state=state)
    delivery = decision.method

    # Skip quiet hours unless urgent
    if is_quiet_hours() and priority != "urgent":
        log.info("Timer %s deferred (quiet hours): %s", timer["id"], timer["label"])
        return False

    # C8: Mark complete BEFORE delivery to prevent retry storms
    timer_store.complete_timer(timer["id"])
    log.info("Timer fired [%s] %s: %s", delivery, timer["id"], timer["label"])

    if delivery == "defer":
        _de.queue_deferred(message, "timer", priority, "timer", decision.reason)
    else:
        success, actual = _sync_deliver(message, delivery)
        if not success:
            log.error("DELIVERY FAILED for timer %s (already marked complete)", timer["id"])

    return True


def process_timers():
    """Check and fire any due timers."""
    due = timer_store.get_due()
    for timer in due:
        fire_timer(timer)


# --- Location-Based Reminders ---

def check_location_reminders():
    """Check location-triggered reminders against current GPS position.

    Supports both "arrive" and "leave" triggers. Leave detection uses
    tick_state to track previous presence at each reminder's location.

    C8: Completes reminder BEFORE delivery. If delivery fails, logs loudly
    but does not retry (prevents infinite retry loops).
    """
    loc = location_store.get_latest()
    if not loc or not loc.get("location"):
        return

    current_location = loc.get("location", "").lower()
    reminders = calendar_store.get_reminders()
    state = load_state()
    state_changed = False

    for r in reminders:
        if not r.get("location") or r.get("done"):
            continue

        reminder_location = r["location"].lower()
        trigger = r.get("location_trigger", "arrive")

        # Resolve known place names (e.g., "home" -> "rapids trail, waukesha")
        known = getattr(config, "KNOWN_PLACES", {})
        resolved = known.get(reminder_location, reminder_location)

        # Check if current location matches
        location_match = (
            resolved in current_location
            or current_location in resolved
        )

        # Track presence state for leave detection
        state_key = f"loc_reminder:{r['id']}"
        was_at = state.get(state_key) == "at"

        if trigger == "arrive" and location_match:
            if is_quiet_hours():
                continue  # retry next tick after quiet hours
            # C8: Complete before delivery
            calendar_store.complete_reminder(r["id"])
            log.info("Location reminder fired: %s", r["id"])
            message = f"Location reminder: {r['text']} (you're at {loc.get('location', 'this location')})"
            try:
                sms.send_to_owner(message)
            except Exception as e:
                log.error("DELIVERY FAILED for location reminder %s: %s "
                          "(reminder already marked complete)", r["id"], e)

        elif trigger == "leave" and was_at and not location_match:
            if is_quiet_hours():
                continue  # retry next tick after quiet hours
            # C8: Complete before delivery
            calendar_store.complete_reminder(r["id"])
            log.info("Location reminder (leave) fired: %s", r["id"])
            message = f"Location reminder: {r['text']} (you left {r['location']})"
            try:
                sms.send_to_owner(message)
            except Exception as e:
                log.error("DELIVERY FAILED for location reminder %s: %s "
                          "(reminder already marked complete)", r["id"], e)

        # Update presence state for next tick
        new_presence = "at" if location_match else "away"
        if state.get(state_key) != new_presence:
            state[state_key] = new_presence
            state_changed = True

    if state_changed:
        save_state(state)


# --- Nudge Evaluation ---

# Cooldown periods in hours per nudge type
NUDGE_COOLDOWNS = {
    "meal_reminder": 4,
    "calendar_warning": 0.5,
    "reminder_due": 12,  # C2: was 2, overdue reminders don't need to ping more than twice a day
    "diet_check": 8,
    "health_pattern": 24,
    "vehicle_maintenance": 168,  # 7 days
    "legal_deadline": 24,
    "battery_low": 2,
    "location_aware": 4,
    "fitbit_sleep": 24,
    "fitbit_hr_anomaly": 12,
    "fitbit_sedentary": 2,
    "fitbit_activity_goal": 4,
    "nutrition_sugar_warn": 4,
    "nutrition_sodium_warn": 4,
    "nutrition_calorie_surplus": 8,
}


def evaluate_nudges() -> list[tuple[str, str]]:
    """Run Python condition checks against all data stores.

    Returns list of (nudge_type, description) for triggered conditions.
    C1: Auto-expires zombie reminders before the reminder loop.
    C3: All time-sensitive triggers include current time context.
    C7: Meal gap uses nutrition_store instead of health_store.
    C10: ValueError in event time parsing is logged, not swallowed.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    time_ctx = now.strftime("%I:%M %p")
    triggers = []

    # C1: Auto-expire stale reminders before the reminder loop
    stale_days = getattr(config, "STALE_REMINDER_DAYS", 3)
    expired = calendar_store.auto_expire_stale_reminders(max_overdue_days=stale_days)
    for r in expired:
        log.info("Auto-expired stale reminder: %s (due %s)", r.get("text"), r.get("due"))

    # --- Meal gap (C7: uses nutrition_store instead of health_store) ---
    if 8 <= now.hour <= 21:
        today_items = nutrition_store.get_items(day=today)
        if today_items:
            # Check time since last nutrition entry
            last_item_time = max(item.get("created", "") for item in today_items)
            if last_item_time:
                try:
                    last_dt = datetime.fromisoformat(last_item_time)
                    hours_since = (now - last_dt).total_seconds() / 3600
                    if hours_since >= 5:
                        triggers.append(("meal_reminder",
                                         f"It's been {hours_since:.0f} hours since the last "
                                         f"logged meal (current time: {time_ctx})"))
                except ValueError:
                    pass
        elif now.hour >= 12:
            triggers.append(("meal_reminder",
                             f"No meals logged today (current time: {time_ctx})"))

    # --- Calendar warning ---
    events = calendar_store.get_events(start=today, end=today)
    for event in events:
        if event.get("time"):
            try:
                event_time = datetime.strptime(
                    f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M"
                )
                minutes_until = (event_time - now).total_seconds() / 60
                if 15 <= minutes_until <= 45:
                    triggers.append(("calendar_warning",
                                     f"'{event['title']}' in {minutes_until:.0f} minutes "
                                     f"(current time: {time_ctx})"))
            except ValueError as e:
                # C10: Log instead of silently swallowing
                log.warning("Failed to parse event time for %s: %s",
                            event.get("title"), e)

    # --- Overdue reminders ---
    reminders = calendar_store.get_reminders()
    for r in reminders:
        if r.get("due") and r["due"] <= today and not r.get("done"):
            triggers.append(("reminder_due",
                             f"Reminder overdue: {r['text']} (due {r['due']}, "
                             f"current time: {time_ctx})"))

    # --- Diet check (evening, C7: uses nutrition_store) ---
    if 20 <= now.hour <= 21:
        today_items = nutrition_store.get_items(day=today)
        if len(today_items) < 2:
            from context import _get_diet_day
            diet_day = _get_diet_day()
            if diet_day:
                triggers.append(("diet_check",
                                 f"Diet day {diet_day}: only {len(today_items)} meal(s) "
                                 f"logged today (current time: {time_ctx})"))

    # --- Health patterns ---
    patterns = health_store.get_patterns(days=7)
    for p in patterns:
        if "warning" in p.lower() or "reported" in p:
            triggers.append(("health_pattern", p))

    # --- Legal deadlines ---
    upcoming = legal_store.get_upcoming_dates()
    for entry in upcoming:
        try:
            deadline = datetime.strptime(entry["date"], "%Y-%m-%d")
            days_until = (deadline.date() - now.date()).days
            if 0 <= days_until <= 3:
                triggers.append(("legal_deadline",
                                 f"Legal deadline in {days_until} day(s): "
                                 f"{entry['description']} (current time: {time_ctx})"))
        except ValueError:
            pass

    # --- Battery low ---
    loc = location_store.get_latest()
    if loc and loc.get("battery_pct") is not None:
        if loc["battery_pct"] <= 15:
            triggers.append(("battery_low",
                             f"Phone battery at {loc['battery_pct']}%"))

    # --- Fitbit: sleep quality ---
    sleep = fitbit_store.get_sleep_summary()
    if sleep and sleep.get("duration_hours"):
        if sleep["duration_hours"] < 5:
            triggers.append(("fitbit_sleep",
                             f"Only {sleep['duration_hours']}h sleep last night "
                             f"(deep: {sleep['deep_minutes']}min, REM: {sleep['rem_minutes']}min)"))

    # --- Fitbit: resting HR anomaly ---
    hr = fitbit_store.get_heart_summary()
    if hr and hr.get("resting_hr"):
        # Single query for 7-day history (replaces 7 sequential get_heart_summary calls)
        resting_hrs = fitbit_store.get_resting_hr_history(days=7)
        if resting_hrs:
            avg = sum(resting_hrs) / len(resting_hrs)
            current = hr["resting_hr"]
            if current > avg + 10:
                triggers.append(("fitbit_hr_anomaly",
                                 f"Resting HR {current} bpm — {current - avg:.0f} bpm above "
                                 f"your 7-day average of {avg:.0f}"))

    # --- Fitbit: sedentary (2+ hours no steps, waking hours only) ---
    if 9 <= now.hour <= 21:
        activity = fitbit_store.get_activity_summary()
        if activity:
            sed = int(activity.get("sedentary_minutes", 0))
            if sed > 120:
                triggers.append(("fitbit_sedentary",
                                 f"You've been sedentary for a while — "
                                 f"{activity['steps']:,} steps today so far "
                                 f"(current time: {time_ctx})"))

    # --- Fitbit: afternoon activity encouragement ---
    if 14 <= now.hour <= 17:
        activity = fitbit_store.get_activity_summary()
        if activity and int(activity.get("steps", 0)) < 3000:
            triggers.append(("fitbit_activity_goal",
                             f"Only {activity['steps']:,} steps so far today — "
                             f"a short walk would help (current time: {time_ctx})"))

    # --- Nutrition: added sugar approaching limit ---
    totals = nutrition_store.get_daily_totals(today)
    if totals["item_count"] > 0:
        if totals.get("added_sugars_g", 0) >= 25:
            triggers.append(("nutrition_sugar_warn",
                             f"Added sugar at {totals['added_sugars_g']:.0f}g — "
                             f"approaching the 36g hard limit for NAFLD"))
        if totals.get("sodium_mg", 0) >= 1600:
            triggers.append(("nutrition_sodium_warn",
                             f"Sodium at {totals['sodium_mg']:.0f}mg — "
                             f"approaching the 1,800mg daily max"))
        # Calorie surplus check (evening)
        if now.hour >= 19:
            net = nutrition_store.get_net_calories(today)
            if net["burned"] > 0 and net["net"] > 0:
                triggers.append(("nutrition_calorie_surplus",
                                 f"Calorie surplus today: {net['consumed']} consumed - "
                                 f"{net['burned']} burned = +{net['net']} net "
                                 f"(target: deficit of 500-1,000)"))

    return triggers


def _get_nudge_counts() -> tuple[int, int]:
    """Query nudge_log for global frequency cap counts.

    Returns (count_last_24h, count_last_1h).
    """
    try:
        with db.get_conn() as conn:
            row_24h = conn.execute(
                """SELECT COUNT(*) AS cnt FROM nudge_log
                   WHERE sent_at > NOW() - INTERVAL '24 hours'
                   AND delivery_status = 'sent'""",
            ).fetchone()
            row_1h = conn.execute(
                """SELECT COUNT(*) AS cnt FROM nudge_log
                   WHERE sent_at > NOW() - INTERVAL '1 hour'
                   AND delivery_status = 'sent'""",
            ).fetchone()
        return (row_24h["cnt"] if row_24h else 0,
                row_1h["cnt"] if row_1h else 0)
    except Exception as e:
        log.error("Failed to query nudge_log counts: %s", e)
        return (0, 0)


def run_unified_delivery():
    """Unified notification pipeline: evaluates nudges + findings, groups, delivers.

    Category behavior:
        A (briefing-only): Suppressed from independent delivery. Injected into
            briefing/debrief context only. 11:50pm safety net catches missed days.
        B (repeat-low): First occurrence delivers immediately. Subsequent same-day
            occurrences only deliver when grouped with a C item.
        C (repeat-high): Always delivers when triggered and cooldown allows.

    Grouping: All actionable items compose into ONE message and count as ONE
    delivery against the unified cap.

    C4: Global frequency cap (MAX_NUDGES_PER_DAY, MAX_NUDGES_PER_HOUR).
    C5: Only update cooldowns on successful delivery.
    """
    from monitors import classify_category, get_undelivered, mark_delivered

    if is_quiet_hours():
        return

    # --- Collect nudge triggers ---
    cooldowns = load_cooldowns()
    triggers = evaluate_nudges()

    cooled_nudges = []
    for nudge_type, description in triggers:
        cooldown_hours = NUDGE_COOLDOWNS.get(nudge_type, 4)
        if is_cooled_down(cooldowns, nudge_type, cooldown_hours):
            category = classify_category(nudge_type, source="nudge")
            cooled_nudges.append((nudge_type, description, category))

    # --- Collect undelivered findings ---
    findings = get_undelivered(min_urgency="low")
    categorized_findings = []
    for f in findings[:10]:
        category = classify_category(f.get("check_key", ""), source="finding")
        categorized_findings.append((f, category))

    # --- Category filtering ---
    # Category A: suppress from delivery (briefing-only)
    cat_a_count = (sum(1 for _, _, c in cooled_nudges if c == "A") +
                   sum(1 for _, c in categorized_findings if c == "A"))

    # Category B + C: eligible for delivery
    deliverable_nudges = [(t, d, c) for t, d, c in cooled_nudges if c != "A"]
    deliverable_findings = [(f, c) for f, c in categorized_findings if c != "A"]

    # Check if any Category C items are present (allows B grouping)
    has_cat_c = (any(c == "C" for _, _, c in deliverable_nudges) or
                 any(c == "C" for _, c in deliverable_findings))

    # Apply B grouping logic
    today_str = datetime.now().strftime("%Y-%m-%d")
    final_nudges = []
    has_first_fire_b = False
    for nudge_type, desc, cat in deliverable_nudges:
        if cat == "C":
            final_nudges.append((nudge_type, desc))
        elif cat == "B":
            last = cooldowns.get(nudge_type)
            first_today = not last or not last.startswith(today_str)
            if first_today:
                final_nudges.append((nudge_type, desc))
                has_first_fire_b = True
            elif has_cat_c:
                final_nudges.append((nudge_type, desc))

    final_findings = []
    for finding, cat in deliverable_findings:
        if cat == "C":
            final_findings.append(finding)
        elif cat == "B":
            if has_cat_c or has_first_fire_b:
                final_findings.append(finding)

    if not final_nudges and not final_findings:
        if cat_a_count:
            log.info("Category A items suppressed (briefing-only): %d", cat_a_count)
        return

    # --- Unified frequency caps ---
    max_per_day = getattr(config, "MAX_NUDGES_PER_DAY", 15)
    max_per_hour = getattr(config, "MAX_NUDGES_PER_HOUR", 2)
    count_24h, count_1h = _get_nudge_counts()

    all_descriptions = ([d for _, d in final_nudges] +
                        [f"[{f['urgency']}] {f['summary']}" for f in final_findings])

    if count_24h >= max_per_day:
        log.info("Delivery suppressed (daily cap %d/%d): %s",
                 count_24h, max_per_day, all_descriptions[:3])
        _log_nudge([t for t, _ in final_nudges],
                   all_descriptions, "", "suppressed_daily_cap")
        return

    if count_1h >= max_per_hour:
        log.info("Delivery suppressed (hourly cap %d/%d): %s",
                 count_1h, max_per_hour, all_descriptions[:3])
        _log_nudge([t for t, _ in final_nudges],
                   all_descriptions, "", "suppressed_hourly_cap")
        return

    # --- Minimum interval ---
    state = load_state()
    min_interval = getattr(config, "MONITOR_DELIVERY_MIN_INTERVAL_MIN", 30)
    last_delivery = state.get("last_unified_delivery", "")
    cutoff = (datetime.now() - timedelta(minutes=min_interval)).isoformat()
    if last_delivery and last_delivery >= cutoff:
        return

    # --- Compose grouped message ---
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/nudge",
            json={"triggers": all_descriptions},
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=300,
        )
    except Exception as e:
        log.error("Unified compose failed (network): %s", e)
        _log_nudge([t for t, _ in final_nudges],
                   all_descriptions, "", "compose_failed")
        return

    if resp.status_code != 200:
        log.error("Unified compose failed (HTTP %s)", resp.status_code)
        _log_nudge([t for t, _ in final_nudges],
                   all_descriptions, "", "compose_failed")
        return

    message = resp.json().get("message", "")
    if not message:
        log.warning("Unified compose returned empty message")
        _log_nudge([t for t, _ in final_nudges],
                   all_descriptions, "", "compose_failed")
        return

    # --- Deliver ---
    try:
        sms.send_long_to_owner(message)
    except Exception as e:
        log.error("Unified delivery failed: %s", e)
        _log_nudge([t for t, _ in final_nudges],
                   all_descriptions, message, "delivery_failed")
        return

    # --- Success: update cooldowns, mark findings, log ---
    log.info("Unified delivery: %d nudges + %d findings: %s",
             len(final_nudges), len(final_findings), all_descriptions)
    _log_nudge([t for t, _ in final_nudges],
               all_descriptions, message, "sent")

    now_str = datetime.now().isoformat()
    for nudge_type, _ in final_nudges:
        cooldowns[nudge_type] = now_str
    save_cooldowns(cooldowns)

    if final_findings:
        finding_ids = [f["id"] for f in final_findings]
        mark_delivered(finding_ids, "sms")

    state["last_unified_delivery"] = now_str
    save_state(state)


# --- Fitbit Polling ---

def fetch_fitbit_snapshot():
    """Fetch a full Fitbit daily snapshot via the daemon."""
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/fitbit/sync",
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=30,
        )
        if resp.status_code == 200:
            log.info("Fitbit snapshot synced: %s", resp.json().get("keys", []))
        else:
            log.error("Fitbit sync returned %s", resp.status_code)
    except Exception as e:
        log.error("Fitbit sync failed: %s", e)


def fetch_exercise_hr():
    """Fetch recent intraday HR for exercise coaching via the daemon.

    Uses a lightweight endpoint that only fetches 1-2 minutes of HR data.
    """
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/fitbit/exercise-hr",
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            log.error("Exercise HR fetch returned %s", resp.status_code)
    except Exception as e:
        log.error("Exercise HR fetch failed: %s", e)
    return None


def send_exercise_nudge(triggers: list[str], context: str):
    """Send an exercise coaching nudge via VOICE (not SMS)."""
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/nudge",
            json={"triggers": triggers, "context": context},
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=30,
        )
        if resp.status_code == 200:
            message = resp.json().get("message", "")
            if message:
                success, method = _sync_deliver(message, "voice")
                log.info("Exercise coaching (%s): %s", method, triggers)
    except Exception as e:
        log.error("Exercise nudge failed: %s", e)


def process_exercise_tick():
    """Every-minute exercise coaching when exercise mode is active.

    C9: Enforces minimum 3-minute interval between coaching nudges.
    """
    exercise = fitbit_store.get_exercise_state()
    if not exercise:
        return

    started = datetime.fromisoformat(exercise["started_at"])
    elapsed_min = int((datetime.now() - started).total_seconds() / 60)
    zones = exercise.get("target_zones", {})
    nudge_count = exercise.get("nudge_count", 0)

    # C9: Rate limit — minimum 3 minutes between exercise nudges
    if elapsed_min > 0 and nudge_count > 0:
        avg_interval = elapsed_min / nudge_count
        if avg_interval < 3:
            return  # Too frequent, skip this tick

    # Fetch latest HR
    hr_data = fetch_exercise_hr()
    if not hr_data or not hr_data.get("readings"):
        return

    readings = hr_data["readings"]
    fitbit_store.record_exercise_hr(readings)

    if not readings:
        return

    # Get current HR (latest reading)
    current_hr = readings[-1].get("value", 0)
    if not current_hr:
        return

    fat_burn = zones.get("fat_burn", {})
    cardio = zones.get("cardio", {})
    peak = zones.get("peak", {})
    warm_up = zones.get("warm_up", {})

    # Determine coaching triggers
    triggers = []
    coaching_ctx = fitbit_store.get_exercise_coaching_context(state=exercise)

    # Nudge every 5 minutes with a status update
    if elapsed_min > 0 and elapsed_min % 5 == 0:
        if current_hr < warm_up.get("min", 100):
            triggers.append(f"{elapsed_min} min in — HR is {current_hr} bpm, below warm-up zone. Pick up the pace!")
        elif current_hr < fat_burn.get("min", 112):
            triggers.append(f"{elapsed_min} min in — HR is {current_hr} bpm. Push a little harder to hit fat burn zone ({fat_burn.get('min', '?')}+ bpm)")
        elif current_hr <= fat_burn.get("max", 140):
            triggers.append(f"{elapsed_min} min in — HR is {current_hr} bpm. Perfect fat burn zone, keep this pace!")
        elif current_hr <= cardio.get("max", 155):
            triggers.append(f"{elapsed_min} min in — HR is {current_hr} bpm. Solid cardio zone!")
        elif current_hr > peak.get("min", 155):
            triggers.append(f"{elapsed_min} min in — HR is {current_hr} bpm. That's peak zone — ease off a bit unless you're doing intervals")

    # Milestone nudges
    if elapsed_min == 30 and nudge_count < 10:
        triggers.append(f"30 minutes done! HR is {current_hr} bpm. Great work — aim for 10-15 more if you're feeling good")
    elif elapsed_min == 45 and nudge_count < 15:
        triggers.append(f"45 minutes! That's your target. HR is {current_hr} bpm. Cool down when you're ready")

    # Safety: HR too high for sustained period
    all_readings = exercise.get("hr_readings", [])
    recent_3 = [r["hr"] for r in all_readings[-3:] if r.get("hr")]
    if recent_3 and all(hr > peak.get("min", 155) for hr in recent_3):
        triggers.append(f"HR has been above {peak.get('min', 155)} bpm for 3+ minutes — consider slowing down")

    if triggers:
        send_exercise_nudge(triggers, coaching_ctx)
        # Update nudge count
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE fitbit_exercise SET nudge_count = nudge_count + 1 WHERE active = TRUE"
            )


def process_fitbit_poll():
    """Fitbit data polling on appropriate cadence."""
    if is_quiet_hours():
        return  # No polling during sleep

    state = load_state()
    last_fitbit = state.get("last_fitbit_sync", "")
    cutoff = (datetime.now() - timedelta(minutes=15)).isoformat()

    if not last_fitbit or last_fitbit < cutoff:
        fetch_fitbit_snapshot()
        state["last_fitbit_sync"] = datetime.now().isoformat()
        save_state(state)


# --- Google Calendar + Gmail Polling ---

def fetch_google_calendar():
    """Fetch upcoming Google Calendar events via the daemon."""
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/google/calendar/sync",
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=30,
        )
        if resp.status_code == 200:
            log.info("Google Calendar synced: %d events",
                     resp.json().get("events_synced", 0))
        else:
            log.warning("Google Calendar sync failed: HTTP %s", resp.status_code)
    except Exception as e:
        log.error("Google Calendar sync error: %s", e)


def fetch_google_gmail():
    """Fetch recent Gmail messages via the daemon."""
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/google/gmail/sync",
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=30,
        )
        if resp.status_code == 200:
            log.info("Gmail synced: %d messages",
                     resp.json().get("messages_synced", 0))
        else:
            log.warning("Gmail sync failed: HTTP %s", resp.status_code)
    except Exception as e:
        log.error("Gmail sync error: %s", e)


def process_google_poll():
    """Google data polling on appropriate cadences."""
    if is_quiet_hours():
        return

    state = load_state()

    # Calendar: every 15 minutes (incremental sync via syncToken)
    last_cal = state.get("last_google_calendar_sync", "")
    cal_cutoff = (datetime.now() - timedelta(minutes=15)).isoformat()
    if not last_cal or last_cal < cal_cutoff:
        fetch_google_calendar()
        state["last_google_calendar_sync"] = datetime.now().isoformat()

    # Gmail: every 3 minutes
    last_gmail = state.get("last_google_gmail_sync", "")
    gmail_cutoff = (datetime.now() - timedelta(minutes=3)).isoformat()
    if not last_gmail or last_gmail < gmail_cutoff:
        fetch_google_gmail()
        state["last_google_gmail_sync"] = datetime.now().isoformat()

    save_state(state)


# --- Monitor Processing ---

def process_monitors():
    """Run domain monitors on their configured schedules.

    Each monitor has a schedule_minutes interval. Last-run timestamps are
    tracked in tick_state. Findings are stored with fingerprint deduplication.
    """
    if not getattr(config, "MONITORS_ENABLED", True):
        return

    from monitors import store_finding, cleanup_expired, mark_delivered_bulk
    from monitors.health import HealthMonitor
    from monitors.fitness import FitnessMonitor
    from monitors.vehicle import VehicleMonitor
    from monitors.legal import LegalMonitor
    from monitors.system import SystemMonitor
    from monitors.gmail import GmailMonitor

    state = load_state()
    now = datetime.now()

    monitor_classes = [
        HealthMonitor, FitnessMonitor, VehicleMonitor,
        LegalMonitor, SystemMonitor, GmailMonitor,
    ]

    for cls in monitor_classes:
        monitor = cls()
        key = f"last_monitor_{monitor.domain}"
        last_run = state.get(key, "")
        cutoff = (now - timedelta(minutes=monitor.schedule_minutes)).isoformat()

        if last_run and last_run >= cutoff:
            continue  # not due yet

        if monitor.waking_only and is_quiet_hours():
            continue

        try:
            findings = monitor.run()
            for finding in findings:
                store_finding(finding)
            state[key] = now.isoformat()
            if findings:
                log.info("[MONITOR] %s: %d findings", monitor.domain, len(findings))
        except Exception:
            log.exception("[MONITOR] %s monitor failed", monitor.domain)

    save_state(state)

    # Drain stale email findings (prevents old emails from clogging context)
    try:
        mark_delivered_bulk("gmail", max_age_hours=24)
    except Exception:
        log.exception("[MONITOR] gmail stale cleanup failed")


def process_junk_archival():
    """Archive (remove from INBOX) emails classified as definite junk.

    Only processes Tier 1 (curated rules) junk — no heuristic or AI junk.
    Runs after monitor classification to catch newly identified junk.
    """
    if not getattr(config, "JUNK_AUTO_ARCHIVE", True):
        return

    try:
        import httpx

        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT e.id FROM email_cache e
                   JOIN email_classifications c ON c.email_id = e.id
                   WHERE c.classification = 'junk'
                   AND c.tier = 'tier1_hard'
                   AND 'INBOX' = ANY(e.labels)
                   LIMIT 1000""",
            ).fetchall()

        if not rows:
            return

        ids = [r["id"] for r in rows]
        log.info("[JUNK_ARCHIVE] Archiving %d Tier 1 junk emails from inbox", len(ids))

        resp = httpx.post(
            f"{DAEMON_URL}/google/gmail/archive",
            json={"message_ids": ids},
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=60,
        )
        if resp.status_code == 200:
            log.info("[JUNK_ARCHIVE] Archived %d junk emails",
                     resp.json().get("archived", 0))
        else:
            log.warning("[JUNK_ARCHIVE] Archive failed: HTTP %s", resp.status_code)
    except Exception:
        log.exception("[JUNK_ARCHIVE] Failed")


def process_email_cleanup():
    """Trash emails matching auto_cleanup rules that have expired.

    Also expires stale email watches.
    Uses gmail_strategy.get_auto_cleanup_candidates() to find expired emails,
    then trashes them via the daemon's Gmail sync endpoint.
    """
    from gmail_strategy import get_auto_cleanup_candidates

    # Expire stale watches
    try:
        import gmail_store
        gmail_store.expire_watches()
    except Exception:
        log.exception("[EMAIL_CLEANUP] Watch expiry failed")

    candidates = get_auto_cleanup_candidates()
    if not candidates:
        return

    import httpx
    log.info("[EMAIL_CLEANUP] %d candidates for auto-cleanup", len(candidates))

    for c in candidates:
        try:
            action = c.get("action", "trash")
            if action == "trash":
                resp = httpx.post(
                    f"{DAEMON_URL}/google/gmail/trash",
                    json={"message_id": c["email_id"]},
                    headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    log.info("[EMAIL_CLEANUP] Trashed %s (%s: %s)",
                             c["email_id"], c["from"], c.get("subject", "")[:50])
                else:
                    log.warning("[EMAIL_CLEANUP] Trash failed for %s: %s",
                                c["email_id"], resp.status_code)
        except Exception:
            log.exception("[EMAIL_CLEANUP] Failed to process %s", c["email_id"])


def _any_briefing_or_debrief_today() -> bool:
    """Check if any briefing or debrief was delivered today."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                """SELECT 1 FROM request_log
                   WHERE timestamp >= %s AND status = 'ok'
                   AND (input ILIKE 'good morning%%'
                        OR input ILIKE 'morning brief%%'
                        OR input ILIKE 'briefing%%'
                        OR input ILIKE 'start my day%%'
                        OR input ILIKE 'good night%%'
                        OR input ILIKE 'end my day%%'
                        OR input ILIKE 'nightly debrief%%'
                        OR input ILIKE 'evening debrief%%'
                        OR input ILIKE 'wrap up my day%%')
                   LIMIT 1""",
                (today,),
            ).fetchone()
        return row is not None
    except Exception as e:
        log.error("Safety net briefing check failed: %s", e)
        return True  # fail safe: assume briefing happened


def process_safety_net():
    """11:50pm safety net: deliver suppressed Category A items if no briefings today.

    If neither a morning briefing nor evening debrief was delivered today,
    composes a consolidated daily summary of all Category A items.
    """
    from monitors import classify_category, get_undelivered

    now = datetime.now()
    if now.hour != 23 or now.minute < 50 or now.minute > 55:
        return

    state = load_state()
    today = now.strftime("%Y-%m-%d")
    if state.get("last_safety_net", "").startswith(today):
        return  # already ran today

    if _any_briefing_or_debrief_today():
        state["last_safety_net"] = now.isoformat()
        save_state(state)
        return

    # Collect Category A items
    cat_a_descriptions = []

    # Category A nudges that would trigger now
    triggers = evaluate_nudges()
    for nudge_type, description in triggers:
        if classify_category(nudge_type, source="nudge") == "A":
            cat_a_descriptions.append(description)

    # Category A undelivered findings
    findings = get_undelivered(min_urgency="info")
    cat_a_finding_ids = []
    for f in findings:
        if classify_category(f.get("check_key", ""), source="finding") == "A":
            cat_a_descriptions.append(f"[{f['urgency']}] {f['summary']}")
            cat_a_finding_ids.append(f["id"])

    if not cat_a_descriptions:
        state["last_safety_net"] = now.isoformat()
        save_state(state)
        return

    # Compose and deliver
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/nudge",
            json={"triggers": cat_a_descriptions,
                   "context": "This is the 11:50pm daily summary. "
                              "The user did not request a briefing or debrief today. "
                              "Frame this as a quick end-of-day summary."},
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=300,
        )
        if resp.status_code != 200:
            log.error("Safety net compose failed: HTTP %s", resp.status_code)
            state["last_safety_net"] = now.isoformat()
            save_state(state)
            return

        message = resp.json().get("message", "")
        if not message:
            state["last_safety_net"] = now.isoformat()
            save_state(state)
            return

        sms.send_long_to_owner(message)

        _log_nudge(["safety_net"], cat_a_descriptions, message, "sent")

        if cat_a_finding_ids:
            from monitors import mark_delivered
            mark_delivered(cat_a_finding_ids, "sms_safety_net")

        log.info("Safety net delivered: %d items", len(cat_a_descriptions))

    except Exception as e:
        log.error("Safety net delivery failed: %s", e)

    state["last_safety_net"] = now.isoformat()
    save_state(state)


# --- Deferred Delivery Processing ---

def process_deferred_deliveries():
    """Attempt to deliver queued deferred items when user state allows.

    Re-evaluates user state for each item. If the engine no longer returns
    "defer", delivers via _sync_deliver(). Expired items are cleaned up.
    """
    import delivery_engine as _de

    pending = _de.get_pending_deferred()
    if not pending:
        return

    for item in pending:
        decision = _de.evaluate(
            content_type=item.get("content_type", "response"),
            priority=item.get("priority", "normal"),
            source=item.get("source", "voice"),
        )

        if decision.method == "defer":
            continue  # still not a good time

        delivered, actual = _sync_deliver(item["content"], decision.method)
        if delivered:
            _de.mark_deferred_delivered(item["id"], actual)
            log.info("[DELIVERY] Deferred item %d delivered via %s",
                     item["id"], actual)

    # Clean up expired items
    _de.cleanup_expired_deferred()


# --- Cleanup Jobs ---

def cleanup_processed_webhooks():
    """Delete webhook idempotency records older than 7 days."""
    try:
        with db.get_conn() as conn:
            result = conn.execute(
                "DELETE FROM processed_webhooks WHERE processed_at < NOW() - INTERVAL '7 days'"
            )
            if result.rowcount:
                log.info("Cleaned up %d old webhook records", result.rowcount)
    except Exception as e:
        log.error("Webhook cleanup failed: %s", e)


# --- Main ---

def main():
    """Single tick — check timers, location reminders, exercise, fitbit, monitors, nudges, deferred.

    Each job is isolated so one failure doesn't block the rest.
    """
    # Write heartbeat FIRST — proves tick.py is running regardless of
    # whether any jobs fire or nudges evaluate.
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO tick_state (key, value, updated_at)
                   VALUES ('last_tick_run', %s, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                (datetime.now().isoformat(),),
            )
    except Exception:
        log.exception("Failed to write tick heartbeat")

    for job_name, job_fn in [
        ("timers", process_timers),
        ("location_reminders", check_location_reminders),
        ("exercise", process_exercise_tick),
        ("fitbit_poll", process_fitbit_poll),
        ("google_poll", process_google_poll),
        ("monitors", process_monitors),
        ("email_cleanup", process_email_cleanup),
        ("junk_archival", process_junk_archival),
        ("safety_net", process_safety_net),
        ("deferred_deliveries", process_deferred_deliveries),
        ("webhook_cleanup", cleanup_processed_webhooks),
    ]:
        try:
            job_fn()
        except Exception:
            log.exception("Tick job '%s' failed", job_name)

    # Job 5: Nudge evaluation on its own cadence
    # C6: Use pg_try_advisory_xact_lock to prevent concurrent evaluations
    try:
        with db.get_transaction() as conn:
            # Advisory lock 42 — if another tick instance holds it, skip
            locked = conn.execute(
                "SELECT pg_try_advisory_xact_lock(42) AS locked"
            ).fetchone()
            if not locked or not locked["locked"]:
                log.info("Nudge evaluation skipped (another instance holds lock)")
                return

            # Check interval under the lock
            row = conn.execute(
                "SELECT value FROM tick_state WHERE key = 'last_nudge_check'"
            ).fetchone()
            last_nudge = row["value"] if row else ""
            cutoff = (datetime.now() - timedelta(minutes=config.NUDGE_INTERVAL_MIN)).isoformat()

            if last_nudge and last_nudge >= cutoff:
                return  # Not time yet

            # Stamp timestamp atomically under the lock
            conn.execute(
                """INSERT INTO tick_state (key, value, updated_at)
                   VALUES ('last_nudge_check', %s, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                (datetime.now().isoformat(),),
            )
        # Lock released on transaction commit — now run unified delivery
        run_unified_delivery()
    except Exception:
        log.exception("Tick job 'nudge_evaluation' failed")

    # Cleanup: expired monitor findings
    try:
        from monitors import cleanup_expired
        cleanup_expired()
    except Exception:
        log.exception("Tick job 'finding_cleanup' failed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Tick failed: %s", e)
