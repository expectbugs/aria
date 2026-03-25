#!/usr/bin/env python3
"""ARIA system health monitor — pushes SVG alerts to phone on failure.

Runs every 5 minutes via cron on both beardos and slappy.
Checks: daemon, PostgreSQL, Redis, backup freshness, peer host.
Pushes formatted SVG alert image to phone on failure.
Falls back to SMS if image push fails.

Cron:
    */5 * * * * /home/user/aria/venv/bin/python /home/user/aria/monitor.py >> /home/user/aria/logs/monitor.log 2>&1
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("monitor")

STATE_FILE = config.LOGS_DIR / "monitor_state.json"
COOLDOWN_SECONDS = 1800  # 30 minutes between repeat alerts for same failure
PEER_HOST = "100.70.66.104" if config.HOST_NAME == "beardos" else "100.107.139.121"
PEER_NAME = "slappy" if config.HOST_NAME == "beardos" else "beardos"


# --- Health Checks ---

def check_daemon() -> str | None:
    """Check if ARIA daemon is responsive. Returns error string or None."""
    try:
        import httpx
        resp = httpx.get(f"http://127.0.0.1:{config.PORT}/health", timeout=5)
        if resp.status_code != 200:
            return f"Daemon returned {resp.status_code}"
        data = resp.json()
        if data.get("status") == "degraded":
            bad = [k for k, v in data.get("checks", {}).items() if v not in ("ok", "loaded", "not loaded")]
            return f"Daemon degraded: {', '.join(bad)}"
        return None
    except Exception as e:
        return f"Daemon unreachable: {e}"


def check_postgres() -> str | None:
    """Check if PostgreSQL is reachable."""
    try:
        import db
        with db.get_conn() as conn:
            conn.execute("SELECT 1")
        return None
    except Exception as e:
        return f"PostgreSQL error: {e}"


def check_redis() -> str | None:
    """Check if Redis is reachable."""
    try:
        import redis_client
        client = redis_client.get_client()
        if client is None:
            return "Redis unavailable"
        client.ping()
        return None
    except Exception as e:
        return f"Redis error: {e}"


def check_backup_freshness() -> str | None:
    """Check if the pg_dump backup is recent (beardos only)."""
    if config.HOST_NAME != "beardos":
        return None
    backup = config.DATA_DIR / "aria_backup.sql"
    if not backup.exists():
        return "No backup file found"
    age_min = (time.time() - backup.stat().st_mtime) / 60
    if age_min > 15:
        return f"Backup is {age_min:.0f} min old (stale)"
    return None


def check_restore_freshness() -> str | None:
    """Check if the database has recent data (slappy only)."""
    if config.HOST_NAME != "slappy":
        return None
    try:
        import db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) as latest FROM request_log"
            ).fetchone()
            if row and row["latest"]:
                from datetime import timezone
                latest = row["latest"]
                if latest.tzinfo:
                    latest = latest.replace(tzinfo=None)
                age_min = (datetime.now() - latest).total_seconds() / 60
                # Only alert if beardos has been active (request_log growing)
                # but slappy hasn't seen updates in 15+ minutes
                if age_min > 60:
                    return f"Latest request_log entry is {age_min:.0f} min old — sync may be broken"
            else:
                # Empty database — check if backup file exists
                backup = config.DATA_DIR / "aria_backup.sql"
                if backup.exists() and backup.stat().st_size > 0:
                    return "Database empty but backup file exists — restore may not be running"
    except Exception as e:
        return f"Restore check failed: {e}"
    return None


def check_peer() -> str | None:
    """Check if the peer host is reachable."""
    try:
        import httpx
        resp = httpx.get(f"http://{PEER_HOST}:8450/health", timeout=5)
        if resp.status_code != 200:
            return f"{PEER_NAME} returned {resp.status_code}"
        return None
    except Exception:
        return f"{PEER_NAME} unreachable"


# --- Alert Generation ---

def generate_svg(host: str, failures: list[str]) -> str:
    """Generate an SVG alert image formatted for phone display (540x1212)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build failure lines
    failure_lines = ""
    y = 280
    for f in failures:
        # Escape XML special chars
        f_escaped = f.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Word wrap long lines
        words = f_escaped.split()
        line = ""
        for word in words:
            if len(line) + len(word) > 45:
                failure_lines += f'  <text x="270" y="{y}" text-anchor="middle" font-size="24" fill="#FFFFFF" font-family="monospace">{line}</text>\n'
                y += 35
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            failure_lines += f'  <text x="270" y="{y}" text-anchor="middle" font-size="24" fill="#FFFFFF" font-family="monospace">{line}</text>\n'
            y += 50

    height = max(500, y + 100)

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="540" height="{height}" viewBox="0 0 540 {height}">
  <rect width="540" height="{height}" fill="#1a1a2e"/>
  <rect x="20" y="20" width="500" height="100" rx="15" fill="#e63946"/>
  <text x="270" y="75" text-anchor="middle" font-size="36" font-weight="bold" fill="#FFFFFF" font-family="sans-serif">ARIA SYSTEM ALERT</text>
  <text x="270" y="160" text-anchor="middle" font-size="28" fill="#a8dadc" font-family="sans-serif">{host.upper()}</text>
  <text x="270" y="200" text-anchor="middle" font-size="20" fill="#888888" font-family="monospace">{timestamp}</text>
  <line x1="40" y1="230" x2="500" y2="230" stroke="#444" stroke-width="1"/>
{failure_lines}
</svg>"""
    return svg


def push_alert(host: str, failures: list[str]):
    """Push SVG alert to phone, fall back to SMS."""
    # Generate and save SVG
    svg = generate_svg(host, failures)
    svg_path = config.DATA_DIR / "alert.svg"
    svg_path.write_text(svg)

    # Try image push first
    try:
        from push_image import push_image
        if push_image(str(svg_path), caption=f"ARIA Alert: {host}"):
            log.info("Alert pushed via image")
            return
    except Exception as e:
        log.warning("Image push failed: %s", e)

    # Fall back to SMS
    try:
        import sms
        text = f"ARIA ALERT ({host}):\n" + "\n".join(f"- {f}" for f in failures)
        sms.send_to_owner(text)
        log.info("Alert sent via SMS")
    except Exception as e:
        log.warning("SMS alert also failed: %s", e)


# --- Cooldown State ---

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def should_alert(state: dict, failure_key: str) -> bool:
    """Check if we should alert for this failure (respects cooldown)."""
    last = state.get(failure_key, 0)
    return (time.time() - last) > COOLDOWN_SECONDS


# --- Main ---

def main():
    checks = [
        ("daemon", check_daemon),
        ("postgres", check_postgres),
        ("redis", check_redis),
        ("backup", check_backup_freshness),
        ("restore", check_restore_freshness),
        ("peer", check_peer),
    ]

    failures = []
    for name, check_fn in checks:
        try:
            result = check_fn()
            if result:
                failures.append(result)
                log.warning("FAIL %s: %s", name, result)
            else:
                log.info("OK %s", name)
        except Exception as e:
            failures.append(f"{name} check crashed: {e}")
            log.error("CHECK CRASH %s: %s", name, e)

    if not failures:
        log.info("All checks passed on %s", config.HOST_NAME)
        return

    # Check cooldown
    state = load_state()
    failure_key = "|".join(sorted(failures))
    if should_alert(state, failure_key):
        push_alert(config.HOST_NAME, failures)
        state[failure_key] = time.time()
        # Clean old state entries
        cutoff = time.time() - 86400
        state = {k: v for k, v in state.items() if v > cutoff}
        save_state(state)
    else:
        log.info("Alert suppressed (cooldown) for: %s", failure_key[:100])


if __name__ == "__main__":
    main()
# Auto-deploy test marker
