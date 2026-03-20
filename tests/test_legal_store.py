"""Tests for legal_store.py — legal case log."""

from datetime import date, datetime
from unittest.mock import patch, MagicMock

import legal_store
from helpers import make_legal_row


def _patch_db():
    mock_conn = MagicMock()
    patcher = patch("legal_store.db.get_conn")
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


class TestGetEntries:
    def test_no_filters(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [make_legal_row()]
        try:
            entries = legal_store.get_entries()
            assert len(entries) == 1
            assert entries[0]["entry_type"] == "court_date"
        finally:
            p.stop()

    def test_with_limit(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            legal_store.get_entries(limit=5)
            sql = mc.execute.call_args[0][0]
            assert "LIMIT %s" in sql
        finally:
            p.stop()

    def test_with_entry_type(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            legal_store.get_entries(entry_type="filing")
            sql = mc.execute.call_args[0][0]
            assert "entry_type = %s" in sql
        finally:
            p.stop()


class TestGetUpcomingDates:
    def test_returns_future_court_dates(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            make_legal_row(d=date(2026, 4, 1), entry_type="court_date"),
        ]
        try:
            upcoming = legal_store.get_upcoming_dates()
            assert len(upcoming) == 1
            sql = mc.execute.call_args[0][0]
            assert "court_date" in sql
            assert "deadline" in sql
            assert "date >= %s" in sql
        finally:
            p.stop()

    def test_empty(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert legal_store.get_upcoming_dates() == []
        finally:
            p.stop()


class TestAddEntry:
    def test_with_contacts(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_legal_row(
            contacts=["Judge Smith", "Attorney Jones"],
        )
        try:
            result = legal_store.add_entry(
                entry_date="2026-03-18", entry_type="court_date",
                description="Hearing", contacts=["Judge Smith", "Attorney Jones"],
            )
            assert result["contacts"] == ["Judge Smith", "Attorney Jones"]
        finally:
            p.stop()

    def test_without_contacts(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_legal_row()
        try:
            result = legal_store.add_entry(
                entry_date="2026-03-18", entry_type="note",
                description="Filed motion",
            )
            # Should pass empty list when contacts=None
            params = mc.execute.call_args[0][1]
            assert params[4] == []
        finally:
            p.stop()


class TestDeleteEntry:
    def test_success(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 1
        try:
            assert legal_store.delete_entry("leg12345") is True
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 0
        try:
            assert legal_store.delete_entry("bad") is False
        finally:
            p.stop()
