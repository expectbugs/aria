# ARIA
## Ambient Reasoning & Intelligence Assistant
### Full Project Plan — Voice-Driven AI Life Assistant

---

## Project Overview

ARIA is a personal voice-driven AI assistant built on your existing Tailscale network, leveraging Claude AI as its reasoning core and your high-end Gentoo Linux machines as the compute backbone. The goal is a frictionless, always-available assistant that handles your schedule, controls your machines, logs your life, and helps compensate for the memory and organizational load of a complex, demanding day.

The system is designed to be entirely self-hosted with no dependency on third-party voice assistant platforms. Your data stays on your hardware.

### Core Architecture

- Phone/Watch → STT → Tailscale → FastAPI Daemon on PC
- Claude API handles all reasoning, routing, and response generation
- TTS response returned to phone speaker or watch
- Claude Code managed via PTY for interactive machine control
- All logs, memory, and databases stored locally on your hardware

### Hardware Stack

- **PC (primary brain):** Gentoo Linux, OpenRC, Claude Code, Qdrant, Whisper
- **Laptop:** Gentoo Linux, OpenRC, Claude Code, secondary compute
- **Phone:** Pixel 10a, Tasker, STT/TTS layer
- **Smartwatch:** Pixel Watch 4, custom WearOS app for hold-to-talk via side button
- **Mic:** DJI Mic 3, clip-on wireless mic for ambient recording (purchased, on hand)
- **Tailscale:** secure mesh network binding all devices

---

## Phase 1: Core Loop
*Wake phrase → briefing → voice commands → response*

### Morning Briefing

Triggered by saying "Good Morning" or pressing the wake button. Claude assembles and reads aloud:

- Date, time, and day of week
- Current weather conditions + today's full forecast
- Severe weather alerts if any
- Today's calendar appointments in order
- Tomorrow's appointments needing prep today
- Active reminders and pending tasks
- Configurable news digest (tech headlines, local Wisconsin news, steel/manufacturing)
- Upcoming events in the next 7 days flagged for prep
- Optional: motivational note or interesting fact

### Basic Voice Commands

**Calendar & Reminders**
- "Add [event] on [date] at [time]"
- "Remind me to [X] before [event/date]"
- "What's on my calendar this week?"
- "Move my [appointment] to [new time]"
- "Set a recurring reminder every [day/week] to [X]"

**Information**
- "What's the weather in [place]?"
- "What time does [X] close?"
- "Look up [topic]"

### Technical Implementation

- **STT:** Android built-in speech recognition via Tasker
  - Tasker: $4.99 on Google Play (by João Dias — not TaskRabbit or "Tasker for Engineers")
- **Daemon:** FastAPI on PC, async endpoints over Tailscale
  - `/ask/start` — accepts text, returns task_id for async polling
  - `/ask/status/{task_id}` — lightweight JSON status check (processing/done/error)
  - `/ask/result/{task_id}` — returns WAV audio when done
  - `/health` — uptime and version check
  - `/snippet/{name}` — serves JS snippets for easy phone copy-paste
- **Claude:** persistent CLI subprocess via stream-json protocol, recycled every 200 requests
- **TTS:** Kokoro (kokoro-onnx, `af_heart` voice), model cached at startup
- **Response:** WAV audio returned to phone, played via Tasker Music Play

### Tasker Setup — Exact Configuration

#### Global Variables (VARIABLES tab)

| Variable | Value |
|----------|-------|
| `ARIA_HOST_PRIMARY` | `http://100.107.139.121:8450` (beardos) |
| `ARIA_HOST_FALLBACK` | `http://100.70.66.104:8450` (slappy) |
| `ARIA_TOKEN` | `<your-auth-token>` |

#### ARIA Ask Task (7 steps)

```
1. Get Voice
2. Variable Set    %voice_result → %VOICE
3. JavaScriptlet   Code: (from /snippet/aria_ask endpoint)
                   Timeout: 3600
4. IF              %ask_success ~ 1
5. HTTP Request    Method: GET
                   URL: %audio_url
                   Headers: Authorization:Bearer <your-auth-token>
                   File To Save With Output: ARIA/response.wav
6. Music Play      File: ARIA/response.wav
7. End If
```

**Trigger:** Profile → Event → UI → Assistance Request (long-press power button, Tasker set as default digital assistant)

#### Health Check Task

```
1. JavaScriptlet   Code: (from /snippet/aria_health endpoint)
```

Sets `%ARIA_STATUS` global to `primary`, `fallback`, or `offline`.

**Trigger:** Profile → Time → every 5 minutes, 12:00AM–11:59PM

#### JavaScriptlet Critical Rules

1. Use `wait(ms)` for delays — `java.lang.Thread.sleep()` crashes silently
2. `writeFile()` is string-only — binary downloads must use Tasker HTTP Request action
3. `exit()` kills the entire task, not just the script — never use it
4. Export variables with bare assignment (`ask_success = "1"`, no `var`) AND `setLocal()` for safety
5. Hardcode auth tokens in HTTP Request headers — variable substitution in headers is unreliable

