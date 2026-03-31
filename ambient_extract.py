"""Extraction engine for ambient transcripts.

Processes transcript batches through Opus 4.6 (CLI, auto effort) to extract
commitments, people, topics, and conversation boundaries. Also generates
per-conversation and daily summaries.

Uses one-shot Claude Code CLI subprocesses — subscription-covered, no API cost.
Effort level is scoped to the subprocess env only (never leaks to daemon).
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta

import config
import ambient_store
import commitment_store
import person_store

log = logging.getLogger("aria.extract")

# CLI configuration
_CLI = getattr(config, "CLAUDE_CLI", "/usr/bin/claude")
_MODEL = "opus"       # Opus 4.6 for extraction quality
_EFFORT = "auto"      # auto effort — fast, no deep thinking


def _ask_cli(prompt: str, timeout: int = 120) -> str | None:
    """Run a one-shot Claude Code CLI query with Opus at auto effort.

    Spawns a subprocess with isolated env (effort level does NOT leak).
    Returns the result text, or None on failure.
    """
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["CLAUDE_CODE_EFFORT_LEVEL"] = _EFFORT
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

    try:
        proc = subprocess.Popen(
            [
                _CLI,
                "--print",
                "--output-format", "stream-json",
                "--input-format", "stream-json",
                "--model", _MODEL,
                "--dangerously-skip-permissions",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(config.BASE_DIR),
        )

        # Send the prompt
        msg = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": prompt},
        }) + "\n"
        proc.stdin.write(msg.encode())
        proc.stdin.flush()

        # Read until result (stream-json protocol)
        start = time.time()
        result_text = None

        while time.time() - start < timeout:
            line = proc.stdout.readline()
            if not line:
                break

            try:
                data = json.loads(line.decode().strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            if data.get("type") == "result":
                if data.get("is_error"):
                    log.warning("CLI extraction error: %s", data.get("result", "")[:200])
                    result_text = None
                else:
                    result_text = data.get("result", "")
                break

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        return result_text

    except FileNotFoundError:
        log.error("Claude CLI not found at %s", _CLI)
        return None
    except Exception as e:
        log.error("CLI extraction failed: %s", e)
        return None


def _parse_json_from_response(text: str) -> dict | None:
    """Extract JSON object from a CLI response that may include markdown fences."""
    if not text:
        return None

    # Try to find JSON in code fences first
    import re
    fence_match = re.search(r'```(?:json)?\s*\n?({.*?})\s*\n?```', text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try the whole text as JSON
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object anywhere in the text
    brace_start = text.find('{')
    if brace_start >= 0:
        # Find matching closing brace
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start:i + 1])
                    except json.JSONDecodeError:
                        break

    log.warning("Could not parse JSON from extraction response")
    return None


# ---------------------------------------------------------------------------
# Conversation boundary detection
# ---------------------------------------------------------------------------

def detect_conversation_boundaries(transcripts: list[dict],
                                   gap_minutes: float = 5.0) -> list[list[dict]]:
    """Group transcript segments into conversations based on time gaps.

    A silence gap > gap_minutes between segments starts a new conversation.
    Returns a list of conversation groups, each a list of transcript dicts.
    """
    if not transcripts:
        return []

    # Sort by started_at
    sorted_t = sorted(transcripts, key=lambda t: t.get("started_at", ""))

    groups = [[sorted_t[0]]]
    for t in sorted_t[1:]:
        prev = groups[-1][-1]
        prev_end = prev.get("ended_at") or prev.get("started_at", "")
        curr_start = t.get("started_at", "")

        try:
            prev_dt = datetime.fromisoformat(prev_end)
            curr_dt = datetime.fromisoformat(curr_start)
            gap = (curr_dt - prev_dt).total_seconds() / 60.0
        except (ValueError, TypeError):
            gap = 0

        if gap > gap_minutes:
            groups.append([t])
        else:
            groups[-1].append(t)

    return groups


# ---------------------------------------------------------------------------
# Extraction from transcript batch
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """You are analyzing ambient audio transcripts. Extract structured data.

TRANSCRIPTS (chronological):
{transcripts}

