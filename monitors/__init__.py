"""ARIA domain monitors — structured findings from pure Python + SQL checks.

Monitors run on a schedule via tick.py. Each monitor produces Finding objects
that are stored in the monitor_findings table with fingerprint deduplication.
Findings are injected into ARIA's context (Tier 1 for urgent/normal) and
delivered to the user via the finding delivery pipeline.

This system is SEPARATE from evaluate_nudges() — monitors check trends and
intervals, nudges check point-in-time conditions. Both coexist.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg.types.json

import config
import db

log = logging.getLogger("aria.monitors")

# Urgency levels ordered by severity (used for filtering)
_URGENCY_LEVELS = {"info": 0, "low": 1, "normal": 2, "urgent": 3}


@dataclass
class Finding:
    """A structured observation from a domain monitor."""
    domain: str      # health, fitness, vehicle, legal, system
    summary: str     # human-readable description
    urgency: str     # urgent, normal, low, info
    check_key: str   # dedup key (e.g., "choline_low", "hr_trend_up")
    data: dict = field(default_factory=dict)


# --- Notification categories ---
# A = briefing-only (daily informational, no independent nudge)
# B = repeat-low (can repeat, subsequent same-day fires wait to group with a C item)
# C = repeat-high (always nudges when triggered and cooled down)

NUDGE_CATEGORIES: dict[str, str] = {
    "health_pattern": "A",
    "fitbit_sleep": "A",
    "nutrition_calorie_surplus": "A",
    "diet_check": "A",
    "fitbit_sedentary": "B",
    "fitbit_activity_goal": "B",
    "nutrition_sugar_warn": "B",
    "nutrition_sodium_warn": "B",
    "battery_low": "B",
    "vehicle_maintenance": "B",
    "calendar_warning": "C",
    "reminder_due": "C",
    "legal_deadline": "C",
    "meal_reminder": "C",
    "fitbit_hr_anomaly": "C",
    "location_aware": "C",
}

FINDING_CATEGORIES: dict[str, str] = {
    # Category A — daily informational
    "choline_low": "A",
    "choline_trend": "A",
    "protein_low": "A",
    "fiber_low": "A",
    "omega3_missing": "A",
    "surplus_trend": "A",
    "sleep_deficit": "A",
    "steps_below_goal": "A",
    "irregular_sleep": "A",
    "hr_trend_up": "A",
    "hrv_declining": "A",
    # Email findings
    "email_urgent": "C",
    "email_important": "B",
    # Category B — repeat-low
    "gpu_temp_elevated": "B",
    # Category C — repeat-high
    "disk_critical": "C",
    "disk_warning": "C",
    "cron_stale": "C",
    "gpu_temp_high": "C",
}

# Dynamic keys matched by prefix
_FINDING_CATEGORY_PREFIXES: dict[str, str] = {
    "portage_stale_": "C",
    "deadline_": "C",
    "log_large_": "B",
}


def classify_category(key: str, source: str = "nudge") -> str:
    """Return category A/B/C for a nudge type or finding check_key.

    source: "nudge" or "finding"
    Falls back to "B" (group-wait) for unknown keys — safe default.
    """
    if source == "nudge":
        return NUDGE_CATEGORIES.get(key, "B")

    cat = FINDING_CATEGORIES.get(key)
    if cat:
        return cat

    for prefix, c in _FINDING_CATEGORY_PREFIXES.items():
        if key.startswith(prefix):
            return c

    # Vehicle overdue pattern (oil_change_overdue, tire_rotation_overdue, etc.)
    if key.endswith("_overdue"):
        return "B"

    return "B"


class BaseMonitor:
    """Base class for domain monitors. Subclasses implement run()."""
    domain: str = ""
    schedule_minutes: int = 60
    waking_only: bool = True

    def run(self) -> list[Finding]:
        """Execute all checks and return findings. Must not raise."""
        raise NotImplementedError


def _fingerprint(domain: str, check_key: str) -> str:
    """Compute dedup fingerprint from domain + check_key."""
    return hashlib.sha256(f"{domain}:{check_key}".encode()).hexdigest()[:16]


def store_finding(finding: Finding):
    """Store a finding with fingerprint deduplication.

    If an undelivered finding with the same fingerprint exists, update it
    (refresh timestamp and data). Otherwise insert a new row.
    """
    fp = _fingerprint(finding.domain, finding.check_key)
    ttl_hours = getattr(config, "MONITOR_FINDING_TTL_HOURS", 24)
    expires = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()

    try:
        with db.get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM monitor_findings "
                "WHERE fingerprint = %s AND delivered = FALSE",
                (fp,),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE monitor_findings
                       SET summary = %s, urgency = %s, data = %s,
                           created_at = NOW(), expires_at = %s
                       WHERE id = %s""",
                    (finding.summary, finding.urgency,
                     psycopg.types.json.Jsonb(finding.data),
                     expires, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO monitor_findings
                       (domain, summary, urgency, data, fingerprint, expires_at)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (finding.domain, finding.summary, finding.urgency,
                     psycopg.types.json.Jsonb(finding.data),
                     fp, expires),
                )
    except Exception as e:
        log.error("[MONITOR] Failed to store finding: %s", e)


