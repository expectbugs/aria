"""Action ARIA — persistent Claude Code worker for complex multi-step tasks.

Handles sustained tasks that need Claude Code's full agentic capabilities:
image generation, multi-step file operations, complex shell workflows.
One task at a time. Progress reported to Redis. Fresh session per task.

Adapted from claude_session.py — same stream-json protocol, different purpose.
"""

import asyncio
import json
import logging
import os

import config
import redis_client

log = logging.getLogger("aria.action")


class ActionAria:
    """Persistent Claude Code session for complex background tasks.

    One instance per user ("adam" or "becky"). Adam's default uses the
    standard action prompt; Becky's uses build_becky_action_prompt which
    defaults delivery to her phone and restricts push_image to explicit
    cross-user requests.
    """

    def __init__(self, user_key: str = "adam"):
        self.user_key = user_key
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _build_prompt(self, task_id: str) -> str:
        """Build and return the system prompt for this action worker."""
        # Lazy import to avoid circular imports at module load time.
        from system_prompt import build_action_prompt, build_becky_action_prompt
        if self.user_key == "becky":
            return build_becky_action_prompt().replace("TASK_ID", task_id)
        return build_action_prompt().replace("TASK_ID", task_id)

    async def _spawn(self, task_id: str):
        """Spawn a fresh Claude Code process with the action prompt.

        Injects the task_id into the system prompt so the worker can
        report progress to the correct Redis hash.
        """
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["CLAUDE_CODE_EFFORT_LEVEL"] = "max"
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

        prompt = self._build_prompt(task_id)

        self._proc = await asyncio.create_subprocess_exec(
            config.CLAUDE_CLI,
            "--print",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--model", "opus",  # full capability for complex tasks
            "--dangerously-skip-permissions",
            "--system-prompt", prompt,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=16 * 1024 * 1024,  # 16MB readline buffer (images can be 4MB+ base64)
        )
        log.info("Action ARIA[%s] spawned for task %s (pid=%s)",
                 self.user_key, task_id, self._proc.pid)

    async def _kill(self):
        """Kill the current process."""
        if self._is_alive():
            try:
                self._proc.kill()
                await self._proc.wait()
            except Exception:
                pass
        self._proc = None

    async def execute(self, task_id: str, brief: str, context: str = "") -> dict:
        """Execute a complex task. Returns {"result": ..., "error": ...}.

        Spawns a fresh session, sends the task brief, captures the result.
        Progress is reported by the worker itself via Redis (instructions
        are in the action system prompt).
        """
        async with self._lock:
            # Kill any existing session (one task at a time)
            await self._kill()
            await self._spawn(task_id)

            # Update Redis state
            redis_client.update_task_state(task_id, status="running", progress=0)

            # Build the task message
            prompt = f"Task ID: {task_id}\n\nTask: {brief}"
            if context:
                prompt += f"\n\nContext: {context}"

            # Send user message
            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": prompt},
            }) + "\n"
            self._proc.stdin.write(msg.encode())
            await self._proc.stdin.drain()

            # Read until result
            timeout = getattr(config, "CLAUDE_TIMEOUT", 600)
            try:
                while True:
                    line = await asyncio.wait_for(
                        self._proc.stdout.readline(),
                        timeout=timeout,
                    )
                    if not line:
                        return {"result": None, "error": "Action ARIA process exited unexpectedly"}

                    try:
                        data = json.loads(line.decode().strip())
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type")

                    if msg_type == "result":
                        await self._kill()
                        if data.get("is_error"):
                            return {"result": None, "error": data.get("result", "unknown error")}
                        return {"result": data.get("result", ""), "error": None}

                    elif msg_type == "control_request":
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

            except asyncio.TimeoutError:
                log.error("Action ARIA task %s timed out after %ds", task_id, timeout)
                await self._kill()
                return {"result": None, "error": f"Task timed out after {timeout}s"}
            except Exception as e:
                log.error("Action ARIA task %s failed: %s", task_id, e)
                await self._kill()
                return {"result": None, "error": str(e)}


# Per-user registry — one ActionAria instance per user_key
_ACTION_ARIAS: dict[str, ActionAria] = {}


def get_action_aria(user_key: str = "adam") -> ActionAria:
    """Get or create the Action ARIA instance for a specific user.

    Becky and Adam each have their own instance so task isolation is
    guaranteed at the subprocess level (even though instances run one
    task at a time, spawning fresh per task).
    """
    if user_key not in _ACTION_ARIAS:
        _ACTION_ARIAS[user_key] = ActionAria(user_key=user_key)
    return _ACTION_ARIAS[user_key]
