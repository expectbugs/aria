# Changelog

All notable changes to ARIA are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: major phases = minor version bumps.

---

## [0.3.7] — 2026-03-19

### Unified Context Architecture

Eliminated context injection gaps across all request paths. Every endpoint now uses the same `build_request_context()` function, ensuring ARIA has identical data availability regardless of whether a request arrives via voice, file upload, or SMS.

### Fixed

- `/ask/file` and `/sms` had incomplete context — missing weather, vehicle, timers, location, legal, projects, and Fitbit data depending on endpoint
- SMS "good morning" / "good night" didn't trigger briefings or debriefs
- MMS photos through SMS lacked health/nutrition context
- Cross-domain queries (e.g. "calories burned vs eaten") could miss data when keywords only triggered one context silo

### Added

- `build_request_context()` — single unified async context builder used by all request paths
- `gather_health_context()` — compact unified health snapshot (meals, nutrition, Fitbit, patterns, calorie balance)
- Briefing/debrief detection in SMS handler
- Incomplete tracking warning — flags when meals exist in diary without structured nutrition data

---

## [0.3.6] — 2026-03-19

### Integrity & Reliability

System prompt overhaul and code-level validation to ensure ARIA never claims actions she didn't take, never presents guesses as facts, and never hallucinations.

### Fixed

- ARIA claimed "logged!" for 15 nutrition label photos without emitting any ACTION blocks — data was never stored
- Double audio response on file uploads — Claude ran `push_audio.py` AND the pipeline generated TTS
- Fitbit `sedentaryMinutes` returned as string from API — crashed nudge evaluation with TypeError

### Added

- **ABSOLUTE RULES — INTEGRITY** section at top of system prompt: never lie, never guess-as-fact, never hallucinate, never claim unperformed actions
- ACTION blocks explicitly marked MANDATORY for all data storage — conversation memory is NOT persistent
- Claim-without-action detection in `process_actions()` — appends system note if response says "logged/stored/saved" but 0 actions found
- Nutrition-specific claim detection — flags responses mentioning 3+ nutrient terms without a `log_nutrition` ACTION block
- Per-request instruction on file uploads: "(Audio response is generated automatically — do NOT use push_audio.py)"
- `push_audio.py` usage clarified in system prompt: only for SMS voice delivery, never for file uploads or voice requests

---

## [0.3.5] — 2026-03-19

### Nutrition Tracking from Label Photos

Structured per-item nutrition logging with daily totals, limit checking, and net calorie balance against Fitbit burn data.

### Added

- `nutrition_store.py` — 16 nutrient fields per item (FDA label format + omega-3), serving size tracking, daily totals, limit checking
- `log_nutrition` / `delete_nutrition_entry` ACTION blocks in system prompt and `process_actions()`
- `get_daily_totals()` — sums all items × servings for a day
- `get_net_calories()` — intake minus Fitbit burn = net surplus/deficit
- `check_limits()` — warns on approaching NAFLD limits (added sugar 36g, saturated fat 15g, sodium 1800mg)
- `get_context()` — running daily totals with alerts for ARIA context injection
- `get_weekly_summary()` — weekly averages for morning briefings
- Nutrition context in morning briefings and evening debriefs
- Nutrition nudge conditions in tick.py: sugar approaching limit, sodium high, evening calorie surplus
- `NUTRITION_DB` path in config.py

---

## [0.3.4] — 2026-03-19

### Fitbit Integration

Pixel Watch 4 + Pixel 10a health data pulled into ARIA via Fitbit Web API. Exercise coaching with real-time heart rate monitoring.

### Added

