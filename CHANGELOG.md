# Changelog

All notable changes to ARIA are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: major phases = minor version bumps.

---

## [0.2.2] — 2026-03-16

### Image Push to Phone

ARIA can now generate images and push them to the phone for display via Tasker.

### Added

- **`push_image.py`** — script to POST images to the phone's Tasker HTTP Server with optional caption; handles content type detection and connection errors
- **Tasker image receiver (`snippets/aria_image_server.js`)** — JavaScriptlet for Tasker HTTP Server that receives images and displays them via Text/Image Dialog
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
