#!/usr/bin/env python3
"""ARIA tick — runs every minute via cron.

Checks for due timers and periodically evaluates nudge conditions.
Most ticks are no-ops (<100ms). Only contacts the daemon when
Claude needs to compose a nudge message.

Cron entry:
    * * * * * /home/user/aria/venv/bin/python /home/user/aria/tick.py
"""

import json
import logging
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Set up path so we can import project modules
sys.path.insert(0, str(Path(__file__).parent))

import config
import timer_store
import calendar_store
import health_store
import vehicle_store
import legal_store
import location_store
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
    if config.TICK_STATE_FILE.exists():
        return json.loads(config.TICK_STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    """Save tick state."""
    config.TICK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TICK_STATE_FILE.write_text(json.dumps(state, default=str))


def load_cooldowns() -> dict:
    """Load nudge cooldowns {nudge_type: last_fired_iso}."""
    if config.NUDGE_COOLDOWNS_FILE.exists():
        return json.loads(config.NUDGE_COOLDOWNS_FILE.read_text())
    return {}


def save_cooldowns(cooldowns: dict):
    """Save nudge cooldowns."""
    config.NUDGE_COOLDOWNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.NUDGE_COOLDOWNS_FILE.write_text(json.dumps(cooldowns, default=str))


def is_cooled_down(cooldowns: dict, nudge_type: str, hours: float) -> bool:
    """Check if enough time has passed since the last nudge of this type."""
    last = cooldowns.get(nudge_type)
    if not last:
        return True
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    return last < cutoff


# --- Timer Execution ---

def fire_timer(timer: dict):
    """Execute a due timer."""
    delivery = timer.get("delivery", "sms")
    message = timer.get("message", timer.get("label", "Timer fired"))
    priority = timer.get("priority", "gentle")

    # Skip quiet hours unless urgent
    if is_quiet_hours() and priority != "urgent":
        log.info("Timer %s deferred (quiet hours): %s", timer["id"], timer["label"])
        return False

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
            log.error("Voice delivery failed for timer %s: %s", timer["id"], e)
            sms.send_to_owner(message)
    else:
        # SMS delivery
        try:
            sms.send_to_owner(message)
        except Exception as e:
            log.error("SMS delivery failed for timer %s: %s", timer["id"], e)
            return False

    timer_store.complete_timer(timer["id"])
    log.info("Timer fired [%s] %s: %s", delivery, timer["id"], timer["label"])
    return True


def process_timers():
    """Check and fire any due timers."""
    due = timer_store.get_due()
    for timer in due:
        fire_timer(timer)


# --- Location-Based Reminders ---

def check_location_reminders():
    """Check location-triggered reminders against current GPS position."""
    loc = location_store.get_latest()
    if not loc or not loc.get("location"):
        return

    current_location = loc.get("location", "").lower()
    reminders = calendar_store.get_reminders()

    for r in reminders:
        if not r.get("location") or r.get("done"):
            continue

        reminder_location = r["location"].lower()
        trigger = r.get("location_trigger", "arrive")

        # Resolve known place names (e.g., "home" → "rapids trail, waukesha")
        known = getattr(config, "KNOWN_PLACES", {})
        resolved = known.get(reminder_location, reminder_location)

        # Check if current location matches
        location_match = (
            resolved in current_location
            or current_location in resolved
        )

        if trigger == "arrive" and location_match:
            # Fire the reminder
            message = f"Location reminder: {r['text']} (you're at {loc.get('location', 'this location')})"
            if not is_quiet_hours():
                try:
                    sms.send_to_owner(message)
                    log.info("Location reminder fired: %s", r["id"])
                except Exception as e:
                    log.error("Location reminder SMS failed: %s", e)
            calendar_store.complete_reminder(r["id"])


# --- Nudge Evaluation ---

# Cooldown periods in hours per nudge type
NUDGE_COOLDOWNS = {
    "meal_reminder": 4,
    "calendar_warning": 0.5,
    "reminder_due": 2,
    "diet_check": 8,
    "health_pattern": 24,
    "vehicle_maintenance": 168,  # 7 days
    "legal_deadline": 24,
    "battery_low": 2,
    "location_aware": 4,
}


def evaluate_nudges() -> list[tuple[str, str]]:
    """Run Python condition checks against all data stores.

    Returns list of (nudge_type, description) for triggered conditions.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    triggers = []

    # --- Meal gap ---
    if 8 <= now.hour <= 21:
        meal_entries = health_store.get_entries(days=1, category="meal")
        today_meals = [m for m in meal_entries if m.get("date") == today]
        if today_meals:
            # Check time since last meal
            last_meal_time = max(m.get("created", "") for m in today_meals)
            if last_meal_time:
                try:
                    last_dt = datetime.fromisoformat(last_meal_time)
                    hours_since = (now - last_dt).total_seconds() / 3600
                    if hours_since >= 5:
                        triggers.append(("meal_reminder",
                                         f"It's been {hours_since:.0f} hours since the last logged meal"))
                except ValueError:
                    pass
        elif now.hour >= 12:
            triggers.append(("meal_reminder", "No meals logged today"))

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
                                     f"'{event['title']}' in {minutes_until:.0f} minutes"))
            except ValueError:
                pass

    # --- Overdue reminders ---
    reminders = calendar_store.get_reminders()
    for r in reminders:
        if r.get("due") and r["due"] <= today and not r.get("done"):
            triggers.append(("reminder_due",
                             f"Reminder overdue: {r['text']} (due {r['due']})"))

    # --- Diet check (evening) ---
    if 20 <= now.hour <= 21:
        today_meals = [m for m in health_store.get_entries(days=1, category="meal")
                       if m.get("date") == today]
        if len(today_meals) < 2:
            diet_start = datetime(2026, 3, 17).date()
            diet_day = (now.date() - diet_start).days + 1
            if diet_day > 0:
                triggers.append(("diet_check",
                                 f"Diet day {diet_day}: only {len(today_meals)} meal(s) logged today"))

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
                                 f"Legal deadline in {days_until} day(s): {entry['description']}"))
        except ValueError:
            pass

    # --- Battery low ---
    loc = location_store.get_latest()
    if loc and loc.get("battery_pct") is not None:
        if loc["battery_pct"] <= 15:
            triggers.append(("battery_low",
                             f"Phone battery at {loc['battery_pct']}%"))

    return triggers


def run_nudge_evaluation():
    """Evaluate nudge conditions and send consolidated SMS if needed."""
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

    # Ask Claude to compose a natural nudge message
    descriptions = [desc for _, desc in actionable]
    try:
        import httpx
        resp = httpx.post(
            f"{DAEMON_URL}/nudge",
            json={"triggers": descriptions},
            headers={"Authorization": f"Bearer {config.AUTH_TOKEN}"},
            timeout=30,
        )
        if resp.status_code == 200:
            message = resp.json().get("message", "")
            if message:
                sms.send_to_owner(message)
                log.info("Nudge sent: %s", descriptions)

                # Update cooldowns for all triggered types
                now_str = datetime.now().isoformat()
                for nudge_type, _ in actionable:
                    cooldowns[nudge_type] = now_str
                save_cooldowns(cooldowns)
        else:
            log.error("Nudge endpoint returned %s", resp.status_code)
    except Exception as e:
        log.error("Nudge evaluation failed: %s", e)


# --- Main ---

def main():
    """Single tick — check timers, location reminders, and maybe nudges."""
    # Job 1: Always check timers
    process_timers()

    # Job 2: Always check location-based reminders
    check_location_reminders()

    # Job 2: Nudge evaluation on its own cadence
    state = load_state()
    last_nudge = state.get("last_nudge_check", "")
    cutoff = (datetime.now() - timedelta(minutes=config.NUDGE_INTERVAL_MIN)).isoformat()

    if not last_nudge or last_nudge < cutoff:
        run_nudge_evaluation()
        state["last_nudge_check"] = datetime.now().isoformat()
        save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Tick failed: %s", e)