Return ONLY a JSON object with this exact structure (no markdown, no explanation):
{{"commitments": [{{"who": "self or person name", "what": "the commitment", "to_whom": "person or null", "due_date": "YYYY-MM-DD or null"}}], "people": [{{"name": "person name", "relationship": "guess: coworker/friend/family/unknown", "organization": "guess or null"}}], "topics": ["topic1", "topic2"], "summary": "1-2 sentence summary of this conversation"}}

Rules:
- Only include commitments you are confident about (explicit promises, not casual mentions)
- "who" is "self" when the speaker made the commitment, or the person's name if someone else did
- Only include people actually mentioned by name (not pronouns)
- Topics should be 1-3 words each
- If nothing notable was said, return empty arrays and a brief summary
"""


def extract_from_batch(transcripts: list[dict]) -> dict | None:
    """Run Opus extraction on a batch of transcript segments.

    Returns parsed JSON with commitments, people, topics, summary.
    Returns None on failure.
    """
    if not transcripts:
        return None

    # Format transcripts for the prompt
    lines = []
    for t in transcripts:
        ts = t.get("started_at", "?")
        if isinstance(ts, str) and "T" in ts:
            ts = ts.split("T")[1][:8]  # HH:MM:SS
        speaker = t.get("quality_speaker") or t.get("speaker") or "?"
        text = t.get("quality_text") or t.get("text", "")
        lines.append(f"[{ts}] {speaker}: {text}")

    transcript_text = "\n".join(lines)
    prompt = _EXTRACTION_PROMPT.format(transcripts=transcript_text)

    result = _ask_cli(prompt, timeout=60)
    if result is None:
        return None

    return _parse_json_from_response(result)


# ---------------------------------------------------------------------------
# Store extraction results
# ---------------------------------------------------------------------------

def store_extraction_results(extraction: dict, transcripts: list[dict],
                             conversation_id: int | None = None):
    """Store extracted commitments and person profiles in the database."""
    if not extraction:
        return

    # Store commitments
    for c in extraction.get("commitments", []):
        who = c.get("who", "unknown")
        what = c.get("what", "")
        if not what:
            continue
        try:
            commitment_store.add(
                who=who,
                what=what,
                to_whom=c.get("to_whom"),
                due_date=c.get("due_date"),
                source="ambient",
                source_id=transcripts[0]["id"] if transcripts else None,
                conversation_id=conversation_id,
            )
        except Exception as e:
            log.warning("Failed to store commitment: %s", e)

    # Store/update person profiles
    for p in extraction.get("people", []):
        name = p.get("name", "").strip()
        if not name or len(name) < 2:
            continue
        try:
            person_store.upsert(
                name=name,
                relationship=p.get("relationship"),
                organization=p.get("organization"),
            )
            person_store.record_mention(name)
        except Exception as e:
            log.warning("Failed to store person %s: %s", name, e)


# ---------------------------------------------------------------------------
# Process a conversation group
# ---------------------------------------------------------------------------

def process_conversation_group(transcripts: list[dict]) -> int | None:
    """Process a group of transcripts as a conversation.

    Creates conversation record, runs extraction, stores results.
    Returns conversation_id or None.
    """
    if not transcripts:
        return None

    first = transcripts[0]
    last = transcripts[-1]

    # Calculate total duration
    total_dur = sum(t.get("duration_s", 0) or 0 for t in transcripts)

    # Get location from the most recent location entry
    location = None
    try:
        import location_store
        loc = location_store.get_latest()
        if loc:
            location = loc.get("location")
    except Exception:
        pass

    # Collect unique speakers
    speakers = list(set(
        t.get("quality_speaker") or t.get("speaker") or "unknown"
        for t in transcripts
        if t.get("quality_speaker") or t.get("speaker")
    ))

    # Create conversation record
    conv = ambient_store.create_conversation(
        started_at=first.get("started_at", datetime.now().isoformat()),
        ended_at=last.get("ended_at") or last.get("started_at"),
        duration_s=total_dur,
        speakers=speakers,
        location=location,
    )
    conv_id = conv["id"]

    # Assign transcripts to conversation
    transcript_ids = [t["id"] for t in transcripts]
    ambient_store.assign_to_conversation(transcript_ids, conv_id)

    # Run extraction
    extraction = extract_from_batch(transcripts)
    if extraction:
        # Update conversation with summary
        ambient_store.update_conversation(
            conv_id,
            title=extraction.get("summary", "")[:100],
            summary=extraction.get("summary"),
            speakers=speakers,
        )
        # Store extracted entities
        store_extraction_results(extraction, transcripts, conv_id)

    return conv_id


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

_DAILY_SUMMARY_PROMPT = """Summarize today's ambient conversations for a personal assistant evening debrief.

