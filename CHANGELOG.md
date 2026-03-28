# Changelog

All notable changes to ARIA are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: major phases = minor version bumps.

---

## [0.8.0] — 2026-03-28

### Added — Phase 4b: Gmail + Google Calendar Intelligence

- **`gmail_store.py`** — Full email cache with tsvector full-text search, classification storage, context builders for ARIA injection, attachment download, From header parsing, Gmail category detection
- **`gmail_strategy.py`** — Three-tier email classification engine: Tier 1 hard rules (YAML sender/domain/content), Tier 2 pattern scoring (Gmail category, domain reputation, promo patterns, reply history, entity DB), Tier 3 AI judgment (Haiku for uncertain emails)
- **`monitors/gmail.py`** — GmailMonitor: classifies unclassified emails from email_cache, produces Finding objects for important/urgent/actionable emails feeding existing delivery pipeline
- **`data/gmail_rules.yaml`** — Empty template strategy file for interactive classification session
- **`email_cache` table** — Full email bodies with tsvector GIN index on subject+body, attachment_paths array, gmail_category
- **`email_classifications` table** — Classification audit trail with tier, confidence, reason, user_override
- **`calendar_sync_state` table** — Singleton row for Google Calendar incremental syncToken
- **Google Calendar bidirectional sync** — `calendar_store.py` now writes to Google first then local, with offline resilience (google_id=NULL, synced next cycle). `sync_from_google()` for incremental sync. Google wins conflicts.
- **`send_email` ACTION block** — ARIA can compose and send email replies via Gmail API (confirmation required — never auto-sends)
- **Email context injection** — Keyword-triggered (email, inbox, mail, gmail) with unread important summary, morning briefing and evening debrief email sections
- **`/email/search` endpoint** — Full-text email search via daemon
- **`query.py email` subcommand** — CLI email search: `--search`, `--from`, `--days`, `--limit`

### Changed — Unified Delivery Pipeline (tick.py overhaul)

- **`run_unified_delivery()`** replaces separate `run_nudge_evaluation()` + `deliver_findings()` — nudges and monitor findings are now grouped into a single composed message and count as one delivery against unified caps
- **ABC category system** — `classify_category()` in monitors/__init__.py assigns each nudge/finding to: A (briefing-only, suppressed from independent delivery), B (first-of-day delivers, subsequent only group with C items), C (always delivers when cooldown allows)
- **`process_safety_net()`** — 11:50pm catch-all: if no briefing/debrief was delivered today, composes and sends a summary of Category A items that would otherwise be lost
- **Tick heartbeat** — `last_tick_run` written to tick_state at the top of every tick, proving cron is running regardless of whether any jobs fire
- **MAX_NUDGES_PER_DAY** increased from 6 to 15 (unified cap covers both nudges and findings)
- **Minimum delivery interval** — unified deliveries respect `MONITOR_DELIVERY_MIN_INTERVAL_MIN` between sends
- **`deliver_findings()` removed** — findings now flow through the unified delivery pipeline

### Changed — Other

- **`process_actions()` is now async** — calendar and email handlers await Google API calls. All 7 call sites updated (daemon.py, verification.py, completion_listener.py)
- **`calendar_store.py` write methods now async** — `add_event()`, `modify_event()`, `delete_event()` write to Google Calendar first, with offline fallback
- **Events table** gains `google_id`, `google_etag`, `last_synced` columns for Calendar sync
- **Calendar sync interval** changed from 5 min to 15 min (incremental via syncToken)
- **Gmail sync** now fetches full bodies (format=full) with MIME body extraction
- **`google_store.py` deleted** — functions split into `gmail_store.py` and `calendar_store.py`
- **OAuth scopes** — `gmail.modify` replaces `gmail.readonly` + `gmail.send`; `calendar.events` replaces `calendar.readonly`
- **Version** bumped to 0.8.0
- **Total test count:** 1926 tests across 86 files, all passing

---

## [0.7.0] — 2026-03-28

### Added — Phase 4a: Google Calendar + Gmail API Plumbing

