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
- **Laptop:** Gentoo Linux, OpenRC, Claude Code, imgen alias, secondary compute
- **Phone:** Android (Motorola), Termux, Termius, Tasker, STT/TTS layer
- **Smartwatch:** WearOS voice trigger, relays to phone
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

- **STT:** Android built-in speech recognition via Tasker HTTP POST
  - Tasker: $4.99 on Google Play (by João Dias — not TaskRabbit or "Tasker for Engineers")
- **Daemon:** FastAPI on PC, single `/ask` endpoint, receives transcribed text over Tailscale
- **Claude:** invoked via `claude` CLI subprocess (Claude Code, authenticated via your Max subscription — no separate API billing)
- **TTS:** Android built-in (Tasker) or Piper local TTS for better voice quality
- **Response:** text returned to phone, spoken aloud

### Billing Clarification

All voice commands that go through Claude Code use your existing Max subscription ($200/mo). No separate API key or pay-per-token billing is needed for Phase 1 or Phase 2. A direct Anthropic API key (console.anthropic.com, pay-per-token) would only be needed if you add lightweight non-agentic queries that bypass the Claude Code CLI entirely — and even then, ARIA usage would be a few dollars a month at most.

---

## Phase 2: Machine Control
*SSH, PTY management, system commands, Claude Code integration*

### System Commands

- "Update my PC" / "Update my laptop" — runs `pacman -Syu`, reports results
- "Check disk space on [machine]"
- "What's running on my PC?"
- "Start/Stop [service]"
- "Reboot [machine]"
- "Is [machine] online?" — Tailscale ping check

### Creative & AI Commands

- "Generate an image of [prompt]" — runs `imgen` alias, returns result path to phone
- "Write a note about [X] and save it"

### Claude Code Voice Permission System

Claude Code in interactive mode requests confirmation before executing actions. The PTY manager handles this via voice:

- Claude Code runs inside a pseudo-terminal (PTY) managed by the daemon
- Permission requests are read aloud: "I'm about to run `pacman -Syu`. Proceed?"
- You respond: **yes, no, skip,** or **yes to all**
- Daemon injects the appropriate keypress into the PTY
- **Trust list:** pre-authorized safe commands skip confirmation entirely
  - Auto-approved: system updates, calendar reads, weather, disk checks, service status
  - Always confirm: file deletion, stopping critical services, network changes

---

## Phase 3: Memory & Proactive Intelligence
*Conversation logging, project briefs, nudges, and pattern awareness*

### Brain Dump Command

Say "brain dump" and speak freely for up to a minute. Claude:

- Transcribes everything
- Extracts action items and adds them to reminders/calendar automatically
- Saves the remainder as a timestamped note
- Confirms extracted items aloud before saving

### Daily Debrief — Good Night

Mirror of the morning brief. Triggered by "Good night" or a button press:

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

### Smartwatch Integration

- WearOS voice trigger fires and hands off to phone
- Short responses displayed as watch notification + haptic
- Longer responses play through phone speaker
- Quick-action tiles: Morning Brief, Brain Dump, Add Reminder, Good Night

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
*All-day audio capture, Whisper transcription, vector search, promise tracking*

Phase 5 transforms ARIA from a reactive assistant into a passive life-logging system. Everything you say throughout the day is captured, transcribed, and made searchable — so you can perfectly recall any conversation, extract commitments made or received, and let Claude surface things you forgot to explicitly log.

### Hardware Options for Ambient Recording

- **Phone (primary):** screen-off background recording via Termux or dedicated recorder app. Battery is the main constraint — keep charger handy.
- **Clip-on Bluetooth mic:** always hot, streams to phone. Good for factory environment.
- **Dedicated always-on device:** rooted cheap Android or Raspberry Pi Zero W in pocket with small mic. Ugly but reliable.
- **Purpose-built wearable:** Limitless AI pendant or Plaud Note. Purpose-built for this — Limitless does AI transcription and recall out of the box. Worth evaluating even if building custom.
- **Smartwatch mic:** useful for targeted captures, not ideal for all-day passive recording.

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

## Phase Summary

| Phase | Title | Key Deliverable |
|-------|-------|-----------------|
| Phase 1 | Core Loop | Morning brief, weather, calendar, basic voice commands. End-to-end pipeline proven. |
| Phase 2 | Machine Control | SSH/PTY management, system commands, Claude Code with voice permission handling. |
| Phase 3 | Memory & Intelligence | Brain dump, Good Night debrief, proactive nudges, legal/vehicle/health logs. |
| Phase 4 | Deep Integrations | Smartwatch, context-aware reminders, GPS triggers, full communications control (SMS, email, calls, voicemail). |
| Phase 5 | Total Recall | All-day ambient recording, Whisper transcription, Qdrant recall, promise tracker, person profiles. |

---

*Next Step: Spec and build Phase 1 — FastAPI daemon, Tasker integration, and the morning briefing pipeline.*
