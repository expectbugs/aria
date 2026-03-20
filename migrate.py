#!/usr/bin/env python3
"""One-time migration: JSON/JSONL files → PostgreSQL.

Imports existing data from ARIA's JSON stores into the new PostgreSQL tables.
Safe to run multiple times — uses ON CONFLICT DO NOTHING for ID-based tables.

Usage: python migrate.py
"""

import json
import sys
from pathlib import Path

import psycopg
import psycopg.types.json
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).parent))
import config

DATA_DIR = config.DATA_DIR
LOGS_DIR = config.LOGS_DIR


def get_conn():
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row, autocommit=True)


def load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    return [data]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().strip().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def migrate_events(conn):
    entries = load_json(DATA_DIR / "calendar.json")
    count = 0
    for e in entries:
        conn.execute(
            """INSERT INTO events (id, title, date, time, notes, created)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (e["id"], e["title"], e["date"], e.get("time"),
             e.get("notes"), e.get("created")),
        )
        count += 1
    print(f"  events: {count} rows")


def migrate_reminders(conn):
    entries = load_json(DATA_DIR / "reminders.json")
    count = 0
    for r in entries:
        conn.execute(
            """INSERT INTO reminders (id, text, due, recurring, location, location_trigger, done, completed_at, created)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (r["id"], r["text"], r.get("due"), r.get("recurring"),
             r.get("location"), r.get("location_trigger"),
             r.get("done", False), r.get("completed_at"), r.get("created")),
        )
        count += 1
    print(f"  reminders: {count} rows")


def migrate_health(conn):
    entries = load_json(DATA_DIR / "health.json")
    count = 0
    for e in entries:
        conn.execute(
            """INSERT INTO health_entries (id, date, category, description, severity, sleep_hours, meal_type, created)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (e["id"], e["date"], e["category"], e["description"],
             e.get("severity"), e.get("sleep_hours"), e.get("meal_type"), e.get("created")),
        )
        count += 1
    print(f"  health_entries: {count} rows")


def migrate_vehicle(conn):
    entries = load_json(DATA_DIR / "vehicle.json")
    count = 0
    for e in entries:
        conn.execute(
            """INSERT INTO vehicle_entries (id, date, event_type, description, mileage, cost, created)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (e["id"], e["date"], e["event_type"], e["description"],
             e.get("mileage"), e.get("cost"), e.get("created")),
        )
        count += 1
    print(f"  vehicle_entries: {count} rows")


