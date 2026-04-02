"""Amnesia ARIA pool — warm stateless Claude Code instances for one-shot tasks.

Each instance handles one agentic task, then is killed and replaced.
No memory, no context accumulation. Used for quick tasks that need
Claude Code's agentic capabilities but not persistent session state.

Shell commands are handled directly by task_dispatcher.py (no Claude needed).
This module handles only agentic mode tasks.
"""

import asyncio
import json
import logging
import os

import config
import redis_client
from system_prompt import build_amnesia_prompt

log = logging.getLogger("aria.amnesia")


class AmnesiaPool:
    """Manages a pool of warm Claude Code instances for one-shot agentic tasks."""

    def __init__(self, size: int | None = None):
        self._size = size or getattr(config, "AMNESIA_POOL_SIZE", 3)
        self._instances: list[asyncio.subprocess.Process | None] = [None] * self._size
        self._locks: list[asyncio.Lock] = [asyncio.Lock() for _ in range(self._size)]
        self._states: list[str] = ["empty"] * self._size  # empty, starting, idle, busy
        self._started = False

    async def start(self):
        """Pre-warm all instances."""
        if self._started:
            return
        self._started = True
        for i in range(self._size):
            asyncio.create_task(self._spawn(i))
        log.info("Amnesia pool starting: %d instances", self._size)

    async def stop(self):
        """Kill all instances."""
        self._started = False
        for i in range(self._size):
            await self._kill(i)
        log.info("Amnesia pool stopped")

    async def _spawn(self, index: int):
        """Spawn a fresh Claude Code instance at the given index."""
        self._states[index] = "starting"
        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

            proc = await asyncio.create_subprocess_exec(
                config.CLAUDE_CLI,
                "--print",
                "--output-format", "stream-json",
                "--input-format", "stream-json",
                "--verbose",
                "--model", "sonnet",  # cheaper model for quick tasks
                "--dangerously-skip-permissions",
                "--system-prompt", build_amnesia_prompt(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                limit=16 * 1024 * 1024,  # 16MB readline buffer (images can be 4MB+ base64)
            )
            self._instances[index] = proc
            self._states[index] = "idle"
            log.info("Amnesia instance %d spawned (pid=%s)", index, proc.pid)
        except Exception as e:
            log.error("Failed to spawn amnesia instance %d: %s", index, e)
            self._instances[index] = None
            self._states[index] = "empty"

    async def _kill(self, index: int):
        """Kill the instance at the given index."""
        proc = self._instances[index]
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        self._instances[index] = None
        self._states[index] = "empty"

    def _find_idle(self) -> int | None:
        """Find an idle instance index, or None if all busy."""
        for i, state in enumerate(self._states):
            if state == "idle":
                return i
        return None

    async def run_agentic(self, task_id: str, brief: str, context: str = "") -> dict:
        """Execute a one-shot agentic task using a pool instance.

        Finds an idle instance, sends the task brief, captures the result,
        then kills and replaces the instance.

        Returns: {"result": "...", "error": None} or {"result": None, "error": "..."}
        """
        # Find an idle instance
        index = self._find_idle()
        if index is None:
            return {"result": None, "error": "All amnesia instances busy — try again shortly"}

        async with self._locks[index]:
            proc = self._instances[index]
            if proc is None or proc.returncode is not None:
                # Instance died, try to spawn a fresh one
                await self._spawn(index)
                proc = self._instances[index]
                if proc is None:
                    return {"result": None, "error": "Failed to spawn amnesia instance"}

            self._states[index] = "busy"
            log.info("Amnesia instance %d handling task %s", index, task_id)

            try:
                # Build the prompt from the task brief
                prompt = f"Task: {brief}"
                if context:
                    prompt += f"\nContext: {context}"

                # Send user message as NDJSON
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": prompt},
                }) + "\n"
                proc.stdin.write(msg.encode())
                await proc.stdin.drain()

                # Read until result
                timeout = getattr(config, "AMNESIA_TASK_TIMEOUT", 120)
                while True:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=timeout,
                    )
                    if not line:
                        return {"result": None, "error": "Amnesia instance exited unexpectedly"}

                    try:
                        data = json.loads(line.decode().strip())
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type")

                    if msg_type == "result":
                        if data.get("is_error"):
                            return {"result": None, "error": data.get("result", "unknown error")}
                        return {"result": data.get("result", ""), "error": None}

                    elif msg_type == "control_request":
                        # Auto-approve permission requests
                        resp = json.dumps({
                            "type": "control_response",
                            "response": {
                                "subtype": "success",
                                "request_id": data.get("request_id"),
                                "response": {"behavior": "allow"},
                            }
                        }) + "\n"
                        proc.stdin.write(resp.encode())
                        await proc.stdin.drain()

            except asyncio.TimeoutError:
                log.error("Amnesia task %s timed out after %ds", task_id, timeout)
                return {"result": None, "error": f"Task timed out after {timeout}s"}
            except Exception as e:
                log.error("Amnesia task %s failed: %s", task_id, e)
                return {"result": None, "error": str(e)}
            finally:
                # Always kill and replace the instance after use
                await self._kill(index)
                if self._started:
                    asyncio.create_task(self._spawn(index))


# Global pool singleton
_pool: AmnesiaPool | None = None


def get_pool() -> AmnesiaPool:
    """Get or create the global amnesia pool."""
    global _pool
    if _pool is None:
        _pool = AmnesiaPool()
    return _pool
