# ARIA Code Review — Comprehensive Audit Report

**Date:** 2026-03-20
**Version:** v0.4.3
**Reviewer:** Claude (Opus 4.6)
**Scope:** All 23 Python source files (~5,500 lines), schema.sql, requirements.txt
**Note:** daemon.py was refactored into 6 modules in v0.4.6. File/line references below predate this split. See `context.py`, `actions.py`, `claude_session.py`, `system_prompt.py`, `tts.py` for current locations. v0.4.7 fixed a Kokoro TTS off-by-one crash (not from this audit — discovered via live error investigation).
**Previous review:** v0.3.8 (2026-03-19)

---

## Executive Summary

Second full audit, covering all changes since v0.3.8 including the PostgreSQL migration (v0.4.0), async TTS (v0.4.1), and 12 prior bug fixes. The codebase has improved substantially — all three original critical bugs are resolved, file locking races eliminated, store layer cleaner. This audit found **6 significant bugs**, **5 moderate issues**, and **4 minor/design items**. The most urgent: `re.sub` missing `re.DOTALL` (ACTION markup leaks into spoken responses), location reminders silently lost during quiet hours, and ghost exercise sessions.

---

## Resolved Issues (from prior versions)

| ID | Summary | Resolved In |
|----|---------|-------------|
| C1 | Task memory leak — completed tasks never expired | v0.3.9 |
| C2 | ACTION block regex failed on multiline JSON | v0.3.9 |
| C3 | No file locking between daemon and tick.py | v0.4.0 (PostgreSQL) |
| S4 | Kokoro TTS blocks event loop | v0.4.1 (thread pool) |
| S5 | `evaluate_nudges` called unused `get_trend` | v0.3.9 |
| S6 | `build_request_context` docstring was wrong | v0.3.9 |
| M1 | `fitbit_store.get_snapshot` only resolved "today" | v0.4.0 |
| M4 | Fitbit `fetch_daily_snapshot` fetched sequentially | v0.4.1 |
| M6 | News feeds fetched sequentially | v0.4.1 |
| M8 | Hardcoded diet start date in 4 places | v0.3.9 |
| M9 | Hardcoded age in `fitbit_store.start_exercise` | v0.3.9 |
| M10 | `modify_event` allowed overwriting internal fields | v0.3.9 |
| M11 | Unused import `Counter` in health_store | v0.3.9 |
| M12 | Unused import `timedelta` in vehicle_store | v0.3.9 |
| m1 | Inline `import re` in daemon.py | v0.3.9 |
| m5 | `process_actions` silently ignored unknown types | v0.3.9 |
| m9 | `nutrition_store.get_context` deficit display wrong | v0.3.9 |
| D1 | All stores used identical boilerplate | v0.4.0 |
| D3 | No health check for dependencies | v0.4.1 |
| D5 | No request log rotation | v0.4.0 |
| D6 | Dead config `PHONE_IMAGE_DIR` | v0.3.9 |
| D7 | No graceful shutdown | v0.4.0 |

---

## Current Issues

### Significant Bugs

#### S9. `process_actions` `re.sub` missing `re.DOTALL` — ACTION markup leaks into responses *(NEW)*
**File:** `daemon.py:1077`
**Priority:** High | **Effort:** Quick fix (add flag)

```python
# Line 950 — extraction has re.DOTALL (fixed in v0.3.9)
actions = re.findall(r'<!--ACTION::(\{.*?\})-->', response_text, re.DOTALL)

# Line 1077 — stripping does NOT have re.DOTALL (missed in v0.3.9 fix)
clean_response = re.sub(r'<!--ACTION::.*?-->', '', response_text).strip()
```

When Claude formats an ACTION block with newlines in the JSON (common for `log_nutrition` with many fields), the `findall` correctly extracts and processes it, but the `sub` fails to strip it from the response. The raw `<!--ACTION::...-->` markup ends up in the spoken/texted response.

**Fix:** Add `re.DOTALL` to the `re.sub` call at line 1077.

---

#### S10. `start_exercise` doesn't deactivate existing active exercise — ghost sessions *(NEW)*
**File:** `fitbit_store.py:305-338`
**Priority:** Medium | **Effort:** Quick fix (add SQL)