- **`google_auth.py`** — One-time OAuth2 PKCE authorization flow for Google Calendar + Gmail. Same paste-URL UX as fitbit_auth.py. Saves tokens to `data/google_tokens.json`
- **`google_client.py`** — Async Google API client via raw httpx (no google-api-python-client). Auto-refresh on 401 with lock-protected stampede prevention. Preserves refresh_token across refreshes (Google doesn't rotate). Calendar events list/get, Gmail messages list/get/fetch_recent with parallel gather
- **`google_store.py`** — PostgreSQL-backed cache for synced Google data. Calendar events upserted with extracted fields + JSONB raw data. Gmail messages with header extraction. Handles both timed and all-day calendar events
- **`google_calendar_events` table** — cached calendar events with start_time index
- **`google_gmail_messages` table** — cached Gmail metadata with date index, label_ids array
- **`/google/calendar/sync` endpoint** — manual trigger, fetches next 7 days of events
- **`/google/gmail/sync` endpoint** — manual trigger, fetches last 24 hours of messages
- **tick.py Google polling** — Calendar every 5 min, Gmail every 3 min, skips quiet hours
- **45 new tests** across 3 test files (google_auth, google_client, google_store)
- **Config entries** — `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_TOKEN_FILE`, `GOOGLE_SCOPES`

### Fixed

- **Gmail RFC2822 date parsing** — Gmail Date headers include parenthetical comments like `(UTC)` that PostgreSQL cannot parse as TIMESTAMPTZ. Added `_parse_email_date()` using Python's `email.utils.parsedate_to_datetime` to handle all RFC2822 variants
- **Gmail rate limiting** — `gmail_fetch_recent()` used unbounded `asyncio.gather` which hit Google's 429 rate limit with 39+ parallel requests. Added `Semaphore(5)` to limit concurrency

### Changed

- **Version** bumped to 0.7.0
- **Total test count:** 1903 tests across 85 files, all passing

---

## [0.6.1] — 2026-03-28

### Fixed — Code Review Audit

- **C1: Delivery hint now flows through `_process_task`** — refactored to run pipeline directly instead of calling `ask()`, exposing `delivery_meta` with `set_delivery` hint. Also eliminates double auth check (L6)
- **C2: ACTION block stripping uses balanced-brace spans** — `_extract_action_blocks()` returns span positions used for both extraction and stripping, eliminating regex mismatch where JSON values containing `-->` would break the non-greedy cleaner
- **C3: Single `get_user_state()` per delivery decision** — `evaluate()` and `log_decision()` accept optional `_state` parameter; `execute_delivery()` calls `get_user_state()` once and reuses it
- **C4: UUID temp WAV files prevent race conditions** — all voice delivery paths (daemon, tick, deferred) use unique filenames with cleanup
- **M1: Shared `execute_delivery()` in delivery_engine.py** — replaces ~120 lines of duplicated delivery routing across 5 handlers with single function handling voice/sms/image/defer
- **M2: `processed_webhooks` table auto-cleanup** — new `cleanup_processed_webhooks()` job in tick.py, 7-day retention, index on `processed_at`
- **M3: Redis task hashes expire after 24h** — TTL set on completion in `complete_task()`
- **M4: Fixed SQL interval parameterization in training_store.py** — `INTERVAL '%s days'` → `%s * INTERVAL '1 day'`
- **M5: Fitbit briefing context reduced from 8 to 1 DB query** — single `get_snapshot()` call, internal `_*_from_snap()` helpers extract all fields from the in-memory JSONB blob
- **M6: Monitor cooldown keyed on check names, not error text** — prevents slightly different error messages from bypassing cooldown
- **M7: `fire_timer()` handles all delivery methods** — added explicit image and defer branches (previously silently treated as SMS)
- **L1: Dead `_marker_count` line removed** from actions.py
- **L2: Unused `psycopg.types.json` import removed** from delivery_engine.py
- **L4: Timestamp conversion simplified** in conversation_history.py — direct formatting instead of roundabout `serialize_row` trick
- **L5: File task context builder documented** — comment explains intentional use of `build_request_context` (not briefing path)
- **L9: Index added on `processed_webhooks(processed_at)`**
- **Shared `_sync_deliver()` in tick.py** — replaces 3 separate sync delivery dispatch blocks (fire_timer, process_deferred_deliveries, send_exercise_nudge) with one function. Fixes missed C4 bug in `send_exercise_nudge` (hardcoded `exercise_audio.wav`)
- **15 tests for `execute_delivery()`** — voice push/no-push, SMS fallback, image render, defer queue, TTS failure, state reuse verification
- **8 tests for `_sync_deliver()`** — voice/SMS/image paths, fallback behavior, unknown methods

## [0.6.0] — 2026-03-28

### Added — Phase 1: Agent System Foundation

- **Domain monitor framework** (`monitors/` package) — pure Python + SQL monitors that produce structured `Finding` objects with fingerprint deduplication, TTL-based expiry, and delivery pipeline
- **5 domain monitors**: health (NAFLD biomarker trends, nutrient compliance), fitness (HR/HRV/sleep trends, step averages), vehicle (maintenance interval tracking), legal (graduated 7d/3d/1d/overdue deadline warnings), system (disk usage, log sizes, cron freshness, GPU temp, Portage sync freshness)
- **Portage sync monitor** — tracks last `emerge --sync` on beardos and slappy, warns at 14d (normal) and 30d (urgent)
- **Finding delivery pipeline** in tick.py — composes via Haiku, delivers via SMS/image, independent frequency caps from nudges
- **Context injection** — undelivered findings in Tier 1 (always visible), recent findings in briefing/debrief
- **`monitor_findings` table** with fingerprint deduplication indexes

### Added — Phase 3: Response Verification Pipeline

- **`ActionResult` dataclass** — replaces string return from `process_actions()` with structured data (clean_response, actions_found, action_types, failures, warnings, claims_without_actions, expect_actions_missing). Backward-compatible via `to_response()`, `__contains__`, `__str__`, `lower()`
- **`verification.py`** — claim extraction and verification engine with action claim retry loop (max 2 retries), date claim detection, numeric calorie claim checking against nutrition_store
- **Context window management** in session_pool.py — tracks estimated context bytes per session, proactively recycles at ~62% of 200K window (~125K tokens), enhanced history injection with Haiku-generated summary of older turns + 10 verbatim recent turns
- **`verification_log` table** — logs all claim checks for LoRA training data

### Added — Phase 2: Delivery Intelligence Engine

- **`delivery_engine.py`** — pure decision function that evaluates user state (location, activity, time, device connectivity) and returns a `DeliveryDecision`. Safety overrides: never voice at work/court, defer during sleep
- **Forward-looking device support** — `device_state` table pre-seeded with phone, glasses (Even Realities G2), watch (Pixel Watch 4), mic (DJI Mic 3). Routing rules include all devices; falls back gracefully when devices aren't connected
- **Deferred delivery queue** — `deferred_deliveries` table stores content when delivery is deferred (sleeping, in court). tick.py re-evaluates user state every minute and delivers when appropriate. Expires after 12h
- **`delivery_log` table** — logs all delivery decisions with user state snapshot
- **All delivery paths** route through the engine: daemon workers (voice, file, SMS, task), tick.py (timers, findings, nudges), completion_listener (task completions)
- Image delivery branch added to all workers (renders response as image, pushes to phone)

### Changed

- **`process_actions()` return type** changed from `str` to `ActionResult` — all 5 call sites updated
- **Session pool** now tracks context bytes and recycles proactively
- **Version** bumped to 0.6.0
- **Total test count:** 1836 tests across 82 files, all passing

---

## [0.5.4] — 2026-03-28

### Added

- **Production data replay tests (Tier 3)** — 7 new test files with ~229 tests replaying real production data through code paths:
  - `test_production_fitbit_replay.py` (37 tests) — all 9 real Fitbit snapshots through every extraction function
  - `test_production_nutrition_replay.py` (42 tests) — all 56 real nutrition entries through validation, aggregation, context
  - `test_production_request_replay.py` (39 tests) — real request_log inputs through context/action pipelines
  - `test_production_health_nutrition_integrity.py` (19 tests) — cross-store data integrity checks
  - `test_production_edge_inputs.py` (39 tests) — real edge-case user inputs
  - `test_production_context_replay.py` (19 tests) — context builders with real multi-store state
  - `test_future_phase_contracts.py` (34 tests) — forward-looking API contracts for Phases 1-3
- **Production data fixtures** in `tests/integration/fixtures/` — sanitized exports of Fitbit snapshots, nutrition entries, health entries, request_log samples, and locations

### Discovered (data quality issue)

- **Combo egg entry missing choline** — "Large coffee + 2 hard-boiled eggs + smoothie" has cholesterol_mg=372 (correct for 2 eggs) but choline_mg is completely absent from the JSONB. Eggs have ~147mg choline each (294mg total), critical for NAFLD tracking. This is a data issue (Claude omitted choline when logging the combo meal), not a code bug.

### Changed

- **Version** bumped to 0.5.4
- **Total test count:** 1779 tests across 77 files, all passing

---

## [0.5.3] — 2026-03-28

### Fixed

- **S14: ACTION blocks inside code fences no longer executed** — `_extract_action_jsons()` now strips triple-backtick code blocks before scanning for ACTION markers. Claude can safely demonstrate ACTION block syntax in code examples.
- **S15: Nested `-->` in ACTION JSON no longer truncates** — Replaced non-greedy `.*?` regex with balanced-brace parser (`_extract_action_jsons()` in actions.py). JSON values containing `-->` (e.g., timer messages with ACTION-like markup) are now parsed correctly.
- **Malformed ACTION marker detection** — When balanced-brace parser can't extract a marker (malformed JSON, unterminated strings), the failure is now reported instead of silently dropped.

### Changed

- **Version** bumped to 0.5.3

---

## [0.5.2] — 2026-03-28

### Added

- **Adversarial testing** (`test_pipeline_adversarial.py`) — 81 tests simulating real-world failure conditions: STT mishearing/mangling (15 tests), ACTION block injection attempts (10), temporal edge cases spanning midnight/DST/year boundaries (10), data integrity under stress (9), config edge cases (5), malformed Claude responses (11), SMS adversarial inputs (5), cross-cutting edge cases (16).

### Discovered (unfixed — documented in tests)

- **BUG: ACTION blocks inside code fences are executed** — If Claude demonstrates an ACTION block inside triple backticks, the regex still extracts and executes it. Documented in `test_pipeline_adversarial.py::TestACTIONInjection::test_action_inside_code_block_still_extracted`.
- **BUG: Nested `-->` truncates outer ACTION block** — When a JSON value inside an ACTION block contains `-->`, the non-greedy `.*?` regex stops at the inner `-->`, truncating the outer JSON. The action silently fails to parse. Documented in `test_pipeline_adversarial.py::TestACTIONInjection::test_nested_action_not_double_executed` and `TestDataIntegrityStress::test_timer_message_containing_action_markup`.

### Changed

- **Version** bumped to 0.5.2

---

## [0.5.1] — 2026-03-28

### Added

- **Pipeline testing suite** — 16 new integration test files (`test_pipeline_*.py`) with **~466 tests** exercising real code paths against a real PostgreSQL test database. Covers:
  - **Regression tests** for all 18 historical production bugs (Bug #1–#18)
  - **Boundary value tests** for every `>`, `>=`, `<`, `<=` comparison at exact boundary values
  - **Null propagation tests** verifying every Optional return through its callers
  - **ACTION pipeline tests** for all 21 action types against real DB
  - **Temporal tests** with `freezegun` for midnight races, timer computation, quiet hours, date validation
  - **Invariant tests** (properties that must always hold: markers stripped, types correct, etc.)
  - **Hypothesis fuzz tests** across process_actions, nutrition validation, entity extraction, Fitbit data shapes
  - **Contract tests** verifying cross-module return shapes and API agreements
  - **Type safety tests** for every unsafe int()/float() cast with string/None/garbage inputs
  - **Encoding tests** (unicode, emoji, multi-byte through every data path)
  - **Capacity tests** (100 timers, 50 reminders, 1000 nutrition entries, 50KB response regex safety)
  - **Concurrency tests** (advisory locks, content_hash dedup, webhook idempotency)
  - **Schema consistency tests** (schema.sql vs Python code agreement)
- **`_block_real_phone_push` safety guard** in tests/conftest.py — prevents `push_image` and `push_audio` from reaching the phone during tests
- **`freezegun` dependency** for deterministic time control in temporal tests

### Fixed

- **Partial ACTION marker leak** — `process_actions()` now strips incomplete `<!--ACTION::` markers (without closing `-->`) from response text. Previously, truncated markers would leak into spoken responses. Found by Hypothesis fuzz testing.
- **psycopg ConnectionPool deprecation warning** — added explicit `open=True` parameter (v0.5.0 fix for db.py, now also in integration conftest)
- **Nutrition test date time bomb** — hardcoded `2026-03-20` dates replaced with dynamic `date.today()` in test_nutrition_store.py

### Changed

- **Version** bumped to 0.5.1

---

## [0.5.0] — 2026-03-27

### Added

- **Session Pool (`session_pool.py`)** — managed pool of persistent Claude CLI sessions replacing the Anthropic API as ARIA Primary. Two sessions: deep (Opus, max effort) for complex queries, fast (Opus, auto effort) for simple queries (timers, greetings, weather). Auto-recycling, crash recovery, cross-session history injection via `conversation_history.get_recent_turns()`.
- **CLI data access (`query.py`)** — replaces 6 API tool definitions with a CLI-callable script. ARIA's sessions query data stores via `./venv/bin/python query.py <subcommand>` during response generation. Self-reports invocations to `tool_traces` table for LoRA training data.
- **Training data collection (`training_store.py`)** — `log_tool_trace()`, `log_entity_mention()`, `log_interaction_quality()` for future LoRA training and Neo4j knowledge graph. Entity extraction via regex (person names, known places, topic categories).
- **`ask_haiku()`** in `aria_api.py` — Haiku model for system-internal composition (nudge messages, task completion summaries). Fast, cheap, no tools/thinking needed.
- **`_route_query()`** in `daemon.py` — routes queries through session pool with automatic API fallback on failure.
- 3 new database tables: `tool_traces`, `entity_mentions`, `interaction_quality` with indexes.
- `_block_real_subprocess` safety guard in test conftest — prevents accidental real Claude CLI spawning in unit tests.
- **84 new tests** across 4 new test files (`test_session_pool.py`, `test_query.py`, `test_training_store.py`, `TestAskHaiku` in `test_aria_api.py`).

### Changed

- **ARIA Primary**: switched from Anthropic Messages API to CLI session pool. API kept as automatic fallback.
- **`/nudge` endpoint**: now uses Haiku instead of Opus for message composition.
- **`completion_listener.py`**: switched from `ask_aria` (Opus API) to `ask_haiku` for task completion composition.
- **`/health` endpoint**: now checks `session_pool` status instead of API client. API reported as `api_fallback`.
- **System prompt**: DATA ACCESS section updated from API tool descriptions to `query.py` CLI usage instructions.
- `AMNESIA_POOL_SIZE` default reduced from 3 to 1 in `config.example.py`.
- **Version** bumped to 0.5.0.

---

## [0.4.46] — 2026-03-27

### Added

- **`get_breathing_rate_summary()`**, **`get_temperature_summary()`**, **`get_vo2max_summary()`** — three new extraction functions in `fitbit_store.py` for breathing rate, skin temperature, and VO2 Max. Follows the same pattern as the existing 5 summary functions: `get_snapshot()` + `_safe_float()` casting + typed dict return. Completes the extraction boundary — no raw JSONB values leak past the summary layer.
- **Late-night debrief rule in system prompt** — second shift past-midnight "good night" handling moved from deleted `data/projects/aria.md` into `system_prompt.py` with explicit 12am-6am time boundary.
- **9 new tests** for the three summary functions (normal values, missing data, string casting).

### Removed

- **`data/projects/aria.md`** — stale project brief, redundant with system prompt and MEMORY.md. The one unique rule (late-night debrief) was moved to `system_prompt.py`.

### Changed

- **`get_briefing_context()`** — now calls the three new summary functions instead of accessing raw snapshot JSONB directly. All 8 Fitbit data types now go through the typed extraction boundary.
- **Version** bumped to 0.4.46

---

## [0.4.45] — 2026-03-27

### Added

- **`_cleanup_expired_tasks()` helper** in `daemon.py` — removes tasks older than 2 hours. Called from 5 endpoints (`ask_start`, `ask_file`, `ask_voice`, `ask_status`, `ask_result`) to prevent unbounded `_tasks` dict growth from unpolled tasks.
- **4 new tests** for task cleanup: old entries removed, entries without `created` preserved, empty dict safe, cleanup runs on status poll.

### Fixed

- **Task memory leak** — `_tasks` dict only cleaned when `/ask/result` was polled. Unpolled tasks (Tasker crash, network drop) accumulated forever. Now cleaned on every task creation and status poll.
- **Stale version in `/health`** — daemon.py now imports `__version__` from `version.py` instead of hardcoding. `/health` endpoint reports correct version.

### Changed

- **Version** bumped to 0.4.45

---

## [0.4.44] — 2026-03-27

### Added

- **`_get_diet_day()` helper** in `context.py` — safely computes diet day number, returns `None` if `DIET_START_DATE` is empty, missing, or invalid. Replaces 4 crash-prone inline `date.fromisoformat()` calls (3 in context.py, 1 in tick.py).
- **5 new tests** for `_get_diet_day`: valid date, empty string, missing attribute, invalid string, future date.

### Fixed

- **DIET_START_DATE crash** — empty/missing config value caused `ValueError` in `gather_health_context()`, `gather_debrief_context()`, `gather_briefing_context()`, and `evaluate_nudges()`. The tick.py crash was the worst: it silently killed ALL nudges (meal reminders, calendar warnings, battery alerts, etc.).
- **Midnight race in tick.py** — `get_daily_totals()` and `get_net_calories()` now receive explicit `today` parameter instead of defaulting to `date.today()` internally, preventing microsecond divergence at midnight.

### Changed

- **Version** bumped to 0.4.44

---

## [0.4.43] — 2026-03-27

### Fixed

- **Double exercise state DB queries** — `get_exercise_coaching_context()` now accepts optional `state` parameter. All 3 callers (`gather_always_context`, `gather_health_context`, `process_exercise_tick`) pass their already-fetched state, eliminating a redundant DB query per request during exercise mode.

### Changed

- **Version** bumped to 0.4.43

---

## [0.4.42] — 2026-03-27

### Added

- **`get_resting_hr_history()`** — single-query function in `fitbit_store.py` replaces 7 sequential `get_heart_summary()` calls in `tick.py`'s HR anomaly detection. Casts values via `_safe_int` per CLAUDE.md external API rule.
- **4 new tests** for `get_resting_hr_history`: int values, string casting, missing data, empty snapshots.

### Changed

- **tick.py HR anomaly check** — uses `get_resting_hr_history(days=7)` instead of per-day loop (7 DB queries → 1).
- **Version** bumped to 0.4.42

---

## [0.4.41] — 2026-03-27

### Fixed

- **Exercise auto-expire via SQL** — `get_exercise_state()` now expires stale sessions (>90 min) via SQL `UPDATE ... WHERE started_at < NOW() - INTERVAL '90 minutes'` instead of Python datetime comparison. Eliminates fragile aware/naive datetime mixing. The UPDATE is a no-op when no rows match.

### Changed

- **Version** bumped to 0.4.41

---

## [0.4.40] — 2026-03-27

### Added

- **`version.py`** — single source of truth for ARIA runtime version string. Eliminates stale hardcoded versions.
- **Fitbit API type safety** — `_safe_int()` and `_safe_float()` helpers in `fitbit_store.py` cast all numeric values at the extraction boundary. Applied to all 7 summary/extraction functions (`get_sleep_summary`, `get_heart_summary`, `get_hrv_summary`, `get_activity_summary`, `get_spo2_summary`, `get_briefing_context`, `get_trend`). Prevents `TypeError`/`ValueError` crashes when Fitbit API returns strings instead of ints (documented in CLAUDE.md, previously only fixed for `sedentaryMinutes`).
- **9 new tests** for safe casting: unit tests for helpers, string-input scenarios for all summary functions and trend.

### Fixed

- **Removed orphaned `test_migrate.py`** — integration test for deleted `migrate.py` (removed in v0.4.39 cleanup).

### Changed

- **Version** bumped to 0.4.40

---

## [0.4.39] — 2026-03-27

### Fixed

- **Test suite leaking real push_image to phone** — 4 tests in `test_tts_pipeline.py` called `_prepare_for_speech()` with ACTION block text without mocking `push_image` at the module level. The local `import push_image` inside `tts.py`'s function body bypassed test mocks, causing real HTTP POSTs to the phone on every test run (~4 "ARIA BUG" alert images per run). Plus 1 test in `test_daemon_actions.py` leaked via `_push_data_quality_alert()`. All 5 now have proper `@patch("push_image.push_image")` decorators.

### Changed

- **Version** bumped to 0.4.39

---

## [0.4.38] — 2026-03-27

### Added

- **Monitor PostgreSQL state** — `monitor.py` cooldown state migrated from JSON file (`monitor_state.json`) to PostgreSQL `monitor_state` table. Fixes architecture violation (all data stores must use PostgreSQL).
- **Monitor quiet hours** — Non-critical alerts (redis, backup, peer, restore) suppressed during hours 0-7. Critical alerts (daemon, postgres) always fire. Uses `QUIET_HOURS_START`/`QUIET_HOURS_END` from config.
- **Monitor delivery-gated cooldown** — `push_alert()` returns bool; cooldown timestamp only updated on successful delivery. Failed deliveries allow retry on next cycle.
- **Monitor state cleanup on every run** — Stale cooldown entries (>24h) cleaned up regardless of whether an alert was sent.
- **SMS redirect failure tracking** — `_redirect_to_image()` now uses `IMG_FAIL_` SID prefix when `push_image()` fails, making failures distinguishable from successes in `sms_outbound` audit trail.
- **26 new tests** in `tests/test_monitor.py` — quiet hours suppression, critical bypass, cooldown on delivery success/failure, PostgreSQL state load/save, cleanup, push_alert return values.

### Changed

- **Version** bumped to 0.4.38

---

## [0.4.37] — 2026-03-27

### Added

- **Zombie reminder auto-expiry** — Reminders overdue by 3+ days are auto-expired (marked done with `auto_expired_at` timestamp). Prevents the immortal reminder bug that spammed 10+ "head out for movers" messages in one day.
- **Nudge audit log** — All nudge attempts (sent, compose_failed, delivery_failed, suppressed) written to `nudge_log` table with timestamps, types, and delivery status.
- **Global nudge frequency cap** — Max 6 nudges/day and 2 nudges/hour, queried from `nudge_log`. Prevents the 14-messages-in-one-day flood.
- **Temporal context for Claude** — All time-sensitive triggers include `(current time: HH:MM AM/PM)`. The `/nudge` prompt tells Claude the current time and instructs it NOT to tell the user to "head out" for past events.
- **Advisory lock for nudge evaluation** — `pg_try_advisory_xact_lock(42)` prevents race conditions between concurrent tick instances evaluating nudges simultaneously.
- **Exercise coaching rate limiting** — Minimum 3-minute average interval between exercise nudges.

### Fixed

- **Cooldown error handling** — Cooldowns now ONLY update after successful delivery. API failures and SMS delivery failures no longer silently update cooldowns (which was hiding message loss).
- **Meal gap check wrong table** — Now uses `nutrition_store.get_items()` instead of `health_store.get_entries()`. No more "no meals logged" when nutrition IS logged.
- **Timer/reminder completion ordering** — Timers and location reminders are marked complete BEFORE delivery, preventing retry storms on transient SMS failures.
- **Silent ValueError swallowing** — Calendar event time parsing errors now logged as warnings instead of silently ignored.

### Changed

- **reminder_due cooldown** — Increased from 2 hours to 12 hours (overdue reminders don't need to ping more than twice a day)
- **Version** bumped to 0.4.37

---

## [0.4.36] — 2026-03-27

### Added

- **Webhook idempotency** — SMS webhook checks `processed_webhooks` table for Twilio `MessageSid` before processing. If already seen, returns empty TwiML immediately. Prevents duplicate entries when Twilio retries on timeout.

### Changed

- **Version** bumped to 0.4.36

---

## [0.4.35] — 2026-03-27

### Added

- **Date field in log_nutrition ACTION template** — `system_prompt.py` now includes `"date": "YYYY-MM-DD"` in the log_nutrition template (was missing, causing wrong dates when logging after midnight). Instruction added: date is REQUIRED, must match between log_health and log_nutrition.
- **Pre-validation in process_actions()** — Three-phase pipeline: parse → validate → execute. Catches issues BEFORE any database writes:
  - Intra-response dedup: duplicate `log_nutrition` blocks with same `(food_name, date, meal_type)` are removed before execution
  - Date cross-check: `log_health` and `log_nutrition` for the same meal must have matching dates — mismatch aborts ALL actions
- **LOUD data quality alerts** — Validation failures push alert images to the phone immediately so problems are never hidden

### Changed

- **Version** bumped to 0.4.35

---

## [0.4.34] — 2026-03-27

### Added

- **Transaction support in db.py** — `get_transaction()` context manager for multi-statement atomic operations. Existing `get_conn()` unchanged for backward compat.
- **Content hash dedup in stores** — `nutrition_store.add_item()` and `health_store.add_entry()` compute SHA-256 content hashes, INSERT with `ON CONFLICT DO NOTHING`, and return `{"inserted": True/False, "duplicate": True/False, "entry": {...}}`.
- **Nutrition entry validation** — `add_item()` validates date range (within 7 days), nutrient sanity bounds (calories 0-5000, sodium 0-10000, etc.), servings range (0-20), non-empty food name. Raises `ValueError` on violations.

### Changed

- **`entry_date` now REQUIRED** on `nutrition_store.add_item()` — no more silent default to today. Prevents wrong-date entries when logging yesterday's meals after midnight. `actions.py` falls back to today with a WARNING if Claude omits the date field.
- **Store return types** — Both `add_item()` and `add_entry()` now return structured dicts with `inserted`, `duplicate`, and `entry` keys instead of raw row dicts.
- **Version** bumped to 0.4.34

---

## [0.4.33] — 2026-03-27

### Added

- **Content hash dedup columns** — `content_hash` and `response_id` columns on `nutrition_entries` and `health_entries` with unique indexes. Prevents duplicate entries at the database level via `ON CONFLICT DO NOTHING`.
- **Nudge audit log** — `nudge_log` table tracks all nudge attempts (sent, failed, suppressed) with timestamps, trigger types, and delivery status. Enables global frequency caps and debugging.
- **Webhook idempotency table** — `processed_webhooks` table stores Twilio `MessageSid` to prevent duplicate processing on webhook retries.
- **Monitor state in PostgreSQL** — `monitor_state` table replaces `monitor_state.json` file (all stores must use PostgreSQL, not JSON files).
- **Reminder auto-expiry tracking** — `auto_expired_at` column on `reminders` table for zombie reminder cleanup.

### Fixed

- **Duplicate nutrition entries cleaned** — One-time cleanup removed 3 exact duplicate entries from Mar 26 (same meals logged 2-3x during a failed 3 AM SMS session). Backfilled content_hash on all 56 nutrition and 47 health entries.

### Changed

- **Schema** — 15 tables → 18 tables (added nudge_log, processed_webhooks, monitor_state). nutrition_entries and health_entries gain content_hash + response_id columns. reminders gains auto_expired_at.
- **Version** bumped to 0.4.33

---

## [0.4.32] — 2026-03-26

### Fixed

- **Subprocess readline buffer overflow** — Claude Code subprocesses (Action ARIA, Claude Session, Amnesia pool) had the default 64KB asyncio readline buffer, causing "Separator is found, but chunk is longer than limit" errors when reading images (3-6MB base64 = 4-8MB JSON lines). Increased to 16MB across all three subprocess spawners.
- **ARIA Primary model ID** — Fixed invalid model ID `claude-opus-4-6-20250610` (404 from API) → `claude-opus-4-6` (stable alias).
- **ARIA Primary max_tokens** — `max_tokens` (16384) was less than `thinking.budget_tokens` (64000), causing API 400 errors. Bumped to 80000.
- **Action ARIA model upgrade** — Changed from `--model sonnet` with effort=high to `--model opus` with effort=max for full capability on complex tasks.

### Changed

- **Version** bumped to 0.4.32

---

## [0.4.31] — 2026-03-25

### Fixed

- **ARIA no longer refuses shell/filesystem tasks** — Reworded task dispatch prompt from "You do NOT have direct shell/filesystem access" to "you can run shell commands, generate images, fetch web pages, read/write files... by dispatching to background workers." ARIA now correctly dispatches these tasks instead of telling the user she can't do them.

### Changed

- **Version** bumped to 0.4.31

---

## [0.4.30] — 2026-03-25

### Fixed

- **Channel-aware task completion delivery** — Completion listener now delivers task results via the same channel the request came in on (SMS→SMS, voice→voice). Channel threaded through the full dispatch pipeline: daemon handler → process_actions → dispatch_action → Redis task hash → completion listener. Default "voice" for backward compatibility with existing tasks.

### Changed

- **Version** bumped to 0.4.30

---

## [0.4.29] — 2026-03-25

### Fixed

- **ACTION blocks no longer spoken by TTS** — Completion listener now calls `process_actions()` on ARIA's response before delivery, executing legitimate ACTION blocks and stripping them from spoken text. Added defense-in-depth: `_prepare_for_speech()` in tts.py detects unprocessed ACTION blocks, logs a WARNING, pushes a bug alert image to phone, and strips them before TTS.
- **SMS redirect test safety** — Added autouse conftest fixture to disable `SMS_REDIRECT_TO_IMAGE` in tests, preventing redirect from interfering with Twilio code path testing.

### Changed

- **Version** bumped to 0.4.29

---

## [0.4.28] — 2026-03-25

### Changed

- **Prompt caching** — System prompt and tool definitions now use Anthropic's prompt caching (`cache_control: ephemeral`). Static content (~3,900 tokens) served at $0.50/MTok on cache hits instead of $5/MTok. Cache persists across tool-call rounds within a request, dramatically reducing cost on multi-round queries.
- **Conversation history reduced to 10 turns** — Down from 25. ARIA has `query_conversations` tool for anything beyond the rolling window. Saves ~4,200 tokens per request.
- **ACTION blocks stripped from history** — Processed ACTION blocks (often 500-1000+ chars each) are now removed from conversation history before sending to the API. They're already persisted in the database and accessible via tool calls.
- **History truncation reduced to 3000 chars** — Down from 4000. Most responses fall under 3000 chars.
- **Conditional extended thinking bypass** — Simple queries (timers, reminders, weather, greetings) skip the 64k thinking budget to save cost and latency. Two-tier keyword matching: starts-with for commands, exact-match for greetings. Defaults to thinking ON for everything else. `ARIA_ALWAYS_THINK` config flag to force thinking on all queries.
- **System prompt tightened** — Merged duplicate delivery routing section, consolidated task dispatch narrative with ACTION examples, removed duplicate "unsure" guidance. ~160 tokens saved with zero behavioral change.
- **Cache usage logging** — API token usage now logged with cache write/read stats for cost observability.
- **Version** bumped to 0.4.28

---

## [0.4.27] — 2026-03-25

### Added

- **SMS → Image redirect** — All outbound SMS/MMS rendered as formatted images and pushed to phone via Tasker, bypassing dead A2P 10DLC carrier channel (error 30034). Single config flag (`SMS_REDIRECT_TO_IMAGE`) controls the redirect — set `False` when A2P is approved. Renders with Pillow + DejaVu Sans at 540px width, dynamic height. Full messages rendered as single images (no SMS splitting). Intercepts `send_sms()`, `send_long_sms()`, and `send_mms()`. Audit trail preserved in `sms_outbound` table.

### Changed

- **Version** bumped to 0.4.27

---

## [0.4.26] — 2026-03-25

### Added

- **Expanded micronutrient tracking** — 17 new nutrient fields (33 total): magnesium, zinc, selenium, choline, vitamins A/C/K/B12, folate, thiamin, riboflavin, niacin, B6, E, manganese, copper, phosphorus. Daily targets for 5 key micronutrients (choline, magnesium, zinc, vitamin C, selenium). Context display and weekly summaries now show choline, magnesium, zinc, vitamin C, selenium, vitamin K when nonzero.
- **Choline tracking for NAFLD** — Choline (target 550mg/day) is critical for liver fat export. Positive note in check_limits() when daily choline reaches target. Egg choline validation warns when egg dishes are missing choline data (~147mg per egg).
- **Magnesium supplement pantry entry** — Nature Made Magnesium Oxide capsules: 100mg elemental magnesium per capsule (not 200mg oxide weight).
- **Micronutrient backfill script** — `backfill_micronutrients.py` for retroactive pantry micronutrient data on existing entries. Dry-run by default, `--apply` to execute.
- **Health keyword expansion** — Queries about magnesium, choline, zinc, selenium, micronutrients, and supplements now trigger health context injection.

### Fixed

- **Corrected 2026-03-24 magnesium log** — Entry corrected from 200mg (oxide weight) to 100mg (elemental), magnesium_mg added to nutrients JSONB.
- **Corrected 2026-03-23 rice+Huel calories** — Seeds of Change rice pouch was logged at 370cal (old estimate) instead of 470cal (verified from label). Entry corrected from 570 to 670 total calories.
- **Seeds of Change rice pantry data** — Updated from estimated ~370cal to label-verified 470cal per pouch (240g). Calcium corrected from 32mg to 20mg, potassium from 400mg to 390mg.
- **Pantry label verification** — Added label-verified micronutrients for Amy's burritos (both varieties) and shredded cheddar. All pantry updates cross-referenced against actual product label photos.

### Changed

- **Version** bumped to 0.4.26

---

## [0.4.25] — 2026-03-25

### Added

- **Auto-deploy to slappy** — Beardos cron polls every minute, compares slappy's HEAD to origin/main. On mismatch: `git pull`, `pip install -r requirements.txt`, `rc-service aria restart`. Max 1-minute delay after any push. No workflow changes needed.

### Changed

- **Version** bumped to 0.4.25

---

## [0.4.24] — 2026-03-25

### Added

- **System health monitor** — New `monitor.py` checks daemon, PostgreSQL, Redis, backup freshness, and peer host reachability every 5 minutes. Pushes formatted SVG alert to phone on failure (falls back to SMS). 30-minute cooldown prevents alert spam.
- **Slappy failover fully operational** — PostgreSQL data sync (pg_dump --clean + restore cron), guarded tick.py (runs only when beardos is down), monitor cron on both hosts.

### Fixed

- **Event loop blocking on startup** — Dispatcher's `xread` and completion listener's `pubsub.get_message` were blocking the asyncio event loop. Dispatcher now uses `asyncio.to_thread()`, listener uses non-blocking poll + `asyncio.sleep`. Daemon starts in <5 seconds instead of hanging.
- **Dispatcher timeout spam** — Redis `socket_timeout` increased from 2s to 15s, `xread` block time to 5s via thread pool. Eliminates constant "Timeout reading from socket" errors.
- **Cron error suppression** — pg_dump and rsync on beardos now log errors to files instead of `/dev/null`.
- **pg_dump uses `--clean --if-exists`** — Backup includes DROP statements so slappy can restore cleanly.
- **Version** bumped to 0.4.24

---

## [0.4.23] — 2026-03-25

### Added

- **Completion listener** — New `completion_listener.py` subscribes to Redis Pub/Sub for task completion events. When `notify=true`, composes a natural response via ARIA Primary and delivers via TTS+push (voice) or SMS (fallback). Runs as background asyncio task.
- **Full swarm pipeline** — End-to-end: ARIA Primary dispatches task → dispatcher routes to worker → worker executes → result in Redis → completion listener fires → ARIA composes response → user notified.
- **Version** bumped to 0.4.23 — **Swarm architecture complete.**

### Changed

- **Daemon lifespan** — Now starts/stops task dispatcher, completion listener, and amnesia pool.
- **Test fixtures** — All TestClient fixtures updated to mock swarm lifespan components (dispatcher, listener, pool) to prevent test hangs.

---

## [0.4.22] — 2026-03-25

### Added

- **Action ARIA** — New `action_aria.py` provides a persistent Claude Code worker for complex multi-step tasks (image generation, multi-step file operations, complex shell workflows). Fresh session per task, mutex for one-at-a-time execution. Task ID injected into system prompt for progress reporting.
- **Intelligent task routing** — Dispatcher now routes agentic tasks based on brief content: image gen, file creation, and multi-step tasks → Action ARIA (persistent session). Quick lookups and simple tasks → Amnesia pool (stateless).
- **Version** bumped to 0.4.22

---

## [0.4.21] — 2026-03-25

### Added

- **Amnesia pool** — New `amnesia_pool.py` manages a pool of warm stateless Claude Code instances for one-shot agentic tasks. Pre-warmed on startup, killed and replaced after each task (zero context accumulation). Configurable pool size (`AMNESIA_POOL_SIZE`, default 3) and task timeout (`AMNESIA_TASK_TIMEOUT`, default 120s).
- **Agentic task dispatch** — `task_dispatcher.py` now routes agentic mode tasks to the Amnesia pool. Uses the amnesia system prompt (minimal, no personality, no ACTION blocks). Auto-approves permission requests.
- **Pool lifecycle** — Integrated into daemon lifespan: instances pre-warmed on startup, killed on shutdown.
- **Version** bumped to 0.4.21

---

## [0.4.20] — 2026-03-25

### Added

- **Redis task queue** — `redis_client.py` extended with `push_task()`, `update_task_state()`, `complete_task()`. Uses Redis Streams for the queue, hashes for state, Pub/Sub for completion notifications.
- **Task dispatcher** — New `task_dispatcher.py` runs as background asyncio task. Reads from `aria:task_queue` Redis Stream, routes by mode. Shell mode executes commands directly via subprocess. Agentic mode placeholder for Steps 5-6.
- **`dispatch_action` ACTION block** — New handler in `actions.py`. ARIA Primary emits `dispatch_action` blocks to request shell commands or agentic tasks. Daemon extracts, generates task_id, pushes to Redis queue.
- **Shell execution** — Shell mode tasks execute immediately via `asyncio.create_subprocess_shell` with configurable timeout. No Claude Code instance needed for simple commands.
- **Version** bumped to 0.4.20

---

## [0.4.19] — 2026-03-25

### Changed

- **ARIA Primary switched to Anthropic API** — The conversational brain now uses the Anthropic Messages API directly instead of a Claude Code CLI subprocess. This is the critical swarm architecture milestone. All existing functionality preserved: ACTION blocks, briefing/debrief, nutrition tracking, delivery routing, file processing, SMS, voice pipeline.
- **Rolling conversation history** — Each API call includes the last 25 turns from `request_log` with timestamps, so ARIA maintains conversational continuity and time awareness across stateless calls.
- **Read-only tool calls for Tier 3** — Historical queries ("what did I eat March 19th?") now use Anthropic API tool calls instead of CLI shell access. Six tools defined for data store access.
- **Extended thinking enabled** — Opus with 64,000-token thinking budget for deep reasoning on complex queries.
- **Conversation history timestamps** — Each historical turn is prefixed with its timestamp so ARIA can distinguish time gaps (e.g., last night's dinner vs this morning's query).
- **Health check updated** — `/health` now reports `api: ok` instead of `claude: ok/down`.
- **CLI subprocess removed from lifespan** — No longer spawned on startup or killed on shutdown. `claude_session.py` kept for Action ARIA (Step 6).
- **Version** bumped to 0.4.19

### Fixed

- **D2: Single session serializes all requests** — RESOLVED. The API is stateless per call — concurrent requests are no longer blocked by a single subprocess lock.

---

## [0.4.18] — 2026-03-25

### Changed

- **System prompt split into three** — `system_prompt.py` now has `build_primary_prompt()` (conversational brain, dispatch-aware, no shell tools), `build_action_prompt()` (persistent worker, full shell/tool access, progress reporting), and `build_amnesia_prompt()` (minimal stateless worker). `build_system_prompt()` kept as alias for backward compatibility.
- **Primary prompt gains dispatch_action** — New ACTION block type for dispatching shell commands and agentic tasks to background workers. Replaces direct shell/tool instructions.
- **Primary prompt loses shell access** — No more generate.py, upscale4k.sh, fetch_page.py, curl, or `python -c` instructions. These capabilities move to Action ARIA's prompt.
- **Primary prompt gains data access note** — Describes read-only tool call access for historical queries.
- **aria_api.py** updated to use `build_primary_prompt()` instead of `build_system_prompt()`.
- **Version** bumped to 0.4.18

---

## [0.4.17] — 2026-03-25

### Added

- **Anthropic API client** — New `aria_api.py` wraps the Anthropic Messages API for ARIA Primary. Includes tool call loop, extended thinking support, file block handling, and config-driven model selection. API key read from `data/api_key.txt` (gitignored). Same function signature as `ask_claude()` for drop-in replacement.
- **Conversation history** — New `conversation_history.py` pulls rolling history from `request_log` as Anthropic API messages array. Strips channel prefixes, filters errors, truncates long responses. Configurable window (default 25 turns).
- **Read-only data access tools** — Six tool definitions for API-based historical queries: `query_health_log`, `query_nutrition_log`, `query_vehicle_log`, `query_legal_log`, `query_calendar`, `query_conversations`. Each wraps existing store functions with formatted text output. Replaces the CLI's implicit shell access for Tier 3 queries.
- **`anthropic` 0.86.0** added to requirements.txt.

### Changed

- **Config** — Added `ANTHROPIC_API_KEY_FILE`, `ARIA_MODEL`, `ARIA_MAX_TOKENS`, `ARIA_HISTORY_TURNS`, `ARIA_THINKING_BUDGET` to `config.example.py`. Claude CLI section renamed to clarify it's for Action ARIA + Amnesia pool.
- **Version** bumped to 0.4.17

---

## [0.4.16] — 2026-03-25

### Added

- **Redis client** — New `redis_client.py` with lazy-initialized singleton, modeled on `db.py`. Graceful failure: if Redis is down, returns None and logs warning once (never crashes ARIA). `decode_responses=True`, 2s connect/socket timeouts for fast failure in critical path.
- **Swarm task status in Tier 1 context** — `gather_always_context()` reads active tasks from Redis (`aria:active_tasks` set + `aria:task:{id}` hashes) and injects compact status lines. Foundation for the swarm architecture — when Action ARIA starts writing task state, the context builder picks it up automatically.
- **Redis in daemon health check** — `/health` endpoint now reports Redis status (`ok`/`unavailable`). Redis unavailable does NOT cause degraded status (ARIA works without it).
- **Redis in daemon lifespan** — Connection warmed on startup, closed on shutdown.
- **Redis config** — `REDIS_URL` and `REDIS_KEY_PREFIX` in `config.example.py`.
- **`redis` 7.4.0** added to requirements.txt.

### Changed

- **Version** bumped to 0.4.16

---

## [0.4.15] — 2026-03-25

### Changed

- **Keyword matching: hybrid substring + word-boundary regex** — Ambiguous single words now use `\b` word-boundary matching instead of substring matching. New `_match_keywords()` helper combines both approaches per category. Follows the `re.compile` pattern already established in `actions.py`.
- **Removed false-positive keywords** — Weather: "cold", "hot", "warm", "outside", "ice". Health: "back", "heart", "active", "sugar", "fat", "burn". Vehicle: "oil" (bare), "car". Calendar: "week" (bare), "event", "plan", "busy", "free", "available". These no longer trigger irrelevant context injection.
- **Hyphens normalized** — Input text normalizes hyphens to spaces before matching, so "heart-rate" matches "heart rate" substring.
- **Version** bumped to 0.4.15

---

## [0.4.14] — 2026-03-25

### Changed

- **Health/nutrition context scoped to today+yesterday** — `gather_health_context()` now includes yesterday's nutrition totals, calorie balance, and Fitbit highlights (sleep, HR, steps) as compact one-liners. Provides day-over-day comparison without tool calls.
- **14-day raw health dump removed** — The `health_store.get_entries(days=14)` block that dumped every raw health entry with descriptions, severity, and sleep hours has been deleted. This was the largest variable-size context payload (~2,000-5,000 chars). 7-day patterns (computed summaries) remain. Historical queries use tool calls.
- **Version** bumped to 0.4.14

### Fixed

- **D4: Context window overflow on health conversations** — RESOLVED. The combination of Tier 1 deduplication (v0.4.13) and 14-day dump removal (v0.4.14) eliminates the "Prompt is too long" errors that occurred during extended health/nutrition conversations.

---

## [0.4.13] — 2026-03-25

### Added

- **Tier 1 always-inject context** — New `gather_always_context()` function injects datetime, active timers, active reminders, location/battery, and exercise state on every call regardless of query keywords. Users no longer need to say "timer" to see their timers or "where am I" to see their location.
- **Context size logging** — `_get_context_for_text()` now logs context size and path (briefing/debrief/regular) at INFO level for observability.

### Changed

- **Datetime ownership consolidated** — `gather_always_context()` is now the single source of datetime injection. Removed from `claude_session.py` (was duplicated on every query), `gather_briefing_context()`, and `gather_debrief_context()`.
- **Reminders moved to Tier 1** — Active reminders are now always-injected instead of being separately included in briefing/debrief/regular paths. Eliminates duplication.
- **Location moved to Tier 1** — Basic location and battery always present. Movement history (4-hour trail) remains keyword-gated.
- **Timers moved to Tier 1** — Active timers always present, no longer require timer keywords.
- **Version** bumped to 0.4.13 (also fixes stale 0.4.10 in daemon.py)

---

## [0.4.12] — 2026-03-25

### Added

- **SMS message splitting** — Long SMS responses are now split at natural break points (paragraphs, sentences, words) into multiple messages instead of being silently truncated at 1600 chars. New `split_sms()`, `send_long_sms()`, `send_long_to_owner()` in `sms.py`. All SMS delivery paths (daemon, tick, exercise coaching) updated to use splitting.
- **Weather alert descriptions in context** — `build_request_context()` and `gather_briefing_context()` now include full NWS alert descriptions with severity level, giving Claude detailed safety information for weather queries and morning briefings.
- **News summaries in briefing context** — Morning briefings now include RSS feed summaries alongside headlines, giving Claude more context for news synthesis.
- **14 new SMS tests** — `split_sms()` edge cases, `send_long_sms()` multi-part delivery, media URL handling.

### Fixed

- **SMS 300-char artificial limit** — System prompt told Aria to keep SMS responses under 300 chars. Removed — Aria now responds naturally via SMS with automatic message splitting.
- **WebSocket STT idle timeout too aggressive** — Increased from 30s to 120s. Users can now pause for up to 2 minutes during real-time transcription without disconnection.
- **Nudge composition timeout too short** — Increased from 30s to 300s (5 minutes), allowing Aria to do web research, image generation, or other complex work when composing nudges. Added overlap prevention by writing `last_nudge_check` before the call to prevent duplicate evaluations from concurrent tick instances.
- **Reverse geocode failures invisible** — Nominatim geocoding errors were logged at DEBUG (invisible in production). Changed to WARNING.
- **News context exception silent** — `gather_briefing_context()` news digest exception was bare `except: pass`. Now logs WARNING.

### Changed

- **System prompt: SMS guidance** — "keep responses under 300 chars" → "respond naturally, long responses split automatically"
- **SMS context note** — "respond concisely, SMS has character limits" → "respond naturally, long responses split automatically"
- **Version** bumped to 0.4.12

---

## [0.4.11] — 2026-03-24

### Fixed

- **News feed silent failure** — `fetch_feed()` had a bare `except Exception: return []` with zero logging. Feed failures (DNS, server errors, malformed RSS) silently produced an empty news digest with no indication anything broke. Now logs `WARNING` with feed name and error.
- **Weather alert description truncation** — `get_alerts()` truncated NWS alert descriptions to 300 characters. This was dead code (context.py only uses event/headline/severity, not description), but now full descriptions are preserved for potential future use in briefings.
- **News summary truncation** — `fetch_feed()` truncated RSS summaries to 200 characters. Also dead code (context.py only uses titles), but now full summaries are preserved.
- **Fitbit incomplete snapshot not logged** — When individual Fitbit API calls failed during parallel fetch (e.g., HRV returns 403), each failure was logged individually but there was no summary showing which keys were missing from the snapshot. Now logs `WARNING: Incomplete snapshot for today — missing: hrv, vo2max`.

### Changed

- **Version** bumped to 0.4.11

---

## [0.4.10] — 2026-03-24

### Added

- **`fetch_page.py`** — web page fetcher with full JavaScript rendering via headless Chromium (Playwright). Works on JS-heavy sites, SPAs, and pages that block simple HTTP requests (Reddit, Wikipedia, Amazon, news sites). Supports `--selector` for targeted CSS extraction and `--timeout`/`--wait` options. Falls back gracefully from `networkidle` to `domcontentloaded` after 5s to avoid stalling on ad-heavy sites. Usable by both ARIA (system prompt) and Claude Code (CLAUDE.md).
- **Playwright dependency** — `playwright` 1.58.0 + bundled Chromium for headless page rendering.

### Fixed

- **TTS parenthesis vocalization artifact** — Kokoro TTS generates a ~250ms audible burst when vocalizing `(` and `)` characters. System notes appended by the claim detector ended with `)`, producing a weird cutoff sound at the end of spoken responses. Fixed by stripping parentheses in `_prepare_for_speech()`.
- **Request log response truncation** — `log_request()` hard-truncated responses to 500 characters, permanently losing conversation history. Removed the limit — full responses now stored. PostgreSQL handles large text fields efficiently, and truncation should only happen on read (query-side), never on write.
- **Pre-existing test bug: `test_sugar_warning` failed after 7pm** — `evaluate_nudges()` calls `get_net_calories()` during evening hours, but the test didn't mock it, causing a MagicMock comparison error. Added `mock_net_cal.return_value`.
- **3 new TTS tests** for parenthesis stripping (basic, system note, markdown link interaction).

### Changed

- **System prompt: `fetch_page.py` added to Tools** — ARIA now knows to use curl/lynx for fast fetches and fall back to fetch_page.py for JS-rendered pages.
- **CLAUDE.md: web fetching guidance** — documents the curl/lynx → fetch_page.py fallback workflow.
- **Version** bumped to 0.4.10

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
