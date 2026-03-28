"""Tests for google_store.py — Google Calendar + Gmail data store."""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import google_store


def _mock_conn():
    """Create a mock connection with execute().fetchall()/fetchone()."""
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


class TestSaveCalendarEvents:
    def test_upsert_timed_events(self):
        conn = _mock_conn()
        events = [{
            "id": "evt1",
            "summary": "Team Meeting",
            "start": {"dateTime": "2026-03-29T10:00:00-05:00"},
            "end": {"dateTime": "2026-03-29T11:00:00-05:00"},
            "location": "Room A",
            "status": "confirmed",
        }]

        with patch("google_store.db.get_conn", return_value=conn):
            google_store.save_calendar_events(events)
        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        assert "INSERT INTO google_calendar_events" in args[0]
        # event_id is first param
        assert args[1][0] == "evt1"

    def test_upsert_all_day_events(self):
        conn = _mock_conn()
        events = [{
            "id": "evt2",
            "summary": "Birthday",
            "start": {"date": "2026-04-01"},
            "end": {"date": "2026-04-02"},
            "status": "confirmed",
        }]

        with patch("google_store.db.get_conn", return_value=conn):
            google_store.save_calendar_events(events)
        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        # start_time should be the date string
        assert args[1][3] == "2026-04-01"

    def test_skips_events_without_id(self):
        conn = _mock_conn()
        events = [
            {"summary": "No ID event", "start": {"date": "2026-04-01"}},
            {"id": "", "summary": "Empty ID", "start": {"date": "2026-04-01"}},
        ]

        with patch("google_store.db.get_conn", return_value=conn):
            google_store.save_calendar_events(events)
        conn.execute.assert_not_called()

    def test_multiple_events(self):
        conn = _mock_conn()
        events = [
            {"id": "e1", "summary": "A", "start": {"date": "2026-04-01"}, "status": "confirmed"},
            {"id": "e2", "summary": "B", "start": {"date": "2026-04-02"}, "status": "confirmed"},
        ]

        with patch("google_store.db.get_conn", return_value=conn):
            google_store.save_calendar_events(events)
        assert conn.execute.call_count == 2


class TestSaveGmailMessages:
    def test_upsert_messages(self):
        conn = _mock_conn()
        messages = [{
            "id": "msg1",
            "threadId": "t1",
            "snippet": "Hello world",
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "Date", "value": "Mon, 28 Mar 2026 10:00:00 -0500"},
                ]
            },
        }]

        with patch("google_store.db.get_conn", return_value=conn):
            google_store.save_gmail_messages(messages)
        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        assert "INSERT INTO google_gmail_messages" in args[0]
        # message_id
        assert args[1][0] == "msg1"
        # subject
        assert args[1][2] == "Test Subject"
        # sender
        assert args[1][3] == "alice@example.com"

    def test_skips_messages_without_id(self):
        conn = _mock_conn()
        messages = [{"snippet": "No ID message"}]

        with patch("google_store.db.get_conn", return_value=conn):
            google_store.save_gmail_messages(messages)
        conn.execute.assert_not_called()

    def test_handles_missing_headers(self):
        conn = _mock_conn()
        messages = [{
            "id": "msg2",
            "threadId": "t2",
            "snippet": "Minimal",
            "labelIds": [],
            "payload": {"headers": []},
        }]

        with patch("google_store.db.get_conn", return_value=conn):
            google_store.save_gmail_messages(messages)
        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        # subject and sender should be empty strings
        assert args[1][2] == ""
        assert args[1][3] == ""


class TestParseEmailDate:
    def test_standard_rfc2822(self):
        dt = google_store._parse_email_date("Sat, 28 Mar 2026 20:19:01 +0000")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 28

    def test_rfc2822_with_comment(self):
        """Gmail often appends (UTC) or timezone name in parens."""
        dt = google_store._parse_email_date("Sat, 28 Mar 2026 20:19:01 +0000 (UTC)")
        assert dt is not None
        assert dt.year == 2026

    def test_empty_string(self):
        assert google_store._parse_email_date("") is None

    def test_garbage(self):
        assert google_store._parse_email_date("not a date") is None


class TestExtractHeader:
    def test_finds_header(self):
        msg = {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Found It"},
                    {"name": "From", "value": "test@test.com"},
                ]
            }
        }
        assert google_store._extract_header(msg, "Subject") == "Found It"
        assert google_store._extract_header(msg, "From") == "test@test.com"

    def test_case_insensitive(self):
        msg = {
            "payload": {
                "headers": [
                    {"name": "SUBJECT", "value": "Upper Case"},
                ]
            }
        }
        assert google_store._extract_header(msg, "subject") == "Upper Case"

    def test_missing_header(self):
        msg = {"payload": {"headers": []}}
        assert google_store._extract_header(msg, "Subject") == ""

    def test_no_payload(self):
        assert google_store._extract_header({}, "Subject") == ""


class TestGetUpcomingEvents:
    def test_returns_serialized_rows(self):
        mock_rows = [
            {"event_id": "e1", "summary": "Meeting",
             "start_time": datetime(2026, 3, 29, 10, 0),
             "synced_at": datetime(2026, 3, 28, 12, 0)},
        ]
        conn = _mock_conn()
        conn.execute.return_value.fetchall.return_value = mock_rows

        with patch("google_store.db.get_conn", return_value=conn), \
             patch("google_store.db.serialize_row",
                   side_effect=lambda r: {k: str(v) for k, v in r.items()}):
            results = google_store.get_upcoming_events(hours=48)
        assert len(results) == 1
        assert results[0]["event_id"] == "e1"


class TestGetRecentMessages:
    def test_returns_serialized_rows(self):
        mock_rows = [
            {"message_id": "m1", "subject": "Hello",
             "date": datetime(2026, 3, 28, 10, 0),
             "synced_at": datetime(2026, 3, 28, 12, 0)},
        ]
        conn = _mock_conn()
        conn.execute.return_value.fetchall.return_value = mock_rows

        with patch("google_store.db.get_conn", return_value=conn), \
             patch("google_store.db.serialize_row",
                   side_effect=lambda r: {k: str(v) for k, v in r.items()}):
            results = google_store.get_recent_messages(hours=24)
        assert len(results) == 1
        assert results[0]["message_id"] == "m1"


class TestGetUnreadCount:
    def test_returns_count(self):
        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = {"cnt": 5}

        with patch("google_store.db.get_conn", return_value=conn):
            count = google_store.get_unread_count()
        assert count == 5

    def test_returns_zero_when_no_results(self):
        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None

        with patch("google_store.db.get_conn", return_value=conn):
            count = google_store.get_unread_count()
        assert count == 0
