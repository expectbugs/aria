"""Tests for db.py — connection pool and row serialization."""

from datetime import date, time, datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import db


class TestSerializeRow:
    """db.serialize_row converts DB types to JSON-safe strings."""

    def test_datetime_naive(self):
        row = {"ts": datetime(2026, 3, 20, 14, 30, 0)}
        result = db.serialize_row(row)
        assert result["ts"] == "2026-03-20T14:30:00"

    def test_datetime_tz_aware_converts_to_naive(self):
        utc = timezone.utc
        row = {"ts": datetime(2026, 3, 20, 19, 0, 0, tzinfo=utc)}
        result = db.serialize_row(row)
        # Should be local time, no timezone info in string
        assert "+" not in result["ts"]
        assert "T" in result["ts"]

    def test_date_to_isoformat(self):
        row = {"d": date(2026, 3, 20)}
        result = db.serialize_row(row)
        assert result["d"] == "2026-03-20"

    def test_time_to_hhmm(self):
        row = {"t": time(14, 30)}
        result = db.serialize_row(row)
        assert result["t"] == "14:30"

    def test_time_midnight(self):
        row = {"t": time(0, 0)}
        result = db.serialize_row(row)
        assert result["t"] == "00:00"

    def test_plain_values_passthrough(self):
        row = {"name": "test", "count": 42, "active": True, "data": None}
        result = db.serialize_row(row)
        assert result == row

    def test_mixed_types(self):
        row = {
            "id": "abc123",
            "date": date(2026, 3, 20),
            "time": time(9, 15),
            "created": datetime(2026, 3, 19, 10, 0, 0),
            "notes": None,
            "count": 3,
        }
        result = db.serialize_row(row)
        assert result["id"] == "abc123"
        assert result["date"] == "2026-03-20"
        assert result["time"] == "09:15"
        assert result["created"] == "2026-03-19T10:00:00"
        assert result["notes"] is None
        assert result["count"] == 3

    def test_empty_row(self):
        assert db.serialize_row({}) == {}



class TestClose:
    def test_close_resets_pool(self):
        mock_pool = MagicMock()
        db._pool = mock_pool
        db.close()
        mock_pool.close.assert_called_once()
        assert db._pool is None

    def test_close_noop_when_no_pool(self):
        db._pool = None
        db.close()  # should not raise