### Deployment Model

One repo, one codebase, config-driven per-host differences. The repo represents ARIA the project — not any specific machine's install.

- **`config.py`** is gitignored. Each machine keeps its own. See `config.example.py` for all options.
- **`config.example.py`** is the tracked reference template with all available settings documented.
- **Per-host differences** (IP, hostname, CLI path, GPU capabilities) live entirely in `config.py`.
- **Feature gating** uses config flags: `ENABLE_GPU`, `ENABLE_IMAGE_GEN`, `IS_PRIMARY`, etc. Daemon code checks these at runtime — no code forks between machines.
- **Deploy to any host:** `git pull`, copy `config.example.py` to `config.py`, edit for the host, restart service.
- **Data sync:** rsync cron from primary → failover every 5 minutes. Data files (calendar, reminders) are gitignored.

#### Current Hosts

| Host | Role | Tailscale IP | GPU | Key Differences |
|------|------|-------------|-----|-----------------|
| beardos | Primary | 100.107.139.121 | RTX 3090 24GB | Full capabilities, auto-starts, image gen, LoRA training |
| slappy | Failover | 100.70.66.104 | None (integrated) | API + TTS, auto-starts, no GPU/Whisper/image gen |

### Billing Clarification

All voice commands that go through Claude Code use your existing Max subscription ($200/mo). No separate API key or pay-per-token billing is needed for Phase 1 or Phase 2. A direct Anthropic API key (console.anthropic.com, pay-per-token) would only be needed if you add lightweight non-agentic queries that bypass the Claude Code CLI entirely — and even then, ARIA usage would be a few dollars a month at most.

---

## Phase 2: Migration to Beardos & Failover — COMPLETE
*Move primary backend to beardos, slappy becomes warm standby*

### Why Migrate

Beardos is the main PC with significantly more compute power. Running ARIA on beardos provides better performance for Claude Code invocations, image generation (`imgen` alias available locally), and future GPU-dependent features (Whisper, Qdrant, LoRA). Slappy remains available as a fallback so ARIA is never fully offline.

### Failover Architecture

Tasker JavaScriptlet handles failover at the request level:

- Tries beardos first via `/ask/start` POST
- If connection fails, announces "Beardos is offline — running from slappy" and retries against slappy
- If both down, queues request locally and announces it
- Adaptive polling: 3s intervals for first minute, 10s for 1–5 min, 30s for 5–60 min
- Slappy's ARIA service is in the default runlevel — auto-starts on boot so failover is always available

### Data Synchronization

- rsync cron on beardos pushes `data/` to slappy every 5 minutes over SSH (port 80)
- During failover, slappy operates on last-synced data — brief staleness is acceptable
- After a failover period, manually sync slappy's data back to beardos before resuming
- Future upgrade path: Syncthing or SQLite on shared storage for real-time sync

---

## Phase 3: Self-Contained Features — COMPLETE
*Specialist logs, debrief, nudges, image generation, file input, SMS/MMS, location tracking, autonomous timers, diet/nutrition tracking*

All Phase 3 features are self-contained — no external service integrations, no new hardware pipelines, and no dependencies on later phases.

### Image Generation & Visual Output — COMPLETE

ARIA can generate and push images to the phone, displayed via Tasker HTTP Server + Display Image.

**Generation Methods — Claude chooses based on context:**
- **FLUX.2** (`~/imgen/generate.py`) — photorealistic and artistic AI-generated images. "Show me a sunset over Lake Geneva" or "Generate a sci-fi landscape"
- **SUPIR** (`~/upscale/upscale4k.sh`) — upscale any generated or existing image to 4K
- **Matplotlib** — charts, graphs, data visualizations. "Graph my Xterra maintenance history" or "Show me my back pain pattern this month"
- **Graphviz** — diagrams, flowcharts, dependency graphs. "Draw a diagram of the ARIA architecture" or "Map out the project timeline"
- **SVG** — clean vector graphics, icons, simple illustrations. "Make me a floor plan" or "Draw a network diagram"

**Delivery Pipeline:**
- ARIA generates the image on beardos using whichever method fits the request
- Pushes the image to the phone via Tasker's HTTP Server
- Tasker receives and displays the image using Display Image action
- Voice response accompanies the image: "Here's that chart — take a look"

**Integration with Specialist Modules:**
- Vehicle maintenance timeline visualization
- Health pattern graphs (sleep, symptoms over time)
- Legal case timeline / relationship diagrams
- Project status visual overviews

### Specialist Modules — COMPLETE