- `fitbit.py` — Fitbit Web API client with auto token refresh, all data type fetchers, intraday HR, subscription management
- `fitbit_store.py` — JSON-backed daily snapshots, sleep/HR/HRV/SpO2/activity summaries, exercise mode with Karvonen HR zones, coaching context
- `fitbit_auth.py` — one-time OAuth2 PKCE authorization flow
- Data types: HR (resting + 1-sec intraday), HRV (RMSSD), SpO2, sleep stages, activity/steps/calories, breathing rate, skin temp, VO2 Max
- Exercise coaching mode — explicit activation via ACTION block, 1-min HR polling, voice coaching nudges every 5 min, milestone nudges, safety alerts, 90-min auto-expire
- Fitbit tick polling — 15-min full snapshots during waking hours via `process_fitbit_poll()`
- Fitbit-aware nudges: poor sleep (<5h), resting HR anomaly (10+ bpm above 7-day avg), sedentary (2h+), afternoon activity encouragement (<3k steps)
- Daemon endpoints: `POST /fitbit/sync`, `POST /fitbit/subscribe`, `POST /fitbit/exercise-hr`, `GET/POST /webhook/fitbit`
- Fitbit data in morning briefings, evening debriefs, and health-keyword queries
- `start_exercise` / `end_exercise` ACTION blocks
- Fitbit config: `FITBIT_CLIENT_ID`, `FITBIT_CLIENT_SECRET`, `FITBIT_REDIRECT_URI`, `FITBIT_TOKEN_FILE`, `FITBIT_DB_DIR`, `FITBIT_WEBHOOK_VERIFY`, `FITBIT_SCOPES`, `FITBIT_EXERCISE_FILE`

---

## [0.3.3] — 2026-03-19

### Outbound SMS Logging & Image Generation

### Added

- Every outbound SMS logged to `data/sms_outbound.jsonl` with timestamp, recipient, exact body text, media URL, and Twilio SID
- 4K image workflow in system prompt: generate at 1920x1080 then upscale, not stretch phone resolution

---

## [0.3.2] — 2026-03-19

### System Prompt Optimization & Missing Functionality

Rewrote the system prompt from scratch for maximum effectiveness. 41% smaller than the original while covering significantly more capabilities. Also wired up the modify_event ACTION block that existed in calendar_store but was never connected.

### Added

- **`modify_event` ACTION block** — was implemented in calendar_store.py but never wired into process_actions(). ARIA can now move/rename events without delete-and-readd.
- **User identity in prompt** — ARIA knows Adam's name, timezone (Central), work schedule (2nd shift), living situation, vehicle (Xterra), and key life context
- **Known places in prompt** — home, my house, work, doctor with addresses so ARIA can use them naturally in conversation
- **Channel awareness** — ARIA adapts response style for voice (natural speech) vs SMS (under 300 chars, no formatting)
- **Recurring reminders documented** — daily/weekly/monthly option was supported but never mentioned in the prompt
- **Multi-ACTION instruction** — ARIA now knows she can emit multiple ACTION blocks in one response
- **Auto meal logging** — when Adam describes eating something specific, ARIA logs it without asking
- **Diet behavioral rules** — never suggest moderation (cold turkey works better), reinforce streak milestones
- **Timer confirmation** — ARIA confirms exact fire time and delivery method when setting timers
- **Relative date resolution** — explicit instruction to resolve "next Tuesday" etc. to exact dates
- **Honesty rule** — say "I think" when estimating, verify when possible, never confabulate
- **Push audio tool** — documented in prompt for voice-delivery timers
- **OpenRC note** — prevents ARIA from defaulting to systemd commands

### Changed

- **System prompt rewritten** — organized by priority (identity → rules → actions → tools → context), 41% smaller than pre-optimization while covering more functionality

### Fixed

- **modify_event ACTION block** — existed in calendar_store.modify_event() since Phase 1 but was never handled in process_actions(), making it impossible to modify events via voice

---

## [0.3.1] — 2026-03-19

### Location-Based Reminders

Geofencing is now handled natively by the existing location tracking + tick system. No separate Tasker GPS profiles needed.

### Added

- **Location-triggered reminders** — reminders with `location` and `location_trigger` (arrive/leave) fields. "Remind me when I get home to check the mail" creates a reminder that fires when GPS shows you at home.
- **Known places in config** — `KNOWN_PLACES` maps names like "home", "work", "my house", "doctor" to partial address matches against reverse-geocoded GPS data
- **`check_location_reminders()`** in tick.py — runs every tick (every minute), checks all location reminders against current GPS position, fires via SMS and marks complete

