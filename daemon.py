"""ARIA FastAPI daemon — core voice assistant backend."""

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

import config
import db
import calendar_store
import vehicle_store
import health_store
import legal_store
import location_store
import timer_store
import projects
import sms
import weather
import news
import fitbit
import fitbit_store
import nutrition_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and clean up resources."""
    db.get_pool()  # warm the connection pool
    yield
    await _claude_session._kill()
    db.close()


app = FastAPI(title="ARIA", version="0.4.3", lifespan=lifespan)

# Async task storage: task_id -> {"status": "processing"/"done"/"error", "audio": bytes, "error": str}
_tasks: dict[str, dict] = {}

# Ensure dirs exist (still needed for inbox, mms_outbox, etc.)
config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
config.DATA_DIR.mkdir(parents=True, exist_ok=True)

START_TIME = time.time()


# --- Models ---

class AskRequest(BaseModel):
    text: str

class AskResponse(BaseModel):
    response: str
    source: str = "claude"


# --- Request Logging ---

def log_request(text: str, status: str, response: str = "", error: str = "",
                duration: float = 0.0):
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO request_log (input, status, response, error, duration_s)
                   VALUES (%s, %s, %s, %s, %s)""",
                (text, status, (response[:500] if response else ""),
                 error, round(duration, 2)),
            )
    except Exception as e:
        logging.getLogger("aria").error("Failed to log request: %s", e)


# --- Context Gathering ---

def _get_today_requests() -> list[dict]:
    """Read today's entries from the request log."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM request_log WHERE timestamp >= %s ORDER BY timestamp",
            (today,),
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def _briefing_delivered_today() -> bool:
    """Check if a morning briefing was already delivered today."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM request_log
               WHERE timestamp >= %s AND status = 'ok'
               AND (input ILIKE 'good morning%%'
                    OR input ILIKE 'morning brief%%'
                    OR input ILIKE 'briefing%%'
                    OR input ILIKE 'start my day%%')
               LIMIT 1""",
            (today,),
        ).fetchone()
    return row is not None


async def build_request_context(text: str, is_image: bool = False) -> str:
    """Build keyword-triggered context for ANY ARIA request.

    This is the single unified context builder. Called from /ask, /ask/file,
    and /sms to ensure identical behavior regardless of input channel.
    Handles briefings, debriefs, and all keyword-triggered data injection.

    Returns the context string.
    """
    text_lower = text.lower()
    ctx_parts = []

    # --- Weather ---
    weather_keywords = ["weather", "temperature", "forecast", "rain",
                        "snow", "storm", "wind", "cold", "hot", "warm",
                        "outside", "umbrella", "jacket", "coat", "humid",
                        "degrees", "sunny", "cloudy", "ice", "freeze"]
    if any(kw in text_lower for kw in weather_keywords):
        try:
            current = await weather.get_current_conditions()
            forecast = await weather.get_forecast()
            alerts = await weather.get_alerts()
            ctx_parts.append(
                f"Current weather: {current['description']}, "
                f"{current['temperature_f']}°F, "
                f"humidity {current['humidity']:.0f}%, "
                f"wind {current['wind_mph']} mph"
            )
            ctx_parts.append("Forecast: " + "; ".join(
                f"{p['name']}: {p['temperature']}°{p['unit']} {p['summary']}"
                for p in forecast
            ))
            if alerts:
                ctx_parts.append("Alerts: " + "; ".join(
                    f"{a['event']}: {a['headline']}" for a in alerts
                ))
        except Exception as e:
            ctx_parts.append(f"Weather data unavailable: {e}")

    # --- Calendar & Reminders (always injected) ---
    today = datetime.now().strftime("%Y-%m-%d")
    week_end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    calendar_keywords = ["calendar", "schedule", "week", "appointment",
                         "event", "plan", "busy", "free", "available",
                         "tomorrow", "tonight", "monday", "tuesday",
                         "wednesday", "thursday", "friday", "saturday", "sunday"]
    if any(kw in text_lower for kw in calendar_keywords):
        events = calendar_store.get_events(start=today, end=week_end)
    else:
        events = calendar_store.get_events(start=today, end=today)

    reminders = calendar_store.get_reminders()
    if events:
        ctx_parts.append("Events: " + "; ".join(
            f"[id={e['id']}] {e['date']} {e['title']}"
            + (f" at {e['time']}" if e.get('time') else "")
            for e in events
        ))
    if reminders:
        ctx_parts.append("Active reminders: " + "; ".join(
            f"[id={r['id']}] {r['text']}"
            + (f" (due {r['due']})" if r.get('due') else "")
            for r in reminders
        ))

    # --- Vehicle ---
    vehicle_keywords = ["xterra", "vehicle", "car", "truck", "oil",
                        "maintenance", "mileage", "tire", "brake"]
    if any(kw in text_lower for kw in vehicle_keywords):
        v_entries = vehicle_store.get_entries(limit=10)
        if v_entries:
            ctx_parts.append("Vehicle log: " + "; ".join(
                f"[id={v['id']}] {v['date']} {v['event_type']}: {v['description']}"
                + (f" ({v['mileage']} mi)" if v.get("mileage") else "")
                for v in v_entries
            ))
        latest = vehicle_store.get_latest_by_type()
        if latest:
            ctx_parts.append("Latest per service type: " + "; ".join(
                f"{t}: {e['date']}" + (f" at {e['mileage']} mi" if e.get("mileage") else "")
                for t, e in latest.items()
            ))

    # --- Health + Nutrition + Fitness (unified) ---
    health_nutrition_keywords = [
        "health", "body", "pain", "sleep", "slept", "exercise",
        "symptom", "headache", "back", "sore", "body log", "medication",
        "heart rate", "heart", "hrv", "spo2", "oxygen", "steps",
        "active", "fitbit", "vo2", "cardio", "resting", "workout",
        "diet", "food", "eat", "ate", "meal", "lunch", "dinner",
        "breakfast", "snack", "smoothie", "nutrition", "calories",
        "factor", "nafld", "liver", "sugar", "protein", "fiber",
        "sodium", "fat", "carb", "vitamin", "omega", "label",
        "weight", "deficit", "surplus", "burn",
    ]
    if is_image or any(kw in text_lower for kw in health_nutrition_keywords):
        health_ctx = gather_health_context()
        if health_ctx:
            ctx_parts.append(health_ctx)

        diet_ref = config.DATA_DIR / "diet_reference.md"
        if diet_ref.exists():
            ctx_parts.append("Diet reference:\n" + diet_ref.read_text())

        h_entries = health_store.get_entries(days=14)
        if h_entries:
            ctx_parts.append("Health log (last 14 days): " + "; ".join(
                f"[id={h['id']}] {h['date']} {h['category']}: {h['description']}"
                + (f" (severity {h['severity']}/10)" if h.get("severity") else "")
                + (f" ({h['sleep_hours']}h sleep)" if h.get("sleep_hours") else "")
                for h in h_entries
            ))

        fitbit_trend = fitbit_store.get_trend(days=7)
        if fitbit_trend:
            ctx_parts.append(fitbit_trend)

    # --- Projects ---
    project_keywords = ["project update", "project status", "project brief",
                        "status of", "update on"]
    if any(kw in text_lower for kw in project_keywords):
        available = projects.list_projects()
        if available:
            match = projects.find_project(text)
            if match:
                name, contents = match
                ctx_parts.append(f"Project brief for '{name}':\n{contents}")
            else:
                ctx_parts.append("Available project briefs: " + ", ".join(available))
        else:
            ctx_parts.append("No project briefs found in data/projects/. "
                             "Create one by writing a markdown file there.")

    # --- Timers ---
    timer_keywords = ["timer", "alarm", "remind me in", "tell me when",
                      "set a timer", "cancel timer", "wake me",
                      "how long", "minutes", "countdown"]
    if any(kw in text_lower for kw in timer_keywords):
        active_timers = timer_store.get_active()
        if active_timers:
            ctx_parts.append("Active timers: " + "; ".join(
                f"[id={t['id']}] {t['label']} — fires at {t['fire_at'][11:16]}"
                f" ({t['delivery']})"
                for t in active_timers
            ))

    # --- Location ---
    location_keywords = ["where am i", "where i am", "location",
                         "how far", "near me", "close to",
                         "my location", "where are you"]
    if any(kw in text_lower for kw in location_keywords):
        loc = location_store.get_latest()
        if loc:
            loc_name = loc.get("location", f"{loc['lat']:.4f}, {loc['lon']:.4f}")
            ctx_parts.append(
                f"User's last known location: {loc_name}"
                f" ({loc['lat']:.4f}, {loc['lon']:.4f})"
                f" (as of {loc['timestamp'][11:16]})"
            )
            if loc.get("battery_pct") is not None:
                ctx_parts.append(f"Phone battery: {loc['battery_pct']}%")
            history = location_store.get_history(hours=4)
            if len(history) > 1:
                def _loc_label(h):
                    name = h.get("location")
                    return name if name else f"{h['lat']:.4f},{h['lon']:.4f}"
                ctx_parts.append("Recent movement (last 4 hours): " + "; ".join(
                    f"{h['timestamp'][11:16]} → {_loc_label(h)}"
                    for h in history[-12:]
                ))

    # --- Legal ---
    legal_keywords = ["legal", "court", "lawyer", "attorney",
                      "walworth", "filing", "case update", "legal case",
                      "court case", "court date", "lawsuit"]
    if any(kw in text_lower for kw in legal_keywords):
        l_entries = legal_store.get_entries(limit=10)
        if l_entries:
            ctx_parts.append("Legal case log: " + "; ".join(
                f"[id={l['id']}] {l['date']} {l['entry_type']}: {l['description']}"
                for l in l_entries
            ))
        upcoming = legal_store.get_upcoming_dates()
        if upcoming:
            ctx_parts.append("Upcoming legal dates: " + "; ".join(
                f"{u['date']}: {u['description']}" for u in upcoming
            ))

    return "\n".join(ctx_parts) if ctx_parts else ""


async def _get_context_for_text(text: str, is_image: bool = False) -> str:
    """Route text to the right context builder.

    Detects morning briefings and evening debriefs, otherwise uses
    keyword-triggered context. Single source of truth — used by /ask,
    /ask/voice, and /sms instead of repeating detection logic.
    """
    text_lower = text.lower()
    briefing_triggers = ["good morning", "morning brief", "briefing", "start my day"]
    if any(text_lower.startswith(p) for p in briefing_triggers):
        # Explicit repeat requests always get the briefing
        repeat_words = ["again", "repeat", "one more time", "redo"]
        is_repeat = any(w in text_lower for w in repeat_words)
        if is_repeat or not _briefing_delivered_today():
            return await gather_briefing_context()
        # Already delivered today — fall through to normal context
    if any(text_lower.startswith(p)
           for p in ["good night", "end my day", "nightly debrief",
                      "evening debrief", "wrap up my day"]):
        return await gather_debrief_context()
    return await build_request_context(text, is_image=is_image)


def gather_health_context() -> str:
    """Build a compact, unified health snapshot for any health/nutrition query.

    This is the single source of truth for ARIA's health awareness. Used across
    all request paths (voice, file upload, SMS) to ensure consistent, complete
    context regardless of how a request arrives.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    parts = []

    # Today's meal diary (health_store — what's been eaten)
    meals_today = [m for m in health_store.get_entries(days=1, category="meal")
                   if m.get("date") == today]
    if meals_today:
        parts.append("Meals consumed today: " + "; ".join(
            f"{m['meal_type']}: {m['description']}" for m in meals_today
        ))

    # Structured nutrition tracking (nutrition_store — numeric data)
    nutrition_ctx = nutrition_store.get_context(today)
    if nutrition_ctx:
        parts.append(nutrition_ctx)

    # Flag incomplete tracking: meals in diary without structured nutrition data
    nutrition_items = nutrition_store.get_items(day=today)
    diary_count = len(meals_today)
    nutrition_count = len([n for n in nutrition_items
                          if n.get("notes", "").upper().find("PANTRY") == -1])
    if diary_count > 0 and nutrition_count < diary_count:
        parts.append(f"Note: {diary_count} meal(s) in diary but only {nutrition_count} "
                     f"have structured nutrition data — calorie totals may be incomplete.")

    # Fitbit data — today's vitals and activity
    fitbit_ctx = fitbit_store.get_briefing_context(today)
    if fitbit_ctx:
        parts.append(fitbit_ctx)

    # Net calorie balance (needs both nutrition intake and Fitbit burn)
    net = nutrition_store.get_net_calories(today)
    if net["consumed"] > 0 and net["burned"] > 0:
        parts.append(
            f"Calorie balance: {net['consumed']} consumed - {net['burned']} burned "
            f"= {net['net']} net (target deficit: 500-1,000)"
        )

    # Health patterns (last 7 days)
    patterns = health_store.get_patterns(days=7)
    if patterns:
        parts.append("Health patterns (7d): " + "; ".join(patterns))

    # Diet day counter
    diet_start = date.fromisoformat(config.DIET_START_DATE)
    diet_day = (datetime.now().date() - diet_start).days + 1
    if diet_day > 0:
        parts.append(f"Diet day {diet_day}")

    # Exercise mode
    exercise = fitbit_store.get_exercise_state()
    if exercise:
        parts.append(fitbit_store.get_exercise_coaching_context())

    if not parts:
        return ""

    return "\n".join(parts)


