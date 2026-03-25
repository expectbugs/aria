# ARIA Context Builder Rebuild — Implementation Record

## Overview

This document describes the planned and completed changes to `context.py` — ARIA's context injection system. The upgrade moves from a blunt keyword-dump approach to a tiered system that guarantees critical data is always present while reducing noise and preventing context window exhaustion.

Implemented across 4 independent commits (v0.4.13–v0.4.16), each testable and rollback-safe.

---

## The Tiered Context System

### Tier 1 — Always Inject (v0.4.13)

`gather_always_context()` runs on every call, returning a compact string with:
- Current date and time (single source of truth — removed from `claude_session.py` and briefing/debrief)
- Active timers with fire times and delivery method
- Active reminders with due dates
- Latest location and phone battery percentage
- Active exercise session (coaching context)
- Background task status from Redis (v0.4.16)

**Architecture:**
- `build_request_context()` calls `gather_always_context()` internally (covers regular + file paths)
- `_get_context_for_text()` prepends `gather_always_context()` for briefing/debrief paths
- No duplication: briefing/debrief no longer include their own datetime/reminders/location sections

### Tier 2 — Keyword-Triggered (v0.4.14, v0.4.15)

Health/nutrition context scoped to today + yesterday + 7-day summaries (v0.4.14):
- Today's data always in full — meals, nutrition totals, Fitbit, net calories
- Yesterday's data as compact summaries — nutrition totals, calorie balance, Fitbit highlights
- 7-day patterns and weekly nutrition averages (computed summaries, not raw entries)
- 14-day raw health entry dump removed entirely

Keyword false positives reduced (v0.4.15):
- Hybrid matching: multi-word substrings + word-boundary regex for ambiguous single words
- Removed: "cold", "hot", "warm", "outside", "back", "heart", "active", "sugar", "fat", "burn", "car", "oil"
- "back pain" still triggers via `\bpain\b`; "heart rate" stays as substring

### Tier 3 — Tool Calls (unchanged)

Historical queries ("what did I eat March 12th?") use Claude's tool access. Never for current/recent data.

---

## What Does NOT Change

- The keyword detection structure (refined, not replaced)
- The briefing/debrief detection in `_get_context_for_text()`
- The `_briefing_delivered_today()` check
- The unified builder pattern (all request paths → one function)
- Calendar, weather, vehicle, legal, project injection logic
- Pantry and diet reference file injection (keyword-triggered)
- Function signatures — no callers need updating

---

## Redis Integration (v0.4.16)

`redis_client.py` provides a `db.py`-style singleton with graceful failure. Task status from `aria:active_tasks` set and `aria:task:{id}` hashes is injected into Tier 1 context. Foundation for the swarm architecture.

---

## Implementation Sequence

| Step | Version | What | Status |
|------|---------|------|--------|
| 1 | v0.4.13 | Tier 1 always-inject, datetime consolidation, context logging | ✅ |
| 2 | v0.4.14 | Health/nutrition scoping, 14-day dump removal (D4 fix) | Planned |
| 3 | v0.4.15 | Keyword refinement (word boundaries, false positive reduction) | Planned |
| 4 | v0.4.16 | Redis client, task status in Tier 1 | Planned |