`start_exercise()` inserts a new row without deactivating any existing active session. If called twice, multiple rows have `active = TRUE`. `record_exercise_hr` (line 392) updates `WHERE active = TRUE`, appending HR data to ALL active sessions. `end_exercise` only deactivates the most recent one, leaving older ones active until 90-minute auto-expire.

**Fix:** Add `UPDATE fitbit_exercise SET active = FALSE, ended_at = NOW(), end_reason = 'superseded' WHERE active = TRUE` at the start of `start_exercise`.

---

#### S11. Location reminders silently completed during quiet hours without delivery *(NEW)*
**File:** `tick.py:192-201`
**Priority:** High | **Effort:** Quick fix (move line)

```python
if trigger == "arrive" and location_match:
    message = f"Location reminder: ..."
    if not is_quiet_hours():
        sms.send_to_owner(message)
    calendar_store.complete_reminder(r["id"])  # BUG: runs even if SMS wasn't sent
```

If the user arrives at a location during quiet hours, the reminder is marked done without notification. Permanently lost.

**Fix:** Move `complete_reminder()` inside the `if not is_quiet_hours()` block.

---

#### S12. Location reminders: "leave" trigger never fires *(NEW)*
**File:** `tick.py:192`
**Priority:** Medium | **Effort:** Moderate (new logic + state tracking)

Only `trigger == "arrive"` is handled. The system prompt documents `location_trigger: arrive|leave`, but tick.py has no `elif trigger == "leave"` branch. "Leave" reminders are silently ignored forever.

Implementing "leave" requires tracking previous location state to detect departure transitions.

---

#### S13. ~~Fitbit snapshot merge overwrites good data with null on partial fetch failure~~ **RESOLVED v0.4.3 + v0.4.11**
**File:** `fitbit_store.py:28`, `fitbit.py:299-307`

Null filter added in `fitbit_store.save_snapshot()` (v0.4.3). Failed keys are now also skipped in `fetch_daily_snapshot()` (never set to None). In v0.4.11, added summary log showing which keys were missing from incomplete snapshots for observability.

---

#### S8. `_process_file_task` ignores delivery routing *(carried forward, re-confirmed)*
**File:** `daemon.py:1374`
**Priority:** Medium | **Effort:** Quick fix

`process_actions(response)` called without `metadata` dict. `set_delivery` ACTION blocks from file upload responses are silently ignored. This means if the user sends a photo and asks ARIA to respond via SMS (instead of voice), the routing is lost. This also blocks future location-aware auto-routing (e.g., never voice at work/court/restaurants).

**Fix:** Add `delivery_meta = {}`, pass `metadata=delivery_meta`, and route response based on `delivery_meta.get("delivery", "default")` — same pattern as `_process_voice_task` and `_process_sms`.

---

### Moderate Issues

#### M2. `process_actions` overwrites entire response on any failure *(carried forward)*
**File:** `daemon.py:1081`
**Priority:** Medium | **Effort:** Quick fix

If Claude answers a question AND tries to log data, but the log fails, the entire answer is replaced with `"Sorry, something went wrong..."`. The user loses Claude's conversational response.

**Fix:** Append the error notice instead of replacing.

---

#### M13. SMS webhook accepts messages from any phone number *(NEW)*
**File:** `daemon.py:1841-1884`
**Priority:** Medium | **Effort:** Quick fix (add filter)

The `webhook_sms` endpoint validates the Twilio signature (proving the request came through Twilio), but doesn't check `from_number`. Any phone number that texts the ARIA Twilio number gets a Claude-powered response. Consumes Claude credits.

**Fix:** Add `if from_number != config.OWNER_PHONE_NUMBER` early in the handler (after STOP/HELP compliance checks).

---

#### M14. tick.py has no per-job error isolation *(NEW)*
**File:** `tick.py:589-611`
**Priority:** Medium | **Effort:** Quick fix (wrap each in try/except)

`main()` calls 5 jobs sequentially. If `process_timers()` raises, none of the remaining jobs run (location reminders, exercise coaching, Fitbit polling, nudges).

**Fix:** Wrap each job call in its own try/except with `log.exception()`.

---

#### M16. Lifespan doesn't kill Claude session on shutdown *(NEW)*
**File:** `daemon.py:37-42`
**Priority:** Medium | **Effort:** Quick fix (1 line)

The lifespan handler closes the DB pool but doesn't call `await _claude_session._kill()`. On daemon restart, the old Claude subprocess may become orphaned.