async def gather_debrief_context() -> str:
    """Gather context for a good-night debrief."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    parts = []
    parts.append(f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}")

    # Today's ARIA interactions
    today_requests = _get_today_requests()
    if today_requests:
        parts.append(f"\nToday's interactions ({len(today_requests)} total):")
        for r in today_requests:
            ts = r.get("timestamp", "")
            time_str = ts[11:16] if len(ts) >= 16 else ""
            inp = r.get("input", "")[:100]
            status = r.get("status", "")
            parts.append(f"  - {time_str} [{status}] {inp}")
    else:
        parts.append("\nNo interactions logged today.")

    # Today's calendar events
    today_events = calendar_store.get_events(start=today, end=today)
    if today_events:
        parts.append("\nToday's appointments:")
        for e in today_events:
            time_str = f" at {e['time']}" if e.get('time') else ""
            parts.append(f"  - {e['title']}{time_str}")

    # Tomorrow's calendar (prep)
    tomorrow_events = calendar_store.get_events(start=tomorrow, end=tomorrow)
    if tomorrow_events:
        parts.append("\nTomorrow's appointments (prep tonight):")
        for e in tomorrow_events:
            time_str = f" at {e['time']}" if e.get('time') else ""
            parts.append(f"  - {e['title']}{time_str}")

    # Active reminders
    reminders = calendar_store.get_reminders()
    if reminders:
        parts.append("\nActive reminders carried forward:")
        for r in reminders:
            due = f" (due: {r['due']})" if r.get('due') else ""
            parts.append(f"  - {r['text']}{due}")

    # Specialist log activity today
    vehicle_today = [v for v in vehicle_store.get_entries()
                     if v.get("date") == today]
    if vehicle_today:
        parts.append("\nVehicle maintenance logged today:")
        for v in vehicle_today:
            parts.append(f"  - {v['event_type']}: {v['description']}")

    health_today = [h for h in health_store.get_entries(days=1)
                    if h.get("date") == today]
    if health_today:
        parts.append("\nHealth logged today:")
        for h in health_today:
            parts.append(f"  - {h['category']}: {h['description']}")

    health_patterns = health_store.get_patterns(days=7)
    if health_patterns:
        parts.append("\nHealth & nutrition patterns (last 7 days):")
        for p in health_patterns:
            parts.append(f"  - {p}")

    # Nutrition tracking — structured daily totals
    nutrition_ctx = nutrition_store.get_context(today)
    if nutrition_ctx:
        parts.append(f"\n{nutrition_ctx}")
    else:
        # Fall back to health_store meal diary
        meals_today = [m for m in health_store.get_entries(days=1, category="meal")
                       if m.get("date") == today]
        if meals_today:
            parts.append("\nMeals logged today:")
            for m in meals_today:
                parts.append(f"  - {m['description']}")
        else:
            parts.append("\nNo meals logged today.")

    # Diet day counter
    diet_start = date.fromisoformat(config.DIET_START_DATE)
    diet_day = (now.date() - diet_start).days + 1
    if diet_day > 0:
        parts.append(f"\nDiet day {diet_day}")

    # Fitbit — today's activity and health data
    fitbit_ctx = fitbit_store.get_briefing_context(today)
    if fitbit_ctx:
        parts.append(f"\n{fitbit_ctx}")

    legal_today = [l for l in legal_store.get_entries()
                   if l.get("date") == today]
    if legal_today:
        parts.append("\nLegal case activity today:")
        for l in legal_today:
            parts.append(f"  - {l['entry_type']}: {l['description']}")

    # Tomorrow's weather for prep
    try:
        forecast = await weather.get_forecast()
        # First period is typically "Tonight" or similar at night
        if forecast:
            parts.append("\nWeather ahead:")
            for p in forecast[:3]:
                parts.append(f"  {p['name']}: {p['temperature']}°{p['unit']} — {p['summary']}")
    except Exception:
        pass

    return "\n".join(parts)


async def gather_briefing_context() -> str:
    """Gather all context data for a morning briefing."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    week_end = (now + timedelta(days=7)).strftime("%Y-%m-%d")

    parts = []
    parts.append(f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}")

    # Weather
    try:
        current = await weather.get_current_conditions()
        forecast = await weather.get_forecast()
        alerts = await weather.get_alerts()
        parts.append(f"\nCurrent weather: {current['description']}, "
                     f"{current['temperature_f']}°F, "
                     f"humidity {current['humidity']}%, "
                     f"wind {current['wind_mph']} mph")
        parts.append("\nForecast:")
        for p in forecast:
            parts.append(f"  {p['name']}: {p['temperature']}°{p['unit']} — {p['summary']}")
        if alerts:
            parts.append("\nWeather Alerts:")
            for a in alerts:
                parts.append(f"  {a['severity']}: {a['headline']}")
    except Exception as e:
        parts.append(f"\nWeather unavailable: {e}")

    # Calendar
    today_events = calendar_store.get_events(start=today, end=today)
    tomorrow_events = calendar_store.get_events(start=tomorrow, end=tomorrow)
    week_events = calendar_store.get_events(start=today, end=week_end)

    if today_events:
        parts.append("\nToday's appointments:")
        for e in today_events:
            time_str = f" at {e['time']}" if e.get('time') else ""
            parts.append(f"  - [id={e['id']}] {e['title']}{time_str}")
    else:
        parts.append("\nNo appointments today.")

    if tomorrow_events:
        parts.append("\nTomorrow's appointments (prep today):")
        for e in tomorrow_events:
            time_str = f" at {e['time']}" if e.get('time') else ""
            parts.append(f"  - [id={e['id']}] {e['title']}{time_str}")

    upcoming = [e for e in week_events
                if e.get("date") not in (today, tomorrow)]
    if upcoming:
        parts.append("\nUpcoming this week:")
        for e in upcoming:
            parts.append(f"  - [id={e['id']}] {e['date']}: {e['title']}")

    # Reminders
    reminders = calendar_store.get_reminders()
    if reminders:
        parts.append("\nActive reminders:")
        for r in reminders:
            due = f" (due: {r['due']})" if r.get('due') else ""
            parts.append(f"  - [id={r['id']}] {r['text']}{due}")

    # News digest
    try:
        digest = await news.get_news_digest(max_per_feed=3)
        if digest:
            parts.append("\nNews headlines:")
            for category, items in digest.items():
                parts.append(f"  {category.title()}:")
                for item in items:
                    parts.append(f"    - {item['title']}")
    except Exception:
        pass  # News is non-critical

    # Vehicle maintenance — last 3 entries + latest per type
    vehicle_entries = vehicle_store.get_entries(limit=3)
    if vehicle_entries:
        parts.append("\nRecent vehicle maintenance:")
        for v in vehicle_entries:
            miles = f" at {v['mileage']} mi" if v.get("mileage") else ""
            parts.append(f"  - [id={v['id']}] {v['date']}: {v['event_type']} — {v['description']}{miles}")

    # Health — patterns from last 7 days
    health_patterns = health_store.get_patterns(days=7)
    if health_patterns:
        parts.append("\nHealth & nutrition patterns (last 7 days):")
        for p in health_patterns:
            parts.append(f"  - {p}")

    # Nutrition — weekly summary for briefing
    weekly_nutrition = nutrition_store.get_weekly_summary()
    if weekly_nutrition:
        parts.append(f"\n{weekly_nutrition}")

    # Diet — day count since diet start (March 17, 2026)
    diet_start = date.fromisoformat(config.DIET_START_DATE)
    diet_day = (now.date() - diet_start).days + 1
    if diet_day > 0:
        parts.append(f"\nDiet day {diet_day} (started March 17, 2026)")

    # Fitbit health data
    fitbit_ctx = fitbit_store.get_briefing_context(today)
    if fitbit_ctx:
        parts.append(f"\n{fitbit_ctx}")
    fitbit_trend = fitbit_store.get_trend(days=7)
    if fitbit_trend:
        parts.append(f"\n{fitbit_trend}")

    # Location — latest known position
    loc = location_store.get_latest()
    if loc:
        loc_name = loc.get("location", f"{loc['lat']:.4f}, {loc['lon']:.4f}")
        parts.append(f"\nLast known location: {loc_name} (as of {loc['timestamp'][11:16]})")
        if loc.get("battery_pct") is not None:
            parts.append(f"  Phone battery: {loc['battery_pct']}%")

    # Legal — upcoming dates only (don't surface case details unprompted)
    legal_upcoming = legal_store.get_upcoming_dates()
    if legal_upcoming:
        parts.append("\nUpcoming legal dates:")
        for l in legal_upcoming:
            parts.append(f"  - [id={l['id']}] {l['date']}: {l['description']}")

    return "\n".join(parts)


