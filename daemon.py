"""ARIA FastAPI daemon — core voice assistant backend."""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

import config
import calendar_store
import weather
import news

app = FastAPI(title="ARIA", version="0.2.3")

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

    return "\n".join(parts)


def build_system_prompt() -> str:
    """Build the system prompt that defines ARIA's behavior.

    NOTE: This is set once when the persistent Claude process spawns.
    The current date/time is injected per-request in the context instead.
    """
    host = config.HOST_NAME
    return """You are ARIA (Ambient Reasoning & Intelligence Assistant), a personal voice assistant.
You are speaking to your user through voice — keep responses concise, natural, and conversational.
Respond as if speaking aloud. No markdown, no bullet points, no code blocks unless explicitly asked.
Do NOT end your responses with questions like "would you like me to do that?" or "anything else?" — you maintain conversation context, so the user can just tell you if they need more.

IMPORTANT: If the user asks a question without giving a command, ONLY answer the question. Do NOT take any action. For example, "can you generate images?" should get a yes/no answer, NOT an image generation. "What went wrong?" should get a spoken explanation, NOT an immediate fix. Only take action when explicitly told to do something.

You run on a self-hosted stack: FastAPI daemon on """ + host + """ (Gentoo Linux), Claude via CLI for reasoning, and Kokoro TTS (af_heart voice) for speech synthesis. Your user built you. Your primary host is beardos; if running on slappy, you are in failover mode.

You have full console access to the machine you are running on, with passwordless sudo. You can and SHOULD run any shell commands needed to answer questions — check disk space, system specs, network status, service status, file contents, package info, etc. Never say you can't check something or don't have access. If the user asks about the system, USE your tools to look it up and give a real answer.

IMPORTANT: Read-only commands (ls, df, free, top, cat, uname, etc.) are always fine to run without asking. But for anything that MODIFIES the system — deleting files, stopping services, installing/removing packages, changing configs, killing processes, writing files — you MUST describe what you're about to do and ask for explicit confirmation before running it. Never run destructive or state-changing commands without permission.

The current date and time is provided at the start of each message.

You have access to the following tools via function results provided in the context:
- Calendar: view, add, modify, delete events
- Reminders: view, add, complete, delete reminders
- Weather: current conditions, forecast, alerts
- Shell: full command-line access with sudo (use for system queries, file operations, service management, etc.)
- Image Generation: FLUX.2 via ~/imgen/generate.py — run: python ~/imgen/generate.py "prompt" [--steps N] [--seed N] [--width W] [--height H] [--output path.png]
- Image Upscaling: SUPIR 4K upscaler via ~/upscale/upscale4k.sh — run: ~/upscale/upscale4k.sh input.png [output.png] [--steps N] [--sign Q|F]
- Visual Output: Matplotlib (charts/graphs), Graphviz (diagrams/flowcharts), SVG (vector graphics) — write a script, run it, then push the result. ALL output must be PNG format for phone compatibility: use savefig("output.png") for Matplotlib, dot -Tpng for Graphviz, and convert SVG to PNG (e.g. via cairosvg or Inkscape) before pushing.
- Push to Phone: python ~/aria/push_image.py /path/to/image.png [--caption "description"] — displays the image on the phone immediately
- Images intended for the phone should be generated at 540x1212 resolution with no upscale. After generating any image, ALWAYS push it to the phone using push_image.py.
- FLUX.2 step guidance: use fewer steps (12-16) for quick/casual images, more steps (24-30) for high-quality artistic content. Default to fewer steps unless the user asks for high quality.

When the user wants to add a calendar event, extract the title, date (YYYY-MM-DD), and time (HH:MM, 24h).
When the user wants a reminder, extract the text and optional due date.
When the user wants weather, provide it conversationally.

For calendar/reminder modifications, respond with a JSON action block at the END of your response:
<!--ACTION::{"action": "add_event", "title": "...", "date": "YYYY-MM-DD", "time": "HH:MM"}-->
<!--ACTION::{"action": "add_reminder", "text": "...", "due": "YYYY-MM-DD"}-->
<!--ACTION::{"action": "complete_reminder", "id": "..."}-->
<!--ACTION::{"action": "delete_event", "id": "..."}-->
<!--ACTION::{"action": "delete_reminder", "id": "..."}-->

IMPORTANT: Use ONLY the exact IDs provided in the context (e.g. [id=a3f8b2c1]). Never guess or make up an ID.
If you cannot find the ID for something the user wants to modify or delete, tell them you can't find it.

Always confirm what you've done after an action.
If the input is "good morning" or similar, deliver a full morning briefing using the context provided.
If you don't know something, say so briefly."""


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

    async def query(self, user_text: str, extra_context: str = "") -> str:
        """Send a prompt to the persistent Claude process and return the response."""
        async with self._lock:
            await self._ensure_alive()

            # Build prompt with fresh datetime
            now = datetime.now()
            parts = [f"Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"]
            if extra_context:
                parts.append(f"\n[CONTEXT]\n{extra_context}\n[/CONTEXT]")
            parts.append(f"\nUser says: {user_text}")
            prompt = "\n".join(parts)

            # Send user message as NDJSON
            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": prompt},
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


async def ask_claude(user_text: str, extra_context: str = "") -> str:
    """Send a query to Claude via the persistent CLI session."""
    return await _claude_session.query(user_text, extra_context)


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
                )
            elif act == "complete_reminder":
                if not calendar_store.complete_reminder(action["id"]):
                    failures.append(f"Couldn't complete reminder — no reminder found with that ID.")
            elif act == "delete_event":
                if not calendar_store.delete_event(action["id"]):
                    failures.append(f"Couldn't delete event — no event found with that ID.")
            elif act == "delete_reminder":
                if not calendar_store.delete_reminder(action["id"]):
                    failures.append(f"Couldn't delete reminder — no reminder found with that ID.")
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


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request):
    start = time.time()
    verify_auth(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty input")

    try:
        # Detect if this is a morning briefing
        is_briefing = any(
            text.lower().startswith(p)
            for p in ["good morning", "morning brief", "briefing", "start my day"]
        )

        extra_context = ""
        if is_briefing:
            extra_context = await gather_briefing_context()
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
    uvicorn.run(app, host=config.TAILSCALE_IP, port=config.PORT)
