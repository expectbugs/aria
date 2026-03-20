"""Tests for vehicle_store.py — vehicle maintenance log."""

from datetime import date, datetime
from unittest.mock import patch, MagicMock

import vehicle_store
from helpers import make_vehicle_row


def _patch_db():
    mock_conn = MagicMock()
    patcher = patch("vehicle_store.db.get_conn")
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


class TestGetEntries:
    def test_returns_all_entries(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [make_vehicle_row()]
        try:
            entries = vehicle_store.get_entries()
            assert len(entries) == 1
            assert entries[0]["event_type"] == "oil_change"
            sql = mc.execute.call_args[0][0]
            assert "ORDER BY date DESC" in sql
        finally:
            p.stop()

    def test_with_limit(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            vehicle_store.get_entries(limit=5)
            sql = mc.execute.call_args[0][0]
            assert "LIMIT %s" in sql
            params = mc.execute.call_args[0][1]
            assert 5 in params
        finally:
            p.stop()

    def test_with_event_type_filter(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            vehicle_store.get_entries(event_type="oil_change")
            sql = mc.execute.call_args[0][0]
            assert "event_type = %s" in sql
        finally:
            p.stop()

    def test_with_both_filters(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            vehicle_store.get_entries(limit=3, event_type="brake_service")
            sql = mc.execute.call_args[0][0]
            assert "event_type = %s" in sql
            assert "LIMIT %s" in sql
        finally:
            p.stop()


class TestGetLatestByType:
    def test_returns_dict_keyed_by_type(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            make_vehicle_row(event_type="oil_change"),
            make_vehicle_row(id="veh67890", event_type="tire_rotation"),
        ]
        try:
            latest = vehicle_store.get_latest_by_type()
            assert "oil_change" in latest
            assert "tire_rotation" in latest
            sql = mc.execute.call_args[0][0]
            assert "DISTINCT ON (event_type)" in sql
        finally:
            p.stop()

    def test_empty(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert vehicle_store.get_latest_by_type() == {}
        finally:
            p.stop()


class TestAddEntry:
    def test_creates_entry(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_vehicle_row()
        try:
            result = vehicle_store.add_entry(
                event_date="2026-03-15", event_type="oil_change",
                description="Full synthetic", mileage=145000, cost=45.99,
            )
            assert result["id"] == "veh12345"
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO vehicle_entries" in sql
            assert "RETURNING *" in sql
        finally:
            p.stop()

    def test_optional_fields(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_vehicle_row(
            mileage=None, cost=None,
        )
        try:
            result = vehicle_store.add_entry(
                event_date="2026-03-15", event_type="inspection",
                description="Annual inspection",
            )
            assert result is not None
        finally:
            p.stop()


class TestDeleteEntry:
    def test_success(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 1
        try:
            assert vehicle_store.delete_entry("veh12345") is True
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 0
        try:
            assert vehicle_store.delete_entry("bad") is False
        finally:
            p.stop()
