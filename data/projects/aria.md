# ARIA — Ambient Reasoning & Intelligence Assistant

## Status
Phase 4 in progress (v0.4.32). **Swarm architecture complete.** ARIA Primary on Anthropic API (Opus 4.6), Action ARIA (Opus, max effort) + Amnesia pool for background tasks, Redis dispatch + completion notifications. Slappy failover fully operational with auto-deploy, db sync, health monitoring.

## Architecture
- FastAPI daemon on beardos (primary, RTX 3090) with slappy as warm failover
- Claude CLI subprocess (stream-json protocol, recycled every 200 requests)
- Kokoro TTS (af_heart voice)
- Tasker on Pixel 10a for voice input, file sharing (AutoShare), polling, image/audio push, location reporting
- Twilio for SMS/MMS (+1 262-475-1990), webhook via Tailscale Funnel
- Cron tick every minute for autonomous timers and proactive nudges
- Tailscale mesh, no internet exposure except Funnel webhook

## What's Working
- Full voice loop: phone mic → Tasker STT → daemon → Claude → TTS → phone speaker
- Morning briefing (weather, calendar, reminders, news, specialist data, diet day, location, battery)
- Evening debrief (day summary, meals, pending items, tomorrow prep, weather)
- Calendar and reminders (CRUD via voice, location-triggered reminders)
- Weather (NWS API), news (RSS feeds)
- Image generation (FLUX.2, SUPIR upscale, Matplotlib, Graphviz, SVG) + push to phone
- Universal file input (share any file from phone via AutoShare)
- SMS/MMS via Twilio (inbound working, outbound redirected to image push pending A2P)
- Specialist logs: vehicle maintenance, health/physical, legal case
- Diet/nutrition tracking with NAFLD-aware compliance, meal types, nutrition label photos
- Structured per-item nutrition tracking with SQL aggregation (33 nutrients including expanded micronutrients), pantry system for verified staple food data, post-log validation checks, choline/magnesium/micronutrient daily tracking
- Project status briefs
- Autonomous timer/scheduler (SMS or voice delivery, priorities, quiet hours)
- Proactive nudges (meal gaps, calendar warnings, overdue reminders, diet compliance, health patterns, legal deadlines, battery alerts)
- Phone location tracking with reverse geocoding (every 5 min)
- Fitbit integration (HR, HRV, SpO2, sleep, activity, exercise coaching with HR zones)
- Whisper STT (batch, voice pipeline, real-time WebSocket streaming)
- All data stores on PostgreSQL 17 (migrated from JSON in v0.4.0)
- 833+ automated tests (unit + integration)
- File inbox (received files saved for future reference)
- Automatic failover (beardos → slappy) with auto-deploy, db sync, health alerts
- Data sync via rsync every 5 minutes, PostgreSQL backup/restore every 5 minutes, code auto-deploy every minute

## Next Steps
1. Live swarm testing + tuning (dispatch from phone, image gen end-to-end)
3. Phase 4: Google Calendar integration
4. Phase 4: Gmail integration
5. Phase 4: Smart alarm + SMS announce

## Behavioral Notes
- **Late-night "today" rule:** Adam works second shift and often stays up past midnight. When he says "good night" after 12am, his "today" is the PREVIOUS calendar date — he hasn't slept yet, so his day hasn't ended. Always check the previous calendar date's logs before claiming meals are missing. Never warn about unlogged meals based on the new calendar date when it's a late-night wind-down.

## Notes
- All data stays local — no cloud dependencies except Claude CLI (Max subscription), Twilio, and Fitbit
- Twilio A2P 10DLC verification pending (submitted 2026-03-19, may take weeks)
- Legal data treated as sensitive — never surfaced unless user brings it up
- Phone image resolution: 540x1212 (half of Pixel 10a native)
- DJI Mic 3 purchased and on hand for Phase 6 ambient recording
- ARIA runs Opus 4.6 with max effort level, auto-memory disabled, CLAUDE.md excluded
- v0.4.9: Fixed TTS truncation on data-heavy responses (phoneme batch overflow)
- v0.4.10: fetch_page.py (Playwright), TTS paren fix, request log untruncated
- v0.4.11: Silent limits audit — news feed failures now logged, weather/news truncations removed, Fitbit incomplete snapshot logging
- v0.4.12: SMS splitting (no more truncation), removed 300-char SMS limit, WebSocket timeout 30→120s, nudge timeout 30→300s, alert descriptions + news summaries in context