### Changed

- **`calendar_store.add_reminder()`** — new optional `location` and `location_trigger` parameters
- **System prompt** — documents location-triggered reminder ACTION blocks, instructs ARIA to use them for "remind me when I get to X" requests
- **Geofencing removed from Phase 5** — no longer needed as a separate feature

---

## [0.3.0] — 2026-03-19

### Phase 3 Complete — Autonomous ARIA

ARIA is now autonomous. She can schedule her own future actions, proactively nudge via SMS, resolve GPS to street addresses, and push voice to the phone on demand. This release completes Phase 3.

### Added

- **Timer system (`timer_store.py`)** — JSON-backed scheduler with SMS or voice delivery. ARIA creates timers via ACTION blocks: relative (`minutes: 30`) or absolute (`time: "14:30"`). Supports priority levels (urgent bypasses quiet hours).
- **Tick script (`tick.py`)** — cron job running every minute. Checks for due timers and fires them. Evaluates nudge conditions every 30 minutes. Most ticks are no-ops (<100ms).
- **Proactive nudge system** — Python condition checks against all data stores with per-type cooldowns:
  - Meal gap (5+ hours without logging, noon-9pm)
  - Calendar warning (event in 15-45 minutes)
  - Overdue reminders
  - Diet compliance (evening check if <2 meals logged)
  - Health patterns (recurring symptoms, low sleep)
  - Legal deadlines (within 3 days)
  - Battery low (<15%)
- **Nudge cooldowns** — per-type minimum intervals prevent nagging (meal: 4h, calendar: 30min, health: 24h, vehicle: 7d, etc.)
- **Quiet hours** (midnight-7am) — nudges suppressed unless timer priority is urgent
- **`POST /nudge` endpoint** — tick sends triggered conditions, Claude composes a natural consolidated SMS
- **Reverse geocoding (`location_store.py`)** — GPS coords resolved to human-readable addresses via Nominatim (OpenStreetMap, free). Results cached by ~100m precision.
- **Voice push (`push_audio.py`)** — push TTS audio to phone via Tasker HTTP Server `/audio` path. Voice-delivery timers only (explicit user opt-in). Falls back to SMS if phone unreachable.
- **`meal_type` field** in health_store — breakfast, lunch, dinner, snack for better diet tracking and nudge evaluation

### Changed

- **Location context** — briefings and queries now show resolved addresses with movement history as place names
- **`location_store.record()`** now async (reverse geocoding via httpx)
- **System prompt** — documents timer ACTION blocks, meal_type field, voice push
- **Cron** — tick.py at `* * * * *` (every minute), alongside existing rsync

---

## [0.2.5] — 2026-03-19

### SMS/MMS via Twilio & Tailscale Funnel

ARIA now has a phone number (+1 262-475-1990) and can receive SMS and MMS messages. Outbound replies pending A2P 10DLC verification.

### Added

- **`sms.py`** — Twilio client wrapper with `send_sms()`, `send_to_owner()`, and webhook signature validation via `RequestValidator`
- **`POST /sms` endpoint** — Twilio webhook for incoming SMS/MMS; validates signature, handles STOP/HELP compliance keywords, downloads MMS attachments to `data/inbox/`, processes through Claude with context injection, responds via outbound SMS
- **Tailscale Funnel** — exposes `/webhook/*` at `https://beardos.tail847be6.ts.net/webhook/` to the public internet for Twilio webhooks; started with `tailscale funnel --bg --set-path /webhook 8450`
- **Twilio credentials in config** — Account SID, Auth Token, API SID, API Key, Messaging Service SID, phone number, webhook URL, owner phone number
- **GitHub Pages** — privacy policy and terms & conditions at `expectbugs.github.io/aria/` for A2P 10DLC compliance (`docs/privacy-policy.md`, `docs/terms-and-conditions.md`)
- **`twilio` Python SDK** (v9.10.3) added to dependencies

