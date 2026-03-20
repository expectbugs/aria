"""Integration tests for legal_store — real SQL against PostgreSQL."""

from datetime import datetime, timedelta

import legal_store


class TestLegalRoundtrip:
    def test_add_with_contacts(self):
        entry = legal_store.add_entry(
            entry_date="2026-03-18", entry_type="court_date",
            description="Hearing", contacts=["Judge Smith", "Attorney Jones"],
        )
        assert entry["entry_type"] == "court_date"
        assert entry["contacts"] == ["Judge Smith", "Attorney Jones"]

    def test_add_without_contacts(self):
        entry = legal_store.add_entry(
            entry_date="2026-03-18", entry_type="note",
            description="Filed motion",
        )
        assert entry["contacts"] == []

    def test_filter_by_entry_type(self):
        legal_store.add_entry("2026-03-18", "court_date", "Hearing")
        legal_store.add_entry("2026-03-19", "filing", "Motion filed")

        filings = legal_store.get_entries(entry_type="filing")
        assert len(filings) == 1
        assert filings[0]["entry_type"] == "filing"

    def test_limit(self):
        for i in range(5):
            legal_store.add_entry(f"2026-03-{10+i:02d}", "note", f"Note {i}")
        assert len(legal_store.get_entries(limit=2)) == 2

    def test_get_upcoming_dates(self):
        future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        past = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

        legal_store.add_entry(future, "court_date", "Future hearing")
        legal_store.add_entry(past, "court_date", "Past hearing")
        legal_store.add_entry(future, "note", "Future note")  # not court/deadline

        upcoming = legal_store.get_upcoming_dates()
        assert len(upcoming) == 1
        assert upcoming[0]["description"] == "Future hearing"

    def test_upcoming_includes_deadlines(self):
        future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        legal_store.add_entry(future, "deadline", "File response")
        upcoming = legal_store.get_upcoming_dates()
        assert len(upcoming) == 1

    def test_delete(self):
        entry = legal_store.add_entry("2026-03-18", "note", "Delete me")
        assert legal_store.delete_entry(entry["id"]) is True
        assert legal_store.get_entries() == []

    def test_text_array_column_roundtrip(self):
        """Verify PostgreSQL TEXT[] contacts column works correctly."""
        entry = legal_store.add_entry(
            "2026-03-20", "contact", "Meeting",
            contacts=["Person A", "Person B", "Person C"],
        )
        retrieved = legal_store.get_entries()
        assert retrieved[0]["contacts"] == ["Person A", "Person B", "Person C"]