**Legal Case File Assistant**
- Command: "Case update"
- JSON-backed log (`legal_store.py`) with entry types: development, filing, contact, note, court_date, deadline
- Surfaces upcoming dates, key contacts, recent developments
- Voice-log new developments with timestamps via ACTION blocks
- Entirely local — never cloud-synced; only upcoming dates appear in morning briefing (no case details surfaced unprompted)

**Vehicle Maintenance Log — Xterra**
- "Log Xterra — oil change today at [mileage]"
- "When did I last change my oil?"
- JSON-backed log (`vehicle_store.py`) with event types, mileage, cost tracking
- `get_latest_by_type()` surfaces last service date per type
- Recent entries appear in morning briefing

**Health & Physical Log**
- "Body log — back is sore, slept 6 hours"
- JSON-backed log (`health_store.py`) with categories: pain, sleep, exercise, symptom, medication, meal, nutrition, general
- Pattern detection: recurring symptoms, sleep averages, fish/omega-3 intake tracking
- Surfaces in morning brief if threshold crossed: "Back pain 4 of last 7 days"

**Diet & Nutrition Tracking**
- Meal logging via "meal" category in health_store
- `data/diet_reference.md` — trimmed dietary guidelines injected as context on food/nutrition keywords
- `data/health_profile.md` — comprehensive medical profile for future specialist AI use
- Diet day counter in morning briefings and evening debriefs
- Claude flags dietary deviations and encourages compliance (NAFLD-aware)

### Project Status Briefs — COMPLETE

Custom voice commands per project. Each brief is a markdown file in `data/projects/`. "Project update on ARIA" reads the relevant file and Claude summarizes current status, open questions, and next steps conversationally. If no specific project named, lists available briefs.

### Daily Debrief — Good Night (Basic Version) — COMPLETE

Triggered by "Good night", "end my day", "evening debrief", or "wrap up my day". Gathers from local data only (no transcript pipeline yet):

- Today's ARIA interactions from request log
- Today's calendar events and tomorrow's appointments (prep tonight)
- Active reminders carried forward
- Specialist log activity (vehicle, health, legal entries from today)
- Meals logged today and diet day counter
- Health patterns (last 7 days)
- Tonight/tomorrow weather forecast

*Upgrades automatically in Phase 6 when the ambient transcript pipeline exists — adds full day-of-conversation parsing, extracted commitments, and verbatim log comparison.*

### File Input — COMPLETE

Universal file input from phone via AutoShare share target + Tasker HTTP Request. Share any file from any app to ARIA.

- `POST /ask/file` accepts images, PDFs, text/code files, and unknown types
- Images sent as base64 visual content blocks; PDFs as document blocks; text files inline
- All received files saved to `data/inbox/` with timestamps for future reference
- Nutrition keywords in caption auto-inject diet reference for food photo analysis
- Supports both multipart form data and raw body with query params (Tasker compatible)
- Same async polling flow as voice requests (/ask/status, /ask/result)

### Proactive Nudges — COMPLETE

Autonomous nudge system via cron-based tick script (every minute). Python condition checks against all data stores, Claude composes natural SMS messages when conditions trigger:

- Meal gap detection (5+ hours without logging, noon-9pm)
- Calendar warnings (event in 15-45 minutes)
- Overdue reminders
- Diet compliance (evening check if <2 meals logged)
- Health pattern alerts
- Legal deadline warnings (within 3 days)
- Battery low alerts (<15%)
- Per-type cooldowns prevent nagging (meal: 4h, calendar: 30min, health: 24h, vehicle: 7d)
- Quiet hours (midnight-7am) suppress non-urgent nudges

*Upgrades automatically in Phase 6 when ambient transcripts provide richer source data for pattern-based nudges.*

### Autonomous Timer System — COMPLETE

ARIA can schedule her own future actions via ACTION blocks:

- Relative timers: "remind me in 30 minutes"
- Absolute timers: "remind me at 2:30 PM"
- SMS delivery (default) or voice push (explicit user opt-in only)
- Priority levels: urgent timers bypass quiet hours
- ARIA creates timers during conversations: "I'll follow up on that in 2 hours"
- Tick script checks for due timers every minute

### SMS/MMS Input — COMPLETE

ARIA has a Twilio phone number (+1 262-475-1990) for receiving SMS and MMS messages. Incoming messages are processed through the same Claude pipeline as voice and file input, with full context injection. MMS attachments (photos, files) are downloaded, saved to `data/inbox/`, and analyzed.

- `POST /sms` webhook receives messages from Twilio, validates signatures, handles A2P compliance (STOP/HELP)
- Exposed to the internet via Tailscale Funnel at `https://beardos.tail847be6.ts.net/webhook/sms`
- Outbound replies and proactive nudges via Twilio REST API (pending A2P 10DLC verification as of March 2026)
- Outbound MMS supported — images staged in `data/mms_outbox/` and served via Funnel for Twilio to fetch
- Same phone number will support voice calls in Phase 5 when Whisper STT is ready
- Privacy policy and terms of service hosted via GitHub Pages for compliance

