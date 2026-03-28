"""Integration test fixtures — real PostgreSQL against a disposable test database.

SAFETY:
  - Uses `aria_test` database, NEVER the production `aria` database.
  - All tables are TRUNCATED before every test for isolation.
  - The test database is DROPPED at the end of the session.
  - SMS/Twilio and all external HTTP remain mocked (inherited from parent conftest).

Prerequisites:
  - PostgreSQL running locally with `aria` user having CREATEDB privilege.
    If not, run once:  psql -U postgres -c "ALTER USER aria CREATEDB;"
  - Or create the database manually:  createdb -U aria aria_test
"""

import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config
import db
import calendar_store
import health_store
import nutrition_store
import vehicle_store
import legal_store
import timer_store
import fitbit_store
import location_store

TEST_DB_NAME = "aria_test"
TEST_DB_URL = f"postgresql://aria@/{TEST_DB_NAME}"

ALL_TABLES = [
    "events", "reminders", "health_entries", "vehicle_entries",
    "legal_entries", "timers", "nutrition_entries", "locations",
    "fitbit_snapshots", "fitbit_exercise", "request_log",
    "sms_log", "sms_outbound", "tick_state", "nudge_cooldowns",
    "nudge_log", "processed_webhooks", "monitor_state",
    "tool_traces", "entity_mentions", "interaction_quality",
]


@pytest.fixture(scope="session")
def test_database():
    """Create the aria_test database and load schema.  Dropped after session."""
    # Connect to the default database to issue CREATE DATABASE
    try:
        admin = psycopg.connect(config.DATABASE_URL, autocommit=True)
    except Exception as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")

    try:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
        admin.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    except psycopg.errors.InsufficientPrivilege:
        admin.close()
        pytest.skip(
            f"aria user lacks CREATEDB. Run: "
            f"psql -U postgres -c \"ALTER USER aria CREATEDB;\""
        )
    admin.close()

    # Load schema into the fresh test database
    schema_path = Path(__file__).parent.parent.parent / "schema.sql"
    test_conn = psycopg.connect(TEST_DB_URL, autocommit=True)
    test_conn.execute(schema_path.read_text())
    test_conn.close()

    yield TEST_DB_URL

    # Teardown — drop test database
    try:
        admin = psycopg.connect(config.DATABASE_URL, autocommit=True)
        # Disconnect any lingering connections
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{TEST_DB_NAME}' AND pid <> pg_backend_pid()"
        )
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
        admin.close()
    except Exception:
        pass  # best-effort cleanup


@pytest.fixture(scope="session")
def test_pool(test_database):
    """Session-scoped connection pool for the test database."""
    pool = ConnectionPool(
        test_database,
        min_size=2,
        max_size=5,
        open=True,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    yield pool
    pool.close()


@pytest.fixture(autouse=True)
def use_test_db(test_pool):
    """Swap db module's pool to point at the test database."""
    original_pool = db._pool
    db._pool = test_pool
    yield
    db._pool = original_pool


@pytest.fixture(autouse=True)
def clean_tables(test_pool):
    """Truncate every table before each test for isolation."""
    with test_pool.connection() as conn:
        conn.execute(
            "TRUNCATE " + ", ".join(ALL_TABLES) + " CASCADE"
        )
    yield


# ---------------------------------------------------------------------------
# Seed helpers — insert real data via store functions for pipeline tests
# ---------------------------------------------------------------------------

def seed_nutrition(day: str, food_name: str, meal_type: str = "lunch",
                   calories: float = 500, protein_g: float = 30,
                   **extra_nutrients):
    """Insert a nutrition entry via the real store function."""
    nutrients = {"calories": calories, "protein_g": protein_g}
    nutrients.update(extra_nutrients)
    return nutrition_store.add_item(
        food_name=food_name, meal_type=meal_type,
        nutrients=nutrients, entry_date=day,
    )


def seed_health(day: str, category: str = "meal", description: str = "test",
                severity: int | None = None, sleep_hours: float | None = None,
                meal_type: str | None = None):
    """Insert a health entry via the real store function."""
    return health_store.add_entry(
        entry_date=day, category=category, description=description,
        severity=severity, sleep_hours=sleep_hours, meal_type=meal_type,
    )


def seed_fitbit_snapshot(day: str, data: dict):
    """Insert a Fitbit snapshot directly into the DB."""
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO fitbit_snapshots (date, data)
               VALUES (%s, %s)
               ON CONFLICT (date) DO UPDATE SET data = fitbit_snapshots.data || EXCLUDED.data,
               fetched_at = NOW()""",
            (day, psycopg.types.json.Json(data)),
        )


def seed_location(location_name: str = "Home", lat: float = 42.58,
                   lon: float = -88.43, battery_pct: int | None = 85,
                   speed_mps: float | None = 0.0):
    """Insert a location row directly into the DB."""
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO locations (lat, lon, location, accuracy_m, speed_mps, battery_pct)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (lat, lon, location_name, 10.0, speed_mps, battery_pct),
        )


def seed_timer(label: str = "Test Timer", fire_at: str | None = None,
               delivery: str = "sms", priority: str = "gentle",
               message: str = "Timer fired"):
    """Insert a pending timer via the real store function."""
    if fire_at is None:
        fire_at = (datetime.now() + timedelta(hours=1)).isoformat()
    return timer_store.add_timer(
        label=label, fire_at=fire_at, delivery=delivery,
        priority=priority, message=message,
    )


def seed_reminder(text: str = "Test reminder", due: str | None = None,
                  recurring: str | None = None, location: str | None = None,
                  location_trigger: str | None = None):
    """Insert a reminder via the real store function."""
    return calendar_store.add_reminder(
        text=text, due=due, recurring=recurring,
        location=location, location_trigger=location_trigger,
    )


def seed_event(title: str = "Test Event", event_date: str | None = None,
               time: str | None = None, notes: str | None = None):
    """Insert a calendar event via the real store function."""
    if event_date is None:
        event_date = date.today().isoformat()
    return calendar_store.add_event(
        title=title, event_date=event_date, time=time, notes=notes,
    )


def seed_legal(entry_date: str | None = None, entry_type: str = "note",
               description: str = "Test legal entry",
               contacts: list | None = None):
    """Insert a legal entry via the real store function."""
    if entry_date is None:
        entry_date = date.today().isoformat()
    return legal_store.add_entry(
        entry_date=entry_date, entry_type=entry_type,
        description=description, contacts=contacts,
    )


def seed_vehicle(event_date: str | None = None, event_type: str = "oil_change",
                 description: str = "Test vehicle entry",
                 mileage: int | None = None, cost: float | None = None):
    """Insert a vehicle entry via the real store function."""
    if event_date is None:
        event_date = date.today().isoformat()
    return vehicle_store.add_entry(
        event_date=event_date, event_type=event_type,
        description=description, mileage=mileage, cost=cost,
    )


def seed_request_log(input_text: str, response: str = "ok",
                     status: str = "ok", duration: float = 1.0):
    """Insert a request_log row directly."""
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO request_log (input, status, response, duration_s)
               VALUES (%s, %s, %s, %s)""",
            (input_text, status, response, duration),
        )


def seed_nudge_log(nudge_types: list[str], descriptions: list[str],
                   message: str = "", status: str = "sent"):
    """Insert a nudge_log row directly."""
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO nudge_log (nudge_types, trigger_descriptions, message, delivery_status)
               VALUES (%s, %s, %s, %s)""",
            (nudge_types, descriptions, message, status),
        )