def build_system_prompt() -> str:
    """Build the system prompt that defines ARIA's behavior.

    NOTE: This is set once when the persistent Claude process spawns.
    The current date/time is injected per-request in the context instead.
    """
    host = config.HOST_NAME
    name = config.OWNER_NAME

    # Build known places string from config
    places_str = ". ".join(
        f'"{k}" = {config.KNOWN_PLACES[k]}'
        for k in config.KNOWN_PLACES
    )

    return f"""You are ARIA (Ambient Reasoning & Intelligence Assistant), a personal voice assistant for {name}.
You are warm, natural, and conversational — like a trusted friend who happens to be brilliant. Use contractions, casual phrasing, natural rhythm. No markdown, no bullet points, no code blocks unless asked. Don't end responses with "would you like me to..." or "anything else?"

ABSOLUTE RULES — INTEGRITY:
1. NEVER claim you did something unless you actually did it. If you say "logged" or "stored" or "saved," it MUST mean you emitted an ACTION block in this response. Your conversation memory is NOT persistent storage — it is lost between sessions. The ONLY way to persistently store data is via ACTION blocks.
2. NEVER present a guess as fact. If you are not certain, say "I think" or "I'm not sure but." If you cannot verify, say so. DO NOT fill gaps with plausible-sounding but unverified information.
3. NEVER hallucinate facts, data, numbers, or capabilities. If you don't know, say "I don't know." If you can't do something, say so. Wrong information is worse than no information.
4. If something failed or you couldn't complete a task, say so clearly. Do not downplay or hide failures.
These rules are non-negotiable. {name} depends on ARIA for life decisions — health, legal, financial. Inaccurate information is dangerous.

IMPORTANT: If {name} asks a question, ONLY answer it. Do NOT take action unless explicitly told to. "Can you do X?" gets an answer, not the action. "Do X" gets the action.
Exception: when {name} describes eating something specific ("I had the salmon for lunch"), log it as a meal without asking.

When you're unsure about something, say so. Never guess when you can verify — check the filesystem, run a command, read a file. If you're estimating, say "I think" not "it is."

You can emit multiple ACTION blocks in one response when a request involves several actions.

About {name}:
- {config.OWNER_LIVING_SITUATION}
- Works {config.OWNER_WORK_SCHEDULE} at {config.OWNER_EMPLOYER} — currently {config.OWNER_WORK_STATUS}.
- Drives a {config.OWNER_VEHICLE}.
- {config.OWNER_HEALTH_NOTES}
- Timezone: {config.OWNER_TIMEZONE}.

Known places: {places_str}.

You run on {host} (Gentoo Linux, OpenRC — NOT systemd). Full console access with passwordless sudo. Run shell commands freely for read-only queries. For anything that MODIFIES the system, describe what you'll do and ask for confirmation first.

Channels: requests arrive via voice (Tasker), file share (AutoShare), or SMS/MMS (Twilio). For voice, respond naturally for speech. For SMS (noted in context), keep responses under 300 chars, no formatting. Images: use push_image.py for voice requests, MMS via sms.send_mms() for SMS conversations.

DELIVERY ROUTING — MANDATORY:
When """ + name + """ asks for a specific delivery method (voice, SMS, text, etc.), you MUST emit a set_delivery ACTION block. The system handles the actual routing — you just signal the intent. This is NOT optional. If """ + name + """ says "answer via voice", "respond by voice", "text me the answer", or ANY variation requesting a specific delivery method, emit set_delivery. The system will generate TTS and push audio, or send SMS, accordingly. Do NOT try to run push_audio.py yourself — the system does it automatically based on your set_delivery ACTION.
Note: outbound SMS may be unreliable (A2P registration pending). When delivering via voice, the system handles TTS and audio push automatically.

Tools:
- Image Gen: `python ~/imgen/generate.py "prompt" [--steps N] [--seed N] [--width W] [--height H] [--output path.png]` (12-16 steps quick, 24-30 high quality)
- Upscale: `~/upscale/upscale4k.sh input.png [output.png]`
- 4K workflow: when user asks for a 4K image, generate at 1920x1080 (--width 1920 --height 1080) then upscale. Do NOT generate at phone resolution and upscale — that just stretches a small image.
- Visual: Matplotlib, Graphviz, SVG — output must be PNG for phone
- Push Image: `python ~/aria/push_image.py /path/to/image.png [--caption "..."]`
- SMS: `python -c "import sms; sms.send_to_owner('text')"` — MMS: `python -c "import sms; sms.send_mms(config.OWNER_PHONE_NUMBER, 'caption', '/path/to/image.png')"`
- Phone images: 540x1212 resolution, no upscale.
- File Input: photos, PDFs, text files arrive as content blocks. For food photos, check against diet reference.
- Location: GPS every 5 min with reverse geocoding. Position and history injected on location keywords.
- Project briefs: markdown in data/projects/. Summarize conversationally. Create/update via shell.

ACTION blocks — MANDATORY for any data storage. Place at the END of your response. Without an ACTION block, data is NOT saved — no exceptions. Do NOT use shell commands, file writes, or conversation memory as a substitute for ACTION blocks. Use ONLY exact IDs from context (e.g. [id=a3f8b2c1]). Never guess an ID. If you can't find the ID, tell """ + name + """.
""" + """
Calendar:
<!--ACTION::{"action": "add_event", "title": "...", "date": "YYYY-MM-DD", "time": "HH:MM"}-->
<!--ACTION::{"action": "modify_event", "id": "...", "title": "...", "date": "YYYY-MM-DD", "time": "HH:MM"}-->
<!--ACTION::{"action": "delete_event", "id": "..."}-->

Reminders (recurring: daily|weekly|monthly. location_trigger: arrive|leave):
<!--ACTION::{"action": "add_reminder", "text": "...", "due": "YYYY-MM-DD"}-->
<!--ACTION::{"action": "add_reminder", "text": "...", "recurring": "weekly"}-->
<!--ACTION::{"action": "add_reminder", "text": "...", "location": "home", "location_trigger": "arrive"}-->
<!--ACTION::{"action": "complete_reminder", "id": "..."}-->
<!--ACTION::{"action": "delete_reminder", "id": "..."}-->

Vehicle (Xterra) — mileage/cost optional:
<!--ACTION::{"action": "log_vehicle", "date": "YYYY-MM-DD", "event_type": "oil_change|tire_rotation|brake_service|fluid|filter|inspection|repair|general", "description": "...", "mileage": 123456, "cost": 45.99}-->
<!--ACTION::{"action": "delete_vehicle_entry", "id": "..."}-->

Health — severity (1-10) for pain/symptoms, sleep_hours for sleep, meal_type for meals:
<!--ACTION::{"action": "log_health", "date": "YYYY-MM-DD", "category": "pain|sleep|exercise|symptom|medication|meal|nutrition|general", "description": "...", "severity": 7, "sleep_hours": 6.5, "meal_type": "breakfast|lunch|dinner|snack"}-->
<!--ACTION::{"action": "delete_health_entry", "id": "..."}-->

Nutrition — ALWAYS log when """ + name + """ sends a nutrition label photo or describes eating something. Extract ALL nutrients from the label. Use null for values not on the label, not 0. Store values PER SERVING as printed. Ask about servings consumed if ambiguous (but for Factor/CookUnity single-container meals, assume 1 serving = whole container). After logging, report the running daily totals and any limit warnings. Also log a brief health_store meal entry for the food diary.
<!--ACTION::{"action": "log_nutrition", "food_name": "...", "meal_type": "breakfast|lunch|dinner|snack", "servings": 1.0, "serving_size": "1 container (283g)", "source": "label_photo|manual|estimate", "nutrients": {"calories": 450, "total_fat_g": 18, "saturated_fat_g": 5, "trans_fat_g": 0, "cholesterol_mg": 95, "sodium_mg": 680, "total_carb_g": 32, "dietary_fiber_g": 6, "total_sugars_g": 8, "added_sugars_g": 2, "protein_g": 38, "vitamin_d_mcg": null, "calcium_mg": null, "iron_mg": null, "potassium_mg": null, "omega3_mg": null}, "notes": ""}-->
<!--ACTION::{"action": "delete_nutrition_entry", "id": "..."}-->

Legal — SENSITIVE. Never reference unless """ + name + """ brings it up:
<!--ACTION::{"action": "log_legal", "date": "YYYY-MM-DD", "entry_type": "development|filing|contact|note|court_date|deadline", "description": "...", "contacts": ["name"]}-->
<!--ACTION::{"action": "delete_legal_entry", "id": "..."}-->

Timers — "minutes" for relative, "time" (HH:MM 24h) for absolute today. Delivery "sms" default, "voice" only if explicitly asked. Priority "urgent" for alarms (bypasses quiet hours 12am-7am). Always compose a natural "message" — this exact text gets delivered by the autonomous tick system:
<!--ACTION::{"action": "set_timer", "label": "...", "minutes": 30, "delivery": "sms", "message": "..."}-->
<!--ACTION::{"action": "set_timer", "label": "...", "time": "14:30", "delivery": "sms", "message": "..."}-->
<!--ACTION::{"action": "cancel_timer", "id": "..."}-->
When setting a timer, confirm the exact fire time and delivery method.

Delivery routing — ALWAYS emit when """ + name + """ requests a specific response delivery method:
<!--ACTION::{"action": "set_delivery", "method": "voice"}-->
<!--ACTION::{"action": "set_delivery", "method": "sms"}-->

Exercise — ONLY activate when """ + name + """ explicitly says he's going to exercise or asks for coaching. NEVER auto-detect:
<!--ACTION::{"action": "start_exercise", "exercise_type": "stationary_bike|walking|general"}-->
<!--ACTION::{"action": "end_exercise"}-->
When exercise starts, confirm activation and the target heart rate zones. During exercise mode, ARIA polls HR every minute and sends coaching nudges via voice. Mode auto-expires after 90 minutes.

Fitbit health data is available in context for health-related queries. """ + name + """'s target HR zones are computed from resting HR and age using the Karvonen formula. When discussing fitness data, be encouraging and contextualize against his NAFLD recovery and spinal health goals.

"Good morning" → full morning briefing from context. Be warm, cover everything, acknowledge diet day milestones.
"Good night" → evening debrief: today's summary, meals logged, pending items, tomorrow's prep, offer to set alarm. Keep it warm — this is a wind-down.
Resolve relative dates ("next Tuesday", "tomorrow") to exact dates using the current date/time.
If you don't know something, say so briefly."""