### Phone Location Tracking — COMPLETE

Tasker reports GPS coordinates every 5 minutes. Reverse geocoded to street addresses via Nominatim (OpenStreetMap, free).

- Location history in `data/location.jsonl` with timestamps
- Latest position and battery level in morning briefings
- Movement history injected as context for location-related queries
- Geocode results cached by ~100m precision

### Context-Aware Reminders — Geofencing — COMPLETE

Originally deferred to Phase 5, but rendered unnecessary by the combination of GPS tracking (every 5 min), reverse geocoding, per-minute tick, and the nudge system. Location-triggered reminders are now a native feature:

- Reminders have optional `location` and `location_trigger` (arrive/leave) fields
- Known places mapped in config (home, work, my house, doctor) with partial address matching
- Tick script checks location reminders every minute against current GPS
- "Remind me when I get home to check the mail" → fires SMS when GPS shows you at home

---

## Phase 4: The Keystones
*Fitbit health data, nutrition tracking, Whisper STT, Google Calendar/Gmail — foundations that unlock everything downstream*

Phase 4 has six deliverables built in sequence. Fitbit integration comes first to establish a health baseline before the April 6 exercise start date. Nutrition label tracking formalizes the existing food photo workflow. Whisper is the keystone that gates the watch app, voicemail, and ambient recording. Calendar/Gmail delivers high daily utility. Smart alarm and SMS announce fall out naturally from the infrastructure.

### 1. Fitbit Integration — BUILD FIRST

Pixel Watch 4 + Pixel 10a Fitbit data pulled into ARIA via the Fitbit Web API. Establishes pre-exercise health baseline.

- **Data available (all free, no Premium needed):** heart rate (resting + 1-sec intraday), HRV (RMSSD, LF, HF at 5-min intervals), SpO2, sleep stages (light/deep/REM/wake with timestamps), steps/distance/floors/calories burned, Active Zone Minutes, exercise sessions, breathing rate (per sleep stage), skin temperature variation, VO2 Max (cardio fitness), ECG
- **Computed scores (Readiness, Stress, Sleep Score) have no API endpoints** — but all underlying raw data is available, so ARIA computes her own equivalents contextualized against the full health profile (NAFLD, spinal injury, diet)
- Register a "Personal" app at dev.fitbit.com — grants full intraday access to own data at no cost
- OAuth2 PKCE flow, one-time browser auth, tokens stored in config.py
- Subscription API webhooks: Fitbit notifies `beardos:8450/webhook/fitbit` when the watch syncs (~every 15 min), ARIA fetches updated data via Web API
- Rate limit: 150 requests/hour (webhook-driven fetches use ~10-20 per sync, plenty of headroom)
- Fitbit Premium ($9.99/mo) is NOT needed — it only affects the in-app experience (guided workouts, coaching, sleep profiles), not API data access. Pixel Watch 4 includes 6-month free trial regardless.

#### Architecture

```
Pixel Watch 4 → Bluetooth → Fitbit App on Pixel 10a → Fitbit Cloud
                                                          │
         Subscription webhook ──► beardos:8450/webhook/fitbit
                                                          │
                                    Fetch via Fitbit Web API
                                                          │
                                    fitbit_store.py (JSON-backed)
```

#### New Files
- `fitbit.py` — API client (httpx), token auto-refresh, data type fetchers
- `fitbit_store.py` — JSON-backed store for daily summaries and intraday snapshots

#### Integration Points
- Morning briefing: last night's sleep stages, resting HR, HRV trend, VO2 Max
- Evening debrief: daily activity summary, calories burned, Active Zone Minutes
- Health keyword context injection: Fitbit data alongside existing health_store entries
- ARIA computes Readiness/Stress/Sleep scores from raw data with full health context
- Nudge conditions: abnormal resting HR, poor sleep quality, inactivity alerts

### 2. Nutrition Tracking from Label Photos

Formalize the existing food photo workflow into structured per-item nutrition logging with daily totals and limit checking.

- New `nutrition_store.py` — structured entries with full nutrient breakdown (calories, protein, fat, carbs, fiber, sugar, sodium, omega-3, etc.), serving size tracking, daily totals computation
- New ACTION block: `log_nutrition` — Claude extracts all values from nutrition label photos and logs structured entries
- `get_daily_totals(date)` sums entries, `check_limits()` compares against diet_reference targets
- **Claude vision handles OCR** — nutrition labels are high-contrast standardized text, ideal for vision models. No Tesseract/OCR pipeline needed.
- Serving size handling: store per-serving values as printed, `servings` field for how much was actually consumed, multiply at query time
- Factor/CookUnity meal cache: photograph label once, subsequent orders match by name
- For foods without labels: USDA FoodData Central API (free, 380k+ foods) for ingredient lookups (smoothie components, fresh produce). Restaurant meals estimated by Claude and flagged as estimates.
- Running daily totals injected into context so Claude can say "That puts you at 1,450 calories for the day"
- Alerts when approaching limits (especially added sugar <36g, saturated fat <15g for NAFLD)

