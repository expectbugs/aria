"""Tests for health_store.py — health log and pattern detection."""

from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

import health_store
from helpers import make_health_row


def _patch_db():
    mock_conn = MagicMock()
    patcher = patch("health_store.db.get_conn")
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


class TestGetEntries:
    def test_no_filters(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            health_store.get_entries()
            sql = mc.execute.call_args[0][0]
            assert "WHERE" not in sql
            assert "ORDER BY date DESC" in sql
        finally:
            p.stop()

    def test_filter_by_days(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            health_store.get_entries(days=7)
            sql = mc.execute.call_args[0][0]
            assert "date >= %s" in sql
        finally:
            p.stop()

    def test_filter_by_category(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            health_store.get_entries(category="pain")
            sql = mc.execute.call_args[0][0]
            assert "category = %s" in sql
        finally:
            p.stop()

    def test_combined_filters(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            health_store.get_entries(days=14, category="meal")
            sql = mc.execute.call_args[0][0]
            assert "date >= %s" in sql
            assert "category = %s" in sql
        finally:
            p.stop()


class TestGetPatterns:
    def _make_entries(self, entries):
        """Helper to set up mock DB returning given entries."""
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = entries
        return mc, p

    def test_pain_reported_multiple_days(self):
        today = datetime.now().strftime("%Y-%m-%d")
        d1 = today
        d2 = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        d3 = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        rows = [
            make_health_row(d=date.fromisoformat(d1), category="pain",
                          description="back pain, lower"),
            make_health_row(d=date.fromisoformat(d2), category="pain",
                          description="back pain, moderate"),
            make_health_row(d=date.fromisoformat(d3), category="pain",
                          description="back pain"),
        ]
        mc, p = self._make_entries(rows)
        try:
            patterns = health_store.get_patterns(days=7)
            assert any("back pain" in pat for pat in patterns)
            assert any("3 of last 7" in pat for pat in patterns)
        finally:
            p.stop()

    def test_pain_below_threshold_not_reported(self):
        today = datetime.now().strftime("%Y-%m-%d")
        rows = [
            make_health_row(category="pain", description="headache",
                          d=date.fromisoformat(today)),
            make_health_row(category="pain", description="headache",
                          d=date.fromisoformat(today)),  # same day
        ]
        mc, p = self._make_entries(rows)
        try:
            patterns = health_store.get_patterns(days=7)
            # Only 1 unique day, threshold is 3
            assert not any("headache" in pat and "reported" in pat
                          for pat in patterns)
        finally:
            p.stop()

    def test_sleep_average(self):
        today = date.today()
        rows = [
            make_health_row(category="sleep", description="slept",
                          sleep_hours=7.0, d=today),
            make_health_row(category="sleep", description="slept",
                          sleep_hours=6.0, d=today - timedelta(days=1)),
            make_health_row(category="sleep", description="slept",
                          sleep_hours=5.0, d=today - timedelta(days=2)),
        ]
        mc, p = self._make_entries(rows)
        try:
            patterns = health_store.get_patterns(days=7)
            assert any("average sleep" in pat for pat in patterns)
            assert any("6.0" in pat for pat in patterns)
        finally:
            p.stop()

    def test_low_sleep_warning(self):
        today = date.today()
        rows = [
            make_health_row(category="sleep", description="slept",
                          sleep_hours=4.0, d=today),
            make_health_row(category="sleep", description="slept",
                          sleep_hours=5.0, d=today - timedelta(days=1)),
        ]
        mc, p = self._make_entries(rows)
        try:
            patterns = health_store.get_patterns(days=7)
            assert any("below 6 hours" in pat for pat in patterns)
        finally:
            p.stop()

    def test_meal_logging_count(self):
        today = date.today()
        rows = [
            make_health_row(category="meal", description="chicken",
                          d=today, meal_type="lunch"),
            make_health_row(category="meal", description="oatmeal",
                          d=today - timedelta(days=1), meal_type="breakfast"),
        ]
        mc, p = self._make_entries(rows)
        try:
            patterns = health_store.get_patterns(days=7)
            assert any("meals logged" in pat for pat in patterns)
        finally:
            p.stop()

    def test_fish_detection(self):
        today = date.today()
        rows = [
            make_health_row(category="meal", description="salmon fillet",
                          d=today, meal_type="dinner"),
        ]
        mc, p = self._make_entries(rows)
        try:
            patterns = health_store.get_patterns(days=7)
            assert any("fish" in pat.lower() or "omega" in pat.lower()
                       for pat in patterns)
        finally:
            p.stop()

    def test_no_fish_warning(self):
        today = date.today()
        rows = [
            make_health_row(category="meal", description="chicken salad",
                          d=today, meal_type="lunch"),
        ]
        mc, p = self._make_entries(rows)
        try:
            patterns = health_store.get_patterns(days=7)
            assert any("no fish" in pat.lower() for pat in patterns)
        finally:
            p.stop()

    def test_empty_entries(self):
        mc, p = self._make_entries([])
        try:
            patterns = health_store.get_patterns(days=7)
            assert patterns == []
        finally:
            p.stop()


class TestAddEntry:
    def test_full_entry(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_health_row()
        try:
            result = health_store.add_entry(
                entry_date="2026-03-20", category="pain",
                description="back pain", severity=5,
            )
            assert result["category"] == "pain"
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO health_entries" in sql
        finally:
            p.stop()

    def test_meal_entry(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_health_row(
            category="meal", description="grilled chicken",
            meal_type="lunch", severity=None,
        )
        try:
            result = health_store.add_entry(
                entry_date="2026-03-20", category="meal",
                description="grilled chicken", meal_type="lunch",
            )
            assert result["meal_type"] == "lunch"
        finally:
            p.stop()

    def test_sleep_entry(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_health_row(
            category="sleep", sleep_hours=7.5, severity=None,
        )
        try:
            result = health_store.add_entry(
                entry_date="2026-03-20", category="sleep",
                description="slept well", sleep_hours=7.5,
            )
            assert result["sleep_hours"] == 7.5
        finally:
            p.stop()


class TestDeleteEntry:
    def test_success(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 1
        try:
            assert health_store.delete_entry("hlt12345") is True
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 0
        try:
            assert health_store.delete_entry("bad") is False
        finally:
            p.stop()
