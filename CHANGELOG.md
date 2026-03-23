# Changelog

All notable changes to ARIA are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: major phases = minor version bumps.

---

## [0.4.9] — 2026-03-23

### Fixed

- **TTS audio cut off mid-sentence on data-heavy responses** — Kokoro's phoneme batcher splits only on `[.,!?;]`. Data listings (nutrition summaries, daily totals) use colons and newlines between items, producing phoneme batches exceeding the 509-character limit. Kokoro silently truncates these, dropping words from the audio. Fixed with two changes in `_prepare_for_speech()`: (1) single newlines now become commas instead of spaces, giving Kokoro split points between data items; (2) new `_ensure_tts_splits()` safety net inserts commas at word boundaries when any text run exceeds 200 characters without Kokoro-friendly punctuation. The lookbehind `(?<![.,!?;:])` prevents redundant commas after lines already ending with punctuation.
- **26 new TTS tests** — 9 newline→comma conversion tests, 9 safety-net split tests, 3 end-to-end truncation prevention tests, plus 5 updated existing tests for new comma behavior.

### Changed

- **Version** bumped to 0.4.9

---

## [0.4.8] — 2026-03-23

### Added

- **Pantry system** — `data/pantry.md` with verified nutrition data for 15+ staple foods (smoothie, Huel, nutpods, salmon, broccoli, cheddar, Amy's burritos, Chomps, Factor meals, Seeds of Change rice, condiments, drinks). Automatically injected into ARIA's context on nutrition-related queries via `context.py`. ARIA uses pantry values over estimates, eliminating day-to-day estimation drift on recurring meals.
- **Nutrition validation** — `_validate_nutrition()` in `actions.py` runs 5 post-log checks after every `log_nutrition` ACTION: missing calories, fish without omega-3, egg dishes with low cholesterol, incomplete label photo nutrients, meal_type mismatch between health diary and nutrition entries. Warnings are appended to the response and logged for audit.
- **14 nutrition validation tests** — covering all 5 checks plus edge cases (eggplant false positive, estimate vs label_photo, no health entry present).
- **Amy's Dairy burrito** added to pantry (second variety alongside non-dairy).

### Changed

- **ARIA effort level: `high` → `max`** — for deeper reasoning on complex queries.
- **System prompt: nutrition estimation rules** — added omega-3 estimation for fish (~920mg/3oz), egg cholesterol rule (186mg each), restaurant sodium baseline (1,000mg+), round-up guidance for deficit diet, separate-entry rule for split meals.
- **System prompt: meal_type consistency** — explicit rule requiring identical meal_type in both `log_health` and `log_nutrition` ACTION blocks for the same food.
- **System prompt: pantry reference** — instructs ARIA to check pantry data in context before estimating.
- **Pantry context injection** — `context.py` now reads `data/pantry.md` alongside `data/diet_reference.md` when health/nutrition keywords trigger.
- **Version** bumped to 0.4.8

### Fixed

- **Nutrition data audit** — comprehensive audit and correction of 7 days of meal/nutrition data (Mar 17-23). Fixes include: salmon entries swapped between Mar 19/20, omega-3 added to all salmon entries, meal_type inconsistencies between health diary and nutrition entries, source field corrections (label_photo → manual for composite entries), missing trans_fat on 3 entries, White Castle sodium corrected (1,900→2,735mg), restaurant meal estimates corrected using USDA data (chicken parm, vegetarian skillet, penne marinara), smoothie+Huel entries corrected for Huel sodium (45-85→260-290mg), broccoli+cheddar entries corrected from product labels, Amy's burrito corrected from product label.

---

## [0.4.7] — 2026-03-22

### Fixed

- **TTS crash on long responses** — Kokoro TTS (kokoro-onnx v0.5.0) has an off-by-one bug: voice embedding array has 510 rows (indices 0-509) but `MAX_PHONEME_LENGTH=510` allows `voice[510]`. Patched at load time by setting `MAX_PHONEME_LENGTH = 509`. This caused "Something went wrong" errors on the phone for any response that produced a phoneme chunk of exactly 510 tokens.
- **Markdown in TTS output** — Added `_prepare_for_speech()` to strip markdown formatting (bold, italic, code blocks, headings, bullets, links) before passing text to Kokoro. Claude uses markdown in ~80% of responses despite the system prompt requesting plain text, causing TTS to pronounce literal asterisks.
- **Silent error swallowing in `/ask/start`** — `_process_task` caught exceptions but only stored them in the in-memory task dict without logging. Added `log.exception()` so background task errors (like the TTS crash) appear in `logs/aria.err` with full tracebacks.

### Added

- **`CLAUDE.md`** — Project rules for Claude Code: Rule Zero (do not implement without explicit permission), verify-before-execute, integrity, system environment, architecture constraints, testing safety, external API data handling.
- **17 TTS tests** — `_prepare_for_speech()` unit tests covering bold, italic, code blocks, headings, bullets, numbered lists, links, real-world responses, and an integration test verifying markdown is stripped before reaching Kokoro.

### Changed

- **ARIA effort level: `auto` → `high`** — ARIA was running at medium effort (the Opus 4.6 default for "auto"), explaining shallow reasoning on complex queries. Now uses `high` for consistently deeper thinking.
- **ARIA auto-memory disabled** — ARIA's subprocess was loading 200 lines of Claude Code's auto-memory (MEMORY.md) into context, which contained irrelevant/conflicting instructions (mock patching rules, "verify before execute", etc.). Disabled via `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. All critical reinforcements are already in ARIA's system prompt.
- **ARIA excludes CLAUDE.md** — Added `--settings '{"claudeMdExcludes": [...]}'` to ARIA's subprocess invocation so Rule Zero (designed for interactive Claude Code) doesn't prevent ARIA from acting autonomously.
- **Version** bumped to 0.4.7

---

## [0.4.6] — 2026-03-21

### Refactored

- **Split daemon.py into 6 modules** — `daemon.py` (1,989 lines) refactored into focused modules: `system_prompt.py` (ARIA system prompt builder), `claude_session.py` (persistent Claude CLI session manager), `context.py` (keyword-triggered context injection for briefings, debriefs, health, calendar, etc.), `actions.py` (ACTION block extraction and dispatch), `tts.py` (Kokoro TTS model caching and audio generation). `daemon.py` retains FastAPI app, endpoints, background workers, auth, and file processing (~940 lines).
- **Dependency injection for `log_request`** — `process_actions()` now accepts an optional `log_fn` parameter instead of importing `log_request` directly, eliminating circular dependency risk and simplifying test mocking.
- **Removed 10 unused imports from daemon.py** — `calendar_store`, `vehicle_store`, `health_store`, `legal_store`, `timer_store`, `nutrition_store`, `weather`, `news`, `projects`, `os` no longer imported directly (accessed via `context.py` and `actions.py`).

### Changed

- **Test suite updated** — 8 test files updated with new mock paths pointing to the actual modules where functions live. 5 test files required no changes. All 534 tests pass.
- **Version** bumped to 0.4.6

---

## [0.4.5] — 2026-03-20

### Fixed

- **Claim detection false positives on briefings** — The claim-without-action detector used single words ("logged", "saved") which triggered on descriptive briefing text like "meals logged 3 of last 7 days." Now uses phrase patterns ("I've logged", "logged your") that only match first-person storage claims. Eliminates false positives on morning briefings, debriefs, and nutrition summaries while still catching real claim-without-action violations.

### Changed

- **Version** bumped to 0.4.5

---

## [0.4.4] — 2026-03-20

### Comprehensive test suite (v0.4.4)

Added 532 tests across 41 test files covering every module in the codebase.

### Added

- **Unit tests (423 tests, 31 files)** — All stores, daemon endpoints, action processing, context building, Claude session protocol, delivery routing, task lifecycle, TTS pipeline, WebSocket STT, tick.py jobs, fuzz testing, concurrency, and real-world input edge cases. All external I/O (database, Claude CLI, SMS, phone push, HTTP APIs) is mocked for safety.
- **Integration tests (103 tests, 10 files)** — Real SQL execution against a disposable `aria_test` PostgreSQL database. Tests every store's CRUD operations, JSONB handling, dynamic SQL aggregation, cross-module data flow, and migration idempotency.
- **Property-based fuzz tests** — Hypothesis-powered fuzzing of ACTION block parsing to verify it never crashes on arbitrary input.
- **Test infrastructure** — `tests/conftest.py` with safety guards, `tests/helpers.py` data factories, `tests/integration/conftest.py` with automatic test database lifecycle (create/truncate/drop).

### Fixed

- **migrate.py NOT NULL violation** — All 7 migrate functions explicitly passed `NULL` for the `created` column when the source JSON lacked the field, violating the `NOT NULL` constraint. Now uses `COALESCE(%s, NOW())` to fall back to current timestamp.

### Changed

- **Version** bumped to 0.4.4

---

## [0.4.3] — 2026-03-20

### Third code audit cleanup: 4 fixes (v0.4.3)

Final batch of fixes from the second comprehensive audit.

### Fixed

- **Nutrition claim-without-action false positive** (M17) — The system note "ARIA claimed to store data but no ACTION blocks were emitted" no longer fires when Claude merely reports existing nutrition data. The nutrient-terms heuristic now only triggers when storage-claim words ("logged", "saved", etc.) are also present.
- **"Leave" location trigger now works** (S12) — Location reminders with `location_trigger: leave` now fire when the user departs from the target location. Uses tick_state to track per-reminder presence, detecting arrive→depart transitions between ticks.
- **Morning briefing limited to once per day** (m15) — Saying "good morning" after the first briefing now falls through to normal context. Explicit re-requests ("morning briefing again", "repeat the briefing") still trigger a full briefing.
- **Removed unused piper-tts** (m14) — `piper-tts==1.4.1` removed from requirements.txt (Kokoro replaced Piper).

### Changed

- **Version** bumped to 0.4.3

---

## [0.4.2] — 2026-03-20

### Second code audit: 9 bug fixes (v0.4.2)

Second comprehensive audit of all 23 source files. Fixed 6 significant and 3 moderate bugs found during the review.

### Fixed

- **ACTION block markup leaks into responses** (S9) — `re.sub` was missing `re.DOTALL` flag (the `re.findall` had it since v0.3.9 but the stripping sub was missed). Multiline ACTION blocks now fully stripped from spoken/texted responses. Also fixed in nudge endpoint.
- **Location reminders lost during quiet hours** (S11) — `complete_reminder()` was called regardless of whether SMS was sent. Now only completes on successful delivery; retries next tick otherwise.
- **Ghost exercise sessions** (S10) — `start_exercise()` now deactivates any existing active session (`end_reason = 'superseded'`) before creating a new one.
- **Fitbit snapshot null overwrite** (S13) — Failed data type fetches no longer set keys to null. Null values are also filtered in `save_snapshot()` as defense-in-depth, preventing JSONB merge from overwriting good data.
- **Claude subprocess orphaned on shutdown** (M16) — Lifespan now calls `_claude_session._kill()` before closing the DB pool.
- **File upload delivery routing** (S8) — `_process_file_task` now supports `set_delivery` ACTION blocks. SMS delivery sends text and skips TTS entirely (no voice output when SMS was requested). Same fix applied to `_process_voice_task`.
- **SMS from any phone number** (M13) — SMS webhook now rejects messages from non-owner phone numbers (STOP/HELP remain open for A2P compliance).
- **ACTION failure replaces entire response** (M2) — Error notice now appended to response instead of replacing it, preserving Claude's conversational answer.
- **tick.py error isolation** (M14) — Each cron job now wrapped in its own try/except. One job failure no longer blocks timers, location reminders, exercise coaching, Fitbit polling, or nudges.

### Changed

- **Delivery routing returns no audio for SMS** — When `set_delivery` routes to SMS, task completes with empty audio and `"delivery": "sms"` in status response. Tasker can skip audio fetch/playback entirely.
- **Version** bumped to 0.4.2

---

## [0.4.1] — 2026-03-20

### Performance: async TTS, parallel fetches, health checks

Offloads TTS to thread pool so the event loop stays responsive. Parallelizes Fitbit and news fetches. Adds dependency health checks to `/health`.

### Changed

- **TTS no longer blocks the event loop** (S4) — `kokoro.create()` now runs via `asyncio.to_thread()` through a centralized `_generate_tts()` helper. Replaces 5 duplicate inline TTS blocks with one shared function.
- **Fitbit snapshot fetches run in parallel** (M4) — `fetch_daily_snapshot()` uses `asyncio.gather` for 8 API calls (~1.6s → ~200ms). Added `asyncio.Lock` on token refresh to prevent stampede when parallel requests all hit 401.
- **News feeds fetched in parallel** (M6) — `get_news_digest()` uses `asyncio.gather` for all RSS feeds (~3x faster morning briefings).
- **`/health` reports dependency status** (D3) — response now includes `checks` dict: database connectivity, Claude CLI process alive, TTS model loaded, Whisper model loaded (if enabled). Returns `"status": "degraded"` if database or Claude are down.
- **Version** bumped to 0.4.1

---

## [0.4.0] — 2026-03-20

### PostgreSQL Migration — All Data Stores

Migrated all 8 data stores, 3 log streams, and 2 state files from JSON/JSONL to PostgreSQL 17. Eliminates file locking race conditions (C3), reduces nutrition query I/O from 5+ file reads to 1 SQL query, and replaces full-file scans with indexed queries.

### Added

- **`db.py`** — Connection pool management for PostgreSQL (psycopg v3, sync connections, dict_row).
- **`schema.sql`** — 15 PostgreSQL tables with indexes on date, timestamp, and status columns.
- **`migrate.py`** — One-time migration script from JSON/JSONL to PostgreSQL. Idempotent (ON CONFLICT DO NOTHING).
- **FastAPI lifespan** — DB connection pool initializes on startup, closes on shutdown.

### Changed

- **All 8 stores** rewritten: `calendar_store`, `health_store`, `legal_store`, `vehicle_store`, `timer_store`, `nutrition_store`, `location_store`, `fitbit_store` — JSON `_load()`/`_save()` replaced with SQL queries.
- **timer_store** — each operation is now a single atomic SQL statement. No more read-modify-write race between daemon and tick.py.
- **fitbit_store** — daily snapshots stored as JSONB. `save_snapshot()` uses `ON CONFLICT DO UPDATE` for atomic merge. Exercise HR append uses JSONB `||` operator (atomic, no read-modify-write). `get_trend()` fetches all days in one query instead of 7 file reads.
- **nutrition_store** — `get_daily_totals()` uses SQL SUM aggregation (1 query, was 5+ file reads). `get_weekly_summary()` uses GROUP BY (1 query, was 14 file reads).
- **location_store** — removed in-memory `_latest` cache and JSONL append. `get_latest()` is now an indexed query.
- **daemon.py** — `log_request()` and `_get_today_requests()` use `request_log` table. SMS log uses `sms_log` table.
- **sms.py** — outbound SMS logging uses `sms_outbound` table.
- **tick.py** — `load_state()`/`save_state()` and cooldowns use PostgreSQL key-value tables.
- **config** — `DATABASE_URL` replaces 11 JSON file path constants.
- **Version** bumped to 0.4.0

### Removed

- JSON file path config constants: `CALENDAR_DB`, `REMINDERS_DB`, `VEHICLE_DB`, `HEALTH_DB`, `LEGAL_DB`, `NUTRITION_DB`, `TIMER_DB`, `FITBIT_DB_DIR`, `FITBIT_EXERCISE_FILE`, `TICK_STATE_FILE`, `NUDGE_COOLDOWNS_FILE`, `REQUEST_LOG`
- `_load()`/`_save()` boilerplate from all stores

### Fixed (post-migration review)

- **tick.py `process_exercise_tick()`** — still referenced removed `config.FITBIT_EXERCISE_FILE`. Replaced with atomic `UPDATE fitbit_exercise SET nudge_count = nudge_count + 1`.
- **Timezone regression** — `serialize_row()` returned timezone-aware ISO strings from TIMESTAMPTZ columns, breaking all `datetime.now() - parsed_timestamp` arithmetic. Fixed: strips to naive local time matching original JSON behavior.
- **Stale imports** — removed unused `json` from tick.py and fitbit_store.py, unused `config` from nutrition_store.py.

### Dependencies

- Added: `psycopg[binary]` 3.3.3, `psycopg_pool` 3.3.0
- Requires: PostgreSQL 17 with `aria` database and user

---

## [0.3.9] — 2026-03-19

### First Code Audit — 12 Bug Fixes

Comprehensive code review of the entire codebase. Found and fixed 3 critical bugs, 5 significant issues, and 4 minor issues. Full report in CODE_REVIEW.md.

### Fixed

- **Critical: task memory leak** — completed background tasks dropped their `created` timestamp, making them immune to the 2-hour expiry. Audio blobs accumulated in memory forever. Fixed by using `.update()` instead of dict replacement, and deleting tasks after audio is fetched.
- **Critical: ACTION block regex ignored multiline JSON** — `re.findall` without `re.DOTALL` silently skipped ACTION blocks where Claude formatted JSON across multiple lines. Added `re.DOTALL` flag.
- **Unused `get_trend()` call in tick.py** — wasted 28+ JSON parse operations every nudge evaluation cycle. Removed.
- **Wrong docstring on `build_request_context`** — claimed it returned a tuple, actually returns a string.
- **Nutrition `on_track` display bug** — showed "Deficit: 0 cal" during a calorie surplus instead of reporting the surplus.
- **Unknown ACTION types silently ignored** — added `log.warning` for unrecognized action types in `process_actions()`.
- **Unused imports** — removed `Counter` from health_store.py, `timedelta` from vehicle_store.py.
- **Inline `import re`** — consolidated 4 inline imports in daemon.py to a single top-level import.
- **Hardcoded diet start date** — `date(2026, 3, 17)` duplicated in 4 places, moved to `config.DIET_START_DATE`.
- **Hardcoded age in exercise HR zones** — `age = 42` replaced with computation from `config.OWNER_BIRTH_DATE`.
- **Dead config** — removed unused `PHONE_IMAGE_DIR` from config.example.py.

### Changed

- **Aria now uses Opus 4.6** with auto effort level (was Sonnet with medium effort).
- **Version** bumped to 0.3.9

---

## [0.3.8] — 2026-03-19

### Whisper STT Integration — Phase 4.3 Keystone

Local speech-to-text on the RTX 3090 via faster-whisper with large-v3-turbo model. Three new endpoints for batch, combined voice pipeline, and real-time streaming transcription. Also fixes delivery routing, nutrition data bugs, and empty SMS errors.

### Added

- **`whisper_engine.py`** — Whisper STT engine with lazy model loading, thread-safe GPU access, energy-based VAD for streaming, sample rate conversion
- **`POST /stt`** — batch audio transcription endpoint. Any format, returns text + timestamped segments. ~0.25s for 3s audio on warm model.
- **`POST /ask/voice`** — combined STT + Claude + TTS. Audio in, audio out. One round trip. Transcript available in `/ask/status` while Claude processes.
- **`WebSocket /ws/stt`** — real-time streaming transcription. Client streams PCM chunks, server returns transcripts per utterance via VAD. ~700ms latency after speech ends.
- **`set_delivery` ACTION block** — Aria emits this when user requests a specific delivery method (voice/SMS). Handler routes accordingly — generates TTS + push_audio for voice delivery, sends SMS for text. Replaces unreliable push_audio.py shell command approach.
- **`_get_context_for_text()` helper** — single source of truth for briefing/debrief detection and context routing. Replaces 3 duplicate code blocks.
- **Transcript in `/ask/status`** — voice tasks show `{"status": "processing", "transcript": "..."}` so clients can display what was heard while Claude processes.

### Fixed

- **Nutrition data zeroed out** — all 15 pantry entries had `servings=0`, zeroing all totals and making nutrition data invisible to Aria. Added guard in `nutrition_store.add_item()`: servings ≤ 0 defaults to 1.0. Repaired existing data with correct per-use serving sizes.
- **Voice delivery via SMS unreliable** — Aria sometimes ignored "respond via voice" instructions (~50% compliance). Root cause: push_audio.py usage was a passive tool description, not a mandatory rule. Fixed with `set_delivery` ACTION block — delivery routing is now handler-enforced, not Claude-dependent.
- **Empty SMS body → Twilio 400** — when Claude consumed the response text via push_audio shell command, empty string was passed to `sms.send_sms()`. Added empty body guard in `_process_sms`.
- **Outbound SMS silently failing** — all outbound SMS blocked by A2P 10DLC carrier filtering (error 30034). Twilio API returns SID (appears successful) but carrier drops the message. No code fix needed (A2P registration pending), but voice delivery routing now works as the fallback channel.

### Changed

- **System prompt** — delivery routing section replaces passive push_audio tool description. `set_delivery` ACTION block is mandatory when user requests specific delivery method.
- **Version** bumped to 0.3.8

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