#### Data Structure (per-item)
```json
{
    "id": "a3f8b2c1",
    "date": "2026-03-19",
    "time": "12:30",
    "meal_type": "lunch",
    "food_name": "Factor Grilled Chicken with Roasted Vegetables",
    "source": "label_photo",
    "servings": 1.0,
    "serving_size": "1 container (283g)",
    "nutrients": {
        "calories": 450, "protein_g": 38, "total_fat_g": 18,
        "saturated_fat_g": 5, "total_carb_g": 32, "dietary_fiber_g": 6,
        "total_sugars_g": 8, "added_sugars_g": 2, "sodium_mg": 680,
        "omega3_mg": null
    },
    "notes": ""
}
```

### 3. Whisper STT on Beardos — KEYSTONE

Local speech-to-text on the RTX 3090 using `faster-whisper` with the `large-v3` model. faster-whisper is NOT a lower-quality model — it runs identical Whisper weights on an optimized CTranslate2 runtime, producing the same output ~4x faster with ~3x less VRAM.

- New endpoint: `POST /ask/stt` — accepts audio upload (WAV/WEBM/OGG), returns transcribed text
- Sub-second latency for short voice commands on the 3090 (~0.5-1s for a 10-second clip)
- ~3GB VRAM (plenty of headroom alongside Kokoro TTS and FLUX image gen)
- **Does NOT replace Android STT on the phone** — Pixel 10a's on-device Google STT is fast and accurate for short commands. Phone flow stays as-is.
- **Gates downstream:** Watch app (Phase 5) — no good on-device STT, needs server-side. Voicemail transcription (Phase 5). Ambient recording pipeline (Phase 6 — DJI Mic 3).
- Phone can optionally switch to Whisper later if desired, but no urgency

### 4. Google Calendar Integration

Read/write Google Calendar access — real appointments replace or supplement the local calendar store.

- Google Cloud project + OAuth2 credentials, one-time browser auth flow
- New `gcal.py` — wraps Google Calendar API, returns events in same format as calendar_store.py
- Merge calendar sources: Google Calendar for events, local store for ARIA-specific reminders
- ACTION blocks (add/modify/delete event) write to Google Calendar
- Periodic sync (every 5-15 min) caches events locally for fast briefings and offline fallback

### 5. Gmail Integration

IMAP/Gmail API email monitoring — Claude summarizes and triages incoming mail.

- Reuses Google Cloud project from calendar step
- New `email_monitor.py` — polls for new messages, extracts sender/subject/snippet
- Claude one-line summaries: "3 new emails — one from your doctor's office, two from mailing lists"
- Morning briefing: overnight important emails, mailing lists suppressed by default
- Auto-extract appointments and deadlines from emails into Google Calendar
- Configurable priority: sender allowlist in config.py, Claude judges the rest

### 6. Smart Alarm + SMS Announce

Falls naturally out of the above infrastructure:

- **Smart Alarm:** "Wake me at [time] with a briefing" — sets urgent timer + queues morning brief on dismissal. ~30 lines in process_actions() + tick.py.
- **SMS Announce:** Tasker monitors incoming SMS, POSTs to daemon for Claude summarization, ARIA speaks it aloud. Configurable interrupt/quiet hours rules. ~50 lines.

---

## Phase 5: Communications Stack & Wearable
*Smartwatch app, full two-way comms control, smart filtering*

Phase 5 builds the full communications layer and the watch interface. The watch app requires Whisper (Phase 4) to be useful. The comms features build on the SMS and email foundations laid in Phase 4.

### Smartwatch Integration — Pixel Watch 4

Custom WearOS app on the Pixel Watch 4 using the side button (`KEYCODE_STEM_1`) as a hold-to-talk trigger. The crown button (`KEYCODE_POWER`) is system-reserved and cannot be remapped.

- **Hold side button** → starts recording from watch mic
- **Release side button** → stops recording, ships audio to beardos via Tailscale
- **Whisper on beardos** (RTX 3090) transcribes the audio, feeds text into existing ARIA pipeline
- Short responses displayed as watch notification + haptic
- Longer responses play through phone speaker
- Quick-action tiles: Morning Brief, Add Reminder, Good Night

#### Why Not WearOS STT APIs

WearOS on the Pixel Watch 4 has severe limitations for third-party voice input — previously attempted and abandoned. The side button + audio upload approach bypasses all WearOS STT restrictions.

### Outgoing SMS by Voice

- "Text [person] — tell them [X]"
- ARIA drafts the message, reads it back for confirmation
- "Send it" / "change it to [X]" / "cancel"
- Sent via Tasker SMS automation

### Outgoing Email by Voice

