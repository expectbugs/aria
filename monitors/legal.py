"""Legal monitor — graduated deadline warnings.

Provides tiered 7d/3d/1d/overdue warnings for legal deadlines.
Nudges fire at 0-3 days with 24h cooldown; this adds 7-day advance notice
and graduated urgency escalation.
"""

import logging
from datetime import datetime, date

from monitors import BaseMonitor, Finding

log = logging.getLogger("aria.monitors.legal")


class LegalMonitor(BaseMonitor):
    domain = "legal"
    schedule_minutes = 360  # 6 hours
    waking_only = True

    def run(self) -> list[Finding]:
        import legal_store

        findings = []
        today = date.today()
        upcoming = legal_store.get_upcoming_dates()

        for entry in upcoming:
            try:
                deadline = date.fromisoformat(entry["date"])
            except (ValueError, KeyError):
                continue

            days_until = (deadline - today).days
            entry_id = entry.get("id", "?")
            desc = entry.get("description", "legal deadline")

            if days_until < 0:
                findings.append(Finding(
                    domain=self.domain,
                    summary=f"OVERDUE: {desc} was due {entry['date']} "
                            f"({-days_until} days ago)",
                    urgency="urgent",
                    check_key=f"deadline_{entry_id}_overdue",
                    data={"entry_id": entry_id, "date": entry["date"],
                          "days_overdue": -days_until},
                ))
            elif days_until <= 1:
                findings.append(Finding(
                    domain=self.domain,
                    summary=f"TOMORROW: {desc} — {entry['date']}",
                    urgency="normal",
                    check_key=f"deadline_{entry_id}_1d",
                    data={"entry_id": entry_id, "date": entry["date"],
                          "days_until": days_until},
                ))
            elif days_until <= 3:
                findings.append(Finding(
                    domain=self.domain,
                    summary=f"{days_until} days: {desc} — {entry['date']}",
                    urgency="low",
                    check_key=f"deadline_{entry_id}_3d",
                    data={"entry_id": entry_id, "date": entry["date"],
                          "days_until": days_until},
                ))
            elif days_until <= 7:
                findings.append(Finding(
                    domain=self.domain,
                    summary=f"{days_until} days: {desc} — {entry['date']}",
                    urgency="info",
                    check_key=f"deadline_{entry_id}_7d",
                    data={"entry_id": entry_id, "date": entry["date"],
                          "days_until": days_until},
                ))

        return findings
