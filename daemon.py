"""ARIA FastAPI daemon — core voice assistant backend."""

import asyncio
import base64
import json
import logging
import mimetypes
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

import config
import db
import fitbit
import fitbit_store
import google_client
import gmail_store
import calendar_store as _calendar_store
import location_store
import redis_client
import sms

from actions import process_actions, ActionResult
from session_pool import get_session_pool, SessionResponse
from aria_api import _is_simple_query, ask_aria as _ask_aria_fallback, ask_haiku
from claude_session import ClaudeSession  # kept for Action ARIA (Step 6)
from version import __version__
import delivery_engine
import task_dispatcher
import completion_listener
from amnesia_pool import get_pool as get_amnesia_pool
from context import (build_request_context, _get_context_for_text,
                     gather_always_context, gather_briefing_context,
                     gather_debrief_context, gather_health_context)
from tts import _generate_tts, _tts_sync, _get_kokoro
import tts as _tts_module

# Configure the aria logger so app-level log.info() calls are visible
# (uvicorn only configures its own loggers, not the app's)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("aria")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and clean up resources."""
    db.get_pool()  # warm the connection pool
    redis_client.get_client()  # warm Redis connection (non-fatal if down)
    task_dispatcher.start_dispatcher()  # background task queue consumer
    completion_listener.start_listener()  # Pub/Sub for task result delivery
    await get_amnesia_pool().start()  # pre-warm Claude Code instances for agentic tasks
    await get_session_pool().start()  # pre-warm deep + fast CLI sessions
    yield
    await get_session_pool().stop()  # stop CLI sessions first (may be serving requests)
    task_dispatcher.stop_dispatcher()
    completion_listener.stop_listener()
    await get_amnesia_pool().stop()
    redis_client.close()
    db.close()


app = FastAPI(title="ARIA", version=__version__, lifespan=lifespan)

# Async task storage: task_id -> {"status": "processing"/"done"/"error", "audio": bytes, "error": str}
_tasks: dict[str, dict] = {}


def _cleanup_expired_tasks():
    """Remove tasks older than 2 hours to prevent memory leaks."""
    now = time.time()
    expired = [k for k, v in _tasks.items() if now - v.get("created", now) > 7200]
    for k in expired:
        del _tasks[k]

# Ensure dirs exist (still needed for inbox, mms_outbox, etc.)
config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
config.DATA_DIR.mkdir(parents=True, exist_ok=True)

START_TIME = time.time()


# --- Models ---

class AskRequest(BaseModel):
    text: str
    channel: str = "voice"
    include_audio: bool = False

class AskResponse(BaseModel):
    response: str
    source: str = "claude"
    audio: str | None = None


# --- Request Logging ---