### Changed

- **Daemon bind address** — changed from Tailscale IP only to `0.0.0.0` so Tailscale Funnel's localhost proxy can reach the daemon
- **SMS context** — incoming SMS messages get calendar/reminder context and nutrition keyword detection (diet reference injection), same as voice and file input channels

---

## [0.2.4] — 2026-03-18

### Specialist Modules, Debrief, File Input & Nutrition Tracking

Major Phase 3 progress — added specialist logging, project briefs, daily debrief, diet/nutrition tracking, and universal file input from phone.

### Added

- **Specialist modules** — three new JSON-backed log stores following the calendar_store pattern:
  - **`vehicle_store.py`** — vehicle maintenance log with CRUD, `get_latest_by_type()` for service interval tracking
  - **`health_store.py`** — health/physical log with pain, sleep, exercise, symptom, medication, meal, and nutrition categories; `get_patterns()` detects recurring symptoms, sleep averages, fish/omega-3 intake tracking
  - **`legal_store.py`** — legal case log with development, filing, contact, note, court_date, and deadline entry types; `get_upcoming_dates()` for court dates
- **6 new ACTION block types** — `log_vehicle`, `delete_vehicle_entry`, `log_health`, `delete_health_entry`, `log_legal`, `delete_legal_entry`
- **Keyword-triggered context injection** — vehicle (xterra, oil, mileage...), health (pain, sleep, body log...), legal (case, court, walworth...) keywords trigger relevant specialist data in Claude's context
- **Specialist data in morning briefing** — recent vehicle maintenance, health patterns (last 7 days), upcoming legal dates
- **Project status briefs (`projects.py`)** — voice-callable project summaries from markdown files in `data/projects/`; keyword detection for "project update", "status of X", etc.
- **Daily debrief ("good night")** — triggered by "good night", "end my day", etc.; gathers today's interactions from request log, calendar events, tomorrow's prep, active reminders, specialist log activity, meals logged, health patterns, diet day counter, and overnight weather forecast
- **Diet/nutrition tracking** — trimmed `data/diet_reference.md` for daily context injection on food/nutrition keywords; full medical profile stored in `data/health_profile.md` for future specialist AI; meal logging via health_store "meal" category; diet day counter in briefings and debriefs (started March 17, 2026); fish/omega-3 intake pattern detection
- **Universal file input (`POST /ask/file`)** — accepts any file from phone via AutoShare + Tasker; supports both multipart form data and raw body with query params; handles images (base64 visual blocks), PDFs (document blocks), text/code files (inline text), and unknown types (metadata); nutrition keywords in caption auto-inject diet reference
- **File inbox (`data/inbox/`)** — all received files saved with timestamps for future reference; Claude is informed of saved path so she can access files later via shell
- **AutoShare polling snippet (`snippets/aria_file_poll.js`)** — Tasker JavaScriptlet for polling file request results with adaptive intervals
- **`python-multipart` dependency** — required by FastAPI for file upload endpoints

### Changed

- **`ClaudeSession.query()`** — now accepts optional `file_blocks` parameter for multimodal messages (text + images/PDFs/files)
- **System prompt expanded** — documents specialist log ACTION blocks with field schemas and trigger phrases; file input capability; diet compliance awareness
- **`config.example.py`** — added `VEHICLE_DB`, `HEALTH_DB`, `LEGAL_DB` paths
- **`.gitignore`** — added `adam_health_nutrition_profile.md` (personal health data)
- **`requirements.txt`** — updated with python-multipart

---

## [0.2.3] — 2026-03-16

### Visual Output Dependencies

Install Matplotlib and Graphviz so ARIA can actually generate charts, graphs, and diagrams.

### Added

- **Matplotlib** — installed in ARIA venv for charts, graphs, and data visualizations
- **Graphviz** — installed system-wide (`dot` command) for diagrams, flowcharts, and dependency graphs
- SVG generation requires no additional dependencies (Claude writes SVG directly as text)

