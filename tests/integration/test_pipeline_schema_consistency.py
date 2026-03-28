"""Schema vs code agreement tests.

Verifies that schema.sql, ALL_TABLES, NUTRIENT_FIELDS, and actual database
columns are consistent with each other.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

import re
from pathlib import Path

import pytest

import db
import nutrition_store

from tests.integration.conftest import ALL_TABLES


SCHEMA_PATH = Path(__file__).parent.parent.parent / "schema.sql"


def _parse_schema_tables() -> list[str]:
    """Parse CREATE TABLE statements from schema.sql."""
    schema = SCHEMA_PATH.read_text()
    return re.findall(
        r'CREATE TABLE IF NOT EXISTS\s+(\w+)',
        schema, re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Schema consistency
# ---------------------------------------------------------------------------

class TestSchemaConsistency:
    def test_table_count_matches(self):
        """Number of CREATE TABLE statements should match len(ALL_TABLES)."""
        schema_tables = _parse_schema_tables()
        assert len(schema_tables) == len(ALL_TABLES), (
            f"schema.sql has {len(schema_tables)} tables, "
            f"ALL_TABLES has {len(ALL_TABLES)}. "
            f"schema: {sorted(schema_tables)}, "
            f"ALL_TABLES: {sorted(ALL_TABLES)}"
        )

    def test_every_all_tables_in_schema(self):
        """Every table in ALL_TABLES should exist in schema.sql."""
        schema_tables = set(_parse_schema_tables())
        for table in ALL_TABLES:
            assert table in schema_tables, (
                f"Table '{table}' is in ALL_TABLES but not in schema.sql"
            )

    def test_every_schema_table_in_all_tables(self):
        """Every table in schema.sql should exist in ALL_TABLES."""
        schema_tables = _parse_schema_tables()
        all_tables_set = set(ALL_TABLES)
        for table in schema_tables:
            assert table in all_tables_set, (
                f"Table '{table}' is in schema.sql but not in ALL_TABLES"
            )

    def test_nutrient_fields_match_daily_totals_sql(self):
        """NUTRIENT_FIELDS list should match fields used in get_daily_totals SQL."""
        # get_daily_totals builds SQL from NUTRIENT_FIELDS, so if the list
        # is incomplete, aggregation will miss nutrients. Verify the fields
        # are the same ones used in the function.
        today = "2099-01-01"
        totals = nutrition_store.get_daily_totals(today)

        # Every NUTRIENT_FIELDS entry should have a key in the returned dict
        for field in nutrition_store.NUTRIENT_FIELDS:
            assert field in totals, (
                f"NUTRIENT_FIELDS has '{field}' but it's not in get_daily_totals output"
            )

    def test_nutrition_entries_has_jsonb_nutrients(self):
        """nutrition_entries table should have a JSONB column 'nutrients'."""
        with db.get_conn() as conn:
            row = conn.execute(
                """SELECT data_type FROM information_schema.columns
                   WHERE table_name = 'nutrition_entries'
                   AND column_name = 'nutrients'""",
            ).fetchone()
        assert row is not None, "Column 'nutrients' not found in nutrition_entries"
        assert row["data_type"] == "jsonb"

    def test_fitbit_snapshots_has_jsonb_data(self):
        """fitbit_snapshots table should have a JSONB column 'data'."""
        with db.get_conn() as conn:
            row = conn.execute(
                """SELECT data_type FROM information_schema.columns
                   WHERE table_name = 'fitbit_snapshots'
                   AND column_name = 'data'""",
            ).fetchone()
        assert row is not None, "Column 'data' not found in fitbit_snapshots"
        assert row["data_type"] == "jsonb"

    def test_all_tables_accessible(self):
        """Every table in ALL_TABLES should be queryable."""
        for table in ALL_TABLES:
            with db.get_conn() as conn:
                row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            # No exception = accessible. Row may be None (empty table).


# ===========================================================================
# Total: 7 tests
# ===========================================================================