def log_request(text: str, status: str, response: str = "", error: str = "",
                duration: float = 0.0):
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO request_log (input, status, response, error, duration_s)
                   VALUES (%s, %s, %s, %s, %s)""",
                (text, status, (response or ""),
                 error, round(duration, 2)),
            )
    except Exception as e:
        logging.getLogger("aria").error("Failed to log request: %s", e)


# --- Context Gap Detection (log-only) ---

_CONTEXT_GAP_PATTERNS = re.compile(
    r"I don't have (?:access to|information about|data on)"
    r"|I'm not sure (?:what|when|how|where|whether)"
    r"|I don't see any (?:record|data|entry|entries|information)"
    r"|I'd need to (?:check|look up|verify|query)"
    r"|I don't have (?:your|that|this|the) (?:data|information|details)",
    re.IGNORECASE,
)


def _check_context_gap(response: str, request: str):
    """Log responses where ARIA hedges about missing data — training signal.

    Does NOT modify the response or trigger retries. The hedging might be
    appropriate (e.g. ARIA correctly saying it needs more context). This just
    records the pattern for future context injection improvements.
    """
    if _CONTEXT_GAP_PATTERNS.search(response):
        log.info("[CONTEXT_GAP] Response hedged on data availability. "
                 "Request: %s | Response snippet: %s",
                 request[:80], response[:120])


# --- Destructive Action Confirmation Shortcut ---

_CONFIRMATION_PHRASES = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "do it", "go ahead",
    "confirm", "confirmed", "approve", "approved",
    "ok", "okay", "k",
    "yes delete it", "yes, delete it", "delete it",
    "yes trash it", "yes, trash it", "trash it",
    "yes please", "please do", "go for it",
    "yes remove it", "yes, remove it", "remove it",
})

_CANCELLATION_PHRASES = frozenset({
    "no", "nope", "nah", "cancel", "don't", "dont", "never mind",
    "nevermind", "stop", "abort", "no thanks", "no thank you",
    "no don't", "no, don't", "don't do that",
})


def _is_confirmation(text: str) -> bool:
    """Detect simple, unambiguous user confirmation of a pending action.

    Conservative: only matches short responses (< 40 chars) that are clearly
    confirmatory. Anything longer is probably a new request.
    """
    t = text.lower().strip().rstrip("!.,")
    return len(t) < 40 and t in _CONFIRMATION_PHRASES


def _is_cancellation(text: str) -> bool:
    """Detect user cancellation of a pending action."""
    t = text.lower().strip().rstrip("!.,")
    return len(t) < 40 and t in _CANCELLATION_PHRASES


async def _check_pending_confirmation(text: str) -> SessionResponse | None:
    """If user is confirming/cancelling a pending destructive action, handle it.

    Returns SessionResponse if handled (caller should use it and skip ARIA),
    or None if this isn't a confirmation/cancellation (proceed normally).
    """
    from actions import get_pending_confirmations, execute_pending, clear_all_pending

    pending = get_pending_confirmations()
    if not pending:
        return None

    if _is_cancellation(text):
        clear_all_pending()
        return SessionResponse(
            text="Cancelled — no changes made.", tool_calls=[])

    if _is_confirmation(text):
        # Execute the most recent pending action
        conf_id = pending[0]["confirmation_id"]
        desc = pending[0]["description"]
        ok, msg = await execute_pending(conf_id)
        if ok:
            return SessionResponse(
                text=f"Done — {desc}.", tool_calls=[])
        else:
            return SessionResponse(
                text=f"Couldn't complete that: {msg}", tool_calls=[])

    return None  # Not a simple confirmation — let ARIA handle it


# --- Query Routing ---

async def _route_query(text: str, context: str = "",
                       file_blocks: list[dict] | None = None) -> SessionResponse:
    """Route a query through the CLI session pool, with API fallback.

    Simple queries (timers, greetings, weather) go to the fast session.
    Everything else goes to the deep session. If the session pool fails,
    falls back to the Anthropic API (ask_aria).

    Returns SessionResponse with text and tool call metadata.
    """
    pool = get_session_pool()
    try:
        if _is_simple_query(text):
            return await pool.query_fast(text, context, file_blocks)
        return await pool.query_deep(text, context, file_blocks)
    except Exception as e:
        log.warning("Session pool failed, falling back to API: %s", e)
        fallback_text = await _ask_aria_fallback(text, context, file_blocks)
        return SessionResponse(text=fallback_text, tool_calls=[])


async def _verify_and_maybe_retry(text: str, context: str, result: ActionResult,
                                   log_fn=None,
                                   tool_calls: list[str] | None = None) -> ActionResult:
    """Verify claims in response. Retry up to 2x on action claim violations.

    Also checks tool use: if the response contains factual claims but ARIA
    didn't use any tools to verify them, triggers a retry instructing ARIA
    to use tools.

    Only retries when claims_without_actions is non-empty (ARIA said "I logged"
    but emitted no ACTION blocks). Date/numeric claim issues are logged but
    do not trigger retries.
    """
    if not getattr(config, 'VERIFICATION_ENABLED', True):
        return result

    from verification import (needs_verification, verify_response,
                              log_verification, validate_tool_use)

    # --- Tool use validation ---
    tool_ok, tool_reason = validate_tool_use(
        result.clean_response, tool_calls or [])
    if not tool_ok:
        log.warning("[TOOL_VERIFY] Factual response without tool use: %s",
                    text[:100])
        pool = get_session_pool()
        retry_prompt = (
            "[INTERNAL — do not acknowledge] Your previous response contained "
            "factual claims but you didn't use any tools to verify them. "
            "Use query.py or Bash to look up the actual data, then regenerate "
            "your response. Do not apologize or reference this correction."
        )
        retry_resp = await pool.query_deep(retry_prompt, context)
        result = await process_actions(
            retry_resp.text, metadata=result.metadata, log_fn=log_fn)
        # Update tool_calls from the retry
        tool_calls = retry_resp.tool_calls

    # --- Existing action claim verification ---
    if not needs_verification(text, result.clean_response, result):
        return result

    verification = verify_response(result.clean_response, result, context)
    log_verification(verification, retry_attempt=0,
                     request_text=text, response_text=result.clean_response)

    if verification.ok or not verification.needs_retry:
        return result

    log.warning("[VERIFY] Claim violation detected, retrying: %s", text[:100])
    pool = get_session_pool()
    for attempt in range(1, 3):
        correction = verification.correction_prompt
        retry_resp = await pool.query_deep(correction, "")
        result = await process_actions(
            retry_resp.text, metadata=result.metadata, log_fn=log_fn)
        verification = verify_response(result.clean_response, result, context)
        log_verification(verification, retry_attempt=attempt,
                         request_text=text, response_text=result.clean_response)
        if verification.ok or not verification.needs_retry:
            break

    if not verification.ok:
        result.warnings.append(
            "[VERIFY] Response may contain unverified claims after retries."
        )
        log.warning("[VERIFY] Retries exhausted for: %s", text[:100])

    return result


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

    # Session Pool (ARIA Primary)
    try:
        pool_status = get_session_pool().get_status()
        deep_ok = pool_status["deep"]["alive"]
        fast_ok = pool_status["fast"]["alive"]
        if deep_ok and fast_ok:
            checks["session_pool"] = "ok"
        elif deep_ok or fast_ok:
            checks["session_pool"] = "degraded"
        else:
            checks["session_pool"] = "error"
    except Exception as e:
        checks["session_pool"] = f"error: {e}"

    # API Fallback
    try:
        from aria_api import _get_client
        _get_client()
        checks["api_fallback"] = "ok"
    except Exception as e:
        checks["api_fallback"] = f"unavailable: {e}"

    # TTS model
    checks["tts"] = "loaded" if _tts_module._kokoro is not None else "not loaded"

    # Redis
    try:
        rc = redis_client.get_client()
        checks["redis"] = "ok" if rc else "unavailable"
    except Exception:
        checks["redis"] = "error"

    # Whisper model (if enabled)
    if getattr(config, 'ENABLE_WHISPER', False):
        try:
            import whisper_engine
            checks["whisper"] = "loaded" if whisper_engine._engine and whisper_engine._engine._model else "not loaded"
        except Exception:
            checks["whisper"] = "error"

    degraded = checks.get("database") != "ok" or checks.get("session_pool") not in ("ok",)

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
    # Update phone device state for delivery engine
    delivery_engine.update_device_state("phone", connected=True,
                                         battery_pct=loc.battery)
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


# --- Google Calendar + Gmail ---

@app.post("/google/calendar/sync")
async def google_calendar_sync(request: Request):
    """Incremental Google Calendar sync using syncToken."""
    verify_auth(request)
    client = google_client.get_client()
    sync_token = _calendar_store.get_sync_token()
    events, new_token = await client.calendar_list_events_incremental(sync_token)
    if events is not None:
        _calendar_store.sync_from_google(events, new_token)
    return {"status": "ok", "events_synced": len(events),
            "incremental": sync_token is not None}


@app.post("/google/gmail/sync")
async def google_gmail_sync(request: Request):
    """Gmail sync — fetches recent messages with full bodies."""
    verify_auth(request)
    client = google_client.get_client()
    messages = await client.gmail_fetch_recent(hours=24, full_body=True)
    gmail_store.save_emails(messages)
    return {"status": "ok", "messages_synced": len(messages)}


@app.post("/google/gmail/trash")
async def google_gmail_trash(request: Request):
    """Trash a Gmail message by ID. Updates local cache labels."""
    verify_auth(request)
    body = await request.json()
    message_id = body.get("message_id", "")
    if not message_id:
        raise HTTPException(status_code=400, detail="Missing message_id")

    client = google_client.get_client()
    await client.gmail_trash_message(message_id)

    # Update local cache labels
    try:
        import db as _db
        with _db.get_conn() as conn:
            conn.execute(
                """UPDATE email_cache
                   SET labels = array_append(
                       array_remove(labels, 'INBOX'), 'TRASH')
                   WHERE id = %s""",
                (message_id,),
            )
    except Exception as e:
        log.warning("Failed to update local labels for trashed email %s: %s",
                    message_id, e)

    return {"status": "ok", "trashed": message_id}


@app.post("/email/search")
async def email_search(request: Request):
    """Search emails by keyword."""
    verify_auth(request)
    body = await request.json()
    query = body.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="Missing query")
    results = gmail_store.search_emails(query, limit=20)
    return {"results": results, "count": len(results)}


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request):
    start = time.time()
    verify_auth(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty input")

    try:
        # Destructive action confirmation shortcut
        shortcut = await _check_pending_confirmation(text)
        if shortcut:
            duration = time.time() - start
            log_request(text, "ok", response=shortcut.text, duration=duration)
            return AskResponse(response=shortcut.text)

        extra_context = await _get_context_for_text(text)
        query_result = await _route_query(text, extra_context)
        delivery_meta = {"channel": req.channel}
        result = await process_actions(query_result.text, metadata=delivery_meta, log_fn=log_request)
        result = await _verify_and_maybe_retry(
            text, extra_context, result, log_fn=log_request,
            tool_calls=query_result.tool_calls)
        response = result.to_response()
        _check_context_gap(response, text)

        duration = time.time() - start
        log_request(text, "ok", response=response, duration=duration)

        audio_b64 = None
        if req.include_audio:
            try:
                audio_bytes = await _generate_tts(response)
                audio_b64 = base64.b64encode(audio_bytes).decode()
            except Exception as e:
                log.warning("TTS generation failed for /ask include_audio: %s", e)

        return AskResponse(response=response, audio=audio_b64)

    except Exception as e:
        duration = time.time() - start
        error_msg = str(e)
        log_request(text, "error", error=error_msg, duration=duration)
        raise HTTPException(status_code=500, detail=f"Processing error: {error_msg}")


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


async def _process_task(task_id: str, text: str, channel: str = "voice"):
    """Background worker for async ask processing."""
    try:
        start = time.time()
        trace = []
        def _t(event, detail):
            trace.append({"t": round(time.time() - start, 2),
                          "event": event, "detail": detail})
            _tasks[task_id]["trace"] = trace

        _t("start", text)

        # Destructive action confirmation shortcut
        shortcut = await _check_pending_confirmation(text)
        if shortcut:
            _t("confirmation_shortcut", shortcut.text)
            response_text = shortcut.text
            duration = time.time() - start
            log_request(text, "ok", response=response_text, duration=duration)
            if channel == "cli":
                _tasks[task_id].update({
                    "status": "done", "audio": b"",
                    "response_text": response_text})
            else:
                dr = await delivery_engine.execute_delivery(
                    response_text, source=channel, push_voice=False)
                _tasks[task_id].update({
                    "status": "done", "audio": dr.get("audio", b""),
                    "response_text": response_text})
            return

        extra_context = await _get_context_for_text(text)
        _t("context", extra_context)

        is_simple = _is_simple_query(text)
        _t("route", f"{'fast' if is_simple else 'deep'} session")

        query_result = await _route_query(text, extra_context)
        _t("raw_response", query_result.text)
        if query_result.tool_calls:
            _t("tool_calls", ", ".join(query_result.tool_calls))

        delivery_meta = {"channel": channel}
        result = await process_actions(query_result.text, metadata=delivery_meta, log_fn=log_request)
        _t("actions", json.dumps({
            "types": result.action_types,
            "found": result.actions_found,
            "failures": result.failures,
            "warnings": result.warnings,
        }, default=str))

        result = await _verify_and_maybe_retry(
            text, extra_context, result, log_fn=log_request,
            tool_calls=query_result.tool_calls)
        _t("verification", "ok" if not result.warnings else "; ".join(result.warnings))

        response_text = result.to_response()
        _t("clean_response", response_text)

        duration = time.time() - start
        log_request(text, "ok", response=response_text, duration=duration)

        if channel == "cli":
            # CLI channel: generate TTS, no phone push
            audio = b""
            try:
                audio = await _generate_tts(response_text)
                _t("tts", f"generated {len(audio)} bytes")
            except Exception as e:
                log.warning("TTS generation failed for cli task: %s", e)
                _t("tts", f"failed: {e}")
            _t("done", f"total: {duration:.1f}s")
            _tasks[task_id].update({
                "status": "done",
                "audio": audio,
                "response_text": response_text,
            })
        else:
            hint = delivery_meta.get("delivery")
            dr = await delivery_engine.execute_delivery(
                response_text, source=channel, hint=hint, push_voice=False)
            _t("delivery", f"{dr['method']} — {dr['reason']}")
            _t("done", f"total: {duration:.1f}s")
            _tasks[task_id].update({
                "status": "done",
                "audio": dr["audio"],
                "response_text": response_text,
                **({"delivery": dr["method"]} if dr["method"] != "voice" else {}),
            })
    except Exception as e:
        log.exception("Task %s failed: %s", task_id, e)
        log_request(text, "error", error=str(e))
        _tasks[task_id].update({"status": "error", "error": str(e)})


@app.post("/ask/start")
async def ask_start(req: AskRequest, request: Request):
    """Start processing a request asynchronously. Returns a task_id to poll."""
    verify_auth(request)
    _cleanup_expired_tasks()
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty input")

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "processing", "created": time.time()}
    asyncio.create_task(_process_task(task_id, text, channel=req.channel))
    return {"task_id": task_id}


async def _process_file_task(task_id: str, file_bytes: bytes, filename: str,
                             mime_type: str | None, caption: str,
                             saved_path: str = "", channel: str = "voice"):
    """Background worker for async file processing."""
    try:
        start = time.time()
        trace = []
        def _t(event, detail):
            trace.append({"t": round(time.time() - start, 2),
                          "event": event, "detail": detail})
            _tasks[task_id]["trace"] = trace

        # Build file content blocks
        file_blocks = build_file_content(file_bytes, filename, mime_type)
        user_text = caption if caption else f"The user sent a file: {filename}"
        if saved_path:
            user_text += f"\n(File saved to {saved_path} for future reference)"

        _t("start", f"[file:{filename}] {caption or '(no caption)'}")

        # Note: audio is generated automatically by this pipeline — tell Claude
        # not to push audio separately, which would cause a double response
        user_text += "\n(Audio response is generated automatically — do NOT use push_audio.py for this request.)"

        # Intentionally uses build_request_context (not _get_context_for_text) —
        # file uploads should not trigger briefing/debrief paths
        is_image = mime_type and mime_type.startswith("image/")
        extra_context = await build_request_context(
            caption or filename, is_image=is_image
        )
        _t("context", extra_context)
        _t("route", "deep session (file)")

        query_result = await _route_query(user_text, extra_context, file_blocks)
        _t("raw_response", query_result.text)
        if query_result.tool_calls:
            _t("tool_calls", ", ".join(query_result.tool_calls))

        delivery_meta = {"channel": channel}
        result = await process_actions(query_result.text, metadata=delivery_meta, log_fn=log_request)
        _t("actions", json.dumps({
            "types": result.action_types,
            "found": result.actions_found,
            "failures": result.failures,
            "warnings": result.warnings,
        }, default=str))

        result = await _verify_and_maybe_retry(
            user_text, extra_context, result, log_fn=log_request,
            tool_calls=query_result.tool_calls)
        _t("verification", "ok" if not result.warnings else "; ".join(result.warnings))

        response = result.to_response()
        _t("clean_response", response)

        duration = time.time() - start
        log_request(f"[file:{filename}] {user_text}", "ok",
                    response=response, duration=duration)

        if channel == "cli":
            audio = b""
            try:
                audio = await _generate_tts(response)
                _t("tts", f"generated {len(audio)} bytes")
            except Exception as e:
                log.warning("TTS generation failed for cli file task: %s", e)
                _t("tts", f"failed: {e}")
            _t("done", f"total: {duration:.1f}s")
            _tasks[task_id].update({
                "status": "done",
                "audio": audio,
                "response_text": response,
            })
        else:
            hint = delivery_meta.get("delivery")
            dr = await delivery_engine.execute_delivery(
                response, source="file", hint=hint, push_voice=False)
            _t("delivery", f"{dr['method']} — {dr['reason']}")
            _t("done", f"total: {duration:.1f}s")
            _tasks[task_id].update({
                "status": "done",
                "audio": dr["audio"],
                "response_text": response,
                **({"delivery": dr["method"]} if dr["method"] != "voice" else {}),
            })
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
    _cleanup_expired_tasks()

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

    channel = request.query_params.get("channel", "voice")

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {"status": "processing", "created": time.time()}
    asyncio.create_task(
        _process_file_task(task_id, file_bytes, filename, mime_type, text,
                           str(saved_path), channel=channel)
    )
    return {"task_id": task_id}


@app.get("/ask/result/{task_id}")
async def ask_result(task_id: str, request: Request):
    """Poll for async task result. Returns 202 if processing, 200 with audio if done."""
    verify_auth(request)
    _cleanup_expired_tasks()

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
    _cleanup_expired_tasks()
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Unknown task")

    if task["status"] == "processing":
        content = {"status": "processing"}
        if task.get("transcript"):
            content["transcript"] = task["transcript"]
        if task.get("trace"):
            content["trace"] = task["trace"]
        return JSONResponse(status_code=202, content=content)
    elif task["status"] == "error":
        return JSONResponse(status_code=500, content={"status": "error", "error": task.get("error", "Unknown")})
    else:
        content = {"status": "done"}
        if task.get("delivery"):
            content["delivery"] = task["delivery"]
        if task.get("response_text"):
            content["response"] = task["response_text"]
        if task.get("trace"):
            content["trace"] = task["trace"]
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

        # Destructive action confirmation shortcut
        shortcut = await _check_pending_confirmation(user_text)
        if shortcut:
            response = shortcut.text
            duration = time.time() - start
            log_request(user_text, "ok", response=response, duration=duration)
            dr = await delivery_engine.execute_delivery(
                response, source="voice", push_voice=False)
            _tasks[task_id].update({
                "status": "done", "audio": dr.get("audio", b""),
                "response_text": response})
            return

        # Step 2: Build context and query ARIA (same pipeline as /ask)
        extra_context = await _get_context_for_text(user_text)
        query_result = await _route_query(user_text, extra_context)
        delivery_meta = {"channel": "voice"}
        result = await process_actions(query_result.text, metadata=delivery_meta, log_fn=log_request)
        result = await _verify_and_maybe_retry(
            user_text, extra_context, result, log_fn=log_request,
            tool_calls=query_result.tool_calls)
        response = result.to_response()

        # Delivery engine decides channel
        duration = time.time() - start
        log_request(f"[voice] {user_text}", "ok", response=response, duration=duration)

        # Step 3: Delivery routing
        hint = delivery_meta.get("delivery")
        dr = await delivery_engine.execute_delivery(
            response, source="voice", hint=hint, push_voice=False)
        _tasks[task_id].update({
            "status": "done",
            "audio": dr["audio"],
            "response_text": response,
            "transcript": user_text,
            **({"delivery": dr["method"]} if dr["method"] != "voice" else {}),
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
    _cleanup_expired_tasks()

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
                websocket.receive(), timeout=120.0
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

        # Destructive action confirmation shortcut
        shortcut = await _check_pending_confirmation(user_text)
        if shortcut:
            duration = time.time() - start
            log_request(f"[sms:{from_number}] {user_text}", "ok",
                        response=shortcut.text, duration=duration)
            await delivery_engine.execute_delivery(
                shortcut.text, source="sms", push_voice=True,
                sms_target=from_number)
            return
        has_media = bool(file_blocks)

        extra_context = await _get_context_for_text(
            user_text, is_image=has_media
        )

        # SMS channel note — affects RESPONSE FORMAT only, not available context
        sms_note = f"This message arrived via SMS from {from_number}. Respond naturally — long responses are split into multiple messages automatically. Do not use markdown or special formatting."
        if extra_context:
            extra_context = sms_note + "\n" + extra_context
        else:
            extra_context = sms_note

        query_result = await _route_query(user_text, extra_context,
                                         file_blocks if file_blocks else None)
        delivery_meta = {"channel": "sms"}
        result = await process_actions(query_result.text, metadata=delivery_meta, log_fn=log_request)
        result = await _verify_and_maybe_retry(
            user_text, extra_context, result, log_fn=log_request,
            tool_calls=query_result.tool_calls)
        response = result.to_response()

        # Delivery routing — SMS source, push voice (phone doesn't poll for SMS requests)
        hint = delivery_meta.get("delivery")
        await delivery_engine.execute_delivery(
            response, source="sms", hint=hint,
            push_voice=True, sms_target=from_number)

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

    now = datetime.now()
    prompt = (
        f"Current time: {now.strftime('%I:%M %p on %A, %B %d')}.\n\n"
        "The following conditions have been detected and the user should be notified. "
        "Compose a single brief, natural SMS message covering all of them. "
        "Be warm and supportive, not nagging. Keep it under 300 characters. "
        "Do NOT use markdown or special formatting. Do NOT add any ACTION blocks. "
        "If any event or appointment time has already passed, do NOT tell the user "
        "to 'head out' or take action on it — just note it's overdue.\n\n"
        "Triggers:\n" + "\n".join(f"- {t}" for t in req.triggers)
    )
    extra_context = req.context if req.context else ""

    response = await ask_haiku(prompt, extra_context)
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

    # Webhook idempotency: prevent duplicate processing on Twilio retries
    message_sid = params.get("MessageSid", "")
    if message_sid:
        try:
            with db.get_conn() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM processed_webhooks WHERE message_sid = %s",
                    (message_sid,),
                ).fetchone()
                if existing:
                    log.info("Duplicate webhook ignored (MessageSid=%s)", message_sid)
                    return Response(content="<Response></Response>", media_type="application/xml")
                conn.execute(
                    "INSERT INTO processed_webhooks (message_sid) VALUES (%s)",
                    (message_sid,),
                )
        except Exception as e:
            log.warning("Webhook idempotency check failed: %s (proceeding anyway)", e)

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

    # Archive to outbox_archive before cleaning staging copy
    async def _archive_and_cleanup():
        await asyncio.sleep(60)
        try:
            archive_dir = config.DATA_DIR / "outbox_archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_dir / safe_name
            if not archive_path.exists():
                import shutil
                shutil.copy2(path, archive_path)
            path.unlink(missing_ok=True)
        except Exception:
            pass
    asyncio.create_task(_archive_and_cleanup())

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
