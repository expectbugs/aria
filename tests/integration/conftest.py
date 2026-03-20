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
from pathlib import Path
from unittest.mock import patch, MagicMock

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config
import db

TEST_DB_NAME = "aria_test"
TEST_DB_URL = f"postgresql://aria@/{TEST_DB_NAME}"

ALL_TABLES = [
    "events", "reminders", "health_entries", "vehicle_entries",
    "legal_entries", "timers", "nutrition_entries", "locations",
    "fitbit_snapshots", "fitbit_exercise", "request_log",
    "sms_log", "sms_outbound", "tick_state", "nudge_cooldowns",
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
