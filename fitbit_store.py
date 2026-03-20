"""Fitbit data store — JSON-backed daily snapshots.

Stores one file per day in data/fitbit/ with all fetched data types.
Provides query functions for ARIA context injection and briefings.
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import config

log = logging.getLogger("aria.fitbit_store")

DB_DIR = config.FITBIT_DB_DIR


def _day_path(day: str) -> Path:
    """Get the JSON file path for a specific day (YYYY-MM-DD)."""
    return DB_DIR / f"{day}.json"


def save_snapshot(snapshot: dict):
    """Save a daily data snapshot."""
    day = snapshot.get("date", date.today().isoformat())
    # Resolve "today" / "yesterday" to actual dates
    if day == "today":
        day = date.today().isoformat()
    elif day == "yesterday":
        day = (date.today() - timedelta(days=1)).isoformat()
    snapshot["date"] = day

    DB_DIR.mkdir(parents=True, exist_ok=True)
    path = _day_path(day)

    # Merge with existing data if present (webhook may fire multiple times)
    if path.exists():
        existing = json.loads(path.read_text())
        for key, value in snapshot.items():
            if value is not None:
                existing[key] = value
        snapshot = existing

    path.write_text(json.dumps(snapshot, indent=2, default=str))
    log.info("Fitbit snapshot saved for %s", day)


def get_snapshot(day: str = "today") -> dict | None:
    """Load a daily snapshot."""
    if day == "today":
        day = date.today().isoformat()
    path = _day_path(day)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def get_sleep_summary(day: str = "today") -> dict | None:
    """Extract a clean sleep summary from a daily snapshot.

    Returns: {total_minutes, deep_minutes, light_minutes, rem_minutes,
              wake_minutes, efficiency, start_time, end_time}
    """
    snap = get_snapshot(day)
    if not snap or not snap.get("sleep"):
        return None

    sleep_data = snap["sleep"]
    sleeps = sleep_data.get("sleep", [])
    if not sleeps:
        return None

    # Use the main sleep (longest, or the one marked isMainSleep)
    main = next((s for s in sleeps if s.get("isMainSleep")), sleeps[0])
    summary = main.get("levels", {}).get("summary", {})

    return {
        "total_minutes": main.get("minutesAsleep", 0),
        "deep_minutes": summary.get("deep", {}).get("minutes", 0),
        "light_minutes": summary.get("light", {}).get("minutes", 0),
        "rem_minutes": summary.get("rem", {}).get("minutes", 0),
        "wake_minutes": summary.get("wake", {}).get("minutes", 0),
        "efficiency": main.get("efficiency", 0),
        "start_time": main.get("startTime", ""),
        "end_time": main.get("endTime", ""),
        "duration_hours": round(main.get("minutesAsleep", 0) / 60, 1),
    }


def get_heart_summary(day: str = "today") -> dict | None:
    """Extract heart rate summary from a daily snapshot.

    Returns: {resting_hr, zones: [{name, minutes, calories_out}]}
    """
    snap = get_snapshot(day)
    if not snap or not snap.get("heart_rate"):
        return None

    hr = snap["heart_rate"]
    value = hr.get("value", {})

    return {
        "resting_hr": value.get("restingHeartRate"),
        "zones": [
            {
                "name": z.get("name", ""),
                "minutes": z.get("minutes", 0),
                "calories_out": z.get("caloriesOut", 0),
            }
            for z in value.get("heartRateZones", [])
        ],
    }


def get_hrv_summary(day: str = "today") -> dict | None:
    """Extract HRV summary. Returns: {rmssd, lf, hf, coverage}"""
    snap = get_snapshot(day)
    if not snap or not snap.get("hrv"):
        return None

    hrv = snap["hrv"]
    value = hrv.get("value", {})
    if not value:
        return None

    return {
        "rmssd": value.get("dailyRmssd"),
        "deep_rmssd": value.get("deepRmssd"),
    }


def get_activity_summary(day: str = "today") -> dict | None:
    """Extract activity summary from a daily snapshot."""
    snap = get_snapshot(day)
    if not snap or not snap.get("activity"):
        return None

    act = snap["activity"]
    return {
        "steps": act.get("steps", 0),
        "distance_miles": round(
            sum(d.get("distance", 0) for d in act.get("distances", [])
                if d.get("activity") == "total"), 2
        ),
        "calories_total": act.get("caloriesOut", 0),
        "calories_active": act.get("activityCalories", 0),
        "active_minutes": (
            act.get("fairlyActiveMinutes", 0) +
            act.get("veryActiveMinutes", 0)
        ),
        "sedentary_minutes": int(act.get("sedentaryMinutes", 0)),
        "floors": act.get("floors", 0),
    }


def get_spo2_summary(day: str = "today") -> dict | None:
    """Extract SpO2 data. Returns: {avg, min, max}"""
    snap = get_snapshot(day)
    if not snap or not snap.get("spo2"):
        return None

    spo2 = snap["spo2"]
    value = spo2.get("value", {})
    if not value:
        return None

    return {
        "avg": value.get("avg"),
        "min": value.get("min"),
        "max": value.get("max"),
    }


def get_briefing_context(day: str = "today") -> str:
    """Build a human-readable Fitbit summary for ARIA context injection."""
    parts = []

    sleep = get_sleep_summary(day)
    if sleep:
        parts.append(
            f"Sleep: {sleep['duration_hours']}h total "
            f"(deep {sleep['deep_minutes']}min, REM {sleep['rem_minutes']}min, "
            f"light {sleep['light_minutes']}min, wake {sleep['wake_minutes']}min) "
            f"— efficiency {sleep['efficiency']}%"
        )

    hr = get_heart_summary(day)
    if hr and hr.get("resting_hr"):
        parts.append(f"Resting heart rate: {hr['resting_hr']} bpm")

    hrv = get_hrv_summary(day)
    if hrv and hrv.get("rmssd"):
        parts.append(f"HRV (RMSSD): {hrv['rmssd']:.1f}ms")
        if hrv.get("deep_rmssd"):
            parts.append(f"HRV during deep sleep: {hrv['deep_rmssd']:.1f}ms")

    spo2 = get_spo2_summary(day)
    if spo2 and spo2.get("avg"):
        parts.append(f"SpO2: avg {spo2['avg']}%, min {spo2['min']}%, max {spo2['max']}%")

    activity = get_activity_summary(day)
    if activity:
        parts.append(
            f"Activity: {activity['steps']:,} steps, "
            f"{activity['distance_miles']} mi, "
            f"{activity['calories_total']:,} cal burned, "
            f"{activity['active_minutes']} active min"
        )

    snap = get_snapshot(day)
    if snap:
        br = snap.get("breathing_rate")
        if br and br.get("value"):
            br_val = br["value"]
            parts.append(f"Breathing rate: {br_val.get('breathingRate', '?')} breaths/min")

        temp = snap.get("temperature")
        if temp and temp.get("value"):
            t_val = temp["value"]
            parts.append(f"Skin temp variation: {t_val.get('nightlyRelative', '?')}°F from baseline")

        vo2 = snap.get("vo2max")
        if vo2 and vo2.get("value"):
            v_val = vo2["value"]
            parts.append(f"VO2 Max: {v_val.get('vo2Max', '?')} mL/kg/min")

    if not parts:
        return ""

    return "Fitbit health data:\n" + "\n".join(f"  - {p}" for p in parts)


def get_trend(days: int = 7) -> str:
    """Build a multi-day trend summary for briefings."""
    parts = []
    resting_hrs = []
    hrvs = []
    sleep_hours = []
    step_counts = []

    for i in range(days):
        day = (date.today() - timedelta(days=i)).isoformat()

        hr = get_heart_summary(day)
        if hr and hr.get("resting_hr"):
            resting_hrs.append(hr["resting_hr"])

        hrv = get_hrv_summary(day)
        if hrv and hrv.get("rmssd"):
            hrvs.append(hrv["rmssd"])

        sleep = get_sleep_summary(day)
        if sleep:
            sleep_hours.append(sleep["duration_hours"])

        activity = get_activity_summary(day)
        if activity:
            step_counts.append(activity["steps"])

    if resting_hrs:
        avg = sum(resting_hrs) / len(resting_hrs)
        parts.append(f"Avg resting HR ({len(resting_hrs)}d): {avg:.0f} bpm")

    if hrvs:
        avg = sum(hrvs) / len(hrvs)
        parts.append(f"Avg HRV RMSSD ({len(hrvs)}d): {avg:.1f}ms")

    if sleep_hours:
        avg = sum(sleep_hours) / len(sleep_hours)
        parts.append(f"Avg sleep ({len(sleep_hours)}d): {avg:.1f}h")

    if step_counts:
        avg = sum(step_counts) / len(step_counts)
        parts.append(f"Avg steps ({len(step_counts)}d): {avg:,.0f}")

    if not parts:
        return ""

    return "Fitbit trends (last " + str(days) + " days):\n" + "\n".join(f"  - {p}" for p in parts)


# --- Exercise Mode ---

def get_exercise_state() -> dict | None:
    """Get current exercise mode state, or None if not active."""
    if not config.FITBIT_EXERCISE_FILE.exists():
        return None
    state = json.loads(config.FITBIT_EXERCISE_FILE.read_text())
    if not state.get("active"):
        return None
    # Auto-expire after 90 minutes as safety net
    started = state.get("started_at", "")
    if started:
        try:
            start_dt = datetime.fromisoformat(started)
            if (datetime.now() - start_dt).total_seconds() > 5400:  # 90 min
                end_exercise("auto-expired after 90 minutes")
                return None
        except ValueError:
            pass
    return state


def start_exercise(exercise_type: str = "general") -> dict:
    """Activate exercise mode. Called by process_actions()."""
    # Compute target HR zones using Karvonen formula
    birth = date.fromisoformat(config.OWNER_BIRTH_DATE)
    age = (date.today() - birth).days // 365
    max_hr = 220 - age
    resting_hr = 68  # default

    hr = get_heart_summary()
    if hr and hr.get("resting_hr"):
        resting_hr = hr["resting_hr"]

    hr_reserve = max_hr - resting_hr
    # Target zones (% of HR reserve + resting HR)
    zones = {
        "warm_up": {"min": round(resting_hr + hr_reserve * 0.4),
                     "max": round(resting_hr + hr_reserve * 0.5)},
        "fat_burn": {"min": round(resting_hr + hr_reserve * 0.5),
                      "max": round(resting_hr + hr_reserve * 0.7)},
        "cardio": {"min": round(resting_hr + hr_reserve * 0.7),
                    "max": round(resting_hr + hr_reserve * 0.85)},
        "peak": {"min": round(resting_hr + hr_reserve * 0.85),
                  "max": max_hr},
    }

    state = {
        "active": True,
        "exercise_type": exercise_type,
        "started_at": datetime.now().isoformat(),
        "resting_hr": resting_hr,
        "max_hr": max_hr,
        "target_zones": zones,
        "hr_readings": [],  # filled by tick.py
        "nudge_count": 0,
    }
    config.FITBIT_EXERCISE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.FITBIT_EXERCISE_FILE.write_text(json.dumps(state, indent=2))
    log.info("Exercise mode activated: %s", exercise_type)
    return state


def end_exercise(reason: str = "user ended") -> dict:
    """Deactivate exercise mode and return session summary."""
    if not config.FITBIT_EXERCISE_FILE.exists():
        return {"status": "not_active"}

    state = json.loads(config.FITBIT_EXERCISE_FILE.read_text())
    state["active"] = False
    state["ended_at"] = datetime.now().isoformat()
    state["end_reason"] = reason

    # Compute session summary
    readings = state.get("hr_readings", [])
    if readings:
        hr_values = [r["hr"] for r in readings if r.get("hr")]
        if hr_values:
            state["summary"] = {
                "duration_min": len(readings),
                "avg_hr": round(sum(hr_values) / len(hr_values)),
                "max_hr": max(hr_values),
                "min_hr": min(hr_values),
            }

    config.FITBIT_EXERCISE_FILE.write_text(json.dumps(state, indent=2))
    log.info("Exercise mode ended: %s", reason)
    return state


def record_exercise_hr(hr_data: list[dict]):
    """Append HR readings to the exercise session. Called by tick.py."""
    if not config.FITBIT_EXERCISE_FILE.exists():
        return
    state = json.loads(config.FITBIT_EXERCISE_FILE.read_text())
    if not state.get("active"):
        return

    now = datetime.now()
    for reading in hr_data:
        state["hr_readings"].append({
            "time": reading.get("time", now.strftime("%H:%M:%S")),
            "hr": reading.get("value", 0),
            "recorded_at": now.isoformat(),
        })

    config.FITBIT_EXERCISE_FILE.write_text(json.dumps(state, indent=2))


def get_exercise_coaching_context() -> str:
    """Build coaching context string for exercise-mode nudges."""
    state = get_exercise_state()
    if not state:
        return ""

    started = datetime.fromisoformat(state["started_at"])
    elapsed = int((datetime.now() - started).total_seconds() / 60)
    zones = state.get("target_zones", {})
    readings = state.get("hr_readings", [])

    parts = [
        f"EXERCISE MODE ACTIVE: {state.get('exercise_type', 'general')} — {elapsed} minutes in",
        f"Resting HR: {state.get('resting_hr', '?')} bpm, Max HR: {state.get('max_hr', '?')} bpm",
    ]

    if zones:
        fb = zones.get("fat_burn", {})
        parts.append(f"Target fat burn zone: {fb.get('min', '?')}-{fb.get('max', '?')} bpm")
        cardio = zones.get("cardio", {})
        parts.append(f"Cardio zone: {cardio.get('min', '?')}-{cardio.get('max', '?')} bpm")

    if readings:
        recent = readings[-5:]  # last 5 minutes
        recent_hrs = [r["hr"] for r in recent if r.get("hr")]
        if recent_hrs:
            avg = sum(recent_hrs) / len(recent_hrs)
            parts.append(f"Recent HR (last {len(recent_hrs)} min): avg {avg:.0f} bpm, latest {recent_hrs[-1]} bpm")

        all_hrs = [r["hr"] for r in readings if r.get("hr")]
        if all_hrs:
            parts.append(f"Session: avg {sum(all_hrs)/len(all_hrs):.0f} bpm, peak {max(all_hrs)} bpm")

    return "\n".join(parts)