### Changed

- **`requirements.txt`** — updated with matplotlib and its dependencies (pillow, contourpy, cycler, fonttools, kiwisolver)

---

## [0.2.2] — 2026-03-16

### Image Push to Phone

ARIA can now generate images and push them to the phone for display via Tasker.

### Added

- **`push_image.py`** — script to POST images to the phone's Tasker HTTP Server with optional caption; handles content type detection and connection errors
- **Tasker image receiver (`snippets/aria_image_server.js`)** — documents exact Tasker setup: HTTP Request event profile + Copy File + HTTP Response + Text/Image Dialog
- **Visual output tools in system prompt** — ARIA now knows about Matplotlib (charts/graphs), Graphviz (diagrams/flowcharts), and SVG (vector graphics) as generation options
- **Push-to-phone in system prompt** — ARIA always pushes generated images to the phone after creation
- **FLUX.2 step guidance in system prompt** — fewer steps (12-16) for quick images, more (24-30) for high quality
- **Phone config** — `PHONE_IP` and `PHONE_PORT` settings in config for Tasker HTTP Server target

### Changed

- **Claude timeout increased** — from 120s to 600s (10 minutes) to accommodate image generation
- **Phone image resolution** — images for phone generated at 540x1212 with no upscale

---

## [0.2.1] — 2026-03-16

### Image Generation & Upscaling

Added FLUX.2 and SUPIR to ARIA's system prompt so she can generate and upscale images on demand.

### Added

- **FLUX.2 in system prompt** — ARIA knows how to invoke `~/imgen/generate.py` with all options (prompt, steps, seed, width, height, output)
- **SUPIR in system prompt** — ARIA knows how to invoke `~/upscale/upscale4k.sh` for 4K upscaling

---

## [0.2.0] — 2026-03-14

### Phase 2: Migration to Beardos & Failover

Migrated the ARIA backend from slappy (laptop) to beardos (main PC) as the primary host. Slappy becomes a warm standby that Tasker fails over to automatically if beardos is unreachable.

### Added

