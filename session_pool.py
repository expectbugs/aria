"""ARIA CLI session pool — deep + fast Opus sessions for primary requests.

Replaces the Anthropic API as the primary brain. Two persistent Claude CLI
sessions with different effort levels, automatic recycling, crash recovery,
and cross-session history continuity via conversation history injection.
"""

import asyncio
import json
import logging
import os
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


# Tool use reminder — injected at the END of every query so it sits near
# response generation where attention is strongest (combats lost-in-the-middle).
_TOOL_USE_REMINDER = (
    "[INSTRUCTION] Before responding, consider: do you have VERIFIED information "
    "for your answer? Your tools are: query.py (health, nutrition, vehicle, legal, "
    "calendar, conversations, email), Bash (shell commands, web fetch). "
    "If your response contains facts, dates, numbers, or state claims, you MUST "
    "verify them with a tool first. Do NOT respond from memory alone."
)

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

    def __init__(self, name: str, effort: str, max_requests: int):
        self.name = name              # "deep" or "fast"
        self._effort = effort         # "max" or "auto"
        self._max_requests = max_requests
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._request_count = 0
        self._history_injected = False
        self._spawned_at: datetime | None = None
        self._context_bytes = 0       # estimated context window usage
        self._max_context_bytes = getattr(
            config, "SESSION_MAX_CONTEXT_BYTES", 500_000
        )  # ~125K tokens ≈ 62% of 200K window

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

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
            "--system-prompt", build_primary_prompt(),
            "--settings", '{"claudeMdExcludes": ["/home/user/aria/CLAUDE.md"]}',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=16 * 1024 * 1024,  # 16MB readline buffer
        )
        self._request_count = 0
        self._history_injected = False
        self._context_bytes = 0
        self._spawned_at = datetime.now()
        log.info("Session '%s' spawned (pid=%s, effort=%s)",
                 self.name, self._proc.pid, self._effort)

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

    async def query(self, user_text: str, extra_context: str = "",
                    file_blocks: list[dict] | None = None) -> SessionResponse:
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
                parts.append(f"[CONTEXT]\n{extra_context}\n[/CONTEXT]")
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

            # Read stdout lines until we get a result
            tool_calls_seen: list[str] = []
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
                        result = data.get("result", "")
                        # Track output size + check context pressure
                        self._context_bytes += len(result)
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
                            text=result, tool_calls=tool_calls_seen)

                    elif msg_type == "assistant":
                        # Track tool_use content blocks in assistant messages
                        msg_data = data.get("message", {})
                        if isinstance(msg_data, dict):
                            content = msg_data.get("content", [])
                            if isinstance(content, list):
                                for block in content:
                                    if (isinstance(block, dict)
                                            and block.get("type") == "tool_use"):
                                        tool_calls_seen.append(
                                            block.get("name", "unknown"))

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
        """
        recent = get_recent_turns(10)
        verbatim = _format_history_for_injection(recent)

        # Try to summarize older conversation via Haiku
        try:
            all_turns = get_recent_turns(30)
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
        }


class SessionPool:
    """Managed pool of deep + fast Claude CLI sessions.

    Deep session: Opus with max effort — default for all user requests.
    Fast session: Opus with auto effort — for _is_simple_query() matches.
    """

    def __init__(self):
        recycle = getattr(config, "SESSION_RECYCLE_AFTER", 150)
        deep_effort = getattr(config, "SESSION_DEEP_EFFORT", "max")
        fast_effort = getattr(config, "SESSION_FAST_EFFORT", "auto")

        self._deep = _Session("deep", deep_effort, recycle)
        self._fast = _Session("fast", fast_effort, recycle)

    async def start(self):
        """Pre-warm both sessions. Called from daemon lifespan."""
        await self._deep._spawn()
        await self._fast._spawn()
        log.info("Session pool started (deep + fast)")

    async def stop(self):
        """Kill both sessions. Called from daemon lifespan shutdown."""
        await self._deep._kill()
        await self._fast._kill()
        log.info("Session pool stopped")

    async def query_deep(self, text: str, context: str = "",
                         file_blocks: list[dict] | None = None) -> SessionResponse:
        """Query the deep (max effort) session."""
        return await self._deep.query(text, context, file_blocks)

    async def query_fast(self, text: str, context: str = "",
                         file_blocks: list[dict] | None = None) -> SessionResponse:
        """Query the fast (auto effort) session."""
        return await self._fast.query(text, context, file_blocks)

    def get_status(self) -> dict:
        """Return status of both sessions for health checks."""
        return {
            "deep": self._deep.get_status(),
            "fast": self._fast.get_status(),
        }


# --- Singleton ---

_pool: SessionPool | None = None


def get_session_pool() -> SessionPool:
    """Get or create the global session pool."""
    global _pool
    if _pool is None:
        _pool = SessionPool()
    return _pool
