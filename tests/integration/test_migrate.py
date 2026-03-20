"""Integration tests for migrate.py — JSON→PostgreSQL migration.

Tests that the migration is idempotent and handles all data formats.

SAFETY: All tests receive the test database URL via fixture, never
reading from config.DATABASE_URL or db._pool directly.
"""

import json
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

import pytest

import migrate


@pytest.fixture
def test_conn(test_pool):
    """Provide a direct psycopg connection to the test database."""
    conn = psycopg.connect(test_pool.conninfo, row_factory=dict_row,
                            autocommit=True)
    yield conn
    conn.close()


class TestMigrateEvents:
    def test_imports_events(self, tmp_path, test_conn):
        cal_file = tmp_path / "calendar.json"
        cal_file.write_text(json.dumps([
            {"id": "ev1", "title": "Test", "date": "2026-03-20",
             "time": "14:00", "notes": "test note", "created": "2026-03-19T10:00:00"},
        ]))
        with patch.object(migrate, "DATA_DIR", tmp_path):
            migrate.migrate_events(test_conn)
            row = test_conn.execute("SELECT * FROM events WHERE id = 'ev1'").fetchone()
            assert row is not None
            assert row["title"] == "Test"

    def test_idempotent(self, tmp_path, test_conn):
        cal_file = tmp_path / "calendar.json"
        cal_file.write_text(json.dumps([
            {"id": "ev1", "title": "Test", "date": "2026-03-20"},
        ]))
        with patch.object(migrate, "DATA_DIR", tmp_path):
            migrate.migrate_events(test_conn)
            migrate.migrate_events(test_conn)  # second run
            rows = test_conn.execute("SELECT * FROM events WHERE id = 'ev1'").fetchall()
            assert len(rows) == 1  # no duplicates


class TestMigrateNutrition:
    def test_jsonb_nutrients(self, tmp_path, test_conn):
        nut_file = tmp_path / "nutrition.json"
        nut_file.write_text(json.dumps([{
            "id": "n1", "date": "2026-03-20", "time": "12:00",
            "meal_type": "lunch", "food_name": "Chicken",
            "source": "manual", "servings": 1.0, "serving_size": "6 oz",
            "nutrients": {"calories": 350, "protein_g": 40},
            "notes": "", "created": "2026-03-20T12:00:00",
        }]))
        with patch.object(migrate, "DATA_DIR", tmp_path):
            migrate.migrate_nutrition(test_conn)
            row = test_conn.execute(
                "SELECT * FROM nutrition_entries WHERE id = 'n1'"
            ).fetchone()
            assert row is not None
            assert row["nutrients"]["calories"] == 350


class TestMigrateLocations:
    def test_imports_jsonl(self, tmp_path, test_conn):
        loc_file = tmp_path / "location.jsonl"
        loc_file.write_text(
            json.dumps({"timestamp": "2026-03-20T14:00:00", "lat": 42.58,
                        "lon": -88.43, "location": "Home"}) + "\n"
            + json.dumps({"timestamp": "2026-03-20T14:05:00", "lat": 42.59,
                          "lon": -88.44, "location": "Work"}) + "\n"
        )
        with patch.object(migrate, "DATA_DIR", tmp_path):
            migrate.migrate_locations(test_conn)
            rows = test_conn.execute("SELECT * FROM locations ORDER BY timestamp").fetchall()
            assert len(rows) == 2
            assert rows[0]["location"] == "Home"


class TestMigrateFitbit:
    def test_imports_daily_snapshots(self, tmp_path, test_conn):
        fitbit_dir = tmp_path / "fitbit"
        fitbit_dir.mkdir()
        (fitbit_dir / "2026-03-20.json").write_text(json.dumps({
            "date": "2026-03-20",
            "heart_rate": {"value": {"restingHeartRate": 65}},
        }))
        with patch.object(migrate, "DATA_DIR", tmp_path):
            migrate.migrate_fitbit_snapshots(test_conn)
            row = test_conn.execute(
                "SELECT data FROM fitbit_snapshots WHERE date = '2026-03-20'"
            ).fetchone()
            assert row is not None
            assert row["data"]["heart_rate"]["value"]["restingHeartRate"] == 65


class TestMigrateEmptyFiles:
    def test_missing_file_no_error(self, tmp_path, test_conn):
        """Migration should handle missing JSON files gracefully."""
        with patch.object(migrate, "DATA_DIR", tmp_path):
            # These files don't exist — should not raise
            migrate.migrate_events(test_conn)
            migrate.migrate_reminders(test_conn)
            migrate.migrate_health(test_conn)
            migrate.migrate_vehicle(test_conn)
