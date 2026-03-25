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
            # Blocking read with 2s timeout (so we can check _running)
            entries = client.xread({stream_key: last_id}, count=1, block=2000)
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


async def _handle_agentic(task_id: str):
    """Handle an agentic mode task. Placeholder until Action ARIA (Step 6)."""
    # For now, mark as error with a helpful message
    redis_client.complete_task(
        task_id,
        error="Agentic task dispatch not yet implemented — coming in Steps 5-6"
    )


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
