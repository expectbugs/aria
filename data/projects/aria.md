# ARIA — Project Status

## Current Version
v0.9.0 (2026-03-30)

## What's Working
- **Email integration polish** (v0.8.7): Classification accuracy 35.5%→28.0% important rate, `check_subject_only` for per-sender overrides, shipping split by urgency, P1-P4 priority scoring, email surfacing tracker, Tier 3 AI upgraded to Sonnet, email body access via query.py, trash_email ACTION, stale finding cleanup, **junk auto-archive** (Tier 1 junk removed from Gmail inbox every tick, batch Gmail API, historical cleanup script)
- **Context dedup + injection** (v0.8.6): Hash-based dedup for static context (~15KB saved per turn), broader keywords, cross-domain triggers, gap detection
- **Context scope annotations** (v0.8.5): Scope info on all injected context, completeness claim detection (log-only)
- **Destructive action gate** (v0.8.4): Code-level confirmation required for all deletes, 10-min pending expiry, daemon shortcut
- **Tool use enforcement** (v0.8.3): Per-query tool reminders, tool call tracking, factual response validation with retry
- **Personality** (v0.8.2): Snarky banter by default, context-gated seriousness, occasional image-gen humor
- **Voice assistant**: Full voice loop via Tasker (STT, Claude, TTS)
- **CLI wrapper** (v0.8.1): `aria_cli.py` — terminal conversation with ARIA, file input, optional audio playback
- **SMS/MMS**: Inbound via Twilio, outbound via SMS-to-image redirect (A2P blocked)
- **CLI Session Pool**: Two persistent Opus sessions (deep + fast) with API fallback
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
- **Phase 6 foundation** (v0.9.0): Ambient audio pipeline data layer — ambient_transcripts (tsvector full-text search), ambient_conversations, commitments (promise tracker), person_profiles, daily_summaries. Three new stores + config for Qdrant, Neo4j, embedding model, capture daemon.
- **Swarm workers**: Action ARIA (Opus, complex tasks) + Amnesia pool (Sonnet, quick tasks)
- **Morning briefing / evening debrief**: Full context aggregation
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
