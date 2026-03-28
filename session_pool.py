"""ARIA CLI session pool — deep + fast Opus sessions for primary requests.

Replaces the Anthropic API as the primary brain. Two persistent Claude CLI
sessions with different effort levels, automatic recycling, crash recovery,
and cross-session history continuity via conversation history injection.
"""

import asyncio
import json
import logging
import os
from datetime import datetime

import config
from conversation_history import get_recent_turns
from system_prompt import build_primary_prompt

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
                    file_blocks: list[dict] | None = None) -> str:
        """Send a prompt to the persistent Claude process and return the response."""
        async with self._lock:
            await self._ensure_alive()

            parts = []

            # History injection on first query after spawn/recycle
            if not self._history_injected:
                turns = get_recent_turns()
                history_block = _format_history_for_injection(turns)
                if history_block:
                    parts.append(history_block)
                self._history_injected = True

            if extra_context:
                parts.append(f"[CONTEXT]\n{extra_context}\n[/CONTEXT]")
            parts.append(f"User says: {user_text}")
            prompt = "\n".join(parts)

            # Build message content — text-only or multimodal
            if file_blocks:
                content = [{"type": "text", "text": prompt}] + file_blocks
            else:
                content = prompt

            # Send user message as NDJSON
            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": content},
            }) + "\n"
            self._proc.stdin.write(msg.encode())
            await self._proc.stdin.drain()
            self._request_count += 1

            # Read stdout lines until we get a result
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
                        return data.get("result", "")

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

                    # Ignore other types (assistant, system, stream_event, etc.)

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
                         file_blocks: list[dict] | None = None) -> str:
        """Query the deep (max effort) session."""
        return await self._deep.query(text, context, file_blocks)

    async def query_fast(self, text: str, context: str = "",
                         file_blocks: list[dict] | None = None) -> str:
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