def migrate_legal(conn):
    entries = load_json(DATA_DIR / "legal.json")
    count = 0
    for e in entries:
        conn.execute(
            """INSERT INTO legal_entries (id, date, entry_type, description, contacts, created)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (e["id"], e["date"], e["entry_type"], e["description"],
             e.get("contacts", []), e.get("created")),
        )
        count += 1
    print(f"  legal_entries: {count} rows")


def migrate_timers(conn):
    entries = load_json(DATA_DIR / "timers.json")
    count = 0
    for t in entries:
        conn.execute(
            """INSERT INTO timers (id, label, fire_at, delivery, priority, message, source, status, created, fired_at, cancelled_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (t["id"], t["label"], t["fire_at"], t.get("delivery", "sms"),
             t.get("priority", "gentle"), t.get("message", ""),
             t.get("source", "user"), t.get("status", "pending"),
             t.get("created"), t.get("fired_at"), t.get("cancelled_at")),
        )
        count += 1
    print(f"  timers: {count} rows")


def migrate_nutrition(conn):
    entries = load_json(DATA_DIR / "nutrition.json")
    count = 0
    for n in entries:
        conn.execute(
            """INSERT INTO nutrition_entries (id, date, time, meal_type, food_name, source, servings, serving_size, nutrients, notes, created)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (n["id"], n["date"], n.get("time", "00:00"), n.get("meal_type", "snack"),
             n["food_name"], n.get("source", "label_photo"),
             n.get("servings", 1.0), n.get("serving_size", ""),
             psycopg.types.json.Jsonb(n.get("nutrients", {})),
             n.get("notes", ""), n.get("created")),
        )
        count += 1
    print(f"  nutrition_entries: {count} rows")


def migrate_locations(conn):
    entries = load_jsonl(DATA_DIR / "location.jsonl")
    count = 0
    for loc in entries:
        conn.execute(
            """INSERT INTO locations (timestamp, lat, lon, location, accuracy_m, speed_mps, battery_pct)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (loc["timestamp"], loc["lat"], loc["lon"],
             loc.get("location"), loc.get("accuracy_m"),
             loc.get("speed_mps"), loc.get("battery_pct")),
        )
        count += 1
    print(f"  locations: {count} rows")


def migrate_fitbit_snapshots(conn):
    fitbit_dir = DATA_DIR / "fitbit"
    if not fitbit_dir.exists():
        print("  fitbit_snapshots: 0 rows (no directory)")
        return
    count = 0
    for path in sorted(fitbit_dir.glob("*.json")):
        day = path.stem  # e.g., "2026-03-19"
        data = json.loads(path.read_text())
        conn.execute(
            """INSERT INTO fitbit_snapshots (date, data)
               VALUES (%s, %s)
               ON CONFLICT (date) DO UPDATE SET data = fitbit_snapshots.data || EXCLUDED.data""",
            (day, psycopg.types.json.Jsonb(data)),
        )
        count += 1
    print(f"  fitbit_snapshots: {count} rows")


def migrate_request_log(conn):
    entries = load_jsonl(LOGS_DIR / "requests.jsonl")
    count = 0
    for r in entries:
        conn.execute(
            """INSERT INTO request_log (timestamp, input, status, response, error, duration_s)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (r.get("timestamp"), r.get("input"), r.get("status", "unknown"),
             r.get("response", ""), r.get("error", ""), r.get("duration_s")),
        )
        count += 1
    print(f"  request_log: {count} rows")


def migrate_sms_log(conn):
    entries = load_jsonl(DATA_DIR / "sms_log.jsonl")
    count = 0
    for s in entries:
        conn.execute(
            """INSERT INTO sms_log (timestamp, from_number, to_number, inbound, media, response, duration_s)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (s.get("timestamp"), s.get("from"), s.get("to"),
             s.get("inbound"), s.get("media", []),
             s.get("response"), s.get("duration_s")),
        )
        count += 1
    print(f"  sms_log: {count} rows")


def migrate_sms_outbound(conn):
    entries = load_jsonl(DATA_DIR / "sms_outbound.jsonl")
    count = 0
    for s in entries:
        conn.execute(
            """INSERT INTO sms_outbound (timestamp, to_number, body, media_url, sid)
               VALUES (%s, %s, %s, %s, %s)""",
            (s.get("timestamp"), s.get("to"), s.get("body"),
             s.get("media_url"), s.get("sid")),
        )
        count += 1
    print(f"  sms_outbound: {count} rows")


def migrate_tick_state(conn):
    path = DATA_DIR / "tick_state.json"
    if not path.exists():
        print("  tick_state: 0 rows")
        return
    state = json.loads(path.read_text())
    count = 0
    for key, value in state.items():
        conn.execute(
            """INSERT INTO tick_state (key, value)
               VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            (key, str(value)),
        )
        count += 1
    print(f"  tick_state: {count} rows")


def migrate_nudge_cooldowns(conn):
    path = DATA_DIR / "nudge_cooldowns.json"
    if not path.exists():
        print("  nudge_cooldowns: 0 rows")
        return
    cooldowns = json.loads(path.read_text())
    count = 0
    for nudge_type, last_fired in cooldowns.items():
        conn.execute(
            """INSERT INTO nudge_cooldowns (nudge_type, last_fired)
               VALUES (%s, %s)
               ON CONFLICT (nudge_type) DO UPDATE SET last_fired = EXCLUDED.last_fired""",
            (nudge_type, last_fired),
        )
        count += 1
    print(f"  nudge_cooldowns: {count} rows")


def main():
    print("ARIA JSON → PostgreSQL Migration")
    print(f"Database: {config.DATABASE_URL}")
    print()

    conn = get_conn()

    print("Migrating stores:")
    migrate_events(conn)
    migrate_reminders(conn)
    migrate_health(conn)
    migrate_vehicle(conn)
    migrate_legal(conn)
    migrate_timers(conn)
    migrate_nutrition(conn)
    migrate_locations(conn)
    migrate_fitbit_snapshots(conn)

    print("\nMigrating logs:")
    migrate_request_log(conn)
    migrate_sms_log(conn)
    migrate_sms_outbound(conn)

    print("\nMigrating state:")
    migrate_tick_state(conn)
    migrate_nudge_cooldowns(conn)

    conn.close()
    print("\nDone! JSON files are preserved as backup.")


if __name__ == "__main__":
    main()
