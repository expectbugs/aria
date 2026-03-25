"""ARIA task dispatcher — reads from Redis Stream, routes to workers.

Runs as a background asyncio task in the daemon lifespan. Reads tasks
from the aria:task_queue Redis Stream and routes them by mode:
- shell → Amnesia pool shell handler (Step 5)
- agentic → Action ARIA or Amnesia pool (Steps 5-6)

Until workers are implemented, tasks are logged and left in queued state.
"""

import asyncio
import logging

import config
import redis_client
from amnesia_pool import get_pool
from action_aria import get_action_aria

# Keywords that suggest a task needs Action ARIA (persistent, complex)
_ACTION_ARIA_KEYWORDS = [
    "generate", "image", "upscale", "4k", "flux", "imgen",
    "create file", "write file", "modify file", "edit file",
    "multi-step", "complex", "long task",
    "push_image", "push_audio",
]

log = logging.getLogger("aria.dispatcher")

_running = False
_task: asyncio.Task | None = None


async def _dispatch_loop():
    """Main dispatcher loop — reads from Redis Stream and routes tasks."""
    global _running
    prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
    stream_key = f"{prefix}task_queue"
    last_id = "0"  # start from beginning on first run

    log.info("Task dispatcher started")

    while _running:
        client = redis_client.get_client()
        if client is None:
            await asyncio.sleep(5)
            continue

        try:
            # Run blocking Redis read in thread pool to avoid freezing the event loop
            entries = await asyncio.to_thread(
                client.xread, {stream_key: last_id}, 1, 5000
            )
            if not entries:
                continue

            for stream_name, messages in entries:
                for msg_id, data in messages:
                    last_id = msg_id
                    task_id = data.get("task_id", "")
                    mode = data.get("mode", "shell")

                    if not task_id:
                        continue

                    log.info("Dispatching task %s (mode=%s)", task_id, mode)

                    # Update state to running
                    redis_client.update_task_state(task_id, status="running")

                    # Route by mode
                    try:
                        if mode == "shell":
                            await _handle_shell(task_id)
                        elif mode == "agentic":
                            await _handle_agentic(task_id)
                        else:
                            log.warning("Unknown task mode: %s", mode)
                            redis_client.complete_task(task_id, error=f"Unknown mode: {mode}")
                    except Exception as e:
                        log.error("Task %s failed: %s", task_id, e)
                        redis_client.complete_task(task_id, error=str(e))

                    # Acknowledge by trimming old entries (keep stream small)
                    try:
                        client.xdel(stream_key, msg_id)
                    except Exception:
                        pass

        except Exception as e:
            if _running:  # only log if we didn't intentionally stop
                log.error("Dispatcher error: %s", e)
            await asyncio.sleep(1)

    log.info("Task dispatcher stopped")


async def _handle_shell(task_id: str):
    """Handle a shell mode task. Placeholder until Amnesia pool (Step 5)."""
    prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
    client = redis_client.get_client()
    if not client:
        redis_client.complete_task(task_id, error="Redis unavailable")
        return

    task_data = client.hgetall(f"{prefix}task:{task_id}")
    command = task_data.get("command", "")

    if not command:
        redis_client.complete_task(task_id, error="No command specified")
        return

    # Execute shell command directly (no Claude Code needed)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout = getattr(config, "AMNESIA_SHELL_TIMEOUT", 60)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        result_parts = []
        if stdout:
            result_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            result_parts.append(f"STDERR: {stderr.decode('utf-8', errors='replace')}")
        if proc.returncode != 0:
            result_parts.append(f"Exit code: {proc.returncode}")

        result = "\n".join(result_parts) if result_parts else "(no output)"
        redis_client.complete_task(task_id, result=result)
        log.info("Shell task %s completed (exit=%d)", task_id, proc.returncode)

    except asyncio.TimeoutError:
        redis_client.complete_task(task_id, error=f"Command timed out after {timeout}s")
    except Exception as e:
        redis_client.complete_task(task_id, error=str(e))


def _needs_action_aria(brief: str) -> bool:
    """Check if a task brief suggests it needs Action ARIA (complex/persistent)."""
    brief_lower = brief.lower()
    return any(kw in brief_lower for kw in _ACTION_ARIA_KEYWORDS)


async def _handle_agentic(task_id: str):
    """Handle an agentic mode task — route to Action ARIA or Amnesia pool."""
    prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
    client = redis_client.get_client()
    if not client:
        redis_client.complete_task(task_id, error="Redis unavailable")
        return

    task_data = client.hgetall(f"{prefix}task:{task_id}")
    brief = task_data.get("task_brief", "")
    context = task_data.get("context", "")

    if not brief:
        redis_client.complete_task(task_id, error="No task brief specified")
        return

    # Route: complex tasks → Action ARIA, quick tasks → Amnesia pool
    if _needs_action_aria(brief):
        log.info("Routing task %s to Action ARIA (complex)", task_id)
        action = get_action_aria()
        result = await action.execute(task_id, brief, context)
    else:
        log.info("Routing task %s to Amnesia pool (quick)", task_id)
        pool = get_pool()
        result = await pool.run_agentic(task_id, brief, context)

    if result.get("error"):
        redis_client.complete_task(task_id, error=result["error"])
    else:
        redis_client.complete_task(task_id, result=result.get("result", ""))


def start_dispatcher():
    """Start the dispatcher as a background asyncio task."""
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_dispatch_loop())
    log.info("Task dispatcher starting")


def stop_dispatcher():
    """Stop the dispatcher."""
    global _running, _task
    _running = False
    if _task and not _task.done():
        _task.cancel()
    _task = None
    log.info("Task dispatcher stopping")
