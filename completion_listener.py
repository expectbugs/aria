"""ARIA task completion listener — delivers results to the user.

Subscribes to Redis Pub/Sub for task completion events. When a task
completes with notify=true, composes a natural response via ARIA Primary
and delivers it (TTS+push for voice, SMS for text).

Runs as a background asyncio task in the daemon lifespan.
"""

import asyncio
import json
import logging

import config
import redis_client
import sms

# Lazy imports to avoid loading TTS/API at module import time (causes test hangs)
ask_haiku = None
process_actions = None


def _ensure_imports():
    """Lazy-load heavy modules on first use."""
    global ask_haiku, process_actions
    if ask_haiku is None:
        from aria_api import ask_haiku as _ask_haiku
        from actions import process_actions as _pa_fn
        ask_haiku = _ask_haiku
        process_actions = _pa_fn

log = logging.getLogger("aria.completion")

_running = False
_task: asyncio.Task | None = None


async def _on_completion(task_id: str, status: str, result_text: str):
    """Handle a completed task — compose and deliver response to user."""
    prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
    client = redis_client.get_client()
    if not client:
        return

    # Read full task data
    task_data = client.hgetall(f"{prefix}task:{task_id}")
    notify = task_data.get("notify", "1") == "1"
    description = task_data.get("description", "background task")
    user_key = task_data.get("user_key", "adam")

    if not notify:
        log.info("Task %s completed silently (notify=false)", task_id)
        return

    log.info("Task %s completed with notify — composing response", task_id)

    _ensure_imports()

    # Compose a natural response via ARIA Primary
    try:

        if status == "completed":
            prompt = (
                f"A background task has completed successfully.\n"
                f"Task: {description}\n"
                f"Result: {result_text[:2000]}\n\n"
                f"Compose a brief, natural response to let the user know. "
                f"If the result includes a file path, mention it. "
                f"Keep it warm and conversational."
            )
        else:
            prompt = (
                f"A background task failed.\n"
                f"Task: {description}\n"
                f"Error: {result_text[:1000]}\n\n"
                f"Let the user know what happened. Be clear about the failure "
                f"but don't be overly apologetic."
            )

        response = await ask_haiku(prompt)

        # Process any ACTION blocks (execute + strip from response text)
        result = await process_actions(response, user_key=user_key)
        response = result.to_response()

        # Delivery routing via shared execute_delivery (push voice — no task to poll)
        import delivery_engine as _de
        task_channel = task_data.get("channel", "voice")
        if task_channel == "cli":
            # CLI channel: deliver as image to phone (user is at PC, phone nearby)
            dr = await _de.execute_delivery(
                response, content_type="task_completion",
                source="monitor_finding", push_voice=False,
                user_key=user_key)
        else:
            dr = await _de.execute_delivery(
                response, content_type="task_completion",
                source=task_channel, push_voice=True,
                user_key=user_key)
        log.info("Task %s[%s] result delivered via %s",
                 task_id, user_key, dr["method"])

    except Exception as e:
        log.error("Failed to compose response for task %s: %s", task_id, e)


async def _listen_loop():
    """Main listener loop — subscribe to Redis Pub/Sub for completions."""
    global _running
    prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
    channel = f"{prefix}task_complete"

    log.info("Completion listener started")

    while _running:
        client = redis_client.get_client()
        if client is None:
            await asyncio.sleep(5)
            continue

        pubsub = None
        try:
            pubsub = client.pubsub()
            pubsub.subscribe(channel)

            while _running:
                # Non-blocking check + async sleep to avoid blocking the event loop
                message = pubsub.get_message(timeout=0.0)
                if message is None:
                    await asyncio.sleep(1)
                    continue
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        task_id = data.get("task_id", "")
                        status = data.get("status", "")
                        result_text = data.get("result", "")

                        if task_id:
                            # Handle completion in a separate task to not block the listener
                            asyncio.create_task(
                                _on_completion(task_id, status, result_text)
                            )
                    except json.JSONDecodeError:
                        log.warning("Invalid completion message: %s", message["data"])

        except Exception as e:
            if _running:
                log.error("Completion listener error: %s", e)
            await asyncio.sleep(2)
        finally:
            if pubsub:
                try:
                    pubsub.unsubscribe()
                    pubsub.close()
                except Exception:
                    pass

    log.info("Completion listener stopped")


def start_listener():
    """Start the completion listener as a background asyncio task."""
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_listen_loop())
    log.info("Completion listener starting")


def stop_listener():
    """Stop the completion listener."""
    global _running, _task
    _running = False
    if _task and not _task.done():
        _task.cancel()
    _task = None
    log.info("Completion listener stopping")
