# ARIA Overhaul — Master Punch List

*Compiled 2026-04-24 from conversation history (back to 2026-03-18), Aria_still_lies.txt, TODO.txt, and today's exchanges. Pre-flight section and Phase Plan added 2026-04-25.*

The overarching architectural goal: stop running multiple isolated subsystems with their own model tiers, context views, and source-of-truth files. Move to a Prime-as-orchestrator pattern where the main ARIA model coordinates domain specialists that share canonical data and conversational state. Refined later (Section 1) into a true SWARM with self-selecting specialists.

---

## 0. Pre-Flight: What Claude Code Needs to Know Before Starting

Adam is steering a clean rewrite of ARIA over a single focused weekend (~two long days). This section tells Claude Code what to load, what to keep reachable, and what non-obvious facts to internalize before writing a single line of new code. Read this section first. Then read the Phase Plan at the bottom of this document. Then read Section 1 (architecture) and Section 17 (specialist roster). Then begin Phase 1.

### Tier 1 — Load Into Initial Context

These files must be in Claude Code's working context from the start of the weekend. They are the irreplaceable design and reference material.

- **`docs/overhaul.md`** (this file, ~899 lines) — the design spec for the entire rewrite.
- **`system_prompt.py`** (~463 lines) — captures personality, integrity rules, ACTION block formats, ambient-audio behavior, every user-facing contract. Heavy investment, easy to flatten if rewritten carelessly. The personality specifics took dozens of iterations to land; preserve specific phrasings unless explicitly told to change them.
- **`schema.sql`** (~523 lines) — current database schema. Phase 1 designs the new schema, but understanding what's there now and what data has to migrate over is non-negotiable.
- **`CLAUDE.md`** (~71 lines) — project memory, intentionally short.
- **`notes/Aria_still_lies.txt`** — every documented lie pattern with source incident. Phase 1 guardrails MUST be tested against these specific failure modes, not just synthetic ones. This file is canonical regression targets.
- **`data/pantry.md`** (~253 lines) and **`data/diet_reference.md`** (~114 lines) — irreplaceable user-curated nutrition data. These are inputs, not code; they survive the overhaul as-is and the Nutrition specialist (17.2) reads from them.

### Tier 2 — Reachable on Demand (do not preload)

Claude Code should grep / read these as needed during the weekend, not load them upfront.

