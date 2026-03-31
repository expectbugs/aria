"""ARIA context building — tiered data injection for requests.

Tier 1 (always): datetime, timers, reminders, location, battery, exercise state
Tier 2 (keyword): weather, calendar expansion, health/nutrition, vehicle, legal, projects
"""

import hashlib
import logging
import re
from datetime import datetime, date, timedelta

log = logging.getLogger("aria")


def _dedup_tag(key: str, content: str) -> str:
    """Wrap a context section with dedup tags including a content hash.

    The session pool scans for these tags and skips re-injecting unchanged
    sections within the same persistent session. Other callers (API fallback,
    briefing/debrief) see the tags as harmless text.
    """
    h = hashlib.md5(content.encode()).hexdigest()[:10]
    return f"[dedup:{key}:{h}]\n{content}\n[/dedup:{key}]"


def _get_diet_day() -> int | None:
    """Get current diet day number, or None if diet tracking not configured."""
    diet_start_str = getattr(config, "DIET_START_DATE", "")
    if not diet_start_str:
        return None
    try:
        diet_start = date.fromisoformat(diet_start_str)
        day = (datetime.now().date() - diet_start).days + 1
        return day if day > 0 else None
    except ValueError:
        return None


# --- Keyword matching infrastructure ---

def _match_keywords(text: str, substrings: list[str],
                    pattern: re.Pattern | None = None) -> bool:
    """Match via substring check + optional word-boundary regex."""
    if any(kw in text for kw in substrings):
        return True
    return bool(pattern and pattern.search(text))


# Compiled patterns for each category — word-boundary matching for ambiguous single words
_WEATHER_SUBSTRINGS = ["weather", "temperature", "forecast", "umbrella",
                       "jacket", "coat", "humid", "degrees", "sunny", "cloudy"]
_WEATHER_REGEX = re.compile(r'\b(rain|snow|storm|wind|freeze)\b', re.IGNORECASE)

_CALENDAR_SUBSTRINGS = ["calendar", "schedule", "appointment",
                        "my week", "this week", "next week"]
_CALENDAR_REGEX = re.compile(
    r'\b(tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
    re.IGNORECASE)

_HEALTH_SUBSTRINGS = [
    "health", "body log", "heart rate", "hrv", "spo2", "oxygen",
    "fitbit", "vo2", "cardio", "workout", "diet", "nutrition",
    "calories", "nafld", "liver", "protein", "fiber", "sodium",
    "vitamin", "omega", "label", "deficit", "surplus", "smoothie",
    "magnesium", "choline", "zinc", "selenium", "micronutrient",
    "supplement",
    # Broader triggers (v0.8.6)
    "what did i eat", "what did i have", "how much did i",
    "how many calories", "any meals", "did i log", "track my",
    "blood pressure", "glucose", "a1c",
]
_HEALTH_REGEX = re.compile(
    r'\b(body|pain|sleep|slept|exercise|symptom|headache|sore|medication|'
    r'steps|resting|food|eat|ate|meals?|lunch|dinner|breakfast|snack|'
    r'carbs?|weight)\b',
    re.IGNORECASE)

# Cross-domain triggers: inject health + calendar + email together
_MULTI_DOMAIN_SUBSTRINGS = [
    "how am i doing", "am i on track", "how's my day",
    "what's going on", "status update", "catch me up",
]

_VEHICLE_SUBSTRINGS = ["xterra", "vehicle", "maintenance", "mileage",
                       "oil change", "tire pressure"]
_VEHICLE_REGEX = re.compile(r'\b(truck|brake|tire)\b', re.IGNORECASE)

_LEGAL_SUBSTRINGS = ["legal", "court", "lawyer", "attorney", "walworth",
                     "case update", "legal case", "court case",
                     "court date", "lawsuit"]
_LEGAL_REGEX = re.compile(r'\b(filing)\b', re.IGNORECASE)

_EMAIL_SUBSTRINGS = ["email", "inbox", "mail", "gmail", "message from",
                     "reply to", "respond to", "unread email"]