**Fix:** Add `await _claude_session._kill()` before `db.close()` in the lifespan.

---

#### M17. Claim-without-action detection false-positives on nutrition queries *(NEW)*
**File:** `daemon.py:1097-1116`
**Priority:** Low | **Effort:** Moderate (logic redesign)

When the user asks "what's my nutrition today?" and Claude responds with existing nutrition data (mentioning 3+ nutrient terms), the system appends a false warning about data not being saved. Claude is reporting existing data, not claiming to have stored anything.

**Fix:** Only trigger the nutrition heuristic when claim_words are also present (i.e., Claude mentions nutrients AND uses words like "logged"/"saved").

---

### Minor Issues & Design

#### m14. `piper-tts` still in requirements.txt *(NEW)*
**File:** `requirements.txt:57`
**Priority:** Low | **Effort:** Quick fix (remove line)

Piper was rejected in favor of Kokoro. The package is unused but still installed.

---

#### m15. Morning briefing should be limited to once per day *(NEW, reframed)*
**File:** `daemon.py:296-297`
**Priority:** Low | **Effort:** Moderate

Currently, any message starting with "good morning" triggers a full morning briefing every time. Should be limited to once per day using tick_state or similar tracking. If the user wants it again, they should explicitly ask for it (e.g., "repeat the morning briefing"), which ARIA should recognize and re-trigger.

---

#### D2. ~~Single Claude session serializes all requests~~ **RESOLVED v0.4.19, evolved v0.5.0**
v0.4.19: ARIA Primary switched to Anthropic API — stateless per call. v0.5.0: Switched to CLI session pool (deep + fast) with API as fallback. Deep and fast sessions run concurrently (separate locks).

#### D4. ~~Context builder can produce very large strings~~ **RESOLVED v0.4.13-v0.4.14**
v0.4.13: Tier 1 always-inject, datetime consolidation, context size logging. v0.4.14: Removed 14-day raw health dump (~2-5K chars), scoped health context to today+yesterday+7d summaries. Context overflow on health conversations eliminated.

---

## Security Notes

### SEC1. `config.py` contains plaintext secrets
Expected pattern for a personal self-hosted app.

### SEC2. `--dangerously-skip-permissions` on Claude CLI
System prompt asks Claude to confirm before modifying the system, but this is prompt-level enforcement only.

### SEC3. Bearer token auth — sound for deployment topology
External requests through Tailscale Funnel (HTTPS), internal on localhost (plaintext).

### SEC4. SMS webhook allows any sender *(see M13)*
Any phone number can trigger Claude queries by texting the ARIA number.

---

## Resolution Status

### Resolved in v0.4.2

S9 (ACTION markup leak), S11 (quiet hours reminder loss), M14 (tick error isolation), S10 (ghost exercise sessions), S13 (snapshot null overwrite), M16 (orphan Claude process), S8 (file delivery routing), M13 (SMS any-sender), M2 (response overwrite on failure)

### Resolved in v0.4.3

M17 (nutrition false positive), S12 (leave location trigger), m15 (briefing once per day), m14 (remove piper-tts)

### Resolved in v0.5.0

D2 evolved (CLI session pool replaces API, deep+fast concurrent sessions), M16 fully resolved (session pool has explicit stop() in lifespan shutdown)

### Resolved in v0.5.1

Partial ACTION marker leak (found by Hypothesis fuzz testing, fixed in actions.py)

### Resolved in v0.5.3

S14 (ACTION in code fences) and S15 (nested --> truncation) — both fixed via `_extract_action_jsons()` balanced-brace parser replacing regex extraction. Found by adversarial pipeline tests.

### Resolved in v0.6.0

M17 structurally superseded — claim-without-action detection now produces structured `ActionResult.claims_without_actions` data instead of directly appending warnings. The verification pipeline (`verification.py`) consumes this data and triggers retries when appropriate, eliminating the false-positive problem for nutrition queries (verification gates on actual claim patterns, not just nutrient term counts). S8 fully resolved — all delivery paths (file, voice, SMS, task) now route through `delivery_engine.evaluate()` which enforces location-aware delivery decisions at the handler level.

### Remaining

None — all tracked issues resolved.

---

*Original review covered 23 Python source files. Codebase now at 39+ source files (v0.6.0: monitors/, verification.py, delivery_engine.py added). v0.6.0 total: 1836 tests across 82 files.*
