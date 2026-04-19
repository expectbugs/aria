"""ARIA CLI session pool — deep + fast Opus sessions for primary requests.

Replaces the Anthropic API as the primary brain. Two persistent Claude CLI
sessions with different effort levels, automatic recycling, crash recovery,
and cross-session history continuity via conversation history injection.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import config
from conversation_history import get_recent_turns
from system_prompt import build_primary_prompt


@dataclass
class SessionResponse:
    """Response from a CLI session query, with tool call metadata."""
    text: str
    tool_calls: list[str] = field(default_factory=list)  # tool names used
    stream_events: list[dict] = field(default_factory=list)  # full stream log


# Tool use reminder — injected at the END of every query so it sits near
# response generation where attention is strongest (combats lost-in-the-middle).
_TOOL_USE_REMINDER = (
    "[INSTRUCTION] Before responding, consider: do you have VERIFIED information "
    "for your answer? Your tools are: query.py (health, nutrition, vehicle, legal, "
    "calendar, conversations, email), Bash (shell commands, web fetch). "
    "If your response contains facts, dates, numbers, or state claims, you MUST "
    "verify them with a tool first. Do NOT respond from memory alone."
)

# --- Context deduplication ---
# Matches [dedup:KEY:HASH]...content...[/dedup:KEY] tags emitted by context.py.
# Used to skip re-injecting large static sections (pantry, diet_reference) and
# unchanged dynamic sections (health snapshot, nutrition) within a session.
_DEDUP_RE = re.compile(
    r'\[dedup:(\w+):(\w+)\]\n(.*?)\n\[/dedup:\1\]',
    re.DOTALL,
)


def _apply_context_dedup(context: str, hashes: dict[str, str]) -> str:
    """Replace unchanged tagged context sections with brief references.

    Scans for [dedup:key:hash]...content...[/dedup:key] tags.
    If a section's hash matches the previous injection, replaces with a one-liner.
    If new or changed, keeps the full content and updates the hash cache.

    Returns the processed context string. Mutates the hashes dict in place.
    """
    def _replace(m: re.Match) -> str:
        key, hash_val, content = m.group(1), m.group(2), m.group(3)
        if key in hashes and hashes[key] == hash_val:
            # Content unchanged — skip re-injection
            return f"[{key}: unchanged from previous context]"
        # New or changed — inject fully, update cache
        hashes[key] = hash_val
        return content.strip()

    return _DEDUP_RE.sub(_replace, context)

log = logging.getLogger("aria")


def _format_history_for_injection(turns: list[dict]) -> str:
    """Format API-style conversation turns as a text block for CLI session injection.

    Converts get_recent_turns() output into a compact text summary prefixed
    to the first user message after spawn/recycle. This bridges the context
    gap between sessions with zero extra inference round-trips.
    """
    if not turns:
        return ""
    lines = ["[CONVERSATION HISTORY — previous session, for continuity]"]
    for turn in turns:
        role = turn["role"].upper()
        content = turn["content"] if isinstance(turn["content"], str) else str(turn["content"])
        if len(content) > 1500:
            content = content[:1500] + "..."
        lines.append(f"{role}: {content}")
    lines.append("[/CONVERSATION HISTORY]")
    return "\n".join(lines)


class _Session:
    """A single persistent Claude CLI session with stream-json protocol.

    Internal to SessionPool — not used directly. Extends the pattern from
    ClaudeSession (claude_session.py) with effort-level configuration and
    history injection on spawn/recycle.
    """

    def __init__(self, name: str, effort: str, max_requests: int,
                 user_key: str = "adam",
                 system_prompt: str | None = None):
        self.name = name              # "deep" or "fast"
        self.user_key = user_key      # "adam" or "becky" — for logging + history filter
        self._effort = effort         # "max" or "auto"
        self._max_requests = max_requests
        self._system_prompt = system_prompt  # if None, resolved lazily in _spawn
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._request_count = 0
        self._history_injected = False
        self._spawned_at: datetime | None = None
        self._context_bytes = 0       # estimated context window usage
        self._max_context_bytes = getattr(
            config, "SESSION_MAX_CONTEXT_BYTES", 500_000
        )  # ~125K tokens ≈ 62% of 200K window
        self._dedup_hashes: dict[str, str] = {}  # key → hash for context dedup
        self._consecutive_failures = 0
        self._last_spawn_attempt: datetime | None = None

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _resolve_system_prompt(self) -> str:
        """Lazy-resolve the system prompt so subclass changes or user-specific
        prompts are picked up each spawn (recycle re-reads)."""
        if self._system_prompt is not None:
            return self._system_prompt
        # Default: Adam's prompt. Becky's pool passes system_prompt explicitly.
        return build_primary_prompt()

    async def _spawn(self):
        """Spawn a fresh Claude CLI process."""
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["CLAUDE_CODE_EFFORT_LEVEL"] = self._effort
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

        self._proc = await asyncio.create_subprocess_exec(
            config.CLAUDE_CLI,
            "--print",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--model", "opus",
            "--dangerously-skip-permissions",
            "--system-prompt", self._resolve_system_prompt(),
            "--settings", '{"claudeMdExcludes": ["/home/user/aria/CLAUDE.md"]}',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=16 * 1024 * 1024,  # 16MB readline buffer
        )
        self._request_count = 0
        self._history_injected = False
        self._context_bytes = 0
        self._dedup_hashes = {}  # fresh session — re-inject everything
        self._spawned_at = datetime.now()
        log.info("Session '%s:%s' spawned (pid=%s, effort=%s)",
                 self.user_key, self.name, self._proc.pid, self._effort)

    async def _kill(self):
        """Kill the current process if alive."""
        if self._is_alive():
            try:
                self._proc.kill()
                await self._proc.wait()
            except Exception:
                pass
        self._proc = None

    async def _ensure_alive(self):
        """Ensure the subprocess is running. Respawn if dead or stale."""
        if not self._is_alive() or self._request_count >= self._max_requests:
            if self._is_alive():
                log.info("Recycling session '%s' after %d requests",
                         self.name, self._request_count)
                await self._kill()
            await self._spawn()

    async def _heal(self) -> str:
        """Respawn if dead, with crash-loop backoff. Thread-safe with query().

        Returns: 'ok', 'respawned', 'crash_loop', 'backoff', or 'respawn_failed: ...'
        """
        async with self._lock:
            if self._is_alive():
                self._consecutive_failures = 0
                return "ok"
            if self._consecutive_failures >= 5:
                log.error(
                    "Session '%s' in crash loop (%d consecutive failures), "
                    "backing off indefinitely",
                    self.name, self._consecutive_failures,
                )
                return "crash_loop"
            if self._last_spawn_attempt:
                backoff = 10 * (2 ** self._consecutive_failures)
                elapsed = (datetime.now() - self._last_spawn_attempt).total_seconds()
                if elapsed < backoff:
                    return "backoff"
            try:
                self._last_spawn_attempt = datetime.now()
                await self._spawn()
                self._consecutive_failures = 0
                return "respawned"
            except Exception as e:
                self._consecutive_failures += 1
                log.error(
                    "Failed to respawn session '%s' (attempt %d): %s",
                    self.name, self._consecutive_failures, e,
                )
                return f"respawn_failed: {e}"

    async def query(self, user_text: str, extra_context: str = "",
                    file_blocks: list[dict] | None = None,
                    system_correction: bool = False) -> SessionResponse:
        """Send a prompt to the persistent Claude process and return the response."""
        async with self._lock:
            await self._ensure_alive()

            parts = []

            # History injection on first query after spawn/recycle
            if not self._history_injected:
                history_block = await self._prepare_history()
                if history_block:
                    parts.append(history_block)
                self._history_injected = True

            if extra_context:
                # Dedup large static sections (pantry, diet_ref, health)
                deduped = _apply_context_dedup(extra_context, self._dedup_hashes)
                parts.append(f"[CONTEXT]\n{deduped}\n[/CONTEXT]")
            if system_correction:
                parts.append(
                    f"[SYSTEM CORRECTION — not from user]\n{user_text}")
            else:
                parts.append(f"User says: {user_text}")

            # Tool use reminder — last item before generation
            parts.append(_TOOL_USE_REMINDER)
            prompt = "\n".join(parts)

            # Build message content — text-only or multimodal
            if file_blocks:
                content = [{"type": "text", "text": prompt}] + file_blocks
            else:
                content = prompt

            # Track estimated context size (input)
            input_bytes = len(prompt)
            if file_blocks:
                input_bytes += sum(len(json.dumps(b)) for b in file_blocks)
            self._context_bytes += input_bytes

            # Send user message as NDJSON
            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": content},
            }) + "\n"
            self._proc.stdin.write(msg.encode())
            await self._proc.stdin.drain()
            self._request_count += 1

            # Read stdout lines until we get a result.
            # Collect ALL intermediate events: assistant text blocks (pre- and
            # between tool calls), tool_use invocations, and tool results.
            # The final "result" message only contains the last text — earlier
            # text blocks are lost without this collection.
            tool_calls_seen: list[str] = []
            stream_events: list[dict] = []
            assistant_text_parts: list[str] = []
            try:
                while True:
                    line = await asyncio.wait_for(
                        self._proc.stdout.readline(),
                        timeout=config.CLAUDE_TIMEOUT,
                    )
                    if not line:
                        raise RuntimeError(
                            f"Session '{self.name}' exited unexpectedly")

                    try:
                        data = json.loads(line.decode().strip())
                    except json.JSONDecodeError:
                        continue  # skip non-JSON lines

                    msg_type = data.get("type")

                    if msg_type == "result":
                        if data.get("is_error"):
                            raise RuntimeError(
                                f"Session '{self.name}' error: "
                                f"{data.get('result', 'unknown')}")
                        result_text = data.get("result", "")

                        # Assemble complete response: the "result" message
                        # contains only the LAST assistant text block. If
                        # ARIA said something before a tool call, that text
                        # was in an earlier assistant message and needs to
                        # be prepended. We collect all assistant text parts
                        # and check which ones are NOT in the final result.
                        if len(assistant_text_parts) > 1:
                            earlier = []
                            for part in assistant_text_parts:
                                if part.strip() not in result_text:
                                    earlier.append(part.strip())
                            if earlier:
                                result_text = "\n".join(earlier) + "\n" + result_text

                        # Track output size + check context pressure
                        self._context_bytes += len(result_text)
                        if self._context_bytes > self._max_context_bytes:
                            log.info(
                                "[CONTEXT] Session '%s' at %d bytes "
                                "(~%dK tokens), scheduling recycle",
                                self.name, self._context_bytes,
                                self._context_bytes // 4000,
                            )
                            self._request_count = self._max_requests
                        if tool_calls_seen:
                            log.info("[TOOLS] Session '%s' used %d tools: %s",
                                     self.name, len(tool_calls_seen),
                                     ", ".join(tool_calls_seen[:10]))
                        return SessionResponse(
                            text=result_text,
                            tool_calls=tool_calls_seen,
                            stream_events=stream_events)

                    elif msg_type == "assistant":
                        # Collect text and tool_use content blocks
                        msg_data = data.get("message", {})
                        if isinstance(msg_data, dict):
                            content = msg_data.get("content", [])
                            if isinstance(content, list):
                                for block in content:
                                    if not isinstance(block, dict):
                                        continue
                                    if block.get("type") == "tool_use":
                                        name = block.get("name", "unknown")
                                        tool_calls_seen.append(name)
                                        stream_events.append({
                                            "event": "tool_call",
                                            "tool": name,
                                            "input": block.get("input", {}),
                                        })
                                    elif block.get("type") == "text":
                                        text_val = block.get("text", "")
                                        if text_val.strip():
                                            assistant_text_parts.append(
                                                text_val)
                                            stream_events.append({
                                                "event": "assistant_text",
                                                "text": text_val,
                                            })

                    elif msg_type == "tool":
                        # Tool result — capture for debug stream
                        tool_content = data.get("message", {})
                        if isinstance(tool_content, dict):
                            content = tool_content.get("content", "")
                            # Truncate large tool results for the stream log
                            if isinstance(content, str) and len(content) > 500:
                                content = content[:500] + "..."
                            stream_events.append({
                                "event": "tool_result",
                                "content": content,
                            })

                    elif msg_type == "control_request":
                        # Auto-approve any permission/hook requests
                        resp = json.dumps({
                            "type": "control_response",
                            "response": {
                                "subtype": "success",
                                "request_id": data.get("request_id"),
                                "response": {"behavior": "allow"},
                            }
                        }) + "\n"
                        self._proc.stdin.write(resp.encode())
                        await self._proc.stdin.drain()

                    # Ignore other types (system, stream_event, etc.)

            except asyncio.TimeoutError:
                log.error("Session '%s' timed out after %ss",
                          self.name, config.CLAUDE_TIMEOUT)
                await self._kill()
                raise RuntimeError(
                    f"Session '{self.name}' timed out after "
                    f"{config.CLAUDE_TIMEOUT}s")
            except Exception:
                log.exception("Session '%s' error, killing process", self.name)
                await self._kill()
                raise

    async def _prepare_history(self) -> str:
        """Prepare history for injection after spawn/recycle.

        Returns verbatim recent turns (10) plus a Haiku-generated summary
        of older turns (11-30) for continuity across context window recycles.
        Filters by user_key so Becky's session only sees her SMS history
        (and vice versa).
        """
        recent = get_recent_turns(10, user_key=self.user_key)
        verbatim = _format_history_for_injection(recent)

        # Try to summarize older conversation via Haiku
        try:
            all_turns = get_recent_turns(30, user_key=self.user_key)
            older = all_turns[:-10] if len(all_turns) > 10 else []
            if older:
                from aria_api import ask_haiku
                older_text = _format_history_for_injection(older)
                summary = await ask_haiku(
                    "Summarize this conversation history in 3-4 sentences. "
                    "Focus on: topics discussed, decisions made, pending items, "
                    "and the user's current mood/situation.\n\n" + older_text
                )
                return (
                    f"[CONVERSATION SUMMARY — earlier session]\n{summary}\n"
                    f"[/CONVERSATION SUMMARY]\n\n{verbatim}"
                )
        except Exception as e:
            log.warning("History summary failed (using verbatim only): %s", e)

        return verbatim

    def get_status(self) -> dict:
        """Return status info for health checks."""
        return {
            "name": self.name,
            "alive": self._is_alive(),
            "effort": self._effort,
            "request_count": self._request_count,
            "max_requests": self._max_requests,
            "pid": self._proc.pid if self._is_alive() else None,
            "spawned_at": self._spawned_at.isoformat() if self._spawned_at else None,
            "context_bytes": self._context_bytes,
            "context_pct": round(
                self._context_bytes / self._max_context_bytes * 100, 1
            ) if self._max_context_bytes else 0,
            "consecutive_failures": self._consecutive_failures,
        }


_UNSET = object()  # sentinel distinguishing "not passed" from "explicitly None"


class SessionPool:
    """Managed pool of deep + fast Claude CLI sessions for ONE user.

    Deep session: Opus with max effort — default for all user requests.
    Fast session: Opus with auto effort — for _is_simple_query() matches.
                  Pass fast_effort=None to skip the fast session entirely
                  (Becky's pool uses this — she has lower message volume).

    Default (no args) behaves like the legacy singleton: Adam's config,
    both sessions present. The registry factory `_build_pool` passes
    explicit values for each user.
    """

    def __init__(self, user_key: str = "adam",
                 deep_effort=_UNSET,
                 fast_effort=_UNSET,
                 system_prompt: str | None = None):
        recycle = getattr(config, "SESSION_RECYCLE_AFTER", 150)
        if deep_effort is _UNSET:
            deep_effort = getattr(config, "SESSION_DEEP_EFFORT", "max")
        if fast_effort is _UNSET:
            fast_effort = getattr(config, "SESSION_FAST_EFFORT", "auto")

        self.user_key = user_key
        self._deep = _Session("deep", deep_effort, recycle,
                              user_key=user_key,
                              system_prompt=system_prompt)
        if fast_effort is not None:
            self._fast = _Session("fast", fast_effort, recycle,
                                  user_key=user_key,
                                  system_prompt=system_prompt)
        else:
            self._fast = None

    async def start(self):
        """Pre-warm sessions. Called from daemon lifespan."""
        await self._deep._spawn()
        if self._fast is not None:
            await self._fast._spawn()
        log.info("Session pool '%s' started (%s)",
                 self.user_key,
                 "deep + fast" if self._fast else "deep only")

    async def stop(self):
        """Kill sessions. Called from daemon lifespan shutdown."""
        await self._deep._kill()
        if self._fast is not None:
            await self._fast._kill()
        log.info("Session pool '%s' stopped", self.user_key)

    async def query_deep(self, text: str, context: str = "",
                         file_blocks: list[dict] | None = None,
                         system_correction: bool = False) -> SessionResponse:
        """Query the deep (max effort) session."""
        return await self._deep.query(text, context, file_blocks,
                                      system_correction)

    async def query_fast(self, text: str, context: str = "",
                         file_blocks: list[dict] | None = None,
                         system_correction: bool = False) -> SessionResponse:
        """Query the fast (auto effort) session.

        If this pool has no fast session (e.g. Becky's), falls back to deep.
        """
        if self._fast is None:
            return await self._deep.query(text, context, file_blocks,
                                          system_correction)
        return await self._fast.query(text, context, file_blocks,
                                      system_correction)

    def get_status(self) -> dict:
        """Return status of both sessions for health checks."""
        result = {"user_key": self.user_key, "deep": self._deep.get_status()}
        if self._fast is not None:
            result["fast"] = self._fast.get_status()
        else:
            result["fast"] = {"name": "fast", "alive": False, "absent": True}
        return result

    async def ensure_healthy(self) -> dict:
        """Check sessions and respawn any that have died."""
        deep_status = await self._deep._heal()
        if self._fast is not None:
            fast_status = await self._fast._heal()
        else:
            fast_status = "absent"
        return {"deep": deep_status, "fast": fast_status}


# --- Registry ---

_SESSION_POOLS: dict[str, SessionPool] = {}


def _build_pool(user_key: str) -> SessionPool:
    """Construct a pool for user_key with the appropriate config + prompt.

    Kept separate so tests can monkey-patch pool construction easily.
    """
    # Lazy import — avoids circular import at module load time. system_prompt
    # imports config, which itself might import from places that need this module
    # in the future.
    from system_prompt import build_primary_prompt, build_becky_primary_prompt

    if user_key == "adam":
        return SessionPool(
            user_key="adam",
            deep_effort=getattr(config, "SESSION_DEEP_EFFORT", "max"),
            fast_effort=getattr(config, "SESSION_FAST_EFFORT", "auto"),
            system_prompt=build_primary_prompt(),
        )
    if user_key == "becky":
        return SessionPool(
            user_key="becky",
            deep_effort=getattr(config, "BECKY_SESSION_DEEP_EFFORT", "max"),
            fast_effort=getattr(config, "BECKY_SESSION_FAST_EFFORT", None),
            system_prompt=build_becky_primary_prompt(),
        )
    raise ValueError(f"Unknown user_key: {user_key}")


def get_session_pool(user_key: str = "adam") -> SessionPool:
    """Get or create the session pool for a specific user."""
    if user_key not in _SESSION_POOLS:
        _SESSION_POOLS[user_key] = _build_pool(user_key)
    return _SESSION_POOLS[user_key]


async def shutdown_all():
    """Stop every pool that has been started. Called from daemon lifespan."""
    for pool in list(_SESSION_POOLS.values()):
        try:
            await pool.stop()
        except Exception as e:
            log.error("Failed to stop pool '%s': %s", pool.user_key, e)
    _SESSION_POOLS.clear()


def get_all_pools() -> dict[str, SessionPool]:
    """Return all active pools — used by the watchdog."""
    return dict(_SESSION_POOLS)
