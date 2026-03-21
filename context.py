"""ARIA context building — keyword-triggered data injection for requests."""

from datetime import datetime, date, timedelta

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