- "Send an email to [person] about [X]"
- Claude drafts the full email, reads subject and body back to you
- Approve, edit by voice, or cancel before sending
- Sent via SMTP on the daemon side

### Incoming Call Handling

- Incoming call announced by name: "Call coming in from Dad"
- Respond by voice: "answer" or "ignore"
- Missed call alert surfaced immediately or at next brief depending on priority rules
- "Call [person]" / "Call [person] back" — initiates via Tasker

### Voicemail Transcription

- New voicemail transcribed locally via Whisper (requires Phase 4 keystone)
- ARIA reads the transcription aloud
- "You have a voicemail from an unknown number — they said [transcript]"
- Original audio retained locally if you need to replay it

### Smart Filtering & Priority Rules

- Configurable quiet hours — no interruptions except allowlisted contacts
- "Only interrupt me for calls from [person] right now"
- VIP list: contacts who always break through regardless of mode
- Low-priority senders (mailing lists, spam) batched into morning/evening brief only
- Morning brief includes: overnight missed calls, voicemail transcripts, important emails

### Voice Query Commands

- "Read me my emails from today"
- "Any texts from [person] this week?"
- "Did [person] call while I was at work?"
- "What did [person]'s voicemail say?"
- "Do I have any unread messages?"

#### Implementation Notes

- **SMS in/out:** Tasker on Android — monitors incoming, fires outgoing
- **Calls:** Tasker call monitoring + Android dialer integration
- **Voicemail:** carrier voicemail forwarded to email as audio attachment, or Google Voice for direct access; Whisper transcribes locally
- **Email:** IMAP polling on the FastAPI daemon; SMTP for outgoing
- **All message content processed locally** — Claude summaries generated on your hardware, nothing forwarded to external APIs except the Claude API call itself

---

## Phase 6: Total Recall — Ambient Logging & AI Memory
*All-day audio capture via DJI Mic 3, Whisper transcription, vector search, promise tracking, upgraded debrief*

Phase 6 transforms ARIA from a reactive assistant into a passive life-logging system. Everything you say throughout the day is captured, transcribed, and made searchable — so you can perfectly recall any conversation, extract commitments made or received, and let Claude surface things you forgot to explicitly log. This also absorbs "brain dump" functionality — no need for a dedicated voice command when ARIA is already listening and extracting everything continuously.

This phase also upgrades the basic Good Night debrief and proactive nudges from Phase 3 to use full transcript data, and adds person-based and topic-based context-aware reminders that depend on conversation logging.

### Recording Hardware — DJI Mic 3

The DJI Mic 3 is a clip-on wireless microphone (purchased, on hand) that serves as the primary ambient recording device.

- Clips to clothing, records all-day audio
- Audio syncs to phone, which relays to beardos over Tailscale for processing
- High-quality directional mic designed for voice — better capture than phone or watch mic
- Long battery life designed for all-day professional use

### Processing Pipeline

```
[Ambient audio chunks]
      ↓ sync over Tailscale
[PC receives chunks]
      ↓ Whisper (local, GPU-accelerated)
[Raw transcripts with timestamps]
      ↓ Claude
[Structured extraction]
      ↓
[Promises / commitments / names / decisions / action items]
      ↓
[Qdrant vector DB + flat timestamped log files]
```

### What Claude Extracts From Every Transcript

- **Commitments you made:** "I'll call you back Thursday", "I'll bring that tool tomorrow"
- **Commitments others made to you:** "I'll have that ready by Friday"
- **Names and context:** who you talked to and what about
- **Decisions made:** "decided to reschedule the dentist"
- Anything that sounds like it should be a reminder
- Emotional tone flags (optional): stress, frustration, fatigue markers

### Voice Recall Queries

Natural language queries against your full log archive via Qdrant semantic search:

- "What did I tell Mike last Tuesday?"
- "Did I promise anything to my sister this month?"
- "What was that conversation about the schedule change at work?"
- "What did the doctor say at my last appointment?"
- "What have I said about [topic] in the last 30 days?"

### Promise Tracker

- Dedicated view of open commitments — yours and others'
- Automatically marked complete when you mention the task is done
- Escalates in morning brief if overdue
- "What promises am I tracking?" gives full rundown

### Person Profiles

Claude auto-builds a contact profile for anyone who appears frequently in your logs:

- "Who is Dave?" → "Dave appears in 12 conversations since October. Works with you at the factory. You've discussed the crane twice and he owes you $40 from November."
- Profiles update automatically as new conversations are processed
- Queryable by voice at any time

### Conversation Summaries

- Every day gets a one-paragraph auto-summary: who you talked to, what happened
- Searchable and browsable by date
- "Summarize last Tuesday" works as a voice command

### Emotional Pattern Logging *(optional)*

Claude tracks tone and stress patterns across your logs:

- "You've seemed frustrated at work 4 of the last 5 days" — surfaced in morning brief
- Sleep quality correlations if you voice-log sleep
- Physical symptom patterns cross-referenced with work schedule

