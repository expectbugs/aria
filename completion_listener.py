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
ask_aria = None
_generate_tts = None
push_audio = None


def _ensure_imports():
    """Lazy-load heavy modules on first use."""
    global ask_aria, _generate_tts, push_audio
    if ask_aria is None:
        from aria_api import ask_aria as _ask
        from tts import _generate_tts as _tts
        import push_audio as _pa
        ask_aria = _ask
        _generate_tts = _tts
        push_audio = _pa

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

        response = await ask_aria(prompt)

        # Deliver via voice (TTS + push) by default
        try:
            audio = await _generate_tts(response)
            wav_path = config.DATA_DIR / "task_response.wav"
            wav_path.write_bytes(audio)
            if push_audio.push_audio(str(wav_path)):
                log.info("Task %s result delivered via voice", task_id)
            else:
                # Voice push failed, fall back to SMS
                sms.send_long_to_owner(response)
                log.info("Task %s result delivered via SMS (voice fallback)", task_id)
        except Exception as e:
            log.error("Voice delivery failed for task %s, trying SMS: %s", task_id, e)
            try:
                sms.send_long_to_owner(response)
            except Exception as se:
                log.error("SMS delivery also failed for task %s: %s", task_id, se)

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
                message = pubsub.get_message(timeout=2.0)
                if message and message["type"] == "message":
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
                elif message is None:
                    # Timeout — just loop and check _running
                    await asyncio.sleep(0.1)

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