- **`CHANGELOG.md`** (~1,892 lines) — the WHY behind every existing fix. Has hard-won bug fixes that absolutely must not regress: webhook idempotency race (v0.9.7 bug #1), GSM-7 encoding for SMS (v0.9.7 22-segment failure), multi-confirmation logic (v0.9.6), owner column for Becky merge (v0.9.5), the bash capability denial pattern (Aria_still_lies.txt #4/#5). When building anything that touches a previously-fixed area, grep CHANGELOG first.
- **Existing core code (read-only reference for patterns being replaced):** `daemon.py`, `session_pool.py`, `actions.py`, `context.py`, `tick.py`. These are what Phase 2 replaces, but the patterns inform the rewrite.
- **Working integration code (preserve the integrations, only rewrite the orchestration around them):** `google_client.py`, `gmail_store.py`, `google_auth.py`, `fitbit.py`, `fitbit_auth.py`, `sms.py`, `send_mms.py`, `fetch_page.py`, `push_image.py`, `push_audio.py`. These have working OAuth flows, API patterns, and platform-specific quirks. The integrations themselves work; only the orchestration around them changes.
- **Existing memory layer (rewritten in Phase 1, but inform the design):** `graph_sync.py`, `qdrant_store.py`, `neo4j_store.py`.
- **Architectural context:** `ARIA_Project_Plan.md`, `docs/beckaning.md`, `CODE_REVIEW.md`.
- **Configuration template:** `config.example.py` is the safe template. NEVER load `config.py` — it contains secrets.
- **Test patterns (most tests will be obsolete after rewrite, but the patterns are gold):** read a representative sample like `tests/test_session_pool.py`, `tests/test_beckaning.py`, `tests/test_sms.py` to learn the assertion style and fixture setup before writing new tests.

### Tier 3 — Non-Obvious Facts Claude Code Must Internalize Before Writing Code

These are the things that won't surface from reading the design doc alone but will sink the overhaul if missed.

- **ARIA runs as a Claude Code subprocess.** The current invocation pattern is `claude --print --output-format stream-json --input-format stream-json --model opus --dangerously-skip-permissions` with the system prompt piped in. The swarm specialists in Phase 2 could each be their own CC subprocess, sharing or duplicating the prompt depending on design decisions. This is non-obvious from the design doc but critical to implementation.
- **Existing user data must survive the overhaul.** Years of nutrition logs, ambient transcripts, conversation history, calendar events, reminders, vehicle entries, legal entries, health entries, Fitbit snapshots — Phase 1's schema design needs explicit migration paths from the current `schema.sql` to the new tables, NOT greenfield-and-orphan. The pantry doc and diet reference doc remain on disk and are read by the Nutrition specialist verbatim.
- **OAuth tokens are not regenerated.** `data/google_tokens.json` and `data/fitbit_tokens.json` hold live OAuth credentials for Gmail/Calendar/Fitbit. The rewrite preserves these files. If credentials get invalidated mid-weekend, re-auth flows are documented in the existing `google_auth.py` and `fitbit_auth.py` — keep both.
- **Telnyx → replacement carrier migration is mid-flight.** Per CHANGELOG v0.9.4 onward, the SMS/MMS provider has been moving off Telnyx. Read v0.9.4 through v0.9.7 entries before touching anything in `sms.py` or `send_mms.py` to know what's done vs what Phase 3 still needs to finish.
- **`Aria_still_lies.txt` is canonical regression test material.** Every pattern in that file is a lie ARIA has previously made. Phase 1 guardrails must specifically detect/block each named pattern. Synthetic test inputs are fine, but every pattern in the file must have a corresponding programmatic detector or blocker that fires on the EXACT failure shape from the file.
- **Hard-won bug fixes that must not regress** (search CHANGELOG for the version tag, then read the entry before touching nearby code):
  - v0.9.7 #1 — webhook idempotency race (use `INSERT ... ON CONFLICT DO NOTHING` + rowcount check, not SELECT-then-INSERT).
  - v0.9.7 #2 — webhook 403 on bad signature triggers carrier retry storms (return 200 with critical-log instead).
  - v0.9.7 GSM-7 normalization — non-GSM-7 chars (em-dash, smart quotes, backticks) must go through `_normalize_for_sms` before send or the message silently dies at 22 segments.
  - v0.9.6 multi-confirmation — a single "yes" must batch-confirm all pending destructive actions, not just the first.
  - v0.9.5 owner column — `reminders`, `events`, `timers` all have an owner column that defaults to 'adam'. Becky merge in Phase 3 deepens this; do not regress to single-user.
  - v0.6.x destructive-action confirmation — code-level gate, not prompt-level. Every delete_* action goes through the gate.
- **The cost posture is "quality over cost."** Adam has explicitly accepted that swarm-with-Opus-max on every prompt is materially more expensive than the current single-instance pattern. Do not optimize for token count. Do not downgrade specialists to Haiku. Do not introduce timeouts to "save money on long-running specialists." See Section 1 swarm coordination details for the no-timeouts rule.
- **Personality preservation is part of the spec.** The Composer specialist (Section 1, stage 3) owns voice/tone/personality. The current personality is documented in `system_prompt.py` lines covering "PERSONALITY" sections, with examples and gates. The rewrite preserves this exactly; the Composer reads from the same canonical personality definition.

### Suggested Kickoff Sequence

1. Adam loads Tier 1 files into Claude Code's initial context.
2. Adam pastes/types the explicit "Tier 3 preamble" from above as a heads-up message before any work begins.
3. Claude Code reads the Phase Plan at the bottom of this document.
4. Claude Code reads Section 1 (architecture) and Section 17 (specialist roster).
5. Claude Code begins Phase 1, with Adam vetting the schema design before any migrations run.
6. After each phase, Adam tests the milestone deliverables before authorizing the next phase.

### Pre-Weekend Prerequisites (must be done before kickoff)

These are real-world setup items Adam handles before the weekend so Phase 4 doesn't stall waiting on a manual action.

- Run a manual Google Takeout export and inspect whether voicemail audio is included (active reminder [id=82e106cd]). Decides which Phase 4 voicemail path is built.
- Install SMS Backup & Restore on the phone and take a full export of SMS + MMS + call history. Phase 4 ingests this on Day 2.
- Install Syncthing on the phone with one-way sync configured to push `/DCIM`, `/Pictures`, `/Pictures/Screenshots`, `/Download` to a server folder. Phase 4 turns it on; the initial backfill will run while other phases happen.
- Confirm Tasker can fire on `Received Text`, `Sent Text`, and phone-state events on the Pixel 10a — these are standard Tasker capabilities but worth a quick verification before relying on them.
- Snapshot the current ARIA database (`pg_dump`) and put a copy somewhere safe. The clean rewrite shouldn't touch existing data, but a backup is cheap insurance.

---

## 1. Architecture: Prime-as-Orchestrator with Domain Specialists

**Source:** Conversations 2026-04-23 23:54 and 23:57, expanded 2026-04-24, refined 2026-04-25.

- Rewrite ARIA's overall structure so Prime is the orchestrator and domain-specific work is delegated to specialist sub-agents.
- The pattern is the same as Claude Code's main-agent / Agent-tool delegation — Prime stays in conversation, hands off heavy lifting (research, multi-step tasks, code changes) to scoped specialists.
- Specialists must inherit Prime's conversational context and read from the same canonical data source — no more out-of-sync subsystems.
- Each specialist is scoped to its domain's data, tools, and prompt rules. Cross-domain queries pass through Prime as the router.
- Full roster and per-specialist scope is enumerated in **Section 17 — Domain Specialist Roster**.

### Pipeline Stages (refined 2026-04-25 — Swarm Architecture)

The current "Prime owns everything" framing collapses three distinct roles into one. The replacement is NOT a central-intent-handler architecture (which would just relocate the bottleneck) — it's a true swarm with self-selecting specialists. Four stages:

1. **Coordinator (lightweight plumbing only — NOT an intent handler).** Receives every input, broadcasts the prompt to ALL specialists in parallel, collects findings as they arrive, fires the initial acknowledgment to the Responder, and shepherds final results to the Responder. Makes ZERO routing or classification decisions. The whole point: the Coordinator does not need to know what each specialist does. Adding a new specialist requires no Coordinator changes.
2. **Specialist Swarm (parallel, self-selecting).** Every specialist independently evaluates the prompt against its own scope and decides whether it has anything to contribute. If yes, it processes and returns structured findings. If no, it returns "not applicable" (or just stays silent past a noted self-decision). Specialists can collaborate peer-to-peer — Health calls Nutrition directly for today's totals, Real Estate calls Research directly for Zillow comps — without going through the Coordinator. Peer calls have a depth limit so nothing recurses forever. Memory specialist is default-on for almost all prompts because conversations should always be remembered and most have storable signal (Qdrant/Neo4j writes).
3. **Responder (Composer ARIA).** Takes the assembled findings from the swarm and composes the user-facing reply. Owns voice, tone, length, personality, and integration of multi-specialist output into a single coherent message. Does NOT decide where the message gets sent. Also fires the optional initial acknowledgment (see Swarm Coordination Details below).
4. **Channel Router (Communications specialist, promoted to final stage).** Takes the composed reply from the Responder and routes it to the correct output channel(s) — voice / SMS / glasses / image attachment / push / multiple channels. Honors explicit user direction ("answer by voice"), default routing rules (input channel = output channel unless overridden), and safety overrides (driving → voice only, sleeping → defer non-urgent).

### Why Swarm Beats Central Intent Handling

A central intent handler has to know enough about every specialist's scope to route correctly. That knowledge load grows linearly as specialists are added, and it's brittle — every scope shift requires a router update. Self-selection moves the relevance check to where the knowledge actually lives: each specialist is the world expert on its own scope, so it's the right place to ask "is this for me?" The swarm scales with the specialist count instead of being a bottleneck under it.

### Why Split Composer From Router

The current single-instance pattern means one agent juggles "what to say," "how to say it," AND "where to send it." That's the diagnosis Adam offered for the random voice-prompt-getting-SMS-reply behavior: too many concerns in one head, channel decisions get dropped or wrong. Separating Composer from Router means the Composer focuses on message quality and the Router focuses on delivery correctness — neither steps on the other's job.

### Swarm Coordination Details

- **Model tier:** every specialist runs on the smartest available model (Opus 4.7 today; whatever's strongest going forward) at MAX effort. No exceptions, no Haiku-tier subsystems anywhere.
- **Latency tolerance:** results matter, latency does not. A response that takes a minute or five is fine. A research request that takes 20+ minutes is fine. Build for quality, not speed.
- **No timeouts.** Nothing should fail entirely just because it took too long. Long-running specialists (Research, Web Specialist deep dives, Code/Engineering large refactor analyses) are allowed to take as long as they need. The Coordinator may emit progress updates while waiting, but never kills the work.
- **Peer-to-peer specialist communication.** Direct calls between specialists for dependency resolution. No central message bus required. Depth-limited to prevent runaway recursion.
- **Shared retrieval cache.** Per-prompt scratchpad keyed by query signature so that if Health, Nutrition, and Memory all want today's nutrition entries, the query runs once and the result is shared. Invalidates at end of prompt processing.
- **Memory specialist is default-on.** Almost every prompt should pass through Memory for two reasons: (a) recall — surface relevant prior context; (b) write — most exchanges contain something worth indexing into Qdrant/Neo4j. Exceptions are trivial passing remarks where neither read nor write adds value, and Memory itself decides when to skip.
- **Initial acknowledgment policy.** Responder fires an early ack via the Channel Router that's vague enough to absorb late-arriving specialists. Personality-laden, not formal. The point is to signal "received, working on it" without committing to a specialist roster the final answer might contradict. Example tone: "Heard. Letting the swarm chew on this — answer incoming." Late-arriving specialist findings get integrated into the final response without a redaction of the initial ack.
- **Orphan-prompt fallback.** If no specialist self-selects (legitimately rare — even casual banter has Memory active), a generalist fallback specialist catches the prompt and produces a conversational response without any domain-specific augmentation. Logged as an orphan event for review.
- **Cost posture:** swarm-with-Opus-max on every prompt is materially more expensive than the current single-instance pattern. Adam has explicitly accepted this — quality over cost. Build accounting into the system to track spend per prompt and per-specialist cost contribution, so high-cost low-value specialists can be identified and tuned.

### Current Bug Worth Investigating Independently

Voice prompts sometimes get answered via SMS rather than voice — happens "at random" per Adam's report 2026-04-25. The existing delivery_engine plus ARIA's set_delivery hint should already produce voice-in / voice-out by default. Investigation items:

- Check delivery_engine's input-channel detection: is it correctly identifying voice inputs, or sometimes misclassifying them?
- Check whether ARIA is consistently emitting `set_delivery` with method "voice" on voice-channel responses.
- Check delivery_engine override rules: is something (location, activity, battery state) silently flipping voice → SMS when it shouldn't?
- Add a delivery_log audit row recording: input channel, ARIA's set_delivery hint, engine's final decision, and the override reason if the engine overruled the hint. Without that audit trail, root-causing intermittent routing bugs is guesswork.

This should ship before the full pipeline split — small fix, high quality-of-life improvement, doesn't depend on the architecture rewrite.

## 2. Nudge System Unification

**Source:** Active reminder [id=27b26729], conversations 2026-04-23 23:54 and today's stale-data incident.

- Replace Haiku-tier nudge composition with Prime's model tier (Opus 4.7 at max effort, matching the rest of ARIA per current policy).
- Verified entry points to refactor:
  - `tick.py` — trigger detection via `DAILY_TARGETS` in `nutrition_store.py`
  - `daemon.py` line 1719 — `ask_haiku` composition call
- Nudge composer must read **current** totals at compose-time, not pre-cached threshold-trigger snapshots. Today's bug: sodium threshold value was current but protein was stale at 56-57g while the actual value was 78g (from a snapshot taken before lunch was logged).
- Share Prime's conversation history so nudges don't contradict decisions made minutes earlier (e.g. "use less dressing" after Adam already settled on a different approach).
- Read from same canonical threshold/diet-ref source as Prime's context.
- Cooldown logic intact, but evaluate cooldown against current state, not the snapshot that triggered it.

## 3. Weekly Summary Generator Bug

**Source:** Conversation 2026-04-24 17:56.

- `nutrition_store.py` line 392: `GROUP BY date` excludes any date with no nutrition entries — including today before breakfast is logged.
- `nutrition_store.py` line 435: Hardcoded `/7` denominator never adjusts for partial-window queries.
- Choose one of two fixes:
  - Option A: Show `n/n` for partial windows (e.g. 6/6 when today hasn't been logged yet).
  - Option B: Always operate on the past 7 completed days only, excluding today entirely.
- Header line 424 ("Nutrition summary (last 7 days, N days logged)") already exposes the partial-window count and could be the canonical source if Option A is chosen.
- This is the same canonical-source-of-truth problem as the nudge system — likely solvable by the same refactor.

## 4. Verification & Guardrails

**Source:** TODO.txt 2026-03-29 brain dump, Aria_still_lies.txt incidents 1-8.

- Verification gates for known lie categories:
  - Action claims without execution
  - Fabricated facts/numbers
  - Guesses presented as fact
  - **Time-based estimates** (lies file #1: claimed "last hour" when session was 3.5 hours)
  - **Narrative-dressing claims** (lies file #7: "first time in a while" / "33-day streak" — both invented to spice up an otherwise correct report)
- Hard-code confirmation step before ANY destructive action at code level, not prompt level. Already partially implemented for delete_* actions; extend to all destructive paths.
- Repeated-instruction injection at end of each query: "USE YOUR TOOLS, they are: <list> — verify factual claims with a tool first." Cost ~30-40 tokens, mitigates lost-in-the-middle attention decay.

### Response Validator (Tool Use Enforcement)

- Build a validator that checks non-conversational responses for evidence of tool use before delivery.
- Read Claude Code's internal tool/conversation logs to confirm whether ARIA actually made any tool calls during response generation.
- If the response contains factual claims (dates, numbers, names, state assertions) and CC logs show no tool use, **reject** the response with an error instructing ARIA to verify first.
- Classify responses as conversational (banter, greetings — no verification) vs. factual (claims, answers — tool use required).

### Specific Lie Patterns To Block (from Aria_still_lies.txt)

- **#1 Temporal claims** — verify via timestamps before stating durations.
- **#3 "No work today"** — never fabricate calendar absence; report missing entry as missing.
- **#4 / #5 Bash capability denial** — ARIA has lied multiple times about being unable to use bash for direct DB writes. The capability exists. Stop denying it.
- **#5 Tool-method substitution** — when asked to use ACTION blocks, don't silently fall back to bash and report results without disclosure.
- **#5 "Verify" misread as "re-create"** — verify means check, not re-log. Re-logging creates duplicates.
- **#6 Chicken/magnesium estimation omissions** — rule is in the prompt, ARIA still misses it. Mitigation `_validate_nutrition()` exists but doesn't prevent. Need stronger enforcement.
- **#7 Narrative fabrication** — when dressing up a report, never invent supporting numbers. If a "first time in a while" claim is being made, it must be backed by a query.
- **#8 Pattern-match auto-pilot on file inputs** — if a file arrives, open and inspect it. Do not pattern-match a near-identical previous response and re-emit ACTION blocks. Also: when forensic evidence in your own output contradicts your conclusion, do not finalize the conclusion.

## 5. Email Integration Overhaul

**Source:** TODO.txt 2026-03-29 brain dump.

- Surface only **recent, unaddressed** emails. Stop showing month-old already-resolved threads.
- Deprioritize emails once read or addressed so they drop out of repeated surfacing.
- Read full email body on demand (partially shipped — verify completeness).
- Email deletion / trash with mandatory confirmation gate (partially shipped via `trash_email` ACTION — verify).
- Ranked priority system — time-sensitive high-priority emails trigger immediate alert routing to Adam.
- Calendar extraction from emails (partially shipped — confirm all paths require user approval, never auto-add).

## 6. Context Injection Logic

**Source:** TODO.txt 2026-03-29 brain dump, conversation 2026-03-29 01:08 (over-streamlined health context).

- Smarter detection of when to inject relevant context (health, calendar, legal, vehicle, ambient).
- Reverse the over-streamlining that broke health-context injection on health conversations.
- System prompt should aggressively encourage tool retrieval over guessing. Already partially in place via the per-query INSTRUCTION block; verify it actually changes behavior.

## 7. Personality (mostly shipped — keep evaluating)

**Source:** TODO.txt 2026-03-29.

- Sarcasm / dry-humor traits bumped (done).
- Image generation for humor — currently used at ~1-in-10 rate (specified in current prompt). Confirm it's actually firing in practice.

## 8. G2 Glasses (Even Realities G2) — Custom App Integration

**Source:** Conversations 2026-04-18, 2026-04-24 15:03–17:02.

- Build a native Android app that talks directly to the G2 over BLE, bypassing the Even Hub companion app entirely.
- Reverse-engineering reference: i-soxi/even-g2-protocol on GitHub (primary spec) and the G1 protocol writeups for context.
- Plan document already lives at `~/G2 Custom/PLAN.md` (159 lines, 2026-04-24).
- Current temporary path: Expo dev-mode QR sideload that requires manual URL re-entry on every relaunch. Need to ship as a proper APK so it persists.
- **Constraint to live with:** the auto-close behavior is G2 firmware-side, not app-side. Adam's app code can't prevent it. Plan around the constraint, don't fight it.
- **Mic capture status (per i-soxi repo):** BLE Connect/Pair works in the reverse-engineered path; mic capture is "in progress" / not yet available. Means today the custom app cannot use the directional mics for input.
- Watch the upstream repo for mic-capture support landing; integrate when it does.

## 9. g2aria System — Reconnection Robustness

**Source:** Conversations 2026-04-24 16:51–17:21.

- Service is healthy server-side; the failure mode is the phone WebSocket dropping and never reconnecting on its own.
- Diagnosed root cause: Tailscale DERP relay flakiness with audio payloads on Verizon 5G.
- Build automatic reconnect with exponential backoff, plus visible status indication so Adam knows when it's failed.
- Consider fallback transport (direct UDP, alternative ingress) when DERP is the bottleneck.
- Add a heartbeat from the phone side so the server knows the difference between "client idle" and "client dead."

## 10. DJI Mic 3 System — Currently Broken

**Source:** Adam's instruction 2026-04-24, plus context from 2026-03-30 14:21–16:13 setup conversations and 2026-04-05 19:42 Raven test, 2026-04-11 23:05 large-WAV anomaly.

- System is currently non-functional. Failure mode not specified in the punch-list creation conversation; needs diagnosis pass.
- Earlier known issues to revisit during repair:
  - Receiver UI got stuck at language selection (1.1-inch touchscreen unfriendly).
  - Three identical 510 MB WAV files (44m13s each, byte-for-byte same size) on 2026-04-11 — the "pzvp" prefix suggested PipeWire/Pulse capture, not the Mic 3 itself. May be a downstream pipeline bug rather than a hardware issue.
  - Charging-case usage from-dock not fully verified.
- Goal: stable always-on ambient audio capture pipeline that survives reboots and reconnects without user intervention.

## 11. Supply / Inventory Tracking ("Running Low")

**Source:** Today's conversation 2026-04-24 ~19:54.

- Track consumption rates from nutrition logs and ambient audio mentions to maintain a running "running low" inventory.
- Surface reorder candidates as notifications with deeplinks to the relevant store (Amazon, Costco, etc.).
- **Adam stays the buyer** — ARIA is the suggester, not the purchaser. No stored credit card numbers, no headless checkout.
- For predictable recurring goods, recommend Amazon Subscribe & Save and adjust cadence based on observed consumption.
- Concrete first targets: Huel Black bags (computed today: ~3.5–4 weeks per 1.75kg bag at current 1.65 scoop/day average), eggs, Nutpods, Trade Coffee, multivitamins, Safe Catch salmon, Steamfresh broccoli.

## 12. System Maintenance & Update Capability

**Source:** Today's conversation 2026-04-24 ~19:00–20:06 (full Gentoo `emerge -vUDN @world` cycle).

- Formalize the maintenance pattern demonstrated today: pretend → diagnose → resolve → real merge → post-merge tasks.
- Codify the canonical fix patterns:
  - USE flag conflict resolution by editing `/etc/portage/package.use/*` in the user's existing organizational style.
  - Soft-block resolution (e.g. setuptools 81 → 82 / pkg-resources transitional split) by explicit version pinning before world.
  - Backtrack budget tuning when emerge gives up early.
  - Post-merge `--depclean` reminder.
  - Post-merge sysctl/init.d follow-ups (e.g. `kernel.task_delayacct=1` for iotop).
- News-item triage flow: read pending items, summarize for Adam, no action without his green light.
- Long-merge dispatch pattern: use `dispatch_action` mode shell with output piped to `/home/user/aria/logs/emerge_world.log`; report progress on demand by parsing `>>> Emerging` / `>>> Completed` markers.

## 13. Domain Registration & Scoped Purchase Capability

**Source:** Today's conversation 2026-04-24 ~19:46–19:54.

- Build a constrained purchase capability for narrow categories where APIs exist and identity verification isn't a barrier.
- First concrete target: Cloudflare Registrar via scoped API token for domain registration. Adam pre-approves a domain, ARIA calls the API with a token that's locked to "registrar" scope and a hard spending limit.
- Confirmation gate identical to existing destructive-action pattern: ARIA describes the purchase and total, Adam replies yes/no, only then execute.
- Audit trail: every purchase logged to a dedicated table for retroactive review.
- **Out of scope (for now):** Amazon, generic e-commerce, anything requiring stored credit card numbers + headless browser checkout. Bot-detection and ToS make these unsafe.

## 14. Email Self-Hosting (Future / Aspirational)

**Source:** Today's conversation 2026-04-24 ~19:30.

- Self-host inbound email at a personal domain (Adam plans to register `expectbugs.com` shortly). Postfix + Dovecot + Let's Encrypt.
- Outbound mail flows via commercial relay (Mailgun, SES, Postmark) to sidestep residential IP reputation issues.
- Eventually: replace Gmail integration with own infra. ARIA's email pipeline becomes IMAP/SMTP-direct instead of Gmail API.

## 15. Original Open TODOs (Pre-Brain-Dump)

**Source:** TODO.txt lines 1-3.

- **Fix Aria's lies** (covered by Section 4 above).
- **Find what is spamming Google Calendar with test data.** Investigate, identify the source, stop it. Probably an old test fixture or a misbehaving cron — needs forensic pass.
- **Email trash/delete** — partially shipped via `trash_email` ACTION + watch flow. Verify completeness.

## 16. Deferred / Watch-List

These came up in conversation but aren't sized for the overhaul itself; capture for later:

- Better verification of background-task progress reporting (don't claim progress without checking).
- File-share dedupe — if the same content arrives twice in seconds, recognize and skip rather than re-emit ACTIONs (extension of lies #8 mitigation).
- Health context injection edge case: when Adam mentions a meal in the middle of a non-meal conversation, decide whether to log silently or surface a confirmation.
- Sleeping / quiet-hours behavior — verify proactive nudges respect Adam's actual sleep window inferred from Fitbit + ambient.

## 17. Domain Specialist Roster

**Source:** Today's expansion 2026-04-24.

Every specialist below is a scoped Prime sub-agent under the orchestration model in Section 1. Each owns a domain, reads from canonical data, and returns structured findings to Prime.

### 17.1 Health Specialist

- Owns NAFLD diet compliance, sleep tracking, exercise coaching, symptom logging, medication reminders.
- Data sources: `health_entries` table, `fitbit_snapshots`, `fitbit_exercise`, diet reference doc, pantry.
- Cross-references with Nutrition specialist for compound queries (calorie balance, micronutrient totals).
- Reads but never writes legal — stays out of that domain.

### 17.2 Nutrition Specialist

- Owns meal logging, label parsing, pantry verification, daily totals, threshold alerts.
- Data sources: `nutrition_entries`, pantry doc, diet reference, USDA estimation rules.
- Owns the canonical threshold/diet-ref source that the Nudge unification (Section 2) pulls from.
- Enforces the chicken/magnesium/choline estimation rules at the validator level (lies #6).

### 17.3 Legal Specialist

- Owns the assault case, Aurora billing, Crime Victim Compensation tracking, court dates, deadlines, contacts.
- Data sources: `legal_entries`, calendar legal events.
- **Sensitive** — never volunteers info; only responds when Prime explicitly invokes it. Never injects legal context into casual conversation.
- Tracks the Aurora $3,000+ billing item flagged 2026-04-17 awaiting house sale closing.

### 17.4 Vehicle Specialist

- Owns Xterra maintenance, mileage tracking, cost history, service interval projections.
- Data sources: `vehicle_entries`.
- Proactively flags upcoming services based on mileage trends.

### 17.5 Real Estate Specialist (NEW)

- Owns the Pine Court (Elkhorn) house sale and the eventual next-house purchase.
- Data sources: new `real_estate_entries` table to be built, with sub-types: listing-status, showings, offers, comps, contacts (agent, attorney, inspector), milestones (listing, accepted-offer, inspection, appraisal, closing), repairs, financials.
- For the buy side: criteria list, candidate properties, walkthroughs, comparison sheets.
- Pulls comps via Zillow / Redfin / Realtor.com web scraping when available.
- Tracks Aurora-billing-pending-on-sale-close as a cross-link to Legal specialist.

### 17.6 Financial Specialist (NEW)

- Owns broader money management — recurring expenses, cash flow, account balances, large purchases, tax docs, budget tracking.
- Data sources: new `financial_entries` table, plus Plaid or manual import for account aggregation. Receipt parsing from email + photos.
- Tracks subscriptions, predicts monthly burn, flags anomalies.
- Coordinates with Real Estate specialist for closing-cost projections, with Legal for billing items, with Vehicle for cost-of-ownership.
- **Sensitive** — like Legal, never injects unprompted; responds only on explicit invocation.

### 17.7 Supplies / Pantry Specialist

- Owns consumption tracking and reorder logic per Section 11.
- Data sources: pantry doc, `nutrition_entries` (for actual usage), ambient mentions of "running low," manual user updates.
- Outputs: running-low list, projected days-until-empty per item, reorder suggestions with deeplinks.
- Coordinates with Financial specialist for budget-aware bulk-buy suggestions.

### 17.8 Email Specialist

- Owns inbox classification, prioritization, body retrieval, drafting replies, watches, calendar extraction proposals, trash gates.
- Data sources: `email_cache`, `email_classifications`, `email_watches`.
- Implements all of Section 5 (Email Integration Overhaul).

### 17.9 Calendar / Scheduling Specialist

- Owns events, conflicts, prep time, travel time, recurrence.
- Data sources: `events` table, `calendar_sync_state`, Google Calendar bidirectional sync.
- Cross-references with Health (medication times), Vehicle (service appointments), Real Estate (showings), Legal (court dates).

### 17.10 Communications Specialist (promoted to Channel Router final stage — see Section 1)

- Owns SMS, MMS, voice routing, delivery decisions, Becky relay.
- Data sources: `sms_log`, `sms_outbound`, `delivery_log`, `deferred_deliveries`.
- Implements channel selection (voice/sms/image/glasses) based on context, location, activity, and explicit user direction.
- **Promoted to the dedicated final stage of the response pipeline (Section 1, stage 4).** Unlike other specialists that contribute findings to the Composer, Communications takes the already-composed reply and decides where to send it. It does not return findings to Prime — it terminates the request by shipping the reply to the channel(s).
- Honors hierarchy of routing signals:
  1. Explicit user direction ("answer by voice", "text me", "send to glasses") — always wins unless safety-overridden.
  2. Input channel = default output channel (voice in → voice out; SMS in → SMS out; CLI in → CLI out).
  3. Activity / safety overrides (driving → voice only; sleeping → defer or push only; in-court → silent).
  4. Content shape (image attachment → MMS or push, not voice).
- Logs every routing decision with: input channel, Composer's set_delivery hint, final channel chosen, override reason if applicable. This audit trail is what makes the current intermittent voice-→-SMS bug diagnosable.
- Owns the Becky relay surface — see Section 18.

### 17.11 System / DevOps Specialist

- Owns the maintenance pattern formalized in Section 12.
- Data sources: shell access, package manager, system logs, journald, monitoring dashboards.
- Handles emerge updates, USE flag conflicts, sysctl tuning, service management, kernel updates, news triage.
- Always describes destructive operations and waits for confirmation.

### 17.12 Hardware Integrations Specialist

- Owns Fitbit, DJI Mic 3, G2 glasses, Tasker integration, AutoShare, Telnyx pipeline, Even Hub workarounds.
- Data sources: device-specific logs, BLE state, websocket connections.
- Implements g2aria reconnection (Section 9), DJI fix (Section 10), G2 custom app (Section 8).

### 17.13 Research / Web Specialist

- Owns external information gathering — fetch_page, web search, RDAP/whois, API queries, document parsing.
- Returns synthesized findings to Prime, never speculates.
- Used for everything from medical literature lookups to domain availability to pricing comparisons.

### 17.14 Memory / Recall Specialist

- Owns semantic search across conversations, ambient transcripts, commitments, person profiles.
- Data sources: `ambient_transcripts`, `ambient_conversations`, `commitments`, `entity_mentions`, Qdrant vector index, Neo4j knowledge graph.
- Surfaces relevant prior context proactively when topics resurface.
- Owns the canonical "what did we decide before" question.
- **Default-on across the swarm** (Section 1). Memory participates in almost every prompt for two reasons: recall (surface relevant prior context) and write (most exchanges contain something worth indexing into Qdrant/Neo4j). The specialist itself decides when to skip — trivial passing remarks where neither read nor write adds value get a no-op.
- Owns the write side of the long-term memory layer: deciding what to embed into which Qdrant collection, what entities and relationships to add to the Neo4j graph, what to summarize and roll up periodically.

### 17.15 People / Relationships Specialist

- Owns person profiles, commitments tracking (promises made/received), social context.
- Data sources: `commitments`, `entity_mentions`, `interaction_quality`.
- Tracks relationship-specific tone (warmer for Becky, family, close friends; neutral for acquaintances).
- Cross-references with Communications for the right tone-and-channel pairing.

### 17.16 Code / Engineering Specialist

- Owns codebase navigation, edits, architectural review, debugging support.
- Data sources: filesystem, git history, language servers, project docs.
- Used for ARIA self-modification, the G2 custom app, future projects.

### 17.17 Creative / Brainstorm Specialist

- Owns longer-form creative work — writing, naming, ideation, image generation prompts, narrative structure.
- Used sparingly; Prime usually handles short creative riffs in-conversation.

### Cross-Cutting Rules

- Specialists never directly write to user; findings flow to the Responder via the Coordinator (per Section 1's swarm pipeline).
- Specialists log their findings to a per-specialist work table for audit / replay.
- **Self-selection.** Each specialist evaluates every incoming prompt and decides whether it has anything to contribute. There is no central intent handler routing prompts to it. Sensitive specialists (Legal, Financial, Health-mental-state) still require explicit invocation and never auto-inject — they self-select OUT of casual prompts even if a keyword superficially matches.
- **Peer-to-peer collaboration.** Specialists can call each other directly for dependency resolution (Health → Nutrition for today's totals, Real Estate → Research for comps). Depth-limited to prevent recursion. No mandatory routing through the Coordinator for peer calls.
- **Memory specialist is default-on** for almost every prompt — see Section 1 swarm coordination details. Memory itself decides when to skip.
- **Shared retrieval cache** per prompt. Specialists hitting the same data source for the same query get a deduplicated result.
- **No timeouts.** A specialist takes as long as it needs. Research / deep code analysis / web crawls of 20+ minutes are acceptable. The Coordinator may surface progress but never kills the work.
- Specialists share Prime/Coordinator conversational state via a structured handoff payload, not full conversation re-injection.
- Specialists run on the smartest available model (Opus 4.7 today, whatever's strongest going forward) at MAX effort — no Haiku-quality regressions anywhere in the system.

---

## 18. Becky's ARIA — Merge with Adam's Instance

**Source:** Today's instruction 2026-04-24, plus existing relay infrastructure.

### Current State

- Becky has her own Claude Code subprocess with its own conversation memory.
- Histories don't cross — Adam's ARIA and Becky's ARIA each see only their own conversations.
- Cross-instance bridge already exists via `relay_to_becky` (sms/mms/push_image/reminder/event methods) and the symmetric capability for Becky's ARIA to write to Adam's reminders/calendar (with auto-SMS notification).
- Adam can read Becky's recent conversations via `query.py conversations --user becky --days 7` for explicit on-demand context.

### Merge Goals

- **Unified data layer.** Single Postgres backend with `user_id` on every row. Each ARIA instance scopes its own queries to its user's data by default but can authorize cross-user reads when warranted.
- **Shared canonical sources.** Pantry, calendar (joint events), shopping lists, household-level data all live as joint records with both users authorized.
- **Personality persistence per user.** Adam's ARIA stays snarky; Becky's ARIA stays noticeably warmer. Same model tier, different prompt seasoning.
- **Cross-instance awareness.** When Adam tells his ARIA something that affects Becky (e.g. "I'm picking her up at 7"), Becky's ARIA gets the relevant context automatically without Adam manually relaying.
- **Coordinated reminders.** A shared event creates one calendar entry for both, one reminder per person, customized per their schedule and tone.
- **Privacy partitions.** Each user has private data (medical, legal, financial details) that the other instance cannot read by default. The merge does not eliminate privacy — it formalizes the boundary.
- **Joint Specialist instances.** Some specialists (Calendar, Communications, Real Estate for next-house, Financial for joint expenses) operate at the household level. Others (Health, Legal) stay per-user.

### Concrete Build Steps

- Add `user_id` and `household_id` to every relevant table.
- Build authorization layer that filters queries by the calling instance's user, with explicit cross-user-read flags for joint data.
- Define the joint-data schema (shared events, shopping list, pantry, household notes).
- Build cross-instance message bus so when one ARIA writes joint data, the other ARIA picks it up and notifies its user appropriately.
- Tone-aware response composition per user — same underlying reasoning, different surface delivery.
- Migration path for Becky's existing ARIA conversation history into the unified backend without losing her current context.

### Open Questions for Adam to Decide

- Which categories are joint-by-default vs. private-by-default?
- Does Becky's ARIA get system-administration capabilities on Adam's machine, or stays read-only there?
- Future-relationship-state handling — what's the policy if circumstances change? (Should be designed in from the start, not retrofitted.)

---

## 19. Telnyx Replacement + Multi-Contact + Answering Service

**Source:** Today's instruction 2026-04-24, plus ongoing Telnyx reliability frustration ("Telnyx still sucks") and current single-user assumption baked into the SMS pipeline.

### Why Move Off Telnyx

- A2P 10DLC trust process has been pending for an extended period; outbound deliverability is unreliable.
- Multiple "Telnyx ate the SMS" incidents in conversation history, including today.
- Telnyx is built for application-to-person traffic at scale, not personal answering-service workloads. The trust model fights us.
- A regular consumer-grade SIM with a real cellular carrier doesn't have the A2P trust gauntlet — it's just a phone number that texts and calls like any human number.

### Target State: Regular SIM, Regular Number, Multi-Contact

- Provision a dedicated phone number on a consumer cellular service. Treated as ARIA's own line — distinct from Adam's personal phone number but reachable by anyone.
- Carrier choice is open — pick an MVNO or major service that allows normal personal SMS volume without A2P registration friction. Confirm MMS support (group chats, image attachments) before committing.
- Number portability — design so the chosen number can move between providers if needed without losing contact history.

### Hardware Path — Decided 2026-04-25

After research (conversation 2026-04-25 14:28–14:54), the picked path is **dedicated Pixel A-series phone running the `capcom6/android-sms-gateway` app**, not Tasker.

**Why this path beats the alternatives:**
- USB cellular modem on the Gentoo box is a non-starter for MMS. Verified: Quectel (and SIMCom) modems on Linux do NOT expose MMS through ModemManager or standard kernel drivers. MMS uses WAP Push protocols handled by the carrier network or specific MMS proxies — not the OS. To send/receive MMS via these modems on Linux you'd have to talk AT commands directly (`AT+QMMS`) and roll a custom MMS protocol handler. Doable but bug-prone and not battle-tested. Reddit and Quectel forum threads show the path is painful even just to keep modems CONNECTED on Linux.
- Industrial appliance (SMSEagle, Multitech) is technically capable but priced at $700–$3,000 — explicitly rejected as too expensive 2026-04-25.
- Dedicated Android phone uses Google's Android MMS stack, which is the same code path running on every Android phone on Earth, handling billions of MMS daily. It is the most field-tested MMS implementation in existence. A real consumer SIM in a real phone looks like a normal subscriber to the carrier — no A2P trust gauntlet, spam-sounding content gets through fine.
- Tasker explicitly rejected by Adam — described as janky and unreliable. The replacement is `capcom6/android-sms-gateway` (GitHub, Apache 2.0, ~4.2K stars, actively maintained), which is purpose-built for the SMS gateway use case rather than being a general-purpose automation tool repurposed for it.

**Hardware spec:**
- Phone: Pixel A-series. Any of 6a / 7a / 8a / 9a works — they all share the same Android MMS stack. Adam's preference is cheap end of the range (used Pixel 6a or 7a). Update lifespans: 6a through 2027, 7a through 2028, 8a through 2031, 9a through 2032. Cellular radio quality is similar across the line; flagship Pro models are explicitly NOT needed (the camera/display/processor premium is wasted on a headless gateway).
- Charging stand to keep the phone always-on (any Qi-compatible Pixel-friendly stand).
- 4GB+ RAM is enough; A-series Pixels all exceed this. 64GB storage is plenty.

**Software spec:**
- App: `capcom6/android-sms-gateway` (https://github.com/capcom6/android-sms-gateway). Installed via APK or sideload. Run in local-server mode pointing at ARIA's webhook URL.
- Explicitly supports MMS — quoting the project's feature list: "MMS download notifications: Receive webhook notifications when MMS messages are fully downloaded, including message body and attachments."
- Supports SMS send/receive, multipart messages, message status tracking, real-time webhook notifications on incoming, and end-to-end encryption between phone and server.
- Cellular service: a consumer line on the same kind of carrier Adam uses for his personal line. A second line on his existing carrier is the simplest activation path.

**Voice calls:**
- This path does NOT cover voice calls — `capcom6/android-sms-gateway` is SMS/MMS only. Voice was explicitly droppable per Adam (2026-04-25 14:28). If voice handling becomes a requirement later, layer on an additional component (e.g. a softphone app on the same handset that forwards call audio + transcripts) or revisit the appliance path.

**Documented fallback (not the picked path):**
- SMSEagle hardware appliance — explicitly rejected on cost (2026-04-25). Documented here only so that if the Pixel + capcom6 path ever proves inadequate (e.g. consumer-OS reliability issues across years of operation), the appliance path is a known escape hatch. Not currently planned.

### Contact-Aware Pipeline

- New `contacts` table with: phone number, normalized E.164, display name, relationship category (family / partner / friend / professional / vendor / unknown), tone preset, autonomy level (per-contact policy on what ARIA can answer alone vs. forward), last contacted, notes.
- Inbound message routing: lookup-by-number first, fall back to "unknown" handling if no match.
- Per-contact conversation history threaded separately. Cross-references with the People/Relationships specialist (Section 17.15) for context like "who is this and what's our recent history."
- Group chat support — multi-recipient threads tracked as their own conversations with all participants resolved.
- Tone calibration per contact: warm for Becky, gentle for family members, professional for vendors, snarky-default-Adam-style for friends.

### Contact Tiers

Family already has Adam's personal number for reaching him directly. When family texts or calls ARIA's number, the intent is to interact with ARIA, not to pass through to Adam. The tier model reflects that.

- **Tier A — Authorized Inner Circle (family, Becky, close trusted).** Full ARIA access. ARIA serves them directly the same way it serves Adam — they can ask Adam's current GPS location, schedule, what he ate, what's on his calendar tomorrow, anything in the knowledge base. Tone-aware (warm for Becky, family-warmth for parents/siblings, etc.). Each authorized contact gets their own conversation thread and history with ARIA, similar to Becky's current cross-instance bridge. ARIA can take action on their behalf (set them a reminder, add a joint event, relay something to Adam) within their authorized scope.
- **Tier B — Personal friends / acquaintances.** Forward to Adam by default with draft replies prepared for one-tap send. Per-contact, Adam can authorize ARIA to handle specific routine cases (confirming an appointment, replying "got it," sharing a pre-approved fact). Never assumes a new capability without Adam's first-time approval per contact.
- **Tier C — Professional / known vendors.** ARIA handles routine inquiries autonomously — appointment confirmations, status questions on known projects, redirecting to email when appropriate. Every autonomous reply logged for Adam's review. Escalates anything ambiguous.
- **Tier D — Unknown / cold contacts.** Full answering-service mode. ARIA screens, takes messages, asks clarifying questions, decides whether to forward or politely deflect. Detects spam / sales / scam patterns and handles them without bothering Adam.

Each tier carries explicit per-action allow-lists (share location, share schedule, book appointment, set reminder, send image, relay to Adam, etc.). Tier A's allow-list defaults to broad — same as Adam's own access — but every action is still logged for transparency.

The tier assignment is per-contact, not per-number-of-contacts. A long-time friend can be Tier A if Adam wants. A cousin he barely talks to can be Tier B. Adam configures.

### Voice Calls

- Inbound calls: ARIA's number rings into a software stack that can either forward to Adam's personal phone (Tier 0/1), route through a screening menu (Tier 2/3), or take a message via TTS+ASR.
- Outbound calls: ARIA can place calls on Adam's behalf for narrow scoped tasks (confirming appointments, calling Aurora about billing holds — see Legal Section 17.3, leaving voicemails). Always with Adam's prior approval per call.
- Voice transcription archived per-contact alongside SMS history.
- Voicemail-to-text and email/SMS notification of any non-Adam-handled calls.

### Privacy & Security

- ARIA's number is publicly shareable. Adam's personal number stays private — only Tier A (family, Becky, close trusted) have it for reaching him directly. Other contacts can only reach Adam via ARIA's number.
- All contact data encrypted at rest.
- Wisconsin is a **one-party consent** state for recording (Wis. Stat. § 968.31(2)(c)) — ARIA being a participant in the call satisfies the consent requirement on its own. No two-party flow required.
- Adam can pull a full per-contact transcript for any call/text history at any time.
- Tier A authorized contacts can pull their own conversation history with ARIA.
- Hard kill-switch to disable autonomous responses globally if anything goes sideways.

### Build Order (Within This Section)

1. Stand up the dedicated number + ingestion pipeline (any of the three hardware paths). Verify reliability against Telnyx baseline.
2. Build the `contacts` table and per-contact routing.
3. Implement Tier A (authorized inner circle) — riding the Becky-merge infrastructure from Section 18. Family + close trusted get cross-instance access patterns, scoped per contact.
4. Implement Tier B (forward + draft reply for personal friends). No autonomy yet.
5. Layer in Tier C with explicit allow-lists per professional/vendor contact, all logged.
6. Layer in Tier D (full answering service) only after the prior tiers have proven solid.
7. Voice handling last — text reliability and tone calibration come first.

### Cross-References

- Communications specialist (17.10) owns the routing surface this builds on.
- People/Relationships specialist (17.15) owns the per-contact context and tone presets.
- Memory/Recall specialist (17.14) owns the searchable history across all threads.
- Email specialist (17.8) cross-coordinates for contacts who reach out across both channels.

---

## 20. Source-Aware Saturated Fat Tracking

**Source:** Today's instruction 2026-04-24, after a Chomps stick log put sat fat right at the 15g cap.

### The Problem

The current model treats saturated fat as a single number against a 15g daily cap. The NAFLD evidence base actually suggests sat fat impact varies dramatically by source. Sat fat from EVOO, avocado, fish, and even moderate dairy doesn't have the same liver-fat effect as sat fat from processed meats, fried foods, or palm oil. The Rosqvist 2014 RCT that's the basis of the 15g target used palm-oil overfeeding specifically — a worst-case source profile. Mediterranean-pattern diets that include sat fat from olive oil and fish reduce steatosis despite hitting similar gram counts.

A single-bucket count makes it look like a Chomps stick (3g sat fat from beef) and a tablespoon of butter (7g sat fat from highly-saturated dairy) and a tablespoon of EVOO (2g sat fat from a primarily-MUFA source) are interchangeable. They're not, for liver-fat purposes.

### Proposed Tracking

- Tag each sat fat gram by source category at log time:
  - **MUFA-dominant source** (EVOO, avocado, olive, nut/seed oils — sat fat is a minority component and comes packaged with high MUFA + polyphenols).
  - **Fish source** (sat fat is trace and comes with significant EPA+DHA — net liver-protective).
  - **Whole-food meat/poultry** (Chomps, eggs, chicken thigh, beef — middle of the pack, no known protective co-factors but no specific NAFLD-aggravating factors either).
  - **Full-fat dairy** (cheese, butter, cream — traditional concern, current evidence ambiguous; track as its own bucket so we can adjust weighting as evidence evolves).
  - **Processed meat / cured** (sausage, bacon, hot dogs, deli meat — paired with nitrites, high sodium, often high in advanced glycation end products).
  - **Tropical / fried** (coconut oil, palm oil, fried foods — closest to the Rosqvist overfeeding profile, weighted heaviest).
- Store the source breakdown alongside the total in `nutrition_entries` — a `sat_fat_by_source` JSON column or similar.
- Daily totals show the breakdown: "Sat fat 15g — 4g MUFA-source, 1g fish, 6g whole-food meat, 4g processed meat, 0g tropical/fried."
- Threshold logic gets weighted: instead of one 15g cap, use category-specific budgets that sum to a tighter overall constraint. Tropical/fried + processed meat get the tightest budgets; MUFA-source and fish get lenient ones.

### Implementation Notes

- Pantry doc gets extended with a `sat_fat_source` field per item so logging is automatic where pantry data exists.
- USDA estimation rules in the system prompt extended to include source-tagging for common foods (e.g. Italian sausage → processed meat, butter → full-fat dairy, EVOO → MUFA-source).
- Backfill historical entries by running a classifier across food_name + ingredient list. Acceptable to leave older entries un-tagged; just don't force them into the wrong bucket.
- Nutrition specialist (17.2) owns the implementation. Health specialist (17.1) consumes the breakdown for compliance reporting.

### Why This Matters Today

Today's example: 12g sat fat from breakfast + lunch was almost entirely whole-food meat (eggs + Italian sausage tortellini, where the sausage was the bigger contributor). The Chomps stick added 3g whole-food meat. The 15g number looks alarming in single-bucket; the source breakdown shows zero processed-meat-with-nitrites, zero tropical/fried, and most of it from sources with no known NAFLD-aggravating factors beyond the sat fat itself. That's a different — and more accurate — story than "you hit your cap."

---

## 21. Memory Layer Overhaul + Self-Learning Agent

**Source:** Today's instruction 2026-04-24.

### Current State

- Qdrant is already integrated (`qdrant_store.py`) but scoped narrowly — a single `aria_memory` collection used only for ambient-transcript semantic recall. Embedding strategy is whatever the default is. No reranking, no hybrid search, no time decay.
- Neo4j is integrated (`neo4j_store.py`, `graph_sync.py`) with a minimal schema: `Person`, `Conversation`, `Topic`, `Commitment` nodes with `PARTICIPATED_IN`, `DISCUSSED`, `COMMITTED_TO`, `KNOWS` relationships. The graph is sparse — most of ARIA's knowledge isn't represented in it.
- Learning today is manual: when ARIA fails or lies, Adam writes the incident into `Aria_still_lies.txt` and Adam writes mitigation rules into the system prompt. ARIA itself does not close the loop.

### Qdrant — Proper Usage

- Move from one bucket to multiple collections per data type, each tuned to its content shape:
  - `ambient_transcripts` — raw transcript chunks (current).
  - `conversations` — past Adam↔ARIA exchanges, indexed by message rather than free chunk.
  - `pantry_items` — for fuzzy "did I log this food before?" lookups during nutrition logging.
  - `diet_rules` — diet reference + estimation rules for retrieval-augmented prompting.
  - `code_patterns` — the codebase, so ARIA can answer "have we already built X?" without grep.
  - `lies_incidents` — every entry from `Aria_still_lies.txt` so similar future situations retrieve the relevant lesson.
  - `health_log_summary`, `legal_log_summary`, `vehicle_log_summary` — pre-summarized rolling buckets per domain.
- Hybrid search: combine vector similarity with BM25 keyword matching. Pure semantic misses exact-phrase matches; pure keyword misses paraphrases.
- Reranker on top — cross-encoder or LLM-based — for the final top-N.
- Time-decay weighting so 3-day-old context outranks 3-month-old context for matched-relevance queries.
- Per-collection embedding model choice — code wants different embeddings than free text.
- Hard-define what gets indexed and when. Today's pattern is "everything always" which is expensive at retrieval time. Better: index strategically and rely on graph traversal for relationships.

### Neo4j — Proper Usage

- Expand schema to cover the actual knowledge ARIA is trying to track:
  - Entities: `Person`, `Conversation`, `Topic`, `Commitment`, `Food`, `Nutrient`, `HealthState`, `Symptom`, `Medication`, `Place`, `Vehicle`, `LegalCase`, `Document`, `Email`, `Event`, `Reminder`, `Specialist` (the 17.x agents themselves).
  - Relationships: `MENTIONED`, `OCCURRED_AT`, `LOCATED_AT`, `OWNED_BY`, `RESPONSIBLE_FOR`, `CONTAINS_NUTRIENT`, `AGGRAVATES`, `IMPROVES`, `RESCHEDULED_FROM`, `RELATES_TO`, `CONTRADICTS`, etc.
- Use graph for the queries it's actually best at:
  - "What foods correlate with poor sleep on the next day?" (`(:Food)-[:CONSUMED_ON]->(:Day)<-[:PRECEDED]-(:Day)-[:HAD_SLEEP]->(:Sleep)` patterns).
  - "Which contacts have I committed to but not delivered on?" (Commitment graph traversal).
  - "Who else was mentioned in the same conversation as Mike?"
  - "What was the chain of decisions that led to picking Cloudflare for the registrar?"
- Build a graph_sync module that keeps the graph current as new data lands in Postgres, with a backfill pass for historical data.
- Use Cypher queries from specialists when they need relational reasoning, not just pattern matching.

### The Self-Learning Agent

- New specialist (call it 17.18 — Learning / Meta-Reflection Specialist) that has explicit write authority on the system itself.
- Watches all Adam↔ARIA conversations (and Becky↔ARIA when authorized). Looks for:
  - Explicit corrections ("no, that's wrong, the answer is X").
  - Implicit corrections (Adam re-asks the same question rephrased after a wrong answer).
  - Preference signals ("I prefer X over Y", "stop doing Z").
  - Pattern repeats — same lie or same mistake across multiple sessions.
  - Successful patterns — what worked, what was praised, what should be reinforced.
- Outputs of the learning loop:
  - **System prompt deltas** — proposed additions or rule changes for Adam to approve, written into a candidate file rather than applied silently.
  - **New `Aria_still_lies.txt` entries** — automatic, with structured fields (date, source session, lie pattern, root cause, mitigation suggestion).
  - **Pantry / diet-ref updates** — when Adam corrects a nutritional fact, propose a pantry diff.
  - **Code change proposals** — when a bug is identified in conversation (like today's nutrition_store.py:392 GROUP BY bug), open a candidate patch and ask Adam to review.
  - **Weighting updates** — adjust nudge thresholds, cooldowns, autonomy tier permissions based on observed outcomes.
- All proposed changes pass through a confirmation gate. The agent never silently rewrites the system. It builds the diff, presents it, and waits for Adam's approval — same pattern as destructive actions.
- Audit trail: every approved change tagged with the conversation that triggered it, so the lineage is queryable.
- Cross-references with Memory/Recall (17.14) for surfacing similar past situations during the analysis pass.
- Cross-references with Code/Engineering (17.16) for the actual diff generation when changes are code-level.

### Why This Matters

Right now the loop between "Adam catches ARIA in a mistake" and "ARIA stops making that mistake" passes through Adam's hands at every step — he reads the lie, decides on mitigation, edits the prompt or code, restarts. That works but it's slow and dependent on Adam doing the bookkeeping. A learning agent automates the bookkeeping and proposes mitigations, while still keeping Adam as the decision-maker on what gets applied. It's the difference between a fixed behavior model and an actually-improving one.

### Build Order (Within This Section)

1. Qdrant: refactor to per-domain collections, add hybrid search and reranker. Existing semantic-recall queries keep working.
2. Neo4j: expand schema, build graph_sync to populate from Postgres, run historical backfill.
3. Learning agent: passive observer mode first — produces proposed diffs to a review queue, never auto-applies. Adam reviews and approves.
4. Layer in active proposals (pattern detection, code change suggestions) once the observer mode has demonstrated reliable signal.
5. Eventually: per-domain learning sub-agents (a Nutrition-learner, a Communications-learner) that surface domain-specific improvements. Coordinated by the Meta-Reflection specialist.

---

## 22. Comprehensive Phone Mirror & Personal Data Ingestion

**Source:** Conversation 2026-04-25 12:17–12:23.

### Goal

Give ARIA full, ongoing access to everything on Adam's phone — photos, SMS/MMS history (incoming and outgoing), call logs (incoming, outgoing, missed), and voicemails. Both the existing historical archive and the live stream of new items as they happen.

### Device & Carrier Context

- Phone: Pixel 10a (Android).
- Carrier: Google Fi (visual voicemail handled through the Messages / Google Fi app, not traditional carrier voicemail).
- Existing on-device automation: Tasker + AutoShare. AutoShare is already ingesting individual files to ARIA today — that endpoint pattern is the reuse target.

### Per-Data-Type Plan

**Photos**
- One-way Syncthing sync from phone to ARIA server. Folders: `/DCIM/Camera`, `/Pictures`, `/Pictures/Screenshots`, `/Download`.
- Initial sync uploads the entire library; incremental sync handles new photos automatically.
- Survives phone reboots, network interruptions, and Tasker config drift better than a Tasker-driven uploader.
- Server-side: store under `/home/user/aria/data/phone_mirror/photos/<YYYY>/<MM>/`. Index EXIF metadata (timestamp, GPS, camera) into Postgres for queryable metadata search.
- Optional: run a vision model pass on each photo to extract a text description for semantic search via Qdrant (`photos` collection in Section 21's per-domain Qdrant refactor).

**SMS / MMS**
- Two-step approach.
- **Historical:** SMS Backup & Restore (Android app) does scheduled XML/JSON exports of full SMS+MMS history. Configure to upload exports to ARIA server (Drive sync or direct HTTP POST). One-time backfill ingests the entire history.
- **Live:** Tasker profiles on `Received Text` and `Sent Text` events. Each event POSTs structured JSON (sender, recipient, body, timestamp, message ID, attachment URIs for MMS) to a new `/ingest/sms` endpoint on the ARIA server. MMS attachments uploaded as separate multipart payloads.
- Server-side: dedicated `sms_messages` table with sender, recipient, direction, body, timestamp, thread ID, attachment refs. Index for full-text search; embed bodies into a `sms_messages` Qdrant collection for semantic recall.

**Call Logs**
- Same two-step pattern as SMS.
- **Historical:** SMS Backup & Restore also exports call logs despite the name.
- **Live:** Tasker profile on `Phone State` events (incoming, outgoing, missed, answered, ended) POSTs to `/ingest/call_log`. Captures number, contact name (if matched), direction, duration, timestamp.
- Server-side: `call_log` table. Joins to a `contacts` table once that's also synced.

**Voicemails (Pixel 10a + Google Fi)**
- Hardest of the four — needs research before locking in an approach.
- Google Fi visual voicemails are stored in Google's infrastructure and surfaced through the Messages app. Audio files are not in a standard `/data` location accessible to non-root apps.
- Candidate paths to investigate:
  - **Google account API access** — does Google expose Fi voicemail audio through any public API tied to the Google account? (Probably not, but check Google Takeout — Takeout exports may include voicemail audio.)
  - **Google Takeout scheduled export** — Takeout supports recurring exports (every 2 months). If Fi voicemails are in Takeout, set up a recurring export to the ARIA server.
  - **Tasker accessibility-service hack** — read voicemail UI text from the Messages app via the Android accessibility service. Captures voicemail transcripts but not original audio.
  - **Notification listener** — Tasker can read notification content when a new voicemail arrives, capturing the auto-transcribed text.
  - **Root-required path** — if none of the above work, voicemail audio extraction would require root access on the Pixel 10a, which Adam hasn't done.
- First step: confirm whether Google Takeout includes Fi voicemail audio. If yes, that's the cleanest path.

**Contacts**
- Worth adding to scope even though Adam didn't explicitly list it — call logs and SMS reference contact IDs that need a contact table to be useful.
- Tasker can read the contacts content provider (`content://com.android.contacts`) and POST a full export. Ongoing sync via Tasker profile on contact-changed events, or simpler: nightly full re-export.

### Server-Side Architecture

- New ingestion endpoints: `/ingest/sms`, `/ingest/mms`, `/ingest/call_log`, `/ingest/contacts`. Photos go through Syncthing rather than HTTP.
- Auth: per-device shared secret in HTTP header. Rotate periodically.
- Bulk endpoints accept multiple records per POST for the historical backfill; live endpoints accept single records.
- Deduplication on message ID and call timestamp+number tuple to make bulk re-imports idempotent.
- Rate limiting on the live endpoints to prevent runaway Tasker loops.

### Indexing & Consumption

- Memory/Recall specialist (17.14) is the primary consumer. Phone data feeds the same Qdrant + Neo4j layers from Section 21:
  - Qdrant collections: `photos` (vision-model captions), `sms_messages`, `call_log_summary`, `voicemails` (transcript text).
  - Neo4j: extend schema with `Photo`, `SmsMessage`, `Call`, `Voicemail`, `Contact` entity types. Relationships: `SENT_BY`, `RECEIVED_BY`, `MENTIONS`, `OCCURRED_AT`, `HAS_ATTACHMENT`, `LOCATED_AT` (for geotagged photos).
- Communications specialist (Section 17 — slot in as a new sub-specialist if not already covered) handles SMS/call queries: "did I text Becky about the dome house?" "when did I last call Mike?"
- Real Estate specialist (17.5) gets photos that match house-tour locations.
- Health specialist (17.1) gets photos that look like meal labels (tie into the existing nutrition label OCR pipeline).
- Legal specialist gets photos that look like documents/letters/forms (OCR + classify).

### Storage & Privacy

- Storage estimate: photos likely 50–200GB depending on library size; SMS/call history likely a few hundred MB; voicemails small. Plan for at least 500GB of dedicated storage with room to grow.
- Privacy model: this is essentially full mirroring of Adam's digital life. Adam is the only authorized reader by default. Becky's ARIA does NOT get access unless Adam explicitly grants per-category access (and even then, probably never the SMS history wholesale).
- Encryption at rest on the server-side storage for the phone-mirror directory.
- Audit log of any specialist that reads from the mirror — query, timestamp, what was returned.
- Retention: keep everything by default. Add a per-category purge tool for specific deletions Adam requests.

### Build Order (Within This Section)

1. **Photos via Syncthing.** Lowest effort, highest immediate utility, no Tasker complexity. Set up sync, verify initial backfill, add EXIF indexing.
2. **SMS + Call log historical backfill** via SMS Backup & Restore. One-shot import to validate the schema and dedup logic.
3. **SMS + Call log live capture** via Tasker. Once the historical schema is settled.
4. **Contacts sync** — small but unblocks meaningful joins on the SMS/call data.
5. **Voicemail research and decision.** Confirm Takeout coverage; if not, fall back to notification-listener transcript capture.
6. **Vision-model indexing** for photos. Adds semantic search to the photo store.
7. **Neo4j schema extension and graph_sync updates** to incorporate phone-mirror entities (depends on Section 21 progress).
8. **Specialist wiring** — Memory/Recall, Communications, Real Estate, Health, Legal can all start querying the new tables once the data is landing.

### Resolved Decisions (2026-04-25)

- **Storage:** existing ARIA server. No separate volume / NAS. Plan capacity headroom accordingly.
- **Outbound SMS unification:** YES — outgoing messages sent by ARIA (via Telnyx today, replacement TBD per Section 19) write to the same `sms_messages` table for unified history. Dedup logic must handle the case where ARIA sends and the phone also captures the same outbound message (key on a composite of timestamp + recipient + body hash, with a small time window for clock skew).
- **Voicemail audio:** original audio is REQUIRED, not just transcripts. Transcripts are a complement, not a substitute. This forces the build path away from notification-listener-only solutions.
- **PC access:** all phone-mirror data must be accessible from Adam's PC, not just queryable through ARIA. He intends to write his own tools against the raw data. Implications:
  - Photos directory exposed via SMB/Samba share or Syncthing two-way (server ↔ PC).
  - Database tables (`sms_messages`, `call_log`, `contacts`, `voicemails`) need either a Postgres connection from the PC, a read-only DB user, or scheduled exports to a PC-accessible directory (CSV / Parquet / SQLite snapshot).
  - Voicemail audio files in a flat directory tree (e.g. `/phone_mirror/voicemails/<YYYY>/<MM>/<callerNumber>_<timestamp>.<ext>`) accessible the same way as photos.
  - Decide on the share mechanism early so directory layout and file naming are consistent from day one.

### Voicemail Audio — Implementation Paths Given Audio Requirement

The original-audio requirement narrows the options significantly:

1. **Google Takeout recurring export** — investigate first. If Takeout includes Google Fi voicemail audio, this is the cleanest non-root path. Schedule recurring exports (every 2 months max per Google's UI) to a Drive folder, then sync that folder to the ARIA server.
2. **Tasker auto-record on voicemail playback** — clunky fallback. Tasker triggers media recording when the Messages app opens a voicemail. Quality depends on Android's audio routing rules, may capture system sounds too.
3. **Third-party voicemail app with accessible storage** — switch off Google Fi's built-in voicemail to an app (e.g. YouMail, HulloMail) that stores audio in a Tasker-readable directory. Trade-off: changes Adam's voicemail UX and may have monthly costs.
4. **Rooting the Pixel 10a** — gives full access to `/data/data/com.google.android.apps.googlevoice/...` style paths. Not currently on the table; only consider if 1–3 all fail.

Build order within voicemail: confirm Takeout coverage first via a manual export and inspect the archive. Only escalate to options 2–4 if Takeout does not include audio.

---

## Phase Plan

**Execution model:** clean rewrite over a single focused weekend (~two long days). ARIA does not need to remain functional during the work. Adam steers Claude Code through each phase, vetting and testing at the milestone between phases. No backward compatibility, no parallel-instance A/B, no incremental migrations. The pre-overhaul codebase exists only as reference — the new system is built fresh against the design in this doc.

**What this means for the plan:**
- Bugs in subsystems being replaced wholesale (delivery_engine routing, nudge composition, weekly summary GROUP BY, single-bucket Qdrant) are NOT separately addressed. They evaporate when their host subsystem is replaced.
- Small "win" phases that exist to incrementally improve the live system are gone. There is no live system to incrementally improve.
- "Folds into the new specialist" is the dominant pattern. Section 5 (email overhaul), Section 11 (supply tracking), Section 12 (system maintenance), Section 13 (domain registration), Section 20 (sat fat) all ship as built-in capabilities of the swarm specialists that own them, not as separate efforts.
- Aspirational/externally-blocked work (G2 custom app, self-hosted email, full Tier A/B answering autonomy, active-mode learning agent) is explicitly OUT of the weekend scope and stays at the bottom as deferred.

Six phases, sized as steerable Claude Code milestones. Each is roughly half a day of focused work plus testing.

---

### Phase 1 — Foundation: Schemas, Memory Layer, Guardrails

**Goal:** build the substrate everything else sits on, in one coordinated pass. Fresh database schema covering every entity the new system needs (no incremental migrations later). Memory infrastructure ready for Memory-default-on. Verification framework wired into every code path that emits to the user.

**Includes:**
- Coordinated single-pass schema design covering: existing domains (health, nutrition, vehicle, legal, calendar, reminders, timers, events), Becky merge data model (shared-household vs per-user, owner columns, view boundaries), source-aware sat fat tracking (Section 20 — pantry tagging + new entry columns), real estate (Section 17.5 entries), financial (Section 17.6 entries), phone mirror entities (Section 22 — sms_messages, call_log, contacts, voicemails, photos metadata, attachments), specialist work-tables, learning-agent review queue, audit/decision logs.
- Memory layer rewrite (Section 21 first half):
  - Qdrant per-domain collections (`ambient_transcripts`, `conversations`, `pantry_items`, `diet_rules`, `code_patterns`, `lies_incidents`, `sms_messages`, `voicemails`, `photos`, per-domain rolling summaries).
  - Hybrid search (vector + BM25) + reranker + time-decay weighting.
  - Per-collection embedding model selection.
  - Neo4j schema covering the full entity/relationship list in Section 21 PLUS phone-mirror entities from Section 22.
  - graph_sync module that populates the graph from Postgres on write.
  - Historical backfill of all existing data into the new memory layer.
- Verification & Guardrails framework (Section 4):
  - Tool-use enforcement validator that gates every user-facing response.
  - Programmatic blockers/detectors for the eight named lie patterns from `Aria_still_lies.txt`.
  - Code-level destructive-action gates extended uniformly across every destructive path.
  - Per-prompt + per-specialist cost tracking infrastructure (used from Phase 2 onward).
- Logging/audit infrastructure that all specialists, the Coordinator, the Composer, and the Channel Router will write into.
- Test scaffolding for the swarm pipeline.

**Test milestone:** memory layer queries return reranked time-decayed results across every Qdrant collection, Neo4j graph traversals work against the new schema, response validator rejects synthetic test responses that contain unverified factual claims, all named lie patterns trip their detectors on synthetic inputs, all schemas migrated and cross-table joins compile.

---

### Phase 2 — Swarm Architecture + Full Specialist Roster

**Goal:** the centerpiece. Build the four-stage swarm pipeline AND every specialist in one go. No "core specialists first, expansion later" split — the rewrite ships the full roster because each one is small once the pipeline is in place and the substrate from Phase 1 exists.

**Includes:**
- Four-stage pipeline (Section 1):
  - Coordinator (lightweight broadcast/collect/no-routing-decisions).
  - Composer ARIA (response composition, owns voice/tone/personality).
  - Channel Router (Communications specialist promoted to final stage, full routing-signal hierarchy with audit log — replaces the entire current delivery_engine).
- Swarm coordination details from Section 1:
  - Peer-to-peer specialist communication with depth limits.
  - Shared per-prompt retrieval cache.
  - No-timeouts policy implemented across asyncio + subprocess + DB connections.
  - Vague-personality initial ack via Channel Router.
  - Orphan-prompt fallback specialist.
  - Memory-default-on across the swarm.
- Full Section 17 specialist roster — all 17 specialists shipped together:
  - 17.1 Health (absorbs the proactive nudge mechanism — Section 2 obsolete)
  - 17.2 Nutrition (absorbs Section 20 sat fat tracking + Section 3 weekly summary fix in passing)
  - 17.3 Legal (sensitive)
  - 17.4 Vehicle
  - 17.5 Real Estate
  - 17.6 Financial (sensitive)
  - 17.7 Supplies / Pantry (absorbs Section 11 supply tracking)
  - 17.8 Email (absorbs Section 5 email overhaul)
  - 17.9 Calendar / Scheduling
  - 17.10 Communications (Channel Router final stage)
  - 17.11 System / DevOps (absorbs Section 12 system maintenance)
  - 17.12 Hardware Integrations
  - 17.13 Research / Web
  - 17.14 Memory / Recall (default-on, write-side owner)
  - 17.15 People / Relationships
  - 17.16 Code / Engineering (absorbs Section 13 domain registration as a Financial+Research+Code joint capability)
  - 17.17 Creative / Brainstorm
- Section 6 context injection improvements absorbed into Coordinator's context-handoff design.

**Test milestone:** end-to-end prompt routing through the swarm. Multi-specialist collaboration (Health + Nutrition + Memory) produces coherent responses. Channel Router routes voice → voice, SMS → SMS, with audit log entries for each decision. Composer preserves personality. Memory specialist is writing every conversation into Qdrant + Neo4j. Cost tracking populating per prompt and per specialist.

---

### Phase 3 — Multi-User Merge + Hardware/Comms Reliability

**Goal:** unify Adam and Becky into one household-data-aware system on the new architecture, and fix the hardware/comms layers the swarm depends on.

**Includes:**
- Section 18 — Becky's ARIA merge fully implemented against the Phase 1 schema:
  - Shared household data (one calendar, one shopping list, one event list both can see).
  - Per-user gated data (health, legal, financial separate per user with no cross-read).
  - Both Aria instances run as separate Composer/Coordinator pairs but query the same shared specialists for shared-data domains.
  - Authorization model: who can read/write what, enforced at specialist level.
  - Cross-user notifications consolidated.
- Section 19 first half — Telnyx replacement (off Telnyx onto the replacement carrier) + multi-contact routing infrastructure (Becky's phone, Adam's phone, future contact slots).
- Section 9 — g2aria reconnection robustness.
- Section 10 — DJI Mic 3 fix. Restores ambient audio pipeline so Memory specialist has live ambient data flowing in.

**Test milestone:** Becky and Adam can both interact with the new ARIA from their respective phones. Cross-user write notifications fire correctly. Shared calendar event added by either user appears in both contexts. Per-user gated data isolation verified. SMS off Telnyx with the new carrier sending and receiving cleanly. DJI Mic 3 producing transcripts. g2aria reconnects within seconds after intentional drops.

---

### Phase 4 — Phone Mirror + Personal Data Ingestion

**Goal:** complete the phone-to-server data ingestion against the Phase 1 schemas with the Phase 2 specialists already wired to consume.

**Includes:**
- Section 22 photos via Syncthing — initial backfill + ongoing one-way sync from `/DCIM`, `/Pictures`, `/Pictures/Screenshots`, `/Download`. EXIF metadata indexed into Postgres.
- Section 22 SMS + MMS bulk historical backfill (SMS Backup & Restore export → `/ingest/sms` endpoint).
- Section 22 SMS + MMS live capture (Tasker `Received Text` / `Sent Text` → `/ingest/sms`).
- Section 22 call log bulk + live capture (Tasker phone-state events → `/ingest/call_log`).
- Section 22 contacts sync (Tasker contacts content provider → `/ingest/contacts`).
- Section 22 voicemail audio — built against whichever of the four paths the Takeout dry-run proves viable. Takeout dry-run is a pre-weekend prerequisite (active reminder [id=82e106cd]). If Takeout works: scheduled recurring exports. If not: fall through to the next path.
- Section 22 vision-model captioning pass for photos, embedded into the `photos` Qdrant collection from Phase 1.
- Section 22 PC access — SMB/Samba shares for file trees (`/phone_mirror/photos/...`, `/phone_mirror/voicemails/...`), read-only Postgres user for DB tables, consistent file naming and directory layout established at ingest time.
- Specialist wiring — Memory/Recall, Communications, Real Estate, Health, Legal all start querying the new phone-mirror tables (no schema changes needed since Phase 1 already shipped them).

**Test milestone:** photo library mirrored and EXIF-queryable. SMS history searchable end-to-end (history + new live messages). Call log queryable. Contacts joined to call/SMS data. At least transcripts captured for voicemails (audio if Takeout works). Photo semantic search working via vision captions. Adam can SMB-mount the phone-mirror directories from his PC and read tables via the read-only Postgres user.

---

### Phase 5 — Self-Learning Agent (Passive Observer)

**Goal:** ship the learning agent in passive observer mode so it accumulates proposal data from day one. Active mode is explicitly deferred — gated on observer reliability metrics, not a date.

**Includes:**
- Learning / Meta-Reflection specialist (17.18, new) per Section 21 second half:
  - Watches all Adam↔ARIA and Becky↔ARIA conversations.
  - Classifies events: explicit corrections, implicit corrections, preference signals, repeat-pattern lies, successful patterns to reinforce.
  - Produces proposed diffs to a review queue:
    - System prompt deltas (additions, rule changes).
    - New `Aria_still_lies.txt` entries with structured fields.
    - Pantry / diet-ref updates.
    - Code change proposals.
    - Weighting / cooldown / autonomy-tier adjustments.
  - Confirmation gate same pattern as destructive actions — never auto-applies.
  - Audit trail tagging each approved change with source conversation.
- Review queue UI — Adam can list, view diff, approve/reject from CLI.
- Cross-references with Memory/Recall (17.14) for prior-situation lookups during analysis.
- Cross-references with Code/Engineering (17.16) for diff generation when changes are code-level.

**Explicitly deferred to post-weekend:**
- Active proposal mode (auto-suggesting changes mid-conversation rather than batch-queueing).
- Per-domain learning sub-agents.
- Both gated on observer-mode reliability data accumulating over weeks.

**Test milestone:** observer agent populating the review queue with at least synthetic-test proposals across each diff category. CLI review tool lists, displays, approves, rejects entries. Approved entries actually apply to the right files / tables / configs.

---

### Phase 6 — Answering Service (Tier D + Tier C)

**Goal:** ship the lower-autonomy tiers of the contact-tier answering service. Higher tiers explicitly deferred to a post-weekend iteration when observer-validated patterns exist.

**Includes:**
- Section 19 second half, partial — first two tiers only:
  - **Tier D** — message-taking only. Polite acknowledgment, ask for callback info if missing, relay full message to Adam. No autonomous answer to anything substantive. Default tier for unknown / unassigned contacts.
  - **Tier C** — limited autonomous responses on factual matters Adam has pre-approved (e.g. "what time does Adam's birthday party start," "is Adam home today"). Anything outside the pre-approved factual set gets the Tier D treatment. Pre-approved set lives in a config Adam edits.
- Per-tier confirmation thresholds (what requires Adam's approval before sending — for Tier C, anything the model is below a confidence threshold on).
- Per-contact tier assignments + override rules.
- Audit log of every autonomous action with tier classification, content, and after-the-fact-review queue.

**Explicitly deferred to post-weekend:**
- **Tier B** — extended autonomy with proactive scheduling, follow-ups, multi-turn conversations within bounded scope.
- **Tier A** (family) — full conversational autonomy with same depth as Adam's own ARIA.
- Both gated on Tier C audit-log review demonstrating that lower-tier autonomy is reliable. Once Adam reviews the Tier C log and confirms the autonomous responses look right, Tier B becomes safe to ship.

**Test milestone:** Tier D and Tier C work end-to-end. An unknown contact texting in gets the Tier D experience. A known Tier C contact (e.g. a friend) texting "what time's Adam's party Saturday" gets the autonomous answer; texting "hey can I borrow $500" gets the Tier D fallback. Audit log captures both with full content.

---

### Deferred — External Dependencies and Post-Weekend Iteration

These do not ship in the weekend overhaul. Tracked here so they aren't lost.

- **Section 8 — G2 glasses custom app.** Blocked on upstream repo's mic capture support. Ships when that lands.
- **Section 14 — Self-hosted email.** Depends on domain ownership + inbound mail infrastructure. Slots into Email specialist (17.8) once infra exists.
- **Active-mode learning agent.** Phase 5 ships passive observer; active mode requires accumulated reliability data.
- **Tier B + Tier A answering service.** Phase 6 ships D + C; higher tiers gate on Tier C audit-log review.
- **Per-domain learning sub-agents** (Nutrition-learner, Communications-learner, etc.) — coordinated by the Meta-Reflection specialist once active mode is proven.
- **Plaid / financial account aggregation** for Section 17.6 Financial specialist — privacy-and-integration project Adam slots in whenever ready.

---

### Cross-Phase Notes

- **Section 15 (original open TODOs) and Section 16 (deferred / watch-list)** items get pulled into whichever phase covers their domain. The lists themselves stay in the doc as a backlog reference for the weekend.
- **Real Estate (17.5)** ships in Phase 2 as part of the full roster but stays mostly passive-tracking until Pine Court sale closes, then pivots to next-house search. No special phase treatment needed.
- **Pre-weekend prerequisites:** Google Takeout dry-run for voicemail (active reminder [id=82e106cd]) so Phase 4 voicemail path is decided before weekend starts. SMS Backup & Restore installed and bulk export taken so Phase 4 SMS/call backfill is ready to ingest. Syncthing app installed on phone so Phase 4 photo sync starts immediately when the server side stands up.
- **Tools that go away entirely** (no separate fix needed because their host subsystem is replaced wholesale): the current `delivery_engine.py` (replaced by Channel Router), the current Haiku-tier `ask_haiku` nudge composer in `daemon.py:1719` (replaced by specialist-owned proactive notifications), the current single-bucket Qdrant `aria_memory` collection (replaced by per-domain collections), the current minimal Neo4j schema (replaced by full schema). The reminder for the voice→SMS bug ([id=1507cd69]) is also obsolete — that bug lives in `delivery_engine.py` and the entire file is gone after Phase 2.

---

*Last updated: 2026-04-25 (afternoon — Section 19 hardware path decided: Pixel A-series + capcom6/android-sms-gateway app; SMSEagle appliance rejected on cost; USB cellular modem rejected on MMS unsupportability; earlier today: added Section 0 "Pre-Flight" at the top, Phase Plan rewritten for clean-rewrite-over-one-weekend execution model, replaced Priority Ordering with phase plan, Section 1 swarm refinement with no-timeouts and Memory-default-on, Section 17 cross-cutting rules updated for swarm/peer-to-peer, Section 17.10 promotion to final stage, Section 17.14 expanded for default-on and write-side ownership, Section 22 phone mirror) by ARIA at Adam's request. Living document — append new items here rather than scattering them across notes/TODOs.*