### Evening Debrief — Upgraded

Replaces the basic Phase 3 Good Night debrief. Now parses actual full transcript logs:

- Summary of useful information gathered throughout the day
- Comparisons between extracted information and verbatim logs — lets you verify ARIA understood everything correctly
- Summary of what was completed today
- Pending items carried forward
- Extracted commitments surfaced for confirm/dismiss: "You told someone you'd call back Thursday — add as reminder?"
- Prep reminders for tomorrow
- Option to set next-morning alarm

### Context-Aware Reminders — Person & Topic Based

These require conversation logging to work well, so they live here rather than earlier:

- "Remind me next time I talk to [person]" — stored as fuzzy context trigger, matched against transcript data
- "Remind me if I mention [topic]" — Claude checks against ongoing conversation context

### Always-On Whisper Pi Node

*Pulled forward from Embodiment phase — this is a Phase 6 companion feature.*

- Dedicated Pi running Whisper for real-time ambient transcription
- Offloads transcription from the RTX 3090, freeing it for image gen and other GPU tasks
- Feeds the Total Recall pipeline continuously without taxing beardos
- Only needed once ambient recording is running continuously and creating GPU contention

### Privacy Architecture

- Everything stays local — no cloud, no Anthropic servers for audio or transcripts
- Whisper runs entirely on your GPU
- Qdrant vector DB runs locally
- "Don't log this conversation" voice command pauses recording
- Auto-delete audio after transcription option (keep only text)
- Wisconsin is a one-party consent state — recording your own conversations is legal
- Phone calls are governed by different rules — know the limits
- Legal case logs: keep especially locked down, never synced anywhere external

---

## Resilience & Error Handling
*Never let a failure produce silence*

The core principle: every failure mode must produce a spoken response. Silence is the worst UX for a voice assistant — you don't know if it heard you, if it's thinking, or if it's dead.

### Phone-Side (Tasker/Termux)

**Request Queuing**
- If the PC doesn't respond within a configurable timeout, save the request locally with a timestamp
- Retry on a backoff schedule (e.g., 5s, 15s, 30s, 60s)
- When the PC comes back online, drain the queue in order
- TTS acknowledgment on queue: "Your PC is offline — I've saved that and will process it when it's back"

**Offline Awareness**
- Periodic Tailscale ping to the PC (via Tasker) to maintain an ambient "brain is online" status
- Persistent notification or watch tile showing connection state
- If the daemon is unreachable at the time of a request, respond immediately with a spoken error instead of hanging

### Daemon-Side (FastAPI on PC)

**Health Endpoint**
- `/health` endpoint that Tasker pings periodically — returns daemon status, uptime, and whether Claude Code is responsive
- Lets the phone know if the brain is available before attempting a full request

**Request Logging**
- Every incoming request logged with timestamp and status: received, processing, completed, or failed
- If Claude Code hangs or crashes mid-request, the log provides a trail for debugging
- Failed requests logged with error details for review

**Subprocess Timeout**
- Hard timeout on every Claude Code CLI invocation
- If Claude Code doesn't respond within the limit, return a graceful error to the phone: "That took too long — try again or simplify the request"
- Prevents a single hung subprocess from blocking the daemon

**Watchdog & Auto-Recovery**
- OpenRC service definition for the FastAPI daemon with automatic restart on crash
- Optional: push a notification to the phone when the daemon restarts so you know it happened
- Periodic self-check: daemon verifies it can still reach Claude Code and reports degraded status if not

### Network (Tailscale)

- Tailscale handles reconnection automatically, but the phone should track reachability independently
- On Tailscale dropout, phone-side queuing kicks in — no requests are lost
- On recovery, queued requests drain automatically and results are spoken in order

### Failure Response Examples

| Failure | User Hears |
|---------|------------|
| PC offline | "Your PC is offline. I've saved your request and will process it when it's back." |
| Daemon crashed | "Something went wrong on the server side. Retrying..." (then auto-recovery) |
| Claude Code timeout | "That request timed out. Try again or try a simpler question." |
| Tailscale down | "I can't reach your network right now. Request saved." |
| STT failed to parse | "I didn't catch that. Can you say it again?" |

---

## Phase 7: Personalized Model
*LoRA fine-tuning, Neo4j relational memory, fully individualized AI*

**IMPORTANT: Do not build before Phase 6 generates real transcript data.** Fine-tuning on thin data produces poor results. This phase requires months of accumulated interaction logs and ambient transcripts to be effective.

### Memory Architecture — Four Layers

ARIA's long-term intelligence is built on four distinct memory layers, each serving a different purpose:

**Layer 1: Qdrant Vector Memory + Neo4j Relational Graph**
- Based on the proven `buddy` project (github.com/expectbugs/buddy) — confirmed working, all known bugs resolved
- Qdrant provides semantic similarity search across all stored knowledge
- Neo4j knowledge graph provides relational reasoning — nodes and edges connecting people, events, commitments, and relationships
- Enables queries like "what do I know about Heidi?" via graph traversal rather than similarity search alone
- Both services already running on beardos and battle-tested

