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
| slappy | Failover | 100.70.66.104 | None (integrated) | TTS + Claude only, service installed but not in default runlevel |

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
- Slappy's ARIA service stays installed but not in default runlevel — starts on boot only if manually added

### Data Synchronization

- rsync cron on beardos pushes `data/` to slappy every 5 minutes over SSH (port 80)
- During failover, slappy operates on last-synced data — brief staleness is acceptable
- After a failover period, manually sync slappy's data back to beardos before resuming
- Future upgrade path: Syncthing or SQLite on shared storage for real-time sync

---

## Phase 3: Memory & Proactive Intelligence
*Good Night debrief, proactive nudges, specialist logs, image generation, and pattern awareness*

### Daily Debrief — Good Night

Mirror of the morning brief. Triggered by "Good night" or a button press:

- Summary of useful information gathered throughout the day
- Comparisons between extracted information and verbatim logs — lets you verify ARIA understood everything correctly
- Summary of what was completed today
- Pending items carried forward
- Prep reminders for tomorrow
- Option to set next-morning alarm

### Proactive Nudges

Claude reviews your conversation and note history and surfaces follow-ups:

- "You mentioned calling the insurance company last Tuesday — still pending"
- "You haven't logged your Xterra mileage in 3 weeks"
- Pattern-based nudges require conversation logging (Phase 5 accelerates this)

### Project Status Briefs

Custom voice commands per project. Each brief reads a structured notes file and summarizes current status, open questions, and next steps. You define what each project brief contains.

### Image Generation & Visual Output

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

### Specialist Modules

**Legal Case File Assistant**
- Command: "Case update"
- Reads a structured local file about your Walworth County case
- Surfaces upcoming dates, key contacts, recent developments
- Lets you voice-log new developments with timestamps
- Entirely local — never cloud-synced

**Vehicle Maintenance Log — Xterra**
- "Log Xterra — oil change today at [mileage]"
- "When did I last change my oil?"
- Tracks all maintenance events and surfaces overdue service

**Health & Physical Log**
- "Body log — back is sore, slept 6 hours"
- Claude tracks patterns over time
- Surfaces in morning brief if threshold crossed: "Back pain 4 of last 7 days"

---

## Phase 4: Deep Integrations & Wearable Support
*Smartwatch, context-aware reminders, full communications control*

### Smartwatch Integration — Pixel Watch 4

Custom WearOS app on the Pixel Watch 4 using the side button (`KEYCODE_STEM_1`) as a hold-to-talk trigger. The crown button (`KEYCODE_POWER`) is system-reserved and cannot be remapped.

- **Hold side button** → starts recording from watch mic
- **Release side button** → stops recording, ships audio to beardos via Tailscale
- **Whisper on beardos** (RTX 3090) transcribes the audio, feeds text into existing ARIA pipeline
- Bypasses Android STT entirely — no more premature cutoff on pauses, full control over recording duration
- Short responses displayed as watch notification + haptic
- Longer responses play through phone speaker
- Quick-action tiles: Morning Brief, Add Reminder, Good Night

#### Why Not Android STT

Android's built-in speech recognizer has a fixed silence detection timeout that cannot be configured. Even a brief pause to take a breath triggers end-of-recording. Whisper on the 3090 transcribes the full audio clip regardless of pauses, adding only ~1-2s of latency for short commands.

#### Why Not WearOS STT APIs

WearOS on the Pixel Watch 4 has severe limitations for third-party voice input — previously attempted and abandoned. The side button + audio upload approach bypasses all WearOS STT restrictions.

### Context-Aware Reminders

Not just time-based — situation-based reminders:

- "Remind me when I get home to [X]" — uses phone GPS geofencing
- "Remind me next time I talk to [person]" — stored as fuzzy context trigger
- "Remind me if I mention [topic]" — Claude checks against conversation context

### Smart Alarm Integration

- "Wake me at [time] with a briefing" — sets alarm AND queues morning brief
- Alarm dismissal triggers the brief automatically

### Communications Module

Full two-way voice control of texts, email, calls, and voicemail.

#### Incoming Notifications — ARIA Tells You

**SMS/Texts**
- ARIA announces new texts aloud: "Text from Mike — he says he'll be 10 minutes late"
- Tasker monitors incoming SMS and POSTs to the ARIA daemon for summarization
- Configurable interrupt rules — suppress during sleep/focus hours

**Email**
- ARIA monitors inbox via IMAP on the daemon side
- New emails announced with sender, subject, and a one-sentence Claude summary
- "You have 3 new emails — one from your doctor's office, two from mailing lists"
- Morning brief includes overnight important emails; mailing lists suppressed by default

**Calls**
- Incoming call announced by name: "Call coming in from Dad"
- Respond by voice: "answer" or "ignore"
- Missed call alert surfaced immediately or at next brief depending on priority rules

**Voicemail**
- New voicemail transcribed locally via Whisper
- ARIA reads the transcription aloud
- "You have a voicemail from an unknown number — they said [transcript]"
- Original audio retained locally if you need to replay it

#### Outgoing Control — You Tell ARIA

**Texts**
- "Text [person] — tell them [X]"
- ARIA drafts the message, reads it back for confirmation
- "Send it" / "change it to [X]" / "cancel"
- Sent via Tasker SMS automation

**Email**
- "Send an email to [person] about [X]"
- Claude drafts the full email, reads subject and body back to you
- Approve, edit by voice, or cancel before sending
- Sent via SMTP on the daemon side

