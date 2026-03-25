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
            socket_timeout=2,
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
