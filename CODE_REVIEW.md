# ARIA Code Review — Comprehensive Audit Report

**Date:** 2026-03-19
**Version:** v0.3.8
**Reviewer:** Claude (Opus 4.6)
**Scope:** All source files — daemon.py, all stores, tick.py, utilities, config

---

## Executive Summary

ARIA is a well-structured personal assistant with clear separation of concerns, consistent patterns across stores, and a thoughtful architecture. The codebase has grown organically through rapid iteration and several bugs have been caught and fixed along the way (documented in memory). This audit found **3 critical bugs**, **8 significant bugs**, **12 moderate issues**, and numerous minor/design observations. The most urgent items are the task memory leak, the ACTION block regex failing on multiline JSON, and file locking race conditions between the daemon and tick.py.

---

## Critical Bugs

### C1. Memory Leak — Completed tasks never expire from `_tasks` dict
**File:** `daemon.py:1298, 1358, 1556, 1563`

When background tasks complete, they replace the task dict entirely:
```python
_tasks[task_id] = {"status": "done", "audio": buf.read()}
```
This **drops the `"created"` field** that was set when the task was created (line 1312). The expiry check in `ask_result` (line 1425) uses:
```python
now - v.get("created", now) > 7200
```
With `"created"` missing, `v.get("created", now)` returns `now`, so `now - now = 0`, which is never `> 7200`. **Completed tasks are immortal.** Each task holds a WAV audio blob (typically 0.5–5 MB), so over time this will exhaust memory.