_EMAIL_REGEX = re.compile(r'\b(email|inbox|mail|gmail)\b', re.IGNORECASE)

_AMBIENT_SUBSTRINGS = [
    "remember", "recall", "who said", "what did i say", "what did i tell",
    "what did he say", "what did she say", "conversation about",
    "commitment", "promise", "did i promise", "what promises",
    "who is", "who's", "person", "people i", "mentioned",
    "what was that", "what were we talking", "what did we discuss",
    "summarize last", "summarize yesterday",
]
_AMBIENT_REGEX = re.compile(
    r'\b(recall|commitment|promise|mentioned|conversation)\b', re.IGNORECASE)

import config
import db
import calendar_store
import vehicle_store
import health_store
import legal_store
import location_store
import timer_store
import projects
import weather
import news
import fitbit_store
import nutrition_store
import redis_client
import monitors


def gather_always_context() -> str:
    """Tier 1 context — always injected on every call regardless of query.

    Returns a compact string with data ARIA should always have:
    datetime, active timers, active reminders, location/battery, exercise state.
    """
    now = datetime.now()
    parts = [f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"]

    # Active timers
    active_timers = timer_store.get_active()
    if active_timers:
        parts.append(f"Active timers ({len(active_timers)} total): " + "; ".join(
            f"[id={t['id']}] {t['label']} — fires at {t['fire_at'][11:16]}"
            f" ({t['delivery']})"
            for t in active_timers
        ))

    # Active reminders
    reminders = calendar_store.get_reminders()
    if reminders:
        parts.append(f"Active reminders ({len(reminders)} total): " + "; ".join(
            f"[id={r['id']}] {r['text']}"
            + (f" (due {r['due']})" if r.get('due') else "")
            for r in reminders
        ))

    # Latest location + battery
    loc = location_store.get_latest()
    if loc:
        loc_name = loc.get("location", f"{loc['lat']:.4f}, {loc['lon']:.4f}")
        parts.append(
            f"Location: {loc_name} (as of {loc['timestamp'][11:16]})"
        )
        if loc.get("battery_pct") is not None:
            parts.append(f"Phone battery: {loc['battery_pct']}%")

    # Exercise mode
    exercise = fitbit_store.get_exercise_state()
    if exercise:
        coaching = fitbit_store.get_exercise_coaching_context(state=exercise)
        if coaching:
            parts.append(coaching)

    # Active background tasks (from Redis — swarm task status)
    active_tasks = redis_client.get_active_tasks()
    task_status = redis_client.format_task_status(active_tasks)
    if task_status:
        parts.append(task_status)

    # Undelivered monitor findings (Tier 1 — ARIA should see these)
    try:
        findings = monitors.get_undelivered(min_urgency="low")
        if findings:
            parts.append("Monitor alerts: " + "; ".join(
                f"[{f['urgency']}] {f['summary']}" for f in findings[:5]
            ))
    except Exception:
        pass  # monitors table may not exist yet during migration

    # Open commitments (Phase 6 — ambient promise tracker)
    try:
        import commitment_store
        open_commitments = commitment_store.get_open(limit=5)
        if open_commitments:
            parts.append("Open commitments: " + "; ".join(
                f"{c['who']} → {c['what']}"
                + (f" (to {c['to_whom']})" if c.get("to_whom") else "")
                + (f" [due {c['due_date']}]" if c.get("due_date") else "")
                for c in open_commitments
            ))
        overdue = commitment_store.get_overdue()
        if overdue:
            parts.append(f"OVERDUE commitments ({len(overdue)}): " + "; ".join(
                f"{c['who']} → {c['what']} (due {c['due_date']})"
                for c in overdue[:3]
            ))
    except Exception:
        pass

    # Ambient capture status (Phase 6)
    try:
        import ambient_store
        today_count = ambient_store.get_today_count()
        if today_count > 0:
            today_dur = ambient_store.get_today_duration()
            dur_min = today_dur / 60
            parts.append(
                f"Ambient capture: {today_count} segments today ({dur_min:.0f} min recorded)"
            )
    except Exception:
        pass

    # Pending destructive actions awaiting confirmation
    try:
        from actions import get_pending_confirmations
        pending = get_pending_confirmations()
        if pending:
            parts.append("PENDING ACTIONS (awaiting your confirmation):")
            for p in pending:
                parts.append(f"  - {p['description']}")
            parts.append(
                "If the user confirms, emit: "
                '<!--ACTION::{"action": "confirm_destructive", '
                '"confirmation_id": "<id>"}-->')
    except Exception:
        pass

    return "\n".join(parts)


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
    text_lower = text.lower().replace("-", " ")  # normalize hyphens for keyword matching
    ctx_parts = []

    # Multi-domain triggers: inject health + calendar + email together
    multi_domain = any(kw in text_lower for kw in _MULTI_DOMAIN_SUBSTRINGS)

    # --- Tier 1: Always-inject ---
    always_ctx = gather_always_context()
    if always_ctx:
        ctx_parts.append(always_ctx)

    # --- Weather ---
    if _match_keywords(text_lower, _WEATHER_SUBSTRINGS, _WEATHER_REGEX):
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
                alert_lines = []
                for a in alerts:
                    alert_lines.append(f"  {a['event']} ({a['severity']}): {a['headline']}")
                    if a.get("description"):
                        alert_lines.append(f"    {a['description']}")
                ctx_parts.append("Alerts:\n" + "\n".join(alert_lines))
        except Exception as e:
            ctx_parts.append(f"Weather data unavailable: {e}")

    # --- Calendar (reminders are in Tier 1) ---
    today = datetime.now().strftime("%Y-%m-%d")
    week_end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    calendar_expanded = _match_keywords(text_lower, _CALENDAR_SUBSTRINGS, _CALENDAR_REGEX) or multi_domain
    if calendar_expanded:
        events = calendar_store.get_events(start=today, end=week_end)
    else:
        events = calendar_store.get_events(start=today, end=today)

    if events:
        scope = "next 7 days" if calendar_expanded else "today only"
        ctx_parts.append(
            f"Events ({scope}, {len(events)} shown — use `query.py calendar` for full range): "
            + "; ".join(
                f"[id={e['id']}] {e['date']} {e['title']}"
                + (f" at {e['time']}" if e.get('time') else "")
                for e in events
            )
        )

    # --- Vehicle ---
    if _match_keywords(text_lower, _VEHICLE_SUBSTRINGS, _VEHICLE_REGEX):
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
    if is_image or multi_domain or _match_keywords(text_lower, _HEALTH_SUBSTRINGS, _HEALTH_REGEX):
        health_ctx = gather_health_context()
        if health_ctx:
            ctx_parts.append(_dedup_tag("health",
                "Health snapshot (today + yesterday — use `query.py health` for older):\n"
                + health_ctx
            ))

        diet_ref = config.DATA_DIR / "diet_reference.md"
        if diet_ref.exists():
            ctx_parts.append(_dedup_tag("diet_ref",
                "Diet reference:\n" + diet_ref.read_text()
            ))

        pantry = config.DATA_DIR / "pantry.md"
        if pantry.exists():
            ctx_parts.append(_dedup_tag("pantry",
                "Pantry (verified nutrition — use these values, do not estimate):\n"
                + pantry.read_text()
            ))

        # Note: 14-day raw health dump removed in v0.4.14 (D4 fix).
        # Today + yesterday are in gather_health_context(). 7-day patterns
        # are computed summaries. Historical queries use tool calls.

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

    # --- Location history (basic location is Tier 1, movement trail is keyword-gated) ---
    location_keywords = ["where am i", "where i am", "location",
                         "how far", "near me", "close to",
                         "my location", "where are you"]
    if any(kw in text_lower for kw in location_keywords):
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
    if _match_keywords(text_lower, _LEGAL_SUBSTRINGS, _LEGAL_REGEX):
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

    # --- Email ---
    if multi_domain or _match_keywords(text_lower, _EMAIL_SUBSTRINGS, _EMAIL_REGEX):
        try:
            import gmail_store
            email_ctx = gmail_store.get_email_context(today)
            if email_ctx:
                ctx_parts.append(
                    "Email (unread important only — use `query.py email --search` for all):\n"
                    + email_ctx
                )
        except Exception as e:
            log.warning("Email context unavailable: %s", e)

    # --- Ambient recall (Phase 6) ---
    # Trigger on recall keywords OR known person names
    ambient_triggered = _match_keywords(text_lower, _AMBIENT_SUBSTRINGS, _AMBIENT_REGEX)
    if not ambient_triggered:
        try:
            import person_store
            known_names = person_store.get_names()
            if any(name.lower() in text_lower for name in known_names):
                ambient_triggered = True
        except Exception:
            pass

    if ambient_triggered:
        try:
            import ambient_store
            import commitment_store as _cs

            # Recent transcript snippets (last 2 hours)
            recent = ambient_store.get_recent(hours=2, limit=10)
            if recent:
                ctx_parts.append(
                    "Recent ambient transcripts (last 2h):\n" + "\n".join(
                        f"  [{t['started_at'][11:16] if isinstance(t.get('started_at'), str) else '?'}] "
                        f"{(t.get('quality_text') or t.get('text', ''))[:150]}"
                        for t in reversed(recent)  # chronological
                    )
                )

            # Qdrant semantic search if query looks like a recall question
            try:
                import qdrant_store
                results = qdrant_store.search(text, limit=3, days=30)
                if results:
                    ctx_parts.append(
                        "Semantic recall (top matches from memory):\n" + "\n".join(
                            f"  [{r.get('timestamp', '?')[:10]}] ({r['category']}) "
                            f"{r['text'][:200]} [score={r['score']}]"
                            for r in results
                        )
                    )
            except Exception:
                pass

            # Person profile if a known name is mentioned
            try:
                import person_store as _ps
                for name in _ps.get_names():
                    if name.lower() in text_lower:
                        profile = _ps.get(name)
                        if profile:
                            ctx_parts.append(
                                f"Person: {profile['name']}"
                                + (f" ({profile['relationship']})" if profile.get("relationship") else "")
                                + (f" at {profile['organization']}" if profile.get("organization") else "")
                                + f" — mentioned {profile['mention_count']} times"
                                + (f", last {profile['last_mentioned'][:10]}" if profile.get("last_mentioned") else "")
                            )
                            break  # only inject first matched person
            except Exception:
                pass

            # Related commitments
            related_commits = _cs.get_recent(days=7, limit=5)
            if related_commits:
                ctx_parts.append(
                    "Recent commitments (7d):\n" + "\n".join(
                        f"  {c['who']} → {c['what']}"
                        + (f" (to {c['to_whom']})" if c.get("to_whom") else "")
                        + f" [{c['status']}]"
                        for c in related_commits
                    )
                )
        except Exception as e:
            log.warning("Ambient context unavailable: %s", e)

    return "\n".join(ctx_parts) if ctx_parts else ""


async def _get_context_for_text(text: str, is_image: bool = False) -> str:
    """Route text to the right context builder.

    Detects morning briefings and evening debriefs, otherwise uses
    keyword-triggered context. Single source of truth — used by /ask,
    /ask/voice, and /sms instead of repeating detection logic.

    Tier 1 (always-inject) data is included on every path:
    - Regular requests: build_request_context() calls gather_always_context()
    - Briefing/debrief: prepended here before the specialized context
    """
    text_lower = text.lower()
    briefing_triggers = ["good morning", "morning brief", "briefing", "start my day"]
    if any(text_lower.startswith(p) for p in briefing_triggers):
        # Explicit repeat requests always get the briefing
        repeat_words = ["again", "repeat", "one more time", "redo"]
        is_repeat = any(w in text_lower for w in repeat_words)
        if is_repeat or not _briefing_delivered_today():
            always = gather_always_context()
            briefing = await gather_briefing_context()
            ctx = always + "\n" + briefing if always else briefing
            log.info("Context: %d chars, path=briefing", len(ctx))
            return ctx
        # Already delivered today — fall through to normal context
    if any(text_lower.startswith(p)
           for p in ["good night", "end my day", "nightly debrief",
                      "evening debrief", "wrap up my day"]):
        always = gather_always_context()
        debrief = await gather_debrief_context()
        ctx = always + "\n" + debrief if always else debrief
        log.info("Context: %d chars, path=debrief", len(ctx))
        return ctx
    ctx = await build_request_context(text, is_image=is_image)
    log.info("Context: %d chars, path=regular", len(ctx))
    return ctx


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

    # Yesterday's summary (compact — totals only, no individual items)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    yesterday_totals = nutrition_store.get_daily_totals(yesterday)
    if yesterday_totals.get("item_count", 0) > 0:
        parts.append(
            f"Yesterday's nutrition: {yesterday_totals['calories']:.0f} cal, "
            f"{yesterday_totals['protein_g']:.0f}g protein, "
            f"{yesterday_totals['dietary_fiber_g']:.0f}g fiber"
        )

    yesterday_net = nutrition_store.get_net_calories(yesterday)
    if yesterday_net["consumed"] > 0 and yesterday_net["burned"] > 0:
        parts.append(
            f"Yesterday's calorie balance: {yesterday_net['consumed']} consumed "
            f"- {yesterday_net['burned']} burned = {yesterday_net['net']} net"
        )

    yesterday_fitbit_parts = []
    yesterday_sleep = fitbit_store.get_sleep_summary(yesterday)
    if yesterday_sleep:
        yesterday_fitbit_parts.append(f"Sleep {yesterday_sleep['duration_hours']}h")
    yesterday_hr = fitbit_store.get_heart_summary(yesterday)
    if yesterday_hr and yesterday_hr.get("resting_hr"):
        yesterday_fitbit_parts.append(f"Resting HR {yesterday_hr['resting_hr']} bpm")
    yesterday_activity = fitbit_store.get_activity_summary(yesterday)
    if yesterday_activity:
        yesterday_fitbit_parts.append(f"{yesterday_activity['steps']:,} steps")
    if yesterday_fitbit_parts:
        parts.append("Yesterday's Fitbit: " + ", ".join(yesterday_fitbit_parts))

    # Health patterns (last 7 days)
    patterns = health_store.get_patterns(days=7)
    if patterns:
        parts.append("Health patterns (7d): " + "; ".join(patterns))

    # Diet day counter
    diet_day = _get_diet_day()
    if diet_day:
        parts.append(f"Diet day {diet_day}")

    # Exercise mode
    exercise = fitbit_store.get_exercise_state()
    if exercise:
        parts.append(fitbit_store.get_exercise_coaching_context(state=exercise))

    if not parts:
        return ""

    return "\n".join(parts)


async def gather_debrief_context() -> str:
    """Gather context for a good-night debrief.

    Note: datetime, reminders, location, and battery are in Tier 1
    (gather_always_context), prepended by _get_context_for_text().
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    parts = []

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
    diet_day = _get_diet_day()
    if diet_day:
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

    # Email activity today
    try:
        import gmail_store
        email_ctx = gmail_store.get_email_context(today)
        if email_ctx:
            parts.append(f"\n{email_ctx}")
    except Exception:
        pass

    # Monitor findings from today
    try:
        today_findings = [f for f in monitors.get_recent(hours=24)
                          if f.get("created_at", "").startswith(today)]
        if today_findings:
            parts.append("\nMonitor findings today:")
            for f in today_findings:
                parts.append(f"  - [{f['urgency']}] {f['domain']}: {f['summary']}")
    except Exception:
        pass

    # Ambient conversations today (Phase 6)
    try:
        import ambient_store
        import commitment_store as _cs
        convs = ambient_store.get_conversations(days=1)
        today_convs = [c for c in convs if c.get("started_at", "").startswith(today)]
        if today_convs:
            total_dur = sum(c.get("duration_s", 0) or 0 for c in today_convs)
            parts.append(f"\nAmbient conversations today ({len(today_convs)} conversations, "
                         f"{total_dur/60:.0f} min):")
            for c in today_convs:
                ts = c.get("started_at", "")
                time_str = ts[11:16] if len(ts) >= 16 else ""
                summary = c.get("summary") or c.get("title") or "(no summary)"
                speakers = ", ".join(c.get("speakers", []))
                parts.append(f"  - [{time_str}] {speakers}: {summary}")

        # Today's commitments
        today_commits = _cs.get_recent(days=1)
        today_commits = [c for c in today_commits
                         if c.get("created_at", "").startswith(today)]
        if today_commits:
            parts.append(f"\nCommitments today ({len(today_commits)}):")
            for c in today_commits:
                parts.append(f"  - {c['who']} → {c['what']}"
                             + (f" (to {c['to_whom']})" if c.get("to_whom") else "")
                             + (f" [due {c['due_date']}]" if c.get("due_date") else ""))

        # Daily summary if already generated
        summary = ambient_store.get_daily_summary(today)
        if summary:
            parts.append(f"\nDaily ambient summary:\n{summary['summary']}")
    except Exception:
        pass

    return "\n".join(parts)


async def gather_briefing_context() -> str:
    """Gather all context data for a morning briefing.

    Note: datetime, reminders, location, and battery are in Tier 1
    (gather_always_context), prepended by _get_context_for_text().
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    week_end = (now + timedelta(days=7)).strftime("%Y-%m-%d")

    parts = []

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
                if a.get("description"):
                    parts.append(f"    Details: {a['description']}")
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

    # News digest
    try:
        digest = await news.get_news_digest(max_per_feed=3)
        if digest:
            parts.append("\nNews headlines:")
            for category, items in digest.items():
                parts.append(f"  {category.title()}:")
                for item in items:
                    summary = f" — {item['summary']}" if item.get("summary") else ""
                    parts.append(f"    - {item['title']}{summary}")
    except Exception as e:
        log.warning("News digest unavailable: %s", e)

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

    # Diet — day count since diet start
    diet_day = _get_diet_day()
    if diet_day:
        parts.append(f"\nDiet day {diet_day}")

    # Fitbit health data
    fitbit_ctx = fitbit_store.get_briefing_context(today)
    if fitbit_ctx:
        parts.append(f"\n{fitbit_ctx}")
    fitbit_trend = fitbit_store.get_trend(days=7)
    if fitbit_trend:
        parts.append(f"\n{fitbit_trend}")

    # Legal — upcoming dates only (don't surface case details unprompted)
    legal_upcoming = legal_store.get_upcoming_dates()
    if legal_upcoming:
        parts.append("\nUpcoming legal dates:")
        for l in legal_upcoming:
            parts.append(f"  - [id={l['id']}] {l['date']}: {l['description']}")

    # Email summary
    try:
        import gmail_store
        email_brief = gmail_store.get_briefing_context()
        if email_brief:
            parts.append(f"\n{email_brief}")
    except Exception:
        pass

    # Monitor findings from last 24h
    try:
        recent_findings = monitors.get_recent(hours=24)
        if recent_findings:
            parts.append("\nMonitor findings (last 24h):")
            for f in recent_findings:
                parts.append(f"  - [{f['urgency']}] {f['domain']}: {f['summary']}")
    except Exception:
        pass

    # Overdue and due-today commitments (Phase 6)
    try:
        import commitment_store as _cs
        overdue = _cs.get_overdue()
        if overdue:
            parts.append(f"\nOVERDUE commitments ({len(overdue)}):")
            for c in overdue:
                parts.append(f"  - {c['who']} → {c['what']} (due {c['due_date']})")
        due_today = _cs.get_due_today()
        if due_today:
            parts.append(f"\nCommitments due today ({len(due_today)}):")
            for c in due_today:
                parts.append(f"  - {c['who']} → {c['what']}"
                             + (f" (to {c['to_whom']})" if c.get("to_whom") else ""))
    except Exception:
        pass

    return "\n".join(parts)