**Calls**
- "Call [person]" — initiates via Tasker
- "Call [person] back" — returns missed call

#### Voice Query Commands

- "Read me my emails from today"
- "Any texts from [person] this week?"
- "Did [person] call while I was at work?"
- "What did [person]'s voicemail say?"
- "Do I have any unread messages?"

#### Smart Filtering & Priority Rules

- Configurable quiet hours — no interruptions except allowlisted contacts
- "Only interrupt me for calls from [person] right now"
- VIP list: contacts who always break through regardless of mode
- Low-priority senders (mailing lists, spam) batched into morning/evening brief only
- Morning brief includes: overnight missed calls, voicemail transcripts, important emails

#### Implementation Notes

- **SMS in/out:** Tasker on Android — monitors incoming, fires outgoing
- **Calls:** Tasker call monitoring + Android dialer integration
- **Voicemail:** carrier voicemail forwarded to email as audio attachment, or Google Voice for direct access; Whisper transcribes locally
- **Email:** IMAP polling on the FastAPI daemon; SMTP for outgoing
- **All message content processed locally** — Claude summaries generated on your hardware, nothing forwarded to external APIs except the Claude API call itself

---

## Phase 5: Total Recall — Ambient Logging & AI Memory
*All-day audio capture via DJI Mic 3, Whisper transcription, vector search, promise tracking*

Phase 5 transforms ARIA from a reactive assistant into a passive life-logging system. Everything you say throughout the day is captured, transcribed, and made searchable — so you can perfectly recall any conversation, extract commitments made or received, and let Claude surface things you forgot to explicitly log. This also absorbs "brain dump" functionality — no need for a dedicated voice command when ARIA is already listening and extracting everything continuously.

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

### Evening Debrief Integration

Extracted items surface in your Good Night debrief:

- "You told someone you'd call back Thursday — add as reminder?"
- "Dave said he'd have the part ready by Friday — want me to track that?"
- You confirm or dismiss each item by voice

### Voice Recall Queries

Natural language queries against your full log archive via Qdrant semantic search:

- "What did I tell Mike last Tuesday?"
- "Did I promise anything to my sister this month?"
- "What was that conversation about the schedule change at work?"
- "What did the doctor say at my last appointment?"
- "What have I said about [topic] in the last 30 days?"

### Person Profiles

Claude auto-builds a contact profile for anyone who appears frequently in your logs:

- "Who is Dave?" → "Dave appears in 12 conversations since October. Works with you at the factory. You've discussed the crane twice and he owes you $40 from November."
- Profiles update automatically as new conversations are processed
- Queryable by voice at any time

### Promise Tracker

- Dedicated view of open commitments — yours and others'
- Automatically marked complete when you mention the task is done
- Escalates in morning brief if overdue
- "What promises am I tracking?" gives full rundown

### Conversation Summaries

- Every day gets a one-paragraph auto-summary: who you talked to, what happened
- Searchable and browsable by date
- "Summarize last Tuesday" works as a voice command

### Emotional Pattern Logging *(optional)*

Claude tracks tone and stress patterns across your logs:

- "You've seemed frustrated at work 4 of the last 5 days" — surfaced in morning brief
- Sleep quality correlations if you voice-log sleep
- Physical symptom patterns cross-referenced with work schedule

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

## Phase 6: Personalized Model
*LoRA fine-tuning, Neo4j relational memory, fully individualized AI*

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

At Phase 6 completion, ARIA is a genuinely unique AI — self-hosted, fully private, trained on one person's life, with relational memory spanning years of interactions. Not a general assistant shared with millions of users, but something built around exactly one person.

---

## Phase 7: Embodiment
*Raspberry Pi sensor and control nodes, physical environment integration*

### Voice-Controlled Physical Devices

- Raspberry Pi Zero nodes embedded in physical devices, communicating back to slappy over Tailscale
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

### Always-On Whisper Node

- Dedicated Pi running Whisper for real-time ambient transcription (Phase 5 companion)
- Offloads transcription from slappy, keeping latency low even during heavy use
- Feeds the Total Recall pipeline continuously without taxing the main machine

### Outcome

Phase 7 gives ARIA a physical presence — not just software on a laptop but a distributed network of ears, eyes, and hands throughout your environment. Voice commands cross the boundary from digital to physical seamlessly.

---

## Phase Summary

| Phase | Title | Key Deliverable |
|-------|-------|-----------------|
| Phase 1 | Core Loop | **COMPLETE.** Morning brief, weather, calendar, basic voice commands. End-to-end pipeline proven. |
| Phase 2 | Migration & Failover | **COMPLETE.** Beardos primary, slappy warm standby, automatic failover via Tasker, rsync data sync. |
| Phase 3 | Memory & Intelligence | Good Night debrief, proactive nudges, image generation & push-to-phone, legal/vehicle/health logs. |
| Phase 4 | Deep Integrations | Pixel Watch 4 hold-to-talk (Whisper STT), context-aware reminders, GPS triggers, full communications control. |
| Phase 5 | Total Recall | DJI Mic 3 ambient recording, Whisper transcription, brain dump via continuous extraction, Qdrant recall, promise tracker, person profiles. |
| Phase 6 | Personalized Model | LoRA fine-tuning on interaction history, Neo4j relational graph memory, fully individualized AI. |
| Phase 7 | Embodiment | Raspberry Pi nodes for physical device control, vehicle OBD-II integration, presence detection, always-on Whisper node. |

---

*Next Step: Spec and build Phase 3 — Memory & Proactive Intelligence.*