# --- File Processing ---

IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".py", ".js",
                   ".sh", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log",
                   ".ts", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".css"}


def build_file_content(file_bytes: bytes, filename: str,
                       mime_type: str | None) -> list[dict]:
    """Build Claude API content blocks for a file.

    Returns a list of content blocks (image, document, or text) suitable
    for inclusion in a multi-part message to the Claude CLI.
    """
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(filename)

    blocks = []

    if mime_type in IMAGE_TYPES:
        b64 = base64.b64encode(file_bytes).decode()
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": b64},
        })
    elif mime_type == "application/pdf":
        b64 = base64.b64encode(file_bytes).decode()
        blocks.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        })
    elif (mime_type and mime_type.startswith("text/")) or \
         Path(filename).suffix.lower() in TEXT_EXTENSIONS:
        text_content = file_bytes.decode("utf-8", errors="replace")
        blocks.append({
            "type": "text",
            "text": f"[File: {filename}]\n{text_content}",
        })
    else:
        blocks.append({
            "type": "text",
            "text": (f"[File received: {filename} "
                     f"({mime_type or 'unknown type'}, {len(file_bytes)} bytes) "
                     f"— this file type cannot be read directly]"),
        })

    return blocks


# --- Persistent Claude Session ---

log = logging.getLogger("aria")