- **`/ask/status/{task_id}` endpoint** — lightweight JSON-only status check for Tasker polling (JavaScriptlet can't handle binary responses from `/ask/result`)
- **`/ask/start` task timestamps** — tasks now track creation time for proper expiry
- **Failover JavaScriptlet (`snippets/aria_ask.js`)** — complete rewrite with dual-host failover: tries beardos first, announces and falls back to slappy if unreachable, queues locally if both are down
- **Adaptive polling** — poll intervals scale with elapsed time: 3s for the first minute, 10s for 1–5 min, 30s for 5–60 min (up to 1 hour max)
- **Health check snippet (`snippets/aria_health.js`)** — checks both hosts and sets Tasker global `ARIA_STATUS` to `primary`, `fallback`, or `offline`; runs every 5 minutes via Tasker Time profile
- **Config-driven deployment (`config.example.py`)** — one codebase, per-host `config.py` (gitignored); includes all settings with docs: identity, paths, network, Claude CLI, auth, TTS, weather, news feeds, and hardware capability flags
- **`requirements.txt`** — pinned dependencies for reproducible venv setup
- **Hardware capability flags** — `ENABLE_GPU`, `ENABLE_IMAGE_GEN`, `IS_PRIMARY` in config for runtime feature gating
- **Host-aware system prompt** — ARIA now knows which machine she's running on and whether she's in failover mode
- **Shell access in system prompt** — Claude can run shell commands to answer system queries (disk, network, services, etc.) with safety guardrails: read-only commands are always fine, destructive commands require explicit user confirmation
- **Tasker setup documentation** — full Tasker configuration (global variables, ARIA Ask task steps, Health Check task, JavaScriptlet rules) moved into the project plan

### Changed

- **Task expiry extended** — from 5 minutes to 2 hours, allowing long-running operations like image generation to complete without being garbage-collected
- **Project plan updated** — Phase 2 marked complete; added deployment model docs, failover architecture, data sync strategy, and host comparison table

### Removed

- **`Tasker_Setup_Guide.md`** — replaced by integrated documentation in `ARIA_Project_Plan.md`

### Fixed

- **Missing task timestamp** — `_tasks[task_id]` in `/ask/start` now includes `"created": time.time()` so expiry cleanup works correctly (was comparing against `now` as fallback, meaning tasks never expired)

---

## [0.1.0] — 2026-03-08

### Phase 1: Core Voice Loop

End-to-end voice assistant pipeline: phone → STT → Tasker → FastAPI daemon → Claude CLI → Kokoro TTS → phone speaker. First working version of ARIA.

### Added

- **FastAPI daemon (`daemon.py`)** — async voice assistant backend bound to Tailscale IP, with bearer token auth
  - `POST /ask` — synchronous text-in, text-out endpoint
  - `POST /ask/audio` — synchronous text-in, WAV-out via Kokoro TTS
  - `POST /ask/start` — async request submission, returns `task_id`
  - `GET /ask/result/{task_id}` — poll for completed audio response (202 while processing, 200 with WAV when done)
  - `GET /health` — uptime and version check
  - `GET /snippet/{name}` — serves JS snippets for Tasker copy-paste
- **Persistent Claude CLI session (`ClaudeSession`)** — keeps a single Claude Code subprocess alive using `stream-json` protocol, recycled every 200 requests to manage context size; auto-approves permission/hook requests
- **System prompt** — defines ARIA's conversational voice persona; instructs Claude to respond naturally without markdown, bullet points, or trailing questions
- **Morning briefing** — triggered by "good morning" or similar; assembles date/time, weather, calendar, reminders, upcoming events, and news headlines into a single spoken response
- **Context-aware routing** — detects weather keywords, calendar keywords, etc. and injects relevant data into Claude's context per-request
- **ACTION block processing** — Claude emits `<!--ACTION::{}-->` JSON blocks for calendar/reminder mutations; daemon parses, executes, and strips them from the spoken response; overrides Claude's response with honest error message if any action fails
- **Calendar & reminders (`calendar_store.py`)** — JSON-backed storage with add, modify, delete, complete operations; 8-char UUID IDs; sorted by date/time
- **Weather (`weather.py`)** — NWS API integration (free, no API key); current conditions from nearest observation station, 7-day forecast, active alerts by zone; cached grid lookups; retry with backoff on transient failures
- **News (`news.py`)** — RSS feed fetcher via feedparser; configurable feeds per category (tech, Wisconsin, manufacturing); async HTTP fetch with httpx
- **Kokoro TTS** — `kokoro-onnx` with `af_heart` voice, model cached at startup so subsequent requests skip load time; outputs WAV audio
- **Tasker JavaScriptlet (`snippets/aria_ask.js`)** — posts voice input to `/ask/start`, polls `/ask/result` every 3s for up to 60 iterations, saves WAV response for Tasker Music Play
- **OpenRC service (`openrc/aria.initd`, `openrc/aria.confd`)** — `supervise-daemon` for auto-respawn (max 10 restarts per 60s, 3s delay); depends on `net`, runs after `tailscale`; creates log/data dirs on start
- **Request logging** — every request logged to `logs/requests.jsonl` with timestamp, input, status, truncated response, error, and duration
- **`.gitignore`** — excludes `config.py` (secrets), `data/`, `logs/`, `tts_models/`, venv, `.claude/`, editor swap files
- **`ARIA_Project_Plan.md`** — full 7-phase project plan from core loop through embodiment

### Bugs Fixed During Phase 1

- **ACTION blocks failed silently** — reminder/event IDs weren't included in context strings passed to Claude, so Claude couldn't reference valid IDs; fixed by adding `[id=...]` to all context entries
- **`process_actions()` hallucinated success** — if an action failed (e.g., bad ID), Claude's original "Done!" response was still spoken; fixed by overriding the response with an honest error message on failure
