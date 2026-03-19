"""ARIA FastAPI daemon — core voice assistant backend."""

import asyncio
import base64
import json
import logging
import mimetypes
import os
import time
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

import config
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

app = FastAPI(title="ARIA", version="0.3.2")

# Async task storage: task_id -> {"status": "processing"/"done"/"error", "audio": bytes, "error": str}
_tasks: dict[str, dict] = {}

# Ensure dirs exist
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
    entry = {
        "timestamp": datetime.now().isoformat(),
        "input": text,
        "status": status,
        "response": response[:500] if response else "",
        "error": error,
        "duration_s": round(duration, 2),
    }
    with open(config.REQUEST_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# --- Context Gathering ---

def _get_today_requests() -> list[dict]:
    """Read today's entries from the request log."""
    today = datetime.now().strftime("%Y-%m-%d")
    entries = []
    if not config.REQUEST_LOG.exists():
        return entries
    for line in config.REQUEST_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("timestamp", "").startswith(today):
                entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


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

    # Meals logged today
    meals_today = [m for m in health_store.get_entries(days=1, category="meal")
                   if m.get("date") == today]
    if meals_today:
        parts.append("\nMeals logged today:")
        for m in meals_today:
            parts.append(f"  - {m['description']}")
    else:
        parts.append("\nNo meals logged today.")

    # Diet day counter
    diet_start = date(2026, 3, 17)
    diet_day = (now.date() - diet_start).days + 1
    if diet_day > 0:
        parts.append(f"\nDiet day {diet_day}")

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

    # Diet — day count since diet start (March 17, 2026)
    diet_start = date(2026, 3, 17)
    diet_day = (now.date() - diet_start).days + 1
    if diet_day > 0:
        parts.append(f"\nDiet day {diet_day} (started March 17, 2026)")

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
    return """You are ARIA (Ambient Reasoning & Intelligence Assistant), a personal voice assistant for Adam.
You are warm, natural, and conversational — like a trusted friend who happens to be brilliant. Use contractions, casual phrasing, natural rhythm. No markdown, no bullet points, no code blocks unless asked. Don't end responses with "would you like me to..." or "anything else?"

IMPORTANT: If Adam asks a question, ONLY answer it. Do NOT take action unless explicitly told to. "Can you do X?" gets an answer, not the action. "Do X" gets the action.
Exception: when Adam describes eating something specific ("I had the salmon for lunch"), log it as a meal without asking.

When you're unsure about something, say so. Never guess when you can verify — check the filesystem, run a command, read a file. If you're estimating, say "I think" not "it is."

You can emit multiple ACTION blocks in one response when a request involves several actions.

About Adam:
- Lives at brother's house in Waukesha, WI (temporary). His own house is in Elkhorn (selling it).
- Works second shift (2pm-11pm) at Banker Wire in Mukwonago — currently on disability leave until April 6, 2026.
- Drives a Nissan Xterra.
- Has NAFLD — on a structured diet (started March 17, 2026). Be supportive about compliance, reinforce the streak on milestones, flag deviations when diet reference is in context. Never suggest "moderation" — he does better with cold turkey.
- Timezone: US Central.

Known places: "home" = 3549 Rapids Trail, Waukesha. "my house" = W4708 Pine Ct, Elkhorn. "work" = 123 W Boxhorn Dr, Mukwonago. "doctor" = Mercyhealth Elkhorn, Wisconsin St.

You run on """ + host + """ (Gentoo Linux, OpenRC — NOT systemd). Full console access with passwordless sudo. Run shell commands freely for read-only queries. For anything that MODIFIES the system, describe what you'll do and ask for confirmation first.

Channels: requests arrive via voice (Tasker), file share (AutoShare), or SMS/MMS (Twilio). For voice, respond naturally for speech. For SMS (noted in context), keep responses under 300 chars, no formatting. Images: use push_image.py for voice requests, MMS via sms.send_mms() for SMS conversations.

Tools:
- Image Gen: `python ~/imgen/generate.py "prompt" [--steps N] [--seed N] [--width W] [--height H] [--output path.png]` (12-16 steps quick, 24-30 high quality)
- Upscale: `~/upscale/upscale4k.sh input.png [output.png]`
- Visual: Matplotlib, Graphviz, SVG — output must be PNG for phone
- Push Image: `python ~/aria/push_image.py /path/to/image.png [--caption "..."]`
- Push Audio: `python ~/aria/push_audio.py /path/to/audio.wav` (only when user explicitly requests voice delivery)
- SMS: `python -c "import sms; sms.send_to_owner('text')"` — MMS: `python -c "import sms; sms.send_mms(config.OWNER_PHONE_NUMBER, 'caption', '/path/to/image.png')"`
- Phone images: 540x1212 resolution, no upscale.
- File Input: photos, PDFs, text files arrive as content blocks. For food photos, check against diet reference.
- Location: GPS every 5 min with reverse geocoding. Position and history injected on location keywords.
- Project briefs: markdown in data/projects/. Summarize conversationally. Create/update via shell.

ACTION blocks — place at the END of your response. Use ONLY exact IDs from context (e.g. [id=a3f8b2c1]). Never guess an ID. If you can't find the ID, tell Adam.

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

Legal — SENSITIVE. Never reference unless Adam brings it up:
<!--ACTION::{"action": "log_legal", "date": "YYYY-MM-DD", "entry_type": "development|filing|contact|note|court_date|deadline", "description": "...", "contacts": ["name"]}-->
<!--ACTION::{"action": "delete_legal_entry", "id": "..."}-->

Timers — "minutes" for relative, "time" (HH:MM 24h) for absolute today. Delivery "sms" default, "voice" only if explicitly asked. Priority "urgent" for alarms (bypasses quiet hours 12am-7am). Always compose a natural "message" — this exact text gets delivered by the autonomous tick system:
<!--ACTION::{"action": "set_timer", "label": "...", "minutes": 30, "delivery": "sms", "message": "..."}-->
<!--ACTION::{"action": "set_timer", "label": "...", "time": "14:30", "delivery": "sms", "message": "..."}-->
<!--ACTION::{"action": "cancel_timer", "id": "..."}-->
When setting a timer, confirm the exact fire time and delivery method.

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
        env["CLAUDE_CODE_EFFORT_LEVEL"] = "medium"

        self._proc = await asyncio.create_subprocess_exec(
            config.CLAUDE_CLI,
            "--print",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--model", "sonnet",
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

def process_actions(response_text: str) -> str:
    """Extract and execute ACTION blocks from Claude's response.

    Returns the cleaned response, replacing it with an error message
    if any actions failed so the user isn't told something worked when it didn't.
    """
    import re
    actions = re.findall(r'<!--ACTION::(\{.*?\})-->', response_text)
    failures = []

    for action_json in actions:
        try:
            action = json.loads(action_json)
            act = action.get("action")

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
        except Exception as e:
            failures.append(f"Action failed: {e}")
            log_request("ACTION", "error", error=str(e))

    # Strip action blocks from spoken response
    clean_response = re.sub(r'<!--ACTION::.*?-->', '', response_text).strip()

    if failures:
        log_request("ACTION", "error", error="; ".join(failures))
        clean_response = "Sorry, something went wrong. " + " ".join(failures) + " Please try again."

    return clean_response


# --- Endpoints ---

@app.get("/health")
async def health():
    uptime = time.time() - START_TIME
    return {
        "status": "ok",
        "uptime_s": round(uptime, 1),
        "version": app.version,
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


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request):
    start = time.time()
    verify_auth(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty input")

    try:
        # Detect if this is a morning briefing or evening debrief
        is_briefing = any(
            text.lower().startswith(p)
            for p in ["good morning", "morning brief", "briefing", "start my day"]
        )
        is_debrief = any(
            text.lower().startswith(p)
            for p in ["good night", "end my day", "nightly debrief",
                       "evening debrief", "wrap up my day"]
        )

        extra_context = ""
        if is_briefing:
            extra_context = await gather_briefing_context()
        elif is_debrief:
            extra_context = await gather_debrief_context()
        else:
            ctx_parts = []

            # Check if this is a weather-related query
            weather_keywords = ["weather", "temperature", "forecast", "rain",
                                "snow", "storm", "wind", "cold", "hot", "warm",
                                "outside", "umbrella"]
            if any(kw in text.lower() for kw in weather_keywords):
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

            # Always provide calendar/reminder context
            today = datetime.now().strftime("%Y-%m-%d")
            week_end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

            # Check if this is a calendar query for a broader range
            calendar_keywords = ["calendar", "schedule", "week", "appointment",
                                 "event", "plan", "busy"]
            if any(kw in text.lower() for kw in calendar_keywords):
                events = calendar_store.get_events(start=today, end=week_end)
            else:
                events = calendar_store.get_events(start=today, end=today)

            reminders = calendar_store.get_reminders()
            if events:
                ctx_parts.append("Events: " + "; ".join(
                    f"[id={e['id']}] {e['date']} {e['title']}" + (f" at {e['time']}" if e.get('time') else "")
                    for e in events
                ))
            if reminders:
                ctx_parts.append("Active reminders: " + "; ".join(
                    f"[id={r['id']}] {r['text']}" + (f" (due {r['due']})" if r.get('due') else "")
                    for r in reminders
                ))

            # Vehicle maintenance context
            vehicle_keywords = ["xterra", "vehicle", "car", "truck", "oil",
                                "maintenance", "mileage", "tire", "brake"]
            if any(kw in text.lower() for kw in vehicle_keywords):
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

            # Health context
            health_keywords = ["health", "body", "pain", "sleep", "slept",
                               "exercise", "symptom", "headache", "back",
                               "sore", "body log", "medication"]
            if any(kw in text.lower() for kw in health_keywords):
                h_entries = health_store.get_entries(days=14)
                if h_entries:
                    ctx_parts.append("Health log (last 14 days): " + "; ".join(
                        f"[id={h['id']}] {h['date']} {h['category']}: {h['description']}"
                        + (f" (severity {h['severity']}/10)" if h.get("severity") else "")
                        + (f" ({h['sleep_hours']}h sleep)" if h.get("sleep_hours") else "")
                        for h in h_entries
                    ))
                patterns = health_store.get_patterns(days=7)
                if patterns:
                    ctx_parts.append("Health patterns: " + "; ".join(patterns))

            # Nutrition / diet context — inject diet reference
            nutrition_keywords = ["diet", "food", "eat", "ate", "meal",
                                  "lunch", "dinner", "breakfast", "snack",
                                  "smoothie", "nutrition", "calories",
                                  "factor", "nafld", "liver"]
            if any(kw in text.lower() for kw in nutrition_keywords):
                diet_ref = config.DATA_DIR / "diet_reference.md"
                if diet_ref.exists():
                    ctx_parts.append("Diet reference:\n" + diet_ref.read_text())
                # Also include recent meal logs
                meal_entries = health_store.get_entries(days=7, category="meal")
                if meal_entries:
                    ctx_parts.append("Recent meals logged: " + "; ".join(
                        f"[id={m['id']}] {m['date']}: {m['description']}"
                        for m in meal_entries
                    ))

            # Project status briefs
            project_keywords = ["project update", "project status", "project brief",
                                "status of", "update on"]
            if any(kw in text.lower() for kw in project_keywords):
                available = projects.list_projects()
                if available:
                    # Try to find a specific project match
                    match = projects.find_project(text)
                    if match:
                        name, contents = match
                        ctx_parts.append(f"Project brief for '{name}':\n{contents}")
                    else:
                        ctx_parts.append("Available project briefs: " + ", ".join(available))
                else:
                    ctx_parts.append("No project briefs found in data/projects/. "
                                     "Create one by writing a markdown file there.")

            # Location context
            # Timer context
            timer_keywords = ["timer", "alarm", "remind me in", "tell me when",
                              "set a timer", "cancel timer", "wake me",
                              "how long", "minutes", "countdown"]
            if any(kw in text.lower() for kw in timer_keywords):
                active_timers = timer_store.get_active()
                if active_timers:
                    ctx_parts.append("Active timers: " + "; ".join(
                        f"[id={t['id']}] {t['label']} — fires at {t['fire_at'][11:16]}"
                        f" ({t['delivery']})"
                        for t in active_timers
                    ))

            location_keywords = ["where am i", "where i am", "location",
                                 "how far", "near me", "close to",
                                 "my location", "where are you"]
            if any(kw in text.lower() for kw in location_keywords):
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
                # Include recent movement if they're asking about location
                history = location_store.get_history(hours=4)
                if len(history) > 1:
                    def _loc_label(h):
                        name = h.get("location")
                        if name:
                            return name
                        return f"{h['lat']:.4f},{h['lon']:.4f}"

                    ctx_parts.append("Recent movement (last 4 hours): " + "; ".join(
                        f"{h['timestamp'][11:16]} → {_loc_label(h)}"
                        for h in history[-12:]
                    ))

            # Legal context
            legal_keywords = ["case", "legal", "court", "lawyer", "attorney",
                              "walworth", "filing", "case update"]
            if any(kw in text.lower() for kw in legal_keywords):
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

            if ctx_parts:
                extra_context = "\n".join(ctx_parts)

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


@app.post("/ask/audio")
async def ask_audio(req: AskRequest, request: Request):
    """Same as /ask but returns WAV audio via Kokoro TTS."""
    result = await ask(req, request)
    text = result.response

    try:
        import io
        import soundfile as sf

        kokoro = _get_kokoro()
        samples, sample_rate = kokoro.create(
            text, voice=config.KOKORO_VOICE, speed=1.0, lang="en-us"
        )
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV")
        buf.seek(0)
        return Response(content=buf.read(), media_type="audio/wav")
    except Exception as e:
        log_request("TTS", "error", error=str(e))
        raise HTTPException(status_code=500, detail=f"TTS error: {e}")


async def _process_task(task_id: str, req: AskRequest, request: Request):
    """Background worker for async ask processing."""
    try:
        result = await ask(req, request)
        text = result.response

        import io
        import soundfile as sf

        kokoro = _get_kokoro()
        samples, sample_rate = kokoro.create(
            text, voice=config.KOKORO_VOICE, speed=1.0, lang="en-us"
        )
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV")
        buf.seek(0)
        _tasks[task_id] = {"status": "done", "audio": buf.read()}
    except Exception as e:
        _tasks[task_id] = {"status": "error", "error": str(e)}


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

        # Build context — inject diet reference for food-related queries
        ctx_parts = []
        text_lower = user_text.lower()

        nutrition_keywords = ["diet", "food", "eat", "ate", "meal",
                              "lunch", "dinner", "breakfast", "snack",
                              "nutrition", "calories", "factor", "nafld",
                              "liver", "healthy", "ingredient"]
        if any(kw in text_lower for kw in nutrition_keywords):
            diet_ref = config.DATA_DIR / "diet_reference.md"
            if diet_ref.exists():
                ctx_parts.append("Diet reference:\n" + diet_ref.read_text())

        extra_context = "\n".join(ctx_parts) if ctx_parts else ""

        response = await ask_claude(user_text, extra_context, file_blocks)
        response = process_actions(response)

        duration = time.time() - start
        log_request(f"[file:{filename}] {user_text}", "ok",
                    response=response, duration=duration)

        import io
        import soundfile as sf

        kokoro = _get_kokoro()
        samples, sample_rate = kokoro.create(
            response, voice=config.KOKORO_VOICE, speed=1.0, lang="en-us"
        )
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV")
        buf.seek(0)
        _tasks[task_id] = {"status": "done", "audio": buf.read()}
    except Exception as e:
        log_request(f"[file:{filename}] {caption}", "error", error=str(e))
        _tasks[task_id] = {"status": "error", "error": str(e)}


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
        return JSONResponse(status_code=202, content={"status": "processing"})
    elif task["status"] == "error":
        return JSONResponse(status_code=500, content={"status": "error", "error": task.get("error", "Unknown")})
    else:
        return JSONResponse(status_code=200, content={"status": "done"})


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

        # Build context — same keyword-triggered injection as /ask
        user_text = body if body else "The user sent a file via SMS."
        ctx_parts = []
        text_lower = user_text.lower()

        # Nutrition context
        nutrition_keywords = ["diet", "food", "eat", "ate", "meal",
                              "lunch", "dinner", "breakfast", "snack",
                              "nutrition", "calories", "factor", "nafld",
                              "liver", "healthy", "ingredient"]
        if any(kw in text_lower for kw in nutrition_keywords):
            diet_ref = config.DATA_DIR / "diet_reference.md"
            if diet_ref.exists():
                ctx_parts.append("Diet reference:\n" + diet_ref.read_text())

        # Always include calendar/reminder context
        today = datetime.now().strftime("%Y-%m-%d")
        events = calendar_store.get_events(start=today, end=today)
        reminders = calendar_store.get_reminders()
        if events:
            ctx_parts.append("Today's events: " + "; ".join(
                f"[id={e['id']}] {e['title']}" + (f" at {e['time']}" if e.get('time') else "")
                for e in events
            ))
        if reminders:
            ctx_parts.append("Active reminders: " + "; ".join(
                f"[id={r['id']}] {r['text']}" + (f" (due {r['due']})" if r.get('due') else "")
                for r in reminders
            ))

        extra_context = "\n".join(ctx_parts) if ctx_parts else ""

        # Add note about SMS channel
        sms_note = f"This message arrived via SMS from {from_number}. Respond concisely — SMS has character limits. Do not use markdown or special formatting."
        if extra_context:
            extra_context = sms_note + "\n" + extra_context
        else:
            extra_context = sms_note

        response = await ask_claude(user_text, extra_context,
                                    file_blocks if file_blocks else None)
        response = process_actions(response)

        # Split long responses into multiple SMS messages (160 char limit per segment,
        # but Twilio handles concatenation up to 1600 chars)
        if len(response) > 1600:
            response = response[:1597] + "..."

        sms.send_sms(from_number, response)

        duration = time.time() - start
        log_request(f"[sms:{from_number}] {user_text}", "ok",
                    response=response, duration=duration)

        # Save full SMS conversation to dedicated log
        sms_log = config.DATA_DIR / "sms_log.jsonl"
        sms_entry = {
            "timestamp": datetime.now().isoformat(),
            "from": from_number,
            "to": config.TWILIO_PHONE_NUMBER,
            "inbound": body,
            "media": [url for url, _ in media_urls] if media_urls else [],
            "response": response,
            "duration_s": round(duration, 2),
        }
        with open(sms_log, "a") as f:
            f.write(json.dumps(sms_entry) + "\n")

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
    import re
    response = re.sub(r'<!--ACTION::.*?-->', '', response).strip()

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
    import re
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
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '', name)
    path = config.BASE_DIR / f"snippets/{safe_name}.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=path.read_text(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
