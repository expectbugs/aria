# ARIA — Project Status

## Current Version
v0.9.8 (2026-04-27)

## What's Working
- **Robust two-tier confirmation gate** (v0.9.8): the destructive-confirmation flow no longer strands actions when the daemon shortcut misses. Daemon `_check_pending_confirmation` is the fast path for typed `yes`/`no`; when it bows out (voice-wrapped transcripts, "yeah do it", apostrophe-less Whisper output, anything > 40 chars or off-whitelist), pending confirmations are surfaced in ARIA's context and she resolves them via `confirm_destructive` or the new symmetric `cancel_destructive` ACTION. Prompts (Adam + Becky) rewritten to make ARIA the authoritative resolver: explicit intent classification, "never claim Done/Cleared without an ACTION block." Hallucination detector regex extended to catch delete-claim phrases ("Done. Clean slate.", "I deleted it.") so silent failures trigger a retry. Fixes the 2026-04-26 Becky failure where her voice "Yes, please clear." stranded a grocery-list reminder. 15 new tests.
- **Webhook robustness + GSM-7 SMS normalization** (v0.9.7): `/sms` webhook now returns 200 for all error paths (invalid signature, malformed JSON, unknown events) to prevent Telnyx retry storms. Idempotency uses atomic `INSERT ON CONFLICT` instead of racy SELECT-then-INSERT. HELP handler validates `from_number` before sending. `sms._normalize_for_sms()` substitutes 49 non-GSM-7 characters (em-dash, smart quotes, backticks, bullets, ©/®/™/°, math, arrows, zero-width) with ASCII equivalents before send; `split_sms` auto-picks 1500-char chunks for GSM-7 vs 600 for UCS-2. Prevents the 22-segment 40302 silent-drop bug.
- **Multi-confirmation** (v0.9.6): single "yes"/"no" now confirms/cancels ALL pending destructive actions for the speaker (not just the first). Cross-user deletes trigger ONE consolidated SMS to the affected owner. `confirm_destructive` accepts `"confirmation_id": "all"` for batch-confirm via ACTION.
- **Beckaning polish** (v0.9.6): fixed 4 bugs found in audit — Becky's `set_delivery: image` now actually delivers MMS; unknown-owner reminders no longer silently consumed; cross-user write notification symmetric (Adam→Becky works); `query.py --user` removed from Adam-only subcommands (errors instead of silently returning Adam's data).
- **The Beckaning — multi-user** (v0.9.5): Second authorized user Becky (girlfriend) via SMS. Isolated Claude Code subprocess, separate "Aria B" prompt (max snark, no diet framing, verification intact). `TRUSTED_USERS` registry + `user_key` threading through every layer. Session pool + Action Aria are per-user registries. `reminders`/`events`/`timers` gain `owner` column. Cross-user writes (Becky → Adam's reminders/calendar) consolidate into ONE notification SMS. Adam-exclusive writes (health, nutrition, vehicle, legal, Fitbit, Gmail, ambient) reject Becky. Per-user pending destructive confirmations. Becky's morning brief: Milwaukee weather, her calendar, her news feeds. `query.py --user` flag + new `reminders` subcommand. 25 new tests.
- **Auto-reminder firing** (v0.9.5): New `process_reminders()` tick job fires time-based reminders for BOTH users. Adam gets image-push, Becky gets SMS. Quiet hours respected. `AUTO_REMINDER_FIRE_ENABLED` config flag.
- **Nudges + safety net via image-push** (v0.9.5): Unified delivery pipeline renders to image via Tasker push (free), no longer SMS. Same visual format as data-quality alerts.
- **Email integration polish** (v0.8.7): Classification accuracy 35.5%→28.0% important rate, `check_subject_only` for per-sender overrides, shipping split by urgency, P1-P4 priority scoring, email surfacing tracker, Tier 3 AI upgraded to Sonnet, email body access via query.py, trash_email ACTION, stale finding cleanup, **junk auto-archive** (Tier 1 junk removed from Gmail inbox every tick, batch Gmail API, historical cleanup script)
- **Context dedup + injection** (v0.8.6): Hash-based dedup for static context (~15KB saved per turn), broader keywords, cross-domain triggers, gap detection
- **Context scope annotations** (v0.8.5): Scope info on all injected context, completeness claim detection (log-only)
- **Destructive action gate** (v0.8.4): Code-level confirmation required for all deletes, 10-min pending expiry, daemon shortcut
- **Tool use enforcement** (v0.8.3): Per-query tool reminders, tool call tracking, factual response validation with retry
- **Personality** (v0.8.2): Snarky banter by default, context-gated seriousness, occasional image-gen humor
- **Voice assistant**: Full voice loop via Tasker (STT, Claude, TTS)
- **CLI wrapper** (v0.8.1): `aria_cli.py` — terminal conversation with ARIA, file input, optional audio playback
- **SMS/MMS (Telnyx, v0.9.2+)**: Full Twilio→Telnyx migration. ED25519 webhook verification. Number: +12624251337. `message.finalized` events captured to `sms_outbound`. Image delivery split (v0.9.4): automated triggers via Tasker `push_image.py` (free LAN); user-initiated via `send_image_mms()` / `send_mms.py` CLI (Telnyx MMS, works off-network). 10DLC campaign CY9XE15 registered.
- **CLI Session Pool**: Two persistent Opus sessions (deep + fast) with API fallback, background watchdog auto-respawn (v0.9.1)
- **Data stores**: All backed by PostgreSQL (21 core tables + 5 new in v0.6.0)
- **Context injection**: Tiered context (always + keyword-triggered) via build_request_context()
- **ACTION blocks**: 21 action types, balanced-brace parser, claim detection
- **Fitbit integration**: HR, HRV, SpO2, sleep, activity, exercise coaching
- **Nutrition tracking**: 33 nutrient fields, pantry system, post-log validation
- **Domain monitors** (v0.6.0): Health, fitness, vehicle, legal, system (incl. Portage sync)
- **Response verification** (v0.6.0): Claim extraction, action retry loop, calorie checking
- **Delivery intelligence** (v0.6.0+): Location-aware routing, device state, deferred queue. Unified `execute_delivery()` async + `_sync_deliver()` sync dispatch (v0.6.1). User-initiated requests never deferred (v0.8.1).
- **Whisper STT**: Batch + streaming + WebSocket, large-v3-turbo on CUDA
- **Kokoro TTS**: af_heart voice, thread pool, markdown stripping, split safety
- **Google Calendar + Gmail** (v0.7.0+): OAuth2 PKCE, async API client, PostgreSQL cache, automated polling
- **Email intelligence** (v0.8.0): 3-tier classification (rules/scoring/AI), full-text search, send capability, context injection, email watches, auto-cleanup, 16 categories, curated rules from 1,401 emails
- **Google Calendar sync** (v0.8.0): Bidirectional sync, Google as source of truth, incremental syncToken
- **Phase 6 ambient audio pipeline** (v0.9.0): DJI Mic 3 → Bluetooth → slappy capture daemon (parecord, VAD, faster-whisper base CPU) → HTTP relay to beardos. Sliding window streaming (~1.5s latency to Redis). Wake word "ARIA" detection. Extraction engine (Opus 4.6 CLI auto effort, conversation grouping, commitment/person/topic extraction, daily summaries). Qdrant semantic search (sentence-transformers embeddings). Neo4j knowledge graph (person/conversation/topic/commitment relationships). Context injection (commitments in Tier 1, recall/person keywords in Tier 2). 5 new query.py subcommands. Debrief + briefing upgraded with ambient data.
- **Swarm workers**: Action ARIA (Opus, complex tasks) + Amnesia pool (Sonnet, quick tasks)
- **Morning briefing / evening debrief**: Full context aggregation, marks Category A findings as delivered (v0.9.1)
- **Autonomous timers/reminders**: Cron-driven, location-based, voice + SMS delivery
- **Proactive nudges**: Cooldown-managed, frequency-capped, Haiku-composed
- **System monitor**: Daemon/DB/Redis/backup/peer health checks with SVG phone alerts

## Next Steps
- Phase 4: Google Calendar + Gmail integration (DONE — plumbing v0.7.0, intelligence v0.8.0)
- Phase 4 follow-up: Interactive email classification session (DONE — 98.7% Tier 1 coverage)
- Phase 5: Pixel Watch 4 app (watch as voice input source)
- Phase 6: DJI Mic 3 ambient audio pipeline (DONE — capture, extraction, Qdrant, Neo4j, ARIA integration)
- Phase 7: Even Realities G2 glasses integration (delivery channel ready in engine)
- Phase 8: Qdrant + Neo4j four-layer memory