**Layer 2: Automated LoRA Retraining**
- Periodic LoRA fine-tuning on beardos RTX 3090 (weekly or monthly schedule)
- Training data: mix of real ARIA interactions and synthetic task completions
- Primary goal: teach ARIA what tools and capabilities she actually has, eliminating false "I can't do that" responses when she can
- Secondary goal: tune communication style, vocabulary, and priorities to one person
- Over time ARIA becomes specifically tuned — not a general assistant

**Layer 3: Multi-Agent Orchestration**
- Based on the proven `agents` project (github.com/expectbugs/agents) — LangGraph multi-agent system, confirmed working
- Complex tasks decomposed and routed to specialized sub-agents
- Persistent memory across agent workflows

**Layer 4: Full-Context Interaction Logs**
- Complete verbatim logs of every ARIA interaction — no summarization, no lossy compression
- ARIA can reference exact quotes from past conversations
- Feeds the LoRA retraining pipeline (Layer 2) with real interaction data
- Enables "what exactly did I say about X last Tuesday?" with word-for-word accuracy
- Complements Qdrant semantic search (Layer 1) — search finds it, logs provide the full original context

### Outcome

At Phase 7 completion, ARIA is a genuinely unique AI — self-hosted, fully private, trained on one person's life, with relational memory spanning years of interactions. Not a general assistant shared with millions of users, but something built around exactly one person.

---

## Phase 8: Embodiment
*Raspberry Pi sensor and control nodes, physical environment integration*

**NOTE: Relatively independent of the AI stack. Can be parallelized with Phase 7 if desired.** The Always-On Whisper Pi Node has been pulled forward to Phase 6 where it's actually needed.

### Voice-Controlled Physical Devices

- Raspberry Pi Zero nodes embedded in physical devices, communicating back to beardos over Tailscale
- Each node runs a lightweight listener that accepts commands from the ARIA daemon
- Examples:
  - **Roomba / robot vacuum** — "Clean the living room" triggers a cleaning cycle
  - **HVAC** — voice control for thermostat, scheduling, and status
  - **Door locks / security** — arm, disarm, check status by voice
  - **Lights** — full smart lighting control without a third-party cloud

### Vehicle Integration — Xterra

- Pi with OBD-II adapter provides live vehicle diagnostics over Tailscale
- "What's my oil life?" / "Any fault codes?" answered in real time
- Feeds directly into the Phase 3 vehicle maintenance log
- Mileage, fuel, and service tracking fully automated

### Presence Detection & Ambient Automation

- Pi-based presence detection on the home network
- ARIA knows when you're home vs. away and arms/disarms automations automatically
- No voice trigger required — state changes happen on arrival and departure

### Outcome

Phase 8 gives ARIA a physical presence — not just software on a laptop but a distributed network of ears, eyes, and hands throughout your environment. Voice commands cross the boundary from digital to physical seamlessly.

---

## Phase Summary

| Phase | Title | Key Deliverable |
|-------|-------|-----------------|
| Phase 1 | Core Loop | **COMPLETE.** Morning brief, weather, calendar, basic voice commands. End-to-end pipeline proven. |
| Phase 2 | Migration & Failover | **COMPLETE.** Beardos primary, slappy warm standby, automatic failover via Tasker, rsync data sync. |
| Phase 3 | Self-Contained Features | **COMPLETE.** Image gen, specialist logs (vehicle/health/legal), diet/nutrition tracking, project briefs, daily debrief, file input, SMS/MMS, location tracking, autonomous timers, proactive nudges, location-based reminders (geofencing). |
| Phase 4 | The Keystones | 1) Fitbit integration (health baseline before April 6 exercise start). 2) Nutrition label photo tracking. 3) Whisper STT (keystone — gates Phases 5-6). 4) Google Calendar. 5) Gmail. 6) Smart alarm + SMS announce. |
| Phase 5 | Comms & Wearable | Pixel Watch 4 hold-to-talk, full two-way comms (SMS, email, calls, voicemail), smart filtering & quiet hours. |
| Phase 6 | Total Recall | DJI Mic 3 ambient recording, Whisper transcription pipeline, Qdrant recall, promise tracker, person profiles, upgraded debrief, person/topic-based reminders, Whisper Pi node. |
| Phase 7 | Personalized Model | LoRA fine-tuning on interaction history, Neo4j relational graph memory, multi-agent orchestration. Requires Phase 6 data. |
| Phase 8 | Embodiment | Raspberry Pi nodes for physical device control, vehicle OBD-II integration, presence detection. Can parallelize with Phase 7. |

---

*Next Step: Phase 4.1 — Fitbit integration (establish health baseline before April 6 exercise start).*
