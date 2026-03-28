"""Health monitor — nutrition compliance, NAFLD biomarkers, supplement adherence.

Checks TRENDS and COMPLIANCE that nudges don't:
- Daily nutrient target compliance (% met)
- NAFLD biomarker trends (choline, omega-3 consistency over 7d)
- Caloric intake vs activity correlation
- Supplement adherence

Does NOT check: meal gaps, point-in-time sugar/sodium limits (nudges handle those).
"""

import logging
from datetime import datetime, date, timedelta

from monitors import BaseMonitor, Finding

log = logging.getLogger("aria.monitors.health")


class HealthMonitor(BaseMonitor):
    domain = "health"
    schedule_minutes = 60
    waking_only = True

    def run(self) -> list[Finding]:
        findings = []
        today = datetime.now().strftime("%Y-%m-%d")

        try:
            findings.extend(self._check_daily_compliance(today))
        except Exception as e:
            log.error("[MONITOR] health daily compliance check failed: %s", e)

        try:
            findings.extend(self._check_nafld_trends())
        except Exception as e:
            log.error("[MONITOR] health NAFLD trend check failed: %s", e)

        try:
            findings.extend(self._check_calorie_activity_correlation(today))
        except Exception as e:
            log.error("[MONITOR] health calorie-activity check failed: %s", e)

        return findings

    def _check_daily_compliance(self, today: str) -> list[Finding]:
        """Check how well today's nutrition hits daily targets."""
        import nutrition_store

        totals = nutrition_store.get_daily_totals(today)
        if totals["item_count"] < 2:
            return []  # not enough data to evaluate

        findings = []
        targets = nutrition_store.DAILY_TARGETS

        # Check critical NAFLD nutrients
        choline = totals.get("choline_mg", 0)
        if choline < 300 and totals["item_count"] >= 3:
            findings.append(Finding(
                domain=self.domain,
                summary=f"Choline only {choline:.0f}mg today with {totals['item_count']} items "
                        f"logged — target 550mg for NAFLD (eggs, liver, salmon are top sources)",
                urgency="normal",
                check_key="choline_low",
                data={"choline_mg": choline, "item_count": totals["item_count"]},
            ))

        # Protein adequacy
        protein = totals.get("protein_g", 0)
        if protein < 60 and totals["item_count"] >= 3:
            findings.append(Finding(
                domain=self.domain,
                summary=f"Protein only {protein:.0f}g today — target 100-130g",
                urgency="low",
                check_key="protein_low",
                data={"protein_g": protein},
            ))

        # Fiber
        fiber = totals.get("dietary_fiber_g", 0)
        if fiber < 15 and totals["item_count"] >= 3:
            findings.append(Finding(
                domain=self.domain,
                summary=f"Fiber only {fiber:.0f}g today — target 25-35g",
                urgency="info",
                check_key="fiber_low",
                data={"dietary_fiber_g": fiber},
            ))

        return findings

    def _check_nafld_trends(self) -> list[Finding]:
        """Check 7-day NAFLD biomarker consistency."""
        import nutrition_store
        import db as _db

        findings = []

        # Query 7-day choline and omega-3 totals
        week_start = (date.today() - timedelta(days=6)).isoformat()
        try:
            with _db.get_conn() as conn:
                rows = conn.execute(
                    """SELECT date,
                        COALESCE(SUM(CASE WHEN nutrients->>'choline_mg' IS NOT NULL
                            THEN (nutrients->>'choline_mg')::float * servings END), 0) AS choline_mg,
                        COALESCE(SUM(CASE WHEN nutrients->>'omega3_mg' IS NOT NULL
                            THEN (nutrients->>'omega3_mg')::float * servings END), 0) AS omega3_mg
                    FROM nutrition_entries
                    WHERE date >= %s
                    GROUP BY date""",
                    (week_start,),
                ).fetchall()
        except Exception:
            return []

        if len(rows) < 3:
            return []  # not enough tracking data

        # Choline consistency: hitting 400mg+ on how many days?
        choline_adequate_days = sum(1 for r in rows if r["choline_mg"] >= 400)
        if choline_adequate_days < 3:
            findings.append(Finding(
                domain=self.domain,
                summary=f"Choline adequate only {choline_adequate_days}/{len(rows)} days this week "
                        f"— consistent intake critical for NAFLD liver fat export",
                urgency="normal" if choline_adequate_days < 2 else "low",
                check_key="choline_trend",
                data={"adequate_days": choline_adequate_days, "total_days": len(rows)},
            ))

        # Omega-3: any fish meals this week?
        omega3_days = sum(1 for r in rows if r["omega3_mg"] > 100)
        if omega3_days == 0 and len(rows) >= 5:
            findings.append(Finding(
                domain=self.domain,
                summary="No omega-3 logged this week — target 2-3 fish meals "
                        "(salmon, sardines, mackerel)",
                urgency="low",
                check_key="omega3_missing",
                data={"omega3_days": 0, "total_days": len(rows)},
            ))

        return findings

    def _check_calorie_activity_correlation(self, today: str) -> list[Finding]:
        """Check if calorie intake matches activity level over recent days."""
        import nutrition_store

        findings = []

        # Check 3-day surplus trend
        surplus_days = 0
        for days_ago in range(3):
            day = (date.today() - timedelta(days=days_ago)).isoformat()
            net = nutrition_store.get_net_calories(day)
            if net["consumed"] > 0 and net["burned"] > 0 and net["net"] > 0:
                surplus_days += 1

        if surplus_days >= 3:
            findings.append(Finding(
                domain=self.domain,
                summary=f"Calorie surplus {surplus_days} consecutive days — "
                        f"review portions or increase activity",
                urgency="normal",
                check_key="surplus_trend",
                data={"surplus_days": surplus_days},
            ))

        return findings
