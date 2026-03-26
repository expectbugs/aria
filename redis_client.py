"""Redis client management for ARIA.

Provides a lazy-initialized Redis client singleton for swarm task status
and inter-process coordination. Graceful failure: if Redis is unavailable,
returns None (never crashes ARIA).

Modeled on db.py — same singleton + atexit pattern.
"""

import atexit
import logging

log = logging.getLogger("aria.redis")

try:
    import redis as _redis_lib
except ImportError:
    _redis_lib = None

import config

_client = None
_warned = False


def get_client():
    """Get or create the Redis client. Returns None if Redis is unavailable."""
    global _client, _warned
    if _client is not None:
        return _client
    if _redis_lib is None:
        if not _warned:
            log.warning("redis package not installed — task status unavailable")
            _warned = True
        return None
    try:
        url = getattr(config, "REDIS_URL", "redis://127.0.0.1:6379/0")
        _client = _redis_lib.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=15,  # must exceed xread block time in dispatcher
        )
        _client.ping()
        _warned = False
        log.info("Redis client connected: %s", url)
        return _client
    except Exception as e:
        if not _warned:
            log.warning("Redis unavailable: %s — task status will not be shown", e)
            _warned = True
        _client = None
        return None


def close():
    """Close the Redis client."""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
        _client = None
        log.info("Redis client closed")


atexit.register(close)


def get_active_tasks() -> list[dict]:
    """Read all active swarm tasks from Redis.

    Returns a list of task dicts with keys: task_id, description,
    progress, status, message, eta_seconds.
    Returns empty list if Redis is unavailable or no active tasks.
    """
    client = get_client()
    if client is None:
        return []
    try:
        prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
        task_ids = client.smembers(f"{prefix}active_tasks")
        if not task_ids:
            return []
        tasks = []
        for task_id in task_ids:
            data = client.hgetall(f"{prefix}task:{task_id}")
            if not data:
                continue
            status = data.get("status", "unknown")
            if status not in ("queued", "running"):
                # Stale entry — clean up
                client.srem(f"{prefix}active_tasks", task_id)
                continue
            tasks.append({
                "task_id": task_id,
                "description": data.get("description", "unknown task"),
                "progress": int(data.get("progress", 0)),
                "status": status,
                "message": data.get("message", ""),
                "eta_seconds": int(data.get("eta_seconds", 0)) if data.get("eta_seconds") else None,
            })
        return tasks
    except Exception as e:
        log.warning("Failed to read active tasks from Redis: %s", e)
        return []


def push_task(task: dict) -> str | None:
    """Push a task to the Redis Stream queue. Returns task_id or None on failure.

    The task dict should contain: task_id, mode, command/task, context, notify.
    Also creates the task state hash and adds to active_tasks set.
    """
    client = get_client()
    if client is None:
        return None
    try:
        prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
        task_id = task["task_id"]

        # Create task state hash
        client.hset(f"{prefix}task:{task_id}", mapping={
            "status": "queued",
            "progress": "0",
            "description": task.get("task") or task.get("command", "unknown"),
            "message": "",
            "mode": task.get("mode", "shell"),
            "command": task.get("command", ""),
            "task_brief": task.get("task", ""),
            "context": task.get("context", ""),
            "notify": "1" if task.get("notify", True) else "0",
            "channel": task.get("channel", "voice"),
            "created_at": __import__("datetime").datetime.now().isoformat(),
        })

        # Add to active tasks set
        client.sadd(f"{prefix}active_tasks", task_id)

        # Push to stream queue
        client.xadd(f"{prefix}task_queue", {"task_id": task_id, "mode": task.get("mode", "shell")})

        log.info("Task queued: %s (mode=%s)", task_id, task.get("mode"))
        return task_id
    except Exception as e:
        log.error("Failed to push task to Redis: %s", e)
        return None


def update_task_state(task_id: str, **fields):
    """Update fields in a task's Redis hash."""
    client = get_client()
    if client is None:
        return
    try:
        prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
        # Convert all values to strings for Redis
        str_fields = {k: str(v) for k, v in fields.items()}
        client.hset(f"{prefix}task:{task_id}", mapping=str_fields)
    except Exception as e:
        log.error("Failed to update task state %s: %s", task_id, e)


def complete_task(task_id: str, result: str | None = None, error: str | None = None):
    """Mark a task as completed, publish notification, remove from active set."""
    client = get_client()
    if client is None:
        return
    try:
        prefix = getattr(config, "REDIS_KEY_PREFIX", "aria:")
        now = __import__("datetime").datetime.now().isoformat()

        status = "error" if error else "completed"
        updates = {
            "status": status,
            "progress": "100",
            "completed_at": now,
        }
        if result:
            updates["result"] = result
        if error:
            updates["error"] = error

        client.hset(f"{prefix}task:{task_id}", mapping=updates)
        client.srem(f"{prefix}active_tasks", task_id)

        # Publish completion notification
        import json
        client.publish(f"{prefix}task_complete", json.dumps({
            "task_id": task_id,
            "status": status,
            "result": result or error or "",
        }))

        log.info("Task %s: %s", status, task_id)
    except Exception as e:
        log.error("Failed to complete task %s: %s", task_id, e)


def format_task_status(tasks: list[dict]) -> str:
    """Format active tasks into a compact context string.

    Returns empty string if no tasks.
    """
    if not tasks:
        return ""
    lines = []
    for t in tasks:
        desc = t["description"]
        pct = t["progress"]
        msg = t.get("message", "")
        eta = t.get("eta_seconds")

        parts = [desc]
        if pct > 0:
            parts.append(f"{pct}%")
        if msg:
            parts.append(msg)
        if eta and eta > 0:
            if eta >= 60:
                parts.append(f"~{eta // 60}m remaining")
            else:
                parts.append(f"~{eta}s remaining")

        status_str = " — ".join(parts)
        lines.append(f"Background task [{t['status']}]: {status_str}")

    return "\n".join(lines)
