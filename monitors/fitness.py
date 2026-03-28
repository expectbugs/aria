"""Fitness monitor — multi-day Fitbit trend analysis.

Checks TRENDS that nudges don't:
- Resting HR trending up over 7 days
- HRV declining trend
- Sleep quality declining (deep/REM declining)
- Weekly step average vs goal
- Sleep schedule regularity

Does NOT check: point-in-time sedentary, single-day HR anomaly (nudges handle those).
"""

import logging
from datetime import datetime, date, timedelta

from monitors import BaseMonitor, Finding

log = logging.getLogger("aria.monitors.fitness")


class FitnessMonitor(BaseMonitor):
    domain = "fitness"
    schedule_minutes = 60
    waking_only = True

    def run(self) -> list[Finding]:
        findings = []

        try:
            findings.extend(self._check_hr_trend())
        except Exception as e:
            log.error("[MONITOR] fitness HR trend check failed: %s", e)

        try:
            findings.extend(self._check_hrv_trend())
        except Exception as e:
            log.error("[MONITOR] fitness HRV trend check failed: %s", e)

        try:
            findings.extend(self._check_sleep_trend())
        except Exception as e:
            log.error("[MONITOR] fitness sleep trend check failed: %s", e)

        try:
            findings.extend(self._check_step_average())
        except Exception as e:
            log.error("[MONITOR] fitness step average check failed: %s", e)

        try:
            findings.extend(self._check_sleep_regularity())
        except Exception as e:
            log.error("[MONITOR] fitness sleep regularity check failed: %s", e)

        return findings

    def _check_hr_trend(self) -> list[Finding]:
        """Check if resting HR is trending up over 7 days."""
        import fitbit_store

        hrs = fitbit_store.get_resting_hr_history(days=7)
        if len(hrs) < 4:
            return []

        avg = sum(hrs) / len(hrs)
        latest = hrs[-1]

        if latest > avg + 5:
            return [Finding(
                domain=self.domain,
                summary=f"Resting HR trending up: {latest} bpm today vs "
                        f"{avg:.0f} bpm 7-day average (+{latest - avg:.0f})",
                urgency="normal",
                check_key="hr_trend_up",
                data={"latest": latest, "avg_7d": round(avg, 1),
                      "delta": round(latest - avg, 1)},
            )]
        return []

    def _check_hrv_trend(self) -> list[Finding]:
        """Check if HRV is declining over 7 days."""
        import fitbit_store
        import db as _db

        start = (date.today() - timedelta(days=7)).isoformat()
        try:
            with _db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT date, data FROM fitbit_snapshots "
                    "WHERE date >= %s ORDER BY date",
                    (start,),
                ).fetchall()
        except Exception:
            return []

        hrvs = []
        for row in rows:
            snap = row["data"]
            hrv = snap.get("hrv", {})
            rmssd = hrv.get("value", {}).get("dailyRmssd") if hrv else None
            if rmssd is not None:
                hrvs.append(float(rmssd))

        if len(hrvs) < 4:
            return []

        avg = sum(hrvs) / len(hrvs)
        latest = hrvs[-1]
        threshold = avg * 0.8  # 20% decline

        if latest < threshold:
            return [Finding(
                domain=self.domain,
                summary=f"HRV declining: {latest:.1f}ms today vs "
                        f"{avg:.1f}ms 7-day average ({(1 - latest/avg)*100:.0f}% drop)",
                urgency="normal",
                check_key="hrv_declining",
                data={"latest": round(latest, 1), "avg_7d": round(avg, 1)},
            )]
        return []

    def _check_sleep_trend(self) -> list[Finding]:
        """Check if sleep quality is declining (3+ short nights)."""
        import fitbit_store
        import db as _db

        start = (date.today() - timedelta(days=7)).isoformat()
        try:
            with _db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT date, data FROM fitbit_snapshots "
                    "WHERE date >= %s ORDER BY date",
                    (start,),
                ).fetchall()
        except Exception:
            return []

        sleep_hours = []
        for row in rows:
            snap = row["data"]
            sleep_data = snap.get("sleep")
            if sleep_data:
                sleeps = sleep_data.get("sleep", [])
                if sleeps:
                    main = next((s for s in sleeps if s.get("isMainSleep")), sleeps[0])
                    mins = int(main.get("minutesAsleep", 0))
                    if mins:
                        sleep_hours.append(round(mins / 60, 1))

        if len(sleep_hours) < 3:
            return []

        short_nights = sum(1 for h in sleep_hours if h < 6)
        if short_nights >= 3:
            avg = sum(sleep_hours) / len(sleep_hours)
            return [Finding(
                domain=self.domain,
                summary=f"Sleep deficit: {short_nights} of last {len(sleep_hours)} nights "
                        f"under 6 hours (avg {avg:.1f}h)",
                urgency="normal",
                check_key="sleep_deficit",
                data={"short_nights": short_nights, "total_nights": len(sleep_hours),
                      "avg_hours": round(avg, 1)},
            )]
        return []

    def _check_step_average(self) -> list[Finding]:
        """Check 7-day step average against goal."""
        import fitbit_store
        import db as _db

        start = (date.today() - timedelta(days=6)).isoformat()
        try:
            with _db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT data FROM fitbit_snapshots WHERE date >= %s",
                    (start,),
                ).fetchall()
        except Exception:
            return []

        steps = []
        for row in rows:
            act = row["data"].get("activity")
            if act:
                s = int(act.get("steps", 0))
                if s > 0:
                    steps.append(s)

        if len(steps) < 4:
            return []

        avg = sum(steps) / len(steps)
        goal = 8000

        if avg < goal:
            return [Finding(
                domain=self.domain,
                summary=f"Weekly step average {avg:,.0f} — below {goal:,} goal "
                        f"({len(steps)} days tracked)",
                urgency="low",
                check_key="steps_below_goal",
                data={"avg_steps": round(avg), "goal": goal, "days": len(steps)},
            )]
        return []

    def _check_sleep_regularity(self) -> list[Finding]:
        """Check bedtime variance over 7 days."""
        import fitbit_store
        import db as _db

        start = (date.today() - timedelta(days=7)).isoformat()
        try:
            with _db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT data FROM fitbit_snapshots WHERE date >= %s ORDER BY date",
                    (start,),
                ).fetchall()
        except Exception:
            return []

        bedtimes_minutes = []  # minutes from midnight
        for row in rows:
            sleep_data = row["data"].get("sleep")
            if not sleep_data:
                continue
            sleeps = sleep_data.get("sleep", [])
            if not sleeps:
                continue
            main = next((s for s in sleeps if s.get("isMainSleep")), sleeps[0])
            start_time = main.get("startTime", "")
            if "T" in start_time:
                try:
                    t = datetime.fromisoformat(start_time)
                    # Convert to minutes from midnight (handle overnight)
                    mins = t.hour * 60 + t.minute
                    if mins < 720:  # before noon = after midnight bedtime
                        mins += 1440  # add 24h worth of minutes
                    bedtimes_minutes.append(mins)
                except (ValueError, TypeError):
                    pass

        if len(bedtimes_minutes) < 4:
            return []

        avg = sum(bedtimes_minutes) / len(bedtimes_minutes)
        variance_minutes = max(abs(b - avg) for b in bedtimes_minutes)

        if variance_minutes > 120:  # 2+ hour variance
            return [Finding(
                domain=self.domain,
                summary=f"Irregular sleep schedule — bedtime varies by "
                        f"{variance_minutes:.0f} minutes over {len(bedtimes_minutes)} nights",
                urgency="info",
                check_key="irregular_sleep",
                data={"variance_minutes": round(variance_minutes),
                      "nights": len(bedtimes_minutes)},
            )]
        return []