def get_undelivered(min_urgency: str = "normal") -> list[dict]:
    """Query undelivered findings with urgency >= threshold.

    Returns list of dicts sorted by urgency (highest first), then created_at.
    """
    min_level = _URGENCY_LEVELS.get(min_urgency, 2)
    qualifying = [u for u, level in _URGENCY_LEVELS.items() if level >= min_level]

    if not qualifying:
        return []

    try:
        placeholders = ", ".join(["%s"] * len(qualifying))
        with db.get_conn() as conn:
            rows = conn.execute(
                f"""SELECT * FROM monitor_findings
                    WHERE delivered = FALSE
                    AND (expires_at IS NULL OR expires_at > NOW())
                    AND urgency IN ({placeholders})
                    ORDER BY
                        CASE urgency
                            WHEN 'urgent' THEN 0
                            WHEN 'normal' THEN 1
                            WHEN 'low' THEN 2
                            WHEN 'info' THEN 3
                        END,
                        created_at DESC
                    LIMIT 20""",
                qualifying,
            ).fetchall()
        return [db.serialize_row(r) for r in rows]
    except Exception as e:
        log.error("[MONITOR] Failed to query findings: %s", e)
        return []


def get_recent(hours: int = 24) -> list[dict]:
    """Get all findings from the last N hours (delivered or not)."""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM monitor_findings
                   WHERE created_at >= %s
                   ORDER BY created_at DESC LIMIT 30""",
                (cutoff,),
            ).fetchall()
        return [db.serialize_row(r) for r in rows]
    except Exception as e:
        log.error("[MONITOR] Failed to query recent findings: %s", e)
        return []


def mark_delivered(finding_ids: list[int], method: str):
    """Mark findings as delivered."""
    if not finding_ids:
        return
    try:
        placeholders = ", ".join(["%s"] * len(finding_ids))
        with db.get_conn() as conn:
            conn.execute(
                f"""UPDATE monitor_findings
                    SET delivered = TRUE, delivered_at = NOW(), delivery_method = %s
                    WHERE id IN ({placeholders})""",
                [method] + finding_ids,
            )
    except Exception as e:
        log.error("[MONITOR] Failed to mark findings delivered: %s", e)


def mark_delivered_bulk(domain: str, max_age_hours: int = 24):
    """Mark all undelivered findings from a domain older than max_age_hours as delivered."""
    try:
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        with db.get_conn() as conn:
            result = conn.execute(
                """UPDATE monitor_findings
                   SET delivered = TRUE, delivered_at = NOW(), delivery_method = 'stale_cleanup'
                   WHERE domain = %s AND delivered = FALSE AND created_at < %s""",
                (domain, cutoff),
            )
            if result.rowcount:
                log.info("[MONITOR] Stale cleanup: marked %d old %s findings delivered",
                         result.rowcount, domain)
    except Exception as e:
        log.error("[MONITOR] Stale cleanup failed: %s", e)


def cleanup_expired():
    """Delete expired findings (delivered or not) and old delivered findings."""
    try:
        with db.get_conn() as conn:
            # Delete expired
            result = conn.execute(
                "DELETE FROM monitor_findings WHERE expires_at IS NOT NULL AND expires_at < NOW()"
            )
            expired_count = result.rowcount
            # Delete delivered findings older than 7 days
            result = conn.execute(
                """DELETE FROM monitor_findings
                   WHERE delivered = TRUE
                   AND delivered_at < NOW() - INTERVAL '7 days'"""
            )
            old_count = result.rowcount
            if expired_count or old_count:
                log.info("[MONITOR] Cleanup: %d expired, %d old delivered",
                         expired_count, old_count)
    except Exception as e:
        log.error("[MONITOR] Cleanup failed: %s", e)