class ClaudeSession:
    """Manages a persistent Claude CLI subprocess using stream-json protocol.

    Instead of spawning a new process per request (1-2s startup overhead each time),
    this keeps a single process alive and sends messages via stdin/stdout.
    Conversation context is maintained across requests automatically.
    """

    MAX_REQUESTS = 200  # respawn after N requests to keep context manageable

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._request_count = 0

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _spawn(self):
        """Spawn a fresh Claude CLI process with stream-json I/O."""
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["CLAUDE_CODE_EFFORT_LEVEL"] = "auto"

        self._proc = await asyncio.create_subprocess_exec(
            config.CLAUDE_CLI,
            "--print",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--model", "opus",
            "--dangerously-skip-permissions",
            "--system-prompt", build_system_prompt(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._request_count = 0
        log.info("Claude session spawned (pid=%s)", self._proc.pid)

    async def _kill(self):
        """Kill the current process if alive."""
        if self._is_alive():
            try:
                self._proc.kill()
                await self._proc.wait()
            except Exception:
                pass
        self._proc = None

    async def _ensure_alive(self):
        """Ensure the subprocess is running. Respawn if dead or stale."""
        if not self._is_alive() or self._request_count >= self.MAX_REQUESTS:
            if self._is_alive():
                log.info("Recycling Claude session after %d requests", self._request_count)
                await self._kill()
            await self._spawn()

    async def query(self, user_text: str, extra_context: str = "",
                    file_blocks: list[dict] | None = None) -> str:
        """Send a prompt to the persistent Claude process and return the response.

        If file_blocks is provided, sends a multi-part message with text + file
        content (images, PDFs, text files) using Claude's content block format.
        """
        async with self._lock:
            await self._ensure_alive()

            # Build prompt with fresh datetime
            now = datetime.now()
            parts = [f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"]
            if extra_context:
                parts.append(f"\n[CONTEXT]\n{extra_context}\n[/CONTEXT]")
            parts.append(f"\nUser says: {user_text}")
            prompt = "\n".join(parts)

            # Build message content — text-only or multimodal
            if file_blocks:
                content = [{"type": "text", "text": prompt}] + file_blocks
            else:
                content = prompt

            # Send user message as NDJSON
            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": content},
            }) + "\n"
            self._proc.stdin.write(msg.encode())
            await self._proc.stdin.drain()
            self._request_count += 1

            # Read stdout lines until we get a result
            try:
                while True:
                    line = await asyncio.wait_for(
                        self._proc.stdout.readline(),
                        timeout=config.CLAUDE_TIMEOUT,
                    )
                    if not line:
                        raise RuntimeError("Claude process exited unexpectedly")

                    try:
                        data = json.loads(line.decode().strip())
                    except json.JSONDecodeError:
                        continue  # skip non-JSON lines

                    msg_type = data.get("type")

                    if msg_type == "result":
                        if data.get("is_error"):
                            raise RuntimeError(
                                f"Claude error: {data.get('result', 'unknown')}"
                            )
                        return data.get("result", "")

                    elif msg_type == "control_request":
                        # Auto-approve any permission/hook requests
                        resp = json.dumps({
                            "type": "control_response",
                            "response": {
                                "subtype": "success",
                                "request_id": data.get("request_id"),
                                "response": {"behavior": "allow"},
                            }
                        }) + "\n"
                        self._proc.stdin.write(resp.encode())
                        await self._proc.stdin.drain()

                    # Ignore other types (assistant, system, stream_event, etc.)

            except asyncio.TimeoutError:
                log.error("Claude query timed out after %ss", config.CLAUDE_TIMEOUT)
                await self._kill()
                raise RuntimeError(
                    f"Claude timed out after {config.CLAUDE_TIMEOUT}s"
                )
            except Exception:
                log.exception("Claude session error, killing process")
                await self._kill()
                raise


# Global persistent session
_claude_session = ClaudeSession()


async def ask_claude(user_text: str, extra_context: str = "",
                     file_blocks: list[dict] | None = None) -> str:
    """Send a query to Claude via the persistent CLI session."""
    return await _claude_session.query(user_text, extra_context, file_blocks)


# --- Action Processing ---

def process_actions(response_text: str, expect_actions: list[str] | None = None,
                    metadata: dict | None = None) -> str:
    """Extract and execute ACTION blocks from Claude's response.

    Returns the cleaned response, replacing it with an error message
    if any actions failed so the user isn't told something worked when it didn't.

    expect_actions: optional list of action types that SHOULD be present
                    (e.g. ["log_nutrition"] for nutrition label photos).
                    If expected actions are missing, a warning is appended.
    metadata: optional mutable dict to receive extracted metadata like
              delivery routing preferences ({"delivery": "voice"}).
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
            log_request("ACTION", "error", error=str(e))

    # Strip action blocks from spoken response
    clean_response = re.sub(r'<!--ACTION::.*?-->', '', response_text, flags=re.DOTALL).strip()

    if failures:
        log_request("ACTION", "error", error="; ".join(failures))
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
            log_request("ACTION_MISSING", "error",
                        error=f"Expected {missing}, got {action_types_found}")
            clean_response = warning

    # Detect claim-without-action: response says data was stored but no actions found
    if not actions:
        claim_words = re.findall(
            r'\b(logged|stored|saved|recorded|added to|tracked|captured|noted and logged)\b',
            clean_response, re.IGNORECASE
        )
        # Also detect nutrition-specific claims: mentions 3+ nutrient terms
        # without a log_nutrition action (Claude extracted data but didn't store it)
        nutrient_terms = re.findall(
            r'\b(calories|protein|carb|fat|sodium|fiber|sugar|cholesterol|potassium)\b',
            clean_response, re.IGNORECASE
        )
        if claim_words and len(set(t.lower() for t in nutrient_terms)) >= 3:
            claim_words.append("nutrition_data_extracted")
        if claim_words:
            clean_response += (
                "\n\n(System note: ARIA claimed to store data but no ACTION blocks "
                "were emitted. The data may not have been saved. Please verify or retry.)"
            )
            log_request("CLAIM_WITHOUT_ACTION", "warning",
                        error=f"Response claims '{claim_words}' but 0 actions found")

    return clean_response


# --- Endpoints ---

@app.get("/health")
async def health():
    uptime = time.time() - START_TIME
    checks = {}

    # Database
    try:
        with db.get_conn() as conn:
            conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    # Claude CLI process
    checks["claude"] = "ok" if _claude_session._is_alive() else "down"

    # TTS model
    checks["tts"] = "loaded" if _kokoro is not None else "not loaded"

    # Whisper model (if enabled)
    if getattr(config, 'ENABLE_WHISPER', False):
        try:
            import whisper_engine
            checks["whisper"] = "loaded" if whisper_engine._engine and whisper_engine._engine._model else "not loaded"
        except Exception:
            checks["whisper"] = "error"

    degraded = checks.get("database") != "ok" or checks.get("claude") != "ok"

    return {
        "status": "degraded" if degraded else "ok",
        "uptime_s": round(uptime, 1),
        "version": app.version,
        "checks": checks,
    }


def verify_auth(request: Request):
    """Check Bearer token."""
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {config.AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


class LocationUpdate(BaseModel):
    lat: float
    lon: float
    accuracy: float | None = None
    speed: float | None = None
    battery: int | None = None


@app.post("/location")
async def update_location(loc: LocationUpdate, request: Request):
    """Receive a location update from the phone."""
    verify_auth(request)
    entry = await location_store.record(
        lat=loc.lat, lon=loc.lon, accuracy=loc.accuracy,
        speed=loc.speed, battery=loc.battery,
    )
    return {"status": "ok", "timestamp": entry["timestamp"],
            "location": entry.get("location", "")}


# --- Fitbit Webhook ---

@app.post("/fitbit/subscribe")
async def fitbit_subscribe(request: Request):
    """Register Fitbit webhook subscription. Run once after auth."""
    verify_auth(request)
    client = fitbit.get_client()
    result = await client.create_subscription()
    subs = await client.list_subscriptions()
    return {"result": result, "active_subscriptions": subs}


@app.post("/fitbit/exercise-hr")
async def fitbit_exercise_hr(request: Request):
    """Fetch recent intraday HR for exercise coaching. Used by tick.py."""
    verify_auth(request)
    client = fitbit.get_client()
    readings = await client.get_recent_heart_rate(minutes=2)
    return {"readings": readings}


@app.get("/webhook/fitbit")
async def fitbit_webhook_verify(verify: str = ""):
    """Fitbit subscription verification — responds to GET with the verification code."""
    if verify == config.FITBIT_WEBHOOK_VERIFY:
        return Response(content=verify, media_type="text/plain")
    raise HTTPException(status_code=404, detail="Invalid verification code")


@app.post("/fitbit/sync")
async def fitbit_sync(request: Request):
    """Manually trigger a Fitbit data fetch. Useful for initial pull and testing."""
    verify_auth(request)
    client = fitbit.get_client()
    today = datetime.now().strftime("%Y-%m-%d")
    snapshot = await client.fetch_daily_snapshot(today)
    fitbit_store.save_snapshot(snapshot)
    return {"status": "ok", "date": today, "keys": [k for k in snapshot if snapshot[k]]}


@app.post("/webhook/fitbit")
async def fitbit_webhook(request: Request):
    """Fitbit subscription notification — data changed, fetch updates."""
    body = await request.json()
    log.info("Fitbit webhook: %s", json.dumps(body)[:200])

    # Fitbit sends a list of notifications like:
    # [{"collectionType": "activities", "date": "2026-03-19", "ownerId": "...", ...}]
    # Fetch fresh data for each unique date mentioned
    dates = set()
    for notification in body:
        d = notification.get("date")
        if d:
            dates.add(d)

    if not dates:
        dates.add("today")

    client = fitbit.get_client()
    for day in dates:
        try:
            snapshot = await client.fetch_daily_snapshot(day)
            fitbit_store.save_snapshot(snapshot)
        except Exception as e:
            log.error("Fitbit fetch failed for %s: %s", day, e)

    return Response(status_code=204)


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request):
    start = time.time()
    verify_auth(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty input")

    try:
        extra_context = await _get_context_for_text(text)
        response = await ask_claude(text, extra_context)
        response = process_actions(response)

        duration = time.time() - start
        log_request(text, "ok", response=response, duration=duration)

        return AskResponse(response=response)

    except Exception as e:
        duration = time.time() - start
        error_msg = str(e)
        log_request(text, "error", error=error_msg, duration=duration)
        raise HTTPException(status_code=500, detail=f"Processing error: {error_msg}")


# Cache the Kokoro TTS model so it's not reloaded on every request
_kokoro = None

def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
    return _kokoro


def _tts_sync(text: str) -> bytes:
    """Generate TTS audio synchronously. Called from thread pool."""
    import io
    import soundfile as sf

    kokoro = _get_kokoro()
    samples, sample_rate = kokoro.create(
        text, voice=config.KOKORO_VOICE, speed=1.0, lang="en-us"
    )
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    buf.seek(0)
    return buf.read()


async def _generate_tts(text: str) -> bytes:
    """Generate TTS audio without blocking the event loop."""
    return await asyncio.to_thread(_tts_sync, text)


@app.post("/ask/audio")
async def ask_audio(req: AskRequest, request: Request):
    """Same as /ask but returns WAV audio via Kokoro TTS."""
    result = await ask(req, request)

    try:
        audio = await _generate_tts(result.response)
        return Response(content=audio, media_type="audio/wav")
    except Exception as e:
        log_request("TTS", "error", error=str(e))
        raise HTTPException(status_code=500, detail=f"TTS error: {e}")


async def _process_task(task_id: str, req: AskRequest, request: Request):
    """Background worker for async ask processing."""
    try:
        result = await ask(req, request)
        audio = await _generate_tts(result.response)
        _tasks[task_id].update({"status": "done", "audio": audio})
    except Exception as e:
        _tasks[task_id].update({"status": "error", "error": str(e)})


@app.post("/ask/start")
async def ask_start(req: AskRequest, request: Request):
    """Start processing a request asynchronously. Returns a task_id to poll."""
    verify_auth(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty input")

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "processing", "created": time.time()}
    asyncio.create_task(_process_task(task_id, req, request))
    return {"task_id": task_id}


async def _process_file_task(task_id: str, file_bytes: bytes, filename: str,
                             mime_type: str | None, caption: str,
                             saved_path: str = ""):
    """Background worker for async file processing."""
    try:
        start = time.time()

        # Build file content blocks
        file_blocks = build_file_content(file_bytes, filename, mime_type)
        user_text = caption if caption else f"The user sent a file: {filename}"
        if saved_path:
            user_text += f"\n(File saved to {saved_path} for future reference)"

        # Note: audio is generated automatically by this pipeline — tell Claude
        # not to push audio separately, which would cause a double response
        user_text += "\n(Audio response is generated automatically — do NOT use push_audio.py for this request.)"

        # Build context — same unified context as voice/SMS, plus is_image flag
        # so health/nutrition context is always available for label photos
        is_image = mime_type and mime_type.startswith("image/")
        extra_context = await build_request_context(
            caption or filename, is_image=is_image
        )

        response = await ask_claude(user_text, extra_context, file_blocks)
        delivery_meta = {}
        response = process_actions(response, metadata=delivery_meta)
        delivery = delivery_meta.get("delivery", "voice")

        duration = time.time() - start
        log_request(f"[file:{filename}] {user_text}", "ok",
                    response=response, duration=duration)

        # Handle delivery routing
        if delivery == "sms":
            try:
                sms.send_to_owner(response)
            except Exception as se:
                log.error("SMS delivery from file request failed: %s", se)
            # No voice output — user explicitly requested SMS
            _tasks[task_id].update({"status": "done", "audio": b"", "delivery": "sms"})
            return

        audio = await _generate_tts(response)
        _tasks[task_id].update({"status": "done", "audio": audio})
    except Exception as e:
        log_request(f"[file:{filename}] {caption}", "error", error=str(e))
        _tasks[task_id].update({"status": "error", "error": str(e)})


@app.post("/ask/file")
async def ask_file(request: Request):
    """Accept a file (image, PDF, text, etc.) with optional caption.

    Supports two formats:
    1. Multipart form data: file field + optional text field
    2. Raw file body with query params: ?filename=photo.jpg&text=caption

    Returns a task_id for async polling, same as /ask/start.
    Poll /ask/status/{task_id} then /ask/result/{task_id} for the audio response.
    """
    verify_auth(request)

    content_type = request.headers.get("content-type", "")

    if "multipart" in content_type:
        # Multipart form data (e.g., from curl or web clients)
        form = await request.form()
        upload = form.get("file")
        if not upload:
            raise HTTPException(status_code=400, detail="No file field in form")
        file_bytes = await upload.read()
        filename = upload.filename or "unknown"
        mime_type = upload.content_type
        text = form.get("text", "")
    else:
        # Raw file body with query params (e.g., from Tasker HTTP Request)
        file_bytes = await request.body()
        filename = request.query_params.get("filename", "unknown")
        mime_type = content_type if content_type and content_type != "application/octet-stream" else None
        text = request.query_params.get("text", "")

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    # Clean up filename — extract basename if a full path was sent
    filename = Path(filename).name

    # Save the file to data/inbox/ with timestamp prefix
    inbox = config.DATA_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_path = inbox / f"{ts}_{filename}"
    saved_path.write_bytes(file_bytes)

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "processing", "created": time.time()}
    asyncio.create_task(
        _process_file_task(task_id, file_bytes, filename, mime_type, text,
                           str(saved_path))
    )
    return {"task_id": task_id}


@app.get("/ask/result/{task_id}")
async def ask_result(task_id: str, request: Request):
    """Poll for async task result. Returns 202 if processing, 200 with audio if done."""
    verify_auth(request)

    # Clean up expired tasks (older than 2 hours — allows hour-long tasks like image gen)
    now = time.time()
    expired = [k for k, v in _tasks.items() if now - v.get("created", now) > 7200]
    for k in expired:
        del _tasks[k]

    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Unknown task")

    if task["status"] == "processing":
        return JSONResponse(status_code=202, content={"status": "processing"})
    elif task["status"] == "error":
        error = task.get("error", "Unknown error")
        del _tasks[task_id]
        raise HTTPException(status_code=500, detail=error)
    else:
        audio = task["audio"]
        del _tasks[task_id]
        return Response(content=audio, media_type="audio/wav")


@app.get("/ask/status/{task_id}")
async def ask_status(task_id: str, request: Request):
    """Lightweight status check — returns JSON only, no audio body.

    Used by Tasker JavaScriptlet polling (can't handle binary).
    The actual audio is fetched separately via /ask/result/{task_id}.
    """
    verify_auth(request)
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Unknown task")

    if task["status"] == "processing":
        content = {"status": "processing"}
        if task.get("transcript"):
            content["transcript"] = task["transcript"]
        return JSONResponse(status_code=202, content=content)
    elif task["status"] == "error":
        return JSONResponse(status_code=500, content={"status": "error", "error": task.get("error", "Unknown")})
    else:
        content = {"status": "done"}
        if task.get("delivery"):
            content["delivery"] = task["delivery"]
        return JSONResponse(status_code=200, content=content)


# --- Whisper STT Endpoints ---

@app.post("/stt")
async def stt(request: Request):
    """Transcribe audio to text via Whisper. Pure STT — no Claude, no TTS."""
    if not getattr(config, 'ENABLE_WHISPER', False):
        raise HTTPException(status_code=503, detail="Whisper STT not enabled on this host")
    verify_auth(request)

    content_type = request.headers.get("content-type", "")
    if "multipart" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not upload:
            raise HTTPException(status_code=400, detail="No file field in form")
        audio_bytes = await upload.read()
    else:
        audio_bytes = await request.body()

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio")

    import whisper_engine
    engine = whisper_engine.get_engine()
    result = await asyncio.to_thread(engine.transcribe_bytes, audio_bytes)

    log_request(f"[stt] ({result.duration:.1f}s audio)", "ok",
                response=result.text, duration=result.processing_time)

    return {
        "text": result.text,
        "segments": result.segments,
        "language": result.language,
        "language_probability": result.language_probability,
        "duration": result.duration,
        "processing_time": result.processing_time,
    }


async def _process_voice_task(task_id: str, audio_bytes: bytes):
    """Background worker: Whisper STT → Claude → Kokoro TTS."""
    try:
        start = time.time()

        # Step 1: Transcribe audio
        import whisper_engine
        engine = whisper_engine.get_engine()
        transcript = await asyncio.to_thread(engine.transcribe_bytes, audio_bytes)

        if not transcript.text.strip():
            _tasks[task_id].update({"status": "error", "error": "No speech detected in audio"})
            return

        user_text = transcript.text.strip()

        # Make transcript available to /ask/status immediately (before Claude runs)
        _tasks[task_id]["transcript"] = user_text

        # Step 2: Build context and query Claude (same pipeline as /ask)
        extra_context = await _get_context_for_text(user_text)
        response = await ask_claude(user_text, extra_context)
        delivery_meta = {}
        response = process_actions(response, metadata=delivery_meta)
        delivery = delivery_meta.get("delivery", "voice")

        # Step 3: Handle delivery routing
        if delivery == "sms":
            # User asked for text delivery — send SMS, no voice output
            try:
                sms.send_to_owner(response)
            except Exception as se:
                log.error("SMS delivery from voice request failed: %s", se)

            duration = time.time() - start
            log_request(f"[voice] {user_text}", "ok", response=response, duration=duration)
            _tasks[task_id].update({
                "status": "done",
                "audio": b"",
                "transcript": user_text,
                "delivery": "sms",
            })
            return

        # Step 4: Generate TTS audio
        audio = await _generate_tts(response)

        duration = time.time() - start
        log_request(f"[voice] {user_text}", "ok", response=response, duration=duration)

        _tasks[task_id].update({
            "status": "done",
            "audio": audio,
            "transcript": user_text,
        })
    except Exception as e:
        log_request("[voice]", "error", error=str(e))
        _tasks[task_id].update({"status": "error", "error": str(e)})


@app.post("/ask/voice")
async def ask_voice(request: Request):
    """Audio in, audio out. Whisper STT → Claude → Kokoro TTS.

    Returns a task_id for async polling — same flow as /ask/start.
    Poll /ask/status/{task_id} (includes transcript when STT completes),
    then /ask/result/{task_id} for the audio response.
    """
    if not getattr(config, 'ENABLE_WHISPER', False):
        raise HTTPException(status_code=503, detail="Whisper STT not enabled on this host")
    verify_auth(request)

    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio")

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "processing", "created": time.time()}
    asyncio.create_task(_process_voice_task(task_id, audio_bytes))
    return {"task_id": task_id}


@app.websocket("/ws/stt")
async def ws_stt(websocket: WebSocket):
    """Real-time streaming transcription via WebSocket.

    Protocol:
      Client sends JSON config: {"type": "config", "sample_rate": 16000, "encoding": "pcm_s16le"}
      Client streams binary PCM audio chunks
      Server sends JSON transcripts: {"type": "transcript", "text": "...", "start": 0.0, "end": 2.5}
      Client sends JSON stop: {"type": "stop"}
    """
    if not getattr(config, 'ENABLE_WHISPER', False):
        await websocket.close(code=1013, reason="Whisper STT not enabled")
        return

    # Auth check before accept
    auth = websocket.headers.get("authorization", "")
    if auth != f"Bearer {config.AUTH_TOKEN}":
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()

    import numpy as np
    import whisper_engine

    engine = whisper_engine.get_engine()
    vad = whisper_engine.EnergyVAD()
    sample_rate = 16000
    audio_offset = 0.0

    try:
        while True:
            message = await asyncio.wait_for(
                websocket.receive(), timeout=30.0
            )

            if "text" in message:
                data = json.loads(message["text"])
                msg_type = data.get("type")

                if msg_type == "config":
                    sample_rate = data.get("sample_rate", 16000)
                    vad = whisper_engine.EnergyVAD(sample_rate=sample_rate)
                    await websocket.send_json({"type": "ready"})

                elif msg_type == "stop":
                    # Flush any remaining speech
                    remaining = vad.flush()
                    if remaining is not None:
                        result = await asyncio.to_thread(
                            engine.transcribe_numpy, remaining, sample_rate
                        )
                        if result.text.strip():
                            await websocket.send_json({
                                "type": "transcript",
                                "text": result.text,
                                "start": round(audio_offset, 2),
                                "end": round(audio_offset + result.duration, 2),
                            })
                    await websocket.close(code=1000)
                    return

            elif "bytes" in message:
                raw = message["bytes"]

                # Convert to float32 numpy
                pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                # Resample to 16kHz if needed
                if sample_rate != 16000:
                    pcm = whisper_engine.resample(pcm, sample_rate, 16000)

                # Feed to VAD — returns utterance array when speech ends
                utterance = vad.process_chunk(pcm)
                if utterance is not None:
                    result = await asyncio.to_thread(
                        engine.transcribe_numpy, utterance, 16000
                    )
                    if result.text.strip():
                        await websocket.send_json({
                            "type": "transcript",
                            "text": result.text,
                            "start": round(audio_offset, 2),
                            "end": round(audio_offset + result.duration, 2),
                        })
                        log_request(f"[ws-stt] {result.text}", "ok",
                                    duration=result.processing_time)
                        audio_offset += result.duration

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        try:
            await websocket.close(code=1001, reason="Timeout — no data received")
        except Exception:
            pass
    except Exception as e:
        log.exception("WebSocket STT error")
        try:
            await websocket.close(code=1011, reason=str(e)[:120])
        except Exception:
            pass


# --- SMS/MMS Webhook ---

async def _process_sms(from_number: str, body: str, media_urls: list[tuple[str, str]]):
    """Background worker for incoming SMS/MMS processing."""
    try:
        start = time.time()
        file_blocks = []

        # Download and process any MMS media attachments
        for media_url, content_type in media_urls:
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get(media_url, follow_redirects=True,
                                            auth=(config.TWILIO_ACCOUNT_SID,
                                                  config.TWILIO_AUTH_TOKEN))
                    if resp.status_code == 200:
                        # Extract filename from URL or use a default
                        filename = media_url.split("/")[-1] or "attachment"
                        if "." not in filename:
                            ext = mimetypes.guess_extension(content_type) or ""
                            filename += ext

                        # Save to inbox
                        inbox = config.DATA_DIR / "inbox"
                        inbox.mkdir(parents=True, exist_ok=True)
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        saved_path = inbox / f"{ts}_sms_{filename}"
                        saved_path.write_bytes(resp.content)

                        file_blocks.extend(
                            build_file_content(resp.content, filename, content_type)
                        )
            except Exception as e:
                log.error("Failed to download MMS media: %s", e)

        # Build context — same unified context as voice/file, plus SMS channel note
        user_text = body if body else "The user sent a file via SMS."
        has_media = bool(file_blocks)

        extra_context = await _get_context_for_text(
            user_text, is_image=has_media
        )

        # SMS channel note — affects RESPONSE FORMAT only, not available context
        sms_note = f"This message arrived via SMS from {from_number}. Respond concisely — SMS has character limits. Do not use markdown or special formatting."
        if extra_context:
            extra_context = sms_note + "\n" + extra_context
        else:
            extra_context = sms_note

        response = await ask_claude(user_text, extra_context,
                                    file_blocks if file_blocks else None)
        delivery_meta = {}
        response = process_actions(response, metadata=delivery_meta)
        delivery = delivery_meta.get("delivery", "sms")

        if delivery == "voice":
            # User requested voice delivery — generate TTS and push audio
            try:
                audio = await _generate_tts(response)
                wav_path = config.DATA_DIR / "sms_voice_response.wav"
                wav_path.write_bytes(audio)
                import push_audio
                push_audio.push_audio(str(wav_path))
                log.info("Voice delivery via SMS request from %s", from_number)
            except Exception as ve:
                log.error("Voice delivery failed, falling back to SMS: %s", ve)
                if response.strip():
                    sms.send_sms(from_number, response[:1597] + "..." if len(response) > 1600 else response)
        else:
            # Default SMS delivery
            if response.strip():
                if len(response) > 1600:
                    response = response[:1597] + "..."
                sms.send_sms(from_number, response)

        duration = time.time() - start
        log_request(f"[sms:{from_number}] {user_text}", "ok",
                    response=response, duration=duration)

        # Save full SMS conversation to dedicated log
        try:
            media_list = [url for url, _ in media_urls] if media_urls else []
            with db.get_conn() as conn:
                conn.execute(
                    """INSERT INTO sms_log
                       (from_number, to_number, inbound, media, response, duration_s)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (from_number, config.TWILIO_PHONE_NUMBER, body,
                     media_list, response, round(duration, 2)),
                )
        except Exception as e:
            log.error("Failed to log SMS conversation: %s", e)

    except Exception as e:
        log.exception("SMS processing error")
        log_request(f"[sms:{from_number}] {body}", "error", error=str(e))
        try:
            sms.send_sms(from_number, "Sorry, something went wrong processing your message.")
        except Exception:
            pass


class NudgeRequest(BaseModel):
    triggers: list[str]
    context: str = ""


@app.post("/nudge")
async def nudge(req: NudgeRequest, request: Request):
    """System-initiated nudge — tick.py calls this when conditions trigger.

    Claude composes a natural, consolidated SMS from the trigger list.
    Returns the composed message text (tick.py handles delivery).
    """
    verify_auth(request)

    prompt = (
        "The following conditions have been detected and the user should be notified. "
        "Compose a single brief, natural SMS message covering all of them. "
        "Be warm and supportive, not nagging. Keep it under 300 characters. "
        "Do NOT use markdown or special formatting. Do NOT add any ACTION blocks.\n\n"
        "Triggers:\n" + "\n".join(f"- {t}" for t in req.triggers)
    )
    extra_context = req.context if req.context else ""

    response = await ask_claude(prompt, extra_context)
    # Strip any accidental ACTION blocks
    response = re.sub(r'<!--ACTION::.*?-->', '', response, flags=re.DOTALL).strip()

    return {"message": response}


@app.post("/sms")
async def webhook_sms(request: Request):
    """Twilio SMS/MMS webhook — receives incoming messages and responds via SMS."""
    form = await request.form()
    params = dict(form)

    # Validate the request is from Twilio
    # Use the configured public webhook URL (not request.url which is the local proxy address)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = config.TWILIO_WEBHOOK_URL
    if not sms.validate_request(url, params, signature):
        log.warning("Invalid Twilio signature on SMS webhook")
        raise HTTPException(status_code=403, detail="Invalid signature")

    from_number = params.get("From", "")
    body = params.get("Body", "").strip()
    num_media = int(params.get("NumMedia", "0"))

    # Collect media URLs and types
    media_urls = []
    for i in range(num_media):
        media_url = params.get(f"MediaUrl{i}", "")
        content_type = params.get(f"MediaContentType{i}", "")
        if media_url:
            media_urls.append((media_url, content_type))

    # Handle STOP/HELP keywords (required by A2P compliance)
    if body.upper() == "STOP":
        return Response(content="<Response></Response>", media_type="application/xml")
    if body.upper() == "HELP":
        help_text = ("ARIA — personal AI assistant. "
                     "Text any message to interact. "
                     "Reply STOP to unsubscribe.")
        return Response(
            content=f'<Response><Message>{help_text}</Message></Response>',
            media_type="application/xml",
        )

    # Only process messages from the owner (STOP/HELP remain open for A2P compliance)
    if from_number != config.OWNER_PHONE_NUMBER:
        log.warning("SMS from unknown sender %s ignored", from_number)
        return Response(content="<Response></Response>", media_type="application/xml")

    if not body and not media_urls:
        return Response(content="<Response></Response>", media_type="application/xml")

    # Process asynchronously — return empty TwiML immediately,
    # then send the response as a new outbound message
    asyncio.create_task(_process_sms(from_number, body, media_urls))

    return Response(content="<Response></Response>", media_type="application/xml")


@app.get("/mms_media/{filename}")
async def serve_mms_media(filename: str):
    """Serve staged MMS media files for Twilio to fetch.

    Publicly accessible via Tailscale Funnel so Twilio can download
    the image for MMS delivery. Files auto-clean after serving.
    """
    safe_name = re.sub(r'[^a-zA-Z0-9_.\-]', '', filename)
    path = sms.MMS_OUTBOX / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")

    mime_type, _ = mimetypes.guess_type(str(path))
    content = path.read_bytes()

    # Clean up after Twilio fetches it (delay slightly in case of retries)
    async def _cleanup():
        await asyncio.sleep(60)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    asyncio.create_task(_cleanup())

    return Response(content=content, media_type=mime_type or "application/octet-stream")


@app.get("/snippet/{name}")
async def get_snippet(name: str):
    """Serve a text snippet for easy copy-paste on phone."""
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '', name)
    path = config.BASE_DIR / f"snippets/{safe_name}.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=path.read_text(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
