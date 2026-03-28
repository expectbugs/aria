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


# --- Timer Execution ---

def fire_timer(timer: dict):
    """Execute a due timer.

    Marks timer complete BEFORE delivery attempt. If delivery fails,
    logs loudly but does not retry (prevents infinite retry loops).
    """
    delivery = timer.get("delivery", "sms")
    message = timer.get("message", timer.get("label", "Timer fired"))
    priority = timer.get("priority", "gentle")

    # Skip quiet hours unless urgent
    if is_quiet_hours() and priority != "urgent":
        log.info("Timer %s deferred (quiet hours): %s", timer["id"], timer["label"])
        return False

    # C8: Mark complete BEFORE delivery to prevent retry storms
    timer_store.complete_timer(timer["id"])
    log.info("Timer fired [%s] %s: %s", delivery, timer["id"], timer["label"])

    if delivery == "voice":
        # Generate TTS via daemon and push audio to phone
        try:
            import httpx
            resp = httpx.post(
                f"{DAEMON_URL}/ask/audio",
                json={"text": message},
                headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
                timeout=60,
            )
            if resp.status_code == 200:
                # Save WAV and push to phone
                wav_path = config.DATA_DIR / "timer_audio.wav"
                wav_path.write_bytes(resp.content)
                import push_audio
                if not push_audio.push_audio(str(wav_path)):
                    # Voice push failed, fall back to SMS
                    log.warning("Voice push failed for timer %s, falling back to SMS",
                                timer["id"])
                    sms.send_to_owner(message)
            else:
                log.error("TTS failed for timer %s: %s", timer["id"], resp.status_code)
                sms.send_to_owner(message)
        except Exception as e:
            log.error("DELIVERY FAILED for timer %s: %s (timer already marked complete)",
                      timer["id"], e)
    else:
        # SMS delivery
        try:
            sms.send_to_owner(message)
        except Exception as e:
            log.error("DELIVERY FAILED for timer %s: %s (timer already marked complete)",
                      timer["id"], e)

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


def run_nudge_evaluation():
    """Evaluate nudge conditions and send consolidated SMS if needed.

    C4: Global nudge frequency cap (MAX_NUDGES_PER_DAY, MAX_NUDGES_PER_HOUR).
    C5: Only update cooldowns on successful delivery. Log all attempts to nudge_log.
    """
    if is_quiet_hours():
        return

    cooldowns = load_cooldowns()
    triggers = evaluate_nudges()

    # Filter by cooldowns
    actionable = []
    for nudge_type, description in triggers:
        cooldown_hours = NUDGE_COOLDOWNS.get(nudge_type, 4)
        if is_cooled_down(cooldowns, nudge_type, cooldown_hours):
            actionable.append((nudge_type, description))

    if not actionable:
        return

    # C4: Global nudge frequency cap
    max_per_day = getattr(config, "MAX_NUDGES_PER_DAY", 6)
    max_per_hour = getattr(config, "MAX_NUDGES_PER_HOUR", 2)
    count_24h, count_1h = _get_nudge_counts()

    if count_24h >= max_per_day:
        log.info("Nudge suppressed (daily cap %d/%d reached): %s",
                 count_24h, max_per_day,
                 [desc for _, desc in actionable])
        _log_nudge([t for t, _ in actionable],
                   [d for _, d in actionable], "", "suppressed_daily_cap")
        return

    if count_1h >= max_per_hour:
        log.info("Nudge suppressed (hourly cap %d/%d reached): %s",
                 count_1h, max_per_hour,
                 [desc for _, desc in actionable])
        _log_nudge([t for t, _ in actionable],
                   [d for _, d in actionable], "", "suppressed_hourly_cap")
        return

    # C5: Three-step compose -> deliver -> confirm
    nudge_types = [t for t, _ in actionable]
    descriptions = [desc for _, desc in actionable]

    # Step 1: Compose message via daemon
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/nudge",
            json={"triggers": descriptions},
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=300,
        )
    except Exception as e:
        log.error("Nudge compose failed (network): %s", e)
        _log_nudge(nudge_types, descriptions, "", "compose_failed")
        return  # Do NOT update cooldowns — allow retry next cycle

    if resp.status_code != 200:
        log.error("Nudge compose failed (HTTP %s)", resp.status_code)
        _log_nudge(nudge_types, descriptions, "", "compose_failed")
        return  # Do NOT update cooldowns

    message = resp.json().get("message", "")
    if not message:
        log.warning("Nudge compose returned empty message")
        _log_nudge(nudge_types, descriptions, "", "compose_failed")
        return  # Do NOT update cooldowns

    # Step 2: Deliver via SMS
    try:
        sms.send_long_to_owner(message)
    except Exception as e:
        log.error("Nudge delivery failed: %s", e)
        _log_nudge(nudge_types, descriptions, message, "delivery_failed")
        return  # Do NOT update cooldowns

    # Step 3: Success — update cooldowns AND log
    log.info("Nudge sent: %s", descriptions)
    _log_nudge(nudge_types, descriptions, message, "sent")

    now_str = datetime.now().isoformat()
    for nudge_type, _ in actionable:
        cooldowns[nudge_type] = now_str
    save_cooldowns(cooldowns)


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
        # Get Claude to compose the coaching message
        resp = httpx.post(
            f"{DAEMON_URL}/nudge",
            json={"triggers": triggers, "context": context},
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=30,
        )
        if resp.status_code == 200:
            message = resp.json().get("message", "")
            if message:
                # Generate TTS and push voice to phone
                tts_resp = httpx.post(
                    f"{DAEMON_URL}/ask/audio",
                    json={"text": message},
                    headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
                    timeout=60,
                )
                if tts_resp.status_code == 200:
                    wav_path = config.DATA_DIR / "exercise_audio.wav"
                    wav_path.write_bytes(tts_resp.content)
                    import push_audio
                    if push_audio.push_audio(str(wav_path)):
                        log.info("Exercise coaching (voice): %s", triggers)
                    else:
                        # Fall back to SMS if voice push fails
                        sms.send_long_to_owner(message)
                        log.info("Exercise coaching (SMS fallback): %s", triggers)
                else:
                    sms.send_long_to_owner(message)
                    log.info("Exercise coaching (SMS, TTS failed): %s", triggers)
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


# --- Main ---

def main():
    """Single tick — check timers, location reminders, exercise, fitbit, and maybe nudges.

    Each job is isolated so one failure doesn't block the rest.
    """
    for job_name, job_fn in [
        ("timers", process_timers),
        ("location_reminders", check_location_reminders),
        ("exercise", process_exercise_tick),
        ("fitbit_poll", process_fitbit_poll),
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
        # Lock released on transaction commit — now run the evaluation
        run_nudge_evaluation()
    except Exception:
        log.exception("Tick job 'nudge_evaluation' failed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Tick failed: %s", e)