Additionally, `ask_result` never deletes successful tasks (line 1440-1441 returns audio but doesn't `del _tasks[task_id]`), while it does delete error tasks (line 1437). Even if the expiry were fixed, fetched results would persist for 2 hours.

**Impact:** Server memory grows unboundedly. After enough requests, the daemon will OOM.

**Fix:** Preserve the `"created"` field when updating task status. Also delete the task after successfully returning the audio.

---

### C2. ACTION block regex fails on multiline JSON
**File:** `daemon.py:946`

```python
actions = re.findall(r'<!--ACTION::(\{.*?\})-->', response_text)
```

The `.*?` pattern does **not match newlines** by default. If Claude formats the ACTION JSON across multiple lines (which it can, especially for large `log_nutrition` blocks with many fields), the regex silently fails to parse it. The ACTION is ignored, data is not stored, and Claude's claim-without-action detector may or may not catch it.

**Impact:** Intermittent data loss. Nutrition labels, health entries, and other ACTION-dependent storage may silently fail.

**Fix:** Add `re.DOTALL` flag: `re.findall(r'<!--ACTION::(\{.*?\})-->', response_text, re.DOTALL)`

---

### C3. No file locking between daemon and tick.py on shared JSON stores
**Files:** All `*_store.py` files, `tick.py`

The daemon (FastAPI async) and tick.py (cron, runs every minute) both read and write the same JSON files (`timers.json`, `fitbit_exercise.json`, `nudge_cooldowns.json`, `tick_state.json`). The pattern is:
1. Process A: `data = _load()` (reads entire file)
2. Process B: `data = _load()` (reads entire file)
3. Process A: `_save(modified_data)` (writes entire file)
4. Process B: `_save(modified_data)` (writes entire file — **overwrites A's changes**)

This is most dangerous for `timer_store`: tick.py calls `complete_timer()` while the daemon may be calling `add_timer()` from `process_actions()` simultaneously. The timer addition could be lost.

**Impact:** Lost writes, particularly for timers and exercise state. Low probability per-tick but guaranteed to happen eventually.

**Fix:** Use `fcntl.flock()` in `_load()`/`_save()`, or switch to SQLite which handles concurrency.

---

## Significant Bugs

### S1. Fitbit webhook endpoint has no authentication
**File:** `daemon.py:1194-1220`

The `POST /webhook/fitbit` endpoint does not call `verify_auth()`. This endpoint is exposed to the public internet via Tailscale Funnel. Anyone who discovers the URL can trigger arbitrary Fitbit data fetches or cause the daemon to make API calls.

Compare with the SMS webhook (line 1843) which properly validates the Twilio signature.

**Fix:** Add Fitbit subscription verification signature checking, or at minimum add `verify_auth(request)`.

---

### S2. `serve_mms_media` path traversal via `..`
**File:** `daemon.py:1889`

```python
safe_name = re.sub(r'[^a-zA-Z0-9_.\-]', '', filename)
```

The sanitization allows `.` characters, meaning `..` survives: `re.sub(r'[^a-zA-Z0-9_.\-]', '', "..")` → `".."`. While `Path(MMS_OUTBOX) / ".."` resolves to the parent directory and `read_bytes()` would fail on a directory, a crafted filename like `..data` or `....secret.json` could be problematic depending on directory structure.

**Fix:** Strip all leading dots, or reject filenames containing `..`.

---

### S3. Concurrent SMS/voice WAV file overwrites
**Files:** `daemon.py:1759`, `tick.py:112, 469`

Several places write to fixed filenames:
- `daemon.py:1759` — `config.DATA_DIR / "sms_voice_response.wav"`
- `tick.py:112` — `config.DATA_DIR / "timer_audio.wav"`
- `tick.py:469` — `config.DATA_DIR / "exercise_audio.wav"`

If two SMS messages arrive simultaneously requesting voice delivery, or two timers fire at the same time, the WAV files overwrite each other. The second request may push audio from the first request's response.

**Fix:** Use `tempfile.NamedTemporaryFile()` or include a UUID in the filename.

---

### S4. Kokoro TTS blocks the async event loop
**Files:** `daemon.py:1270, 1293, 1351, 1546, 1756`

`kokoro.create()` is a synchronous, CPU-bound operation that runs directly in the async event loop. While Claude queries are serialized by the session lock, TTS generation in background tasks (`_process_task`, `_process_file_task`, `_process_voice_task`, `_process_sms`) blocks the event loop, preventing other async operations (health checks, new request acceptance) from proceeding.

The synchronous `/ask/audio` endpoint (line 1270) is particularly bad — it blocks the entire server during TTS.

**Fix:** Wrap `kokoro.create()` calls in `await asyncio.to_thread(kokoro.create, ...)`.

---

### S5. `evaluate_nudges` calls `get_trend` but discards the result
**File:** `tick.py:305`

```python
trend = fitbit_store.get_trend(days=7)
```

The `trend` variable is never used. `get_trend()` reads 7 daily snapshot files from disk (28+ JSON parse operations via `get_heart_summary`, `get_hrv_summary`, `get_sleep_summary`, `get_activity_summary` for each day). This is pure wasted I/O on every nudge evaluation.

**Fix:** Remove the unused call.

---

### S6. `build_request_context` docstring is wrong
**File:** `daemon.py:91-99`

Docstring says: *"Returns (context_string, is_briefing_or_debrief)."*

The function actually returns a `str`. This is a documentation bug but could mislead future development — someone might try to unpack a tuple from the return value.

**Fix:** Update docstring to match the actual return type.

---

### S7. `_process_file_task` skips briefing/debrief detection
**File:** `daemon.py:1337`

File uploads call `build_request_context()` directly instead of `_get_context_for_text()`. This means a file upload with caption "good morning" won't trigger the morning briefing context. While this might be intentional, it's inconsistent with the docstring of `_get_context_for_text` which says it's the "single source of truth."

**Fix:** Route through `_get_context_for_text()` for consistency.

---

### S8. `_process_file_task` ignores delivery routing
**File:** `daemon.py:1342`

```python
response = process_actions(response)
```

Called without `metadata` dict, so `set_delivery` ACTION blocks from file upload responses are silently ignored. If Claude responds to a file upload with `<!--ACTION::{"action": "set_delivery", "method": "sms"}-->`, the routing is lost.

Compare with `_process_voice_task` (line 1529) and `_process_sms` (line 1746) which both pass `metadata=delivery_meta`.

**Fix:** Add `delivery_meta = {}` and pass `metadata=delivery_meta`.

---

## Moderate Issues

### M1. `fitbit_store.get_snapshot` only resolves "today", not "yesterday"
**File:** `fitbit_store.py:49-52`

`save_snapshot()` resolves both "today" and "yesterday" to ISO dates (lines 28-31), but `get_snapshot()` only resolves "today". If the Fitbit webhook sends a notification with `"date": "yesterday"` and code calls `get_snapshot("yesterday")`, it would look for a file literally named `yesterday.json`.

Currently not triggered in practice because callers use ISO date strings, but it's an inconsistency waiting to bite.

---

### M2. `process_actions` overwrites entire response on any failure
**File:** `daemon.py:1075`

```python
clean_response = "Sorry, something went wrong. " + " ".join(failures) + " Please try again."
```

If Claude provides useful information alongside a failed action (e.g., answers a question AND tries to log data, but the log fails), the entire conversational response is replaced with the error. The user loses the answer.

**Recommendation:** Append the error notice instead of replacing the response.

---

### M3. httpx.AsyncClient created per-request everywhere
**Files:** `fitbit.py:68`, `location_store.py:38`, `weather.py:20,33`, `news.py:11`

Every API call creates a new `httpx.AsyncClient()`, losing HTTP connection pooling and keep-alive benefits. `fitbit.py:fetch_daily_snapshot` creates 8 separate clients sequentially.

**Recommendation:** Use module-level or class-level persistent clients.

---

### M4. Fitbit `fetch_daily_snapshot` fetches sequentially, not in parallel
**File:** `fitbit.py:284-288`

```python
for key, coro in fetchers.items():
    try:
        snapshot[key] = await coro
```

Each of the 8 API calls waits for the previous one to complete. With `asyncio.gather`, all 8 could run concurrently, reducing snapshot time from ~8×latency to ~1×latency.

---

### M5. `location_store._geocode_cache` grows unboundedly
**File:** `location_store.py:23`

Every unique 100m grid cell visited adds an entry. No eviction policy. Over months/years of GPS tracking, this will consume increasing memory.

**Recommendation:** Use an LRU cache (e.g., `functools.lru_cache` or a dict with max-size check).

---

### M6. `news.py` fetches feeds sequentially
**File:** `news.py:29`

```python
for name, url in NEWS_FEEDS.items():
    items = await fetch_feed(name, url, max_per_feed)
```

Three feeds fetched one at a time. Could use `asyncio.gather` for parallel fetching, saving ~2× latency on morning briefings.

---

### M7. `nutrition_store` redundant file reads
**Files:** `nutrition_store.py:212, 265, 275`, `daemon.py:320-325`

`get_context()` triggers 5+ reads of `nutrition.json` via cascading calls:
- `get_daily_totals()` → `_load()`
- `get_items()` → `_load()`
- `get_net_calories()` → `get_daily_totals()` → `_load()`
- `check_limits()` → `get_daily_totals()` → `_load()`, `get_net_calories()` → `get_daily_totals()` → `_load()`

Then `gather_health_context()` also calls both `get_context()` AND `get_items()` separately.

**Recommendation:** Cache the loaded data within a single context-building call.

---

### M8. Hardcoded diet start date in 4 places
**Files:** `daemon.py:352, 451, 578`, `tick.py:264`

```python
diet_start = date(2026, 3, 17)
```

Duplicated across `gather_health_context`, `gather_debrief_context`, `gather_briefing_context`, and `evaluate_nudges`. If the date ever needs to change, it must be updated in all four places.

**Fix:** Add `DIET_START_DATE` to `config.py`.

---

### M9. Hardcoded age in `fitbit_store.start_exercise`
**File:** `fitbit_store.py:309`

```python
age = 42
max_hr = 220 - age  # 178
```

Age is hardcoded. As time passes, this becomes incorrect and HR zone calculations drift. Should be computed from a birth date in config.

---

### M10. `modify_event` allows overwriting internal fields
**File:** `calendar_store.py:56`

```python
event.update(updates)
```

The `updates` dict comes from Claude's ACTION block data (via `process_actions` line 976). If Claude includes `"id"` or `"created"` in the update fields (which it shouldn't, but could), those internal fields get overwritten.

**Fix:** Filter out protected fields: `updates = {k: v for k, v in updates.items() if k not in ("id", "created")}`.

---

### M11. Unused import: `Counter` in `health_store.py`
**File:** `health_store.py:5`

```python
from collections import Counter
```

`Counter` is imported but never used anywhere in the module.

---

### M12. Unused import: `timedelta` in `vehicle_store.py`
**File:** `vehicle_store.py:7`

```python
from datetime import datetime, timedelta
```

`timedelta` is imported but never used.

---

## Minor Issues & Code Quality

### m1. Inline `import re` in multiple places
**File:** `daemon.py:945, 1827, 1888, 1912`

`re` is imported inside four different functions rather than at the module level. Since `re` is a stdlib module with negligible import cost, these should be top-level imports.

---

### m2. Inline `import httpx` in `tick.py` functions
**File:** `tick.py:103, 383, 413, 433, 452`

`httpx` is imported inside five different functions. This is likely intentional for cron startup speed, but since `tick.py` always needs httpx for daemon communication, it should be a top-level import.

---

### m3. `_process_sms` uses `has_media` as proxy for `is_image`
**File:** `daemon.py:1733`

```python
is_image = mime_type and mime_type.startswith("image/")  # This is in _process_file_task
...
has_media = bool(file_blocks)  # In _process_sms, this means ANY media type
```

In `_process_sms`, `is_image=has_media` means a text file sent via MMS triggers health/nutrition context injection. The flag should check actual media types.

---

### m4. `nutrition_store.get_context` hardcodes target strings
**File:** `nutrition_store.py:252-257`

Target values like `"1,600-1,900 target"` are hardcoded strings that don't reference the `DAILY_TARGETS` dict. If targets change, these display strings won't update.

---

### m5. `process_actions` silently ignores unknown action types
**File:** `daemon.py:950-1065`

If Claude emits an unknown action type (e.g., `{"action": "log_mood"}`), it falls through all the `elif` branches silently. No warning is logged and no error is reported. This makes it hard to diagnose when Claude uses a wrong action name.

**Recommendation:** Add an `else` clause that logs a warning.

---

### m6. `sms.stage_media` URL construction is fragile
**File:** `sms.py:49`

```python
public_url = f"{config.TWILIO_WEBHOOK_URL.rsplit('/sms', 1)[0]}/mms_media/{dest.name}"
```

Derives the MMS media URL by stripping `/sms` from the webhook URL. If the webhook URL ever changes structure, this breaks silently.

**Recommendation:** Add a dedicated `FUNNEL_BASE_URL` to config.

---

### m7. `ask_start` passes `Request` object to background task
**File:** `daemon.py:1313`

```python
asyncio.create_task(_process_task(task_id, req, request))
```

The `request` object is passed to a background task. While it works because Starlette keeps the parsed data in memory, it's semantically wrong — the HTTP connection may be closed by the time the background task runs. The only use is `verify_auth(request)` inside `ask()`, which is redundant since `ask_start` already verified auth.

---

### m8. `claim_words` detection can false-positive
**File:** `daemon.py:1092-1110`

The claim-without-action check looks for words like "logged", "stored", "saved" in the response. If Claude is discussing the concept of logging (e.g., "You can log meals by describing them"), the system note gets appended.

---

### m9. `nutrition_store.get_context` `on_track` display logic
**File:** `nutrition_store.py:268-272`

```python
elif net["on_track"] is False:
    deficit = -net["net"] if net["net"] < 0 else 0
```

When `on_track` is False and `net` is positive (surplus), `deficit` is set to 0. The display then says "Deficit: 0 cal" which is misleading — there IS no deficit, there's a surplus.

---

### m10. No size limit on file uploads
**Files:** `daemon.py:1391, 1484, 1704`

`/ask/file`, `/stt`, and MMS media downloads have no file size limits. A large upload could exhaust memory (since it's read entirely into bytes) or fill the data/inbox directory.

---

### m11. No periodic task cleanup
**File:** `daemon.py`

Task cleanup only runs reactively when `ask_result` is called. If no one polls results, completed tasks (with their audio blobs) accumulate indefinitely. Should have a periodic cleanup task.

---

### m12. `get_snippet` hardcodes `.js` extension
**File:** `daemon.py:1914`

```python
path = config.BASE_DIR / f"snippets/{safe_name}.js"
```

Only serves `.js` files. If snippets in other formats are ever needed, the endpoint won't support them.

---

## Design Observations (Non-Urgent)

### D1. All stores use identical boilerplate
Every store (`calendar_store`, `health_store`, `legal_store`, `vehicle_store`, `timer_store`, `nutrition_store`) implements the same `_load()` / `_save()` pattern. A `JsonStore` base class would eliminate ~60 lines of duplicated code and ensure consistent behavior (e.g., file locking could be added in one place).

### D2. Single Claude session serializes all requests
The `ClaudeSession._lock` means only one request can be processed at a time across all channels (voice, SMS, file, nudge). A slow image generation request blocks all other requests for up to 10 minutes. Consider request prioritization or multiple session pools.

### D3. No health check for dependencies
`/health` only reports uptime. It doesn't check whether the Claude CLI process is alive, whether TTS model is loaded, whether Fitbit tokens are valid, or whether the phone is reachable. A richer health check would help with monitoring.

### D4. Context builder can produce very large strings
A morning briefing concatenates weather, calendar, reminders, news, vehicle, health patterns, nutrition weekly summary, diet counter, Fitbit data, Fitbit trends, location, and legal dates. For an active user, this could be thousands of tokens of context, consuming a significant portion of Claude's context window.

### D5. No request log rotation
`requests.jsonl`, `location.jsonl`, `sms_log.jsonl`, `sms_outbound.jsonl` grow forever. Over months, `_get_today_requests()` will scan increasingly large files to find today's entries.

### D6. `PHONE_IMAGE_DIR` in config.example.py is never used
`config.example.py` defines `PHONE_IMAGE_DIR` but no code references it. Dead config.

### D7. No graceful shutdown
No cleanup of the Claude subprocess, no flushing of in-progress tasks, no shutdown hooks. When the daemon restarts, any in-flight requests are silently dropped.

### D8. Weather retries sleep in the event loop
`weather.py:40` uses `await asyncio.sleep(1)` between retries. This doesn't block other tasks (since it's await, not time.sleep), so it's correct — but it adds up to 2 seconds of latency on weather failures during briefings.

---

## Security Notes

### SEC1. `config.py` contains plaintext secrets
The actual `config.py` (gitignored) contains Twilio account SID, auth token, API keys, Fitbit client secret, the bearer auth token, personal phone numbers, and home addresses. This is the expected pattern (secrets in gitignored config), but:
- There's no encryption at rest
- Any process on the machine can read these
- The rsync cron copies the data dir to slappy, potentially including secrets if config is in the data dir (it's not — it's in BASE_DIR, so this is fine)

### SEC2. `--dangerously-skip-permissions` on Claude CLI
The Claude subprocess runs with full system access and auto-approves all permission requests (line 893-904). The system prompt asks Claude to confirm before modifying the system, but this is prompt-level enforcement only — there's no technical barrier to destructive commands.

### SEC3. Bearer token auth over plaintext
External requests come through Tailscale Funnel (HTTPS), so they're encrypted. Internal requests from tick.py use `http://127.0.0.1:8450` (plaintext) which is fine for localhost. The auth model is sound for the deployment topology.

---

## Summary by Priority

| Priority | Count | Examples |
|----------|-------|---------|
| **Critical** | 3 | Task memory leak, ACTION regex, file locking |
| **Significant** | 8 | Fitbit webhook auth, path traversal, WAV overwrites, TTS blocking |
| **Moderate** | 12 | Inconsistent date resolution, redundant file reads, hardcoded values |
| **Minor** | 12 | Import style, false positives, dead code |
| **Design** | 8 | Boilerplate dedup, serialized sessions, log rotation |

---

*This review covers all 21 Python source files (excluding venv) totaling ~3,200 lines of application code.*
