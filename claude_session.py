"""ARIA persistent Claude CLI session manager."""

import asyncio
import json
import logging
import os
from datetime import datetime

import config
from system_prompt import build_system_prompt

log = logging.getLogger("aria")


class ClaudeSession:
    """Manages a persistent Claude CLI subprocess using stream-json protocol.

    Instead of spawning a new process per request (1-2s startup overhead each time),
    this keeps a single process alive and sends messages via stdin/stdout.
    Conversation context is maintained across requests automatically.
    """

    MAX_REQUESTS = 200  # respawn after N requests to keep context manageable

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._request_count = 0

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _spawn(self):
        """Spawn a fresh Claude CLI process with stream-json I/O."""
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["CLAUDE_CODE_EFFORT_LEVEL"] = "max"
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

        self._proc = await asyncio.create_subprocess_exec(
            config.CLAUDE_CLI,
            "--print",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--model", "opus",
            "--dangerously-skip-permissions",
            "--system-prompt", build_system_prompt(),
            "--settings", '{"claudeMdExcludes": ["/home/user/aria/CLAUDE.md"]}',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=16 * 1024 * 1024,  # 16MB readline buffer (images can be 4MB+ base64)
        )
        self._request_count = 0
        log.info("Claude session spawned (pid=%s)", self._proc.pid)

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
        if not self._is_alive() or self._request_count >= self.MAX_REQUESTS:
            if self._is_alive():
                log.info("Recycling Claude session after %d requests", self._request_count)
                await self._kill()
            await self._spawn()

    async def query(self, user_text: str, extra_context: str = "",
                    file_blocks: list[dict] | None = None) -> str:
        """Send a prompt to the persistent Claude process and return the response.

        If file_blocks is provided, sends a multi-part message with text + file
        content (images, PDFs, text files) using Claude's content block format.
        """
        async with self._lock:
            await self._ensure_alive()

            # Build prompt — datetime is now in gather_always_context() (Tier 1)
            parts = []
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
                        raise RuntimeError("Claude process exited unexpectedly")

                    try:
                        data = json.loads(line.decode().strip())
                    except json.JSONDecodeError:
                        continue  # skip non-JSON lines

                    msg_type = data.get("type")

                    if msg_type == "result":
                        if data.get("is_error"):
                            raise RuntimeError(
                                f"Claude error: {data.get('result', 'unknown')}"
                            )
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
                log.error("Claude query timed out after %ss", config.CLAUDE_TIMEOUT)
                await self._kill()
                raise RuntimeError(
                    f"Claude timed out after {config.CLAUDE_TIMEOUT}s"
                )
            except Exception:
                log.exception("Claude session error, killing process")
                await self._kill()
                raise


# Global persistent session
_claude_session = ClaudeSession()


async def ask_claude(user_text: str, extra_context: str = "",
                     file_blocks: list[dict] | None = None) -> str:
    """Send a query to Claude via the persistent CLI session."""
    return await _claude_session.query(user_text, extra_context, file_blocks)