DATE: {date}
CONVERSATIONS ({count} total, {duration} of recorded audio):

{conversations}

Write a 2-4 paragraph narrative summary covering:
- Key people encountered and what was discussed
- Commitments made or received
- Notable decisions or information
- Anything that seems important to follow up on

Be concise but thorough. This will be read during an evening debrief.
"""


def generate_daily_summary(day: str | None = None) -> dict | None:
    """Generate a daily summary from the day's conversations.

    Returns the daily_summaries row dict, or None on failure.
    """
    if day is None:
        day = datetime.now().strftime("%Y-%m-%d")

    # Get today's conversations
    convs = ambient_store.get_conversations(days=1)
    convs = [c for c in convs if c.get("started_at", "").startswith(day)]

    if not convs:
        log.info("No conversations to summarize for %s", day)
        return None

    # Format conversations for the prompt
    conv_lines = []
    total_dur = 0
    all_people = set()
    commitment_count = 0

    for c in convs:
        started = c.get("started_at", "?")
        if isinstance(started, str) and "T" in started:
            started = started.split("T")[1][:5]  # HH:MM
        dur = c.get("duration_s", 0) or 0
        total_dur += dur
        speakers = ", ".join(c.get("speakers", []))
        all_people.update(c.get("speakers", []))
        summary = c.get("summary") or c.get("title") or "(no summary)"
        conv_lines.append(f"- [{started}] ({dur/60:.0f}min) Speakers: {speakers}\n  {summary}")

    # Get today's commitments
    try:
        recent = commitment_store.get_recent(days=1)
        commitment_count = len([c for c in recent
                                if c.get("created_at", "").startswith(day)])
    except Exception:
        pass

    duration_str = f"{total_dur/60:.0f} minutes"
    prompt = _DAILY_SUMMARY_PROMPT.format(
        date=day,
        count=len(convs),
        duration=duration_str,
        conversations="\n".join(conv_lines),
    )

    result = _ask_cli(prompt, timeout=90)
    if not result:
        log.warning("Daily summary generation failed for %s", day)
        return None

    # Clean up the result (strip any markdown artifacts)
    summary_text = result.strip()

    # Remove people who aren't real names
    people_list = [p for p in all_people if p not in ("unknown", "?", "owner")]

    return ambient_store.upsert_daily_summary(
        day=day,
        summary=summary_text,
        key_topics=[],  # could be extracted from conversations
        people_mentioned=people_list,
        commitments_made=commitment_count,
        conversation_count=len(convs),
        total_duration_s=total_dur,
    )


# ---------------------------------------------------------------------------
# Main extraction pass (called from tick.py)
# ---------------------------------------------------------------------------

def run_extraction_pass(limit: int = 100) -> int:
    """Process unextracted transcripts: group into conversations, extract entities.

    Returns count of transcripts processed.
    """
    transcripts = ambient_store.get_unextracted(limit=limit)
    if not transcripts:
        return 0

    log.info("Extraction pass: %d unextracted transcripts", len(transcripts))

    # Group into conversations
    groups = detect_conversation_boundaries(transcripts)
    log.info("Detected %d conversation groups", len(groups))

    processed = 0
    for group in groups:
        try:
            process_conversation_group(group)
            # Mark as extracted
            ids = [t["id"] for t in group]
            ambient_store.mark_extracted(ids)
            processed += len(ids)
        except Exception as e:
            log.error("Extraction failed for group starting at %s: %s",
                      group[0].get("started_at", "?"), e)

    log.info("Extraction pass complete: %d transcripts processed", processed)
    return processed
