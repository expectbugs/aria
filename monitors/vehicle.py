"""Vehicle monitor — maintenance interval tracking.

Checks service intervals based on time since last service per type.
Does NOT duplicate nudge checks (there are no vehicle nudges currently).
"""

import logging
from datetime import datetime, date, timedelta

from monitors import BaseMonitor, Finding

log = logging.getLogger("aria.monitors.vehicle")

# Service intervals in days
_INTERVALS = {
    "oil_change": {"days": 180, "label": "Oil change"},
    "tire_rotation": {"days": 180, "label": "Tire rotation"},
    "brake_service": {"days": 365, "label": "Brake service"},
    "fluid": {"days": 365, "label": "Fluid check"},
    "filter": {"days": 365, "label": "Filter replacement"},
    "inspection": {"days": 365, "label": "Inspection"},
}


class VehicleMonitor(BaseMonitor):
    domain = "vehicle"
    schedule_minutes = 1440  # daily
    waking_only = True

    def run(self) -> list[Finding]:
        import vehicle_store

        findings = []
        latest = vehicle_store.get_latest_by_type()
        today = date.today()

        for service_type, interval in _INTERVALS.items():
            entry = latest.get(service_type)
            if not entry:
                continue  # no history for this type

            try:
                last_date = date.fromisoformat(entry["date"])
            except (ValueError, KeyError):
                continue

            days_since = (today - last_date).days
            threshold = interval["days"]
            label = interval["label"]

            if days_since > threshold:
                overdue_days = days_since - threshold
                urgency = "normal" if overdue_days > 30 else "low"
                mileage_note = ""
                if entry.get("mileage"):
                    mileage_note = f" at {entry['mileage']:,} mi"

                findings.append(Finding(
                    domain=self.domain,
                    summary=f"{label} overdue — last done {entry['date']}{mileage_note} "
                            f"({days_since} days ago, interval is {threshold} days)",
                    urgency=urgency,
                    check_key=f"{service_type}_overdue",
                    data={"service_type": service_type, "last_date": entry["date"],
                          "days_since": days_since, "threshold": threshold},
                ))

        return findings
