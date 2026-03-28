"""Tests for gmail_store.py — email cache, search, classification."""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import gmail_store


def _mock_conn():
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


class TestParseEmailDate:
    def test_standard_rfc2822(self):
        dt = gmail_store._parse_email_date("Sat, 28 Mar 2026 20:19:01 +0000")
        assert dt is not None
        assert dt.year == 2026

    def test_rfc2822_with_comment(self):
        dt = gmail_store._parse_email_date("Sat, 28 Mar 2026 20:19:01 +0000 (UTC)")
        assert dt is not None

    def test_empty(self):
        assert gmail_store._parse_email_date("") is None

    def test_garbage(self):
        assert gmail_store._parse_email_date("not a date") is None


class TestExtractFromParts:
    def test_name_and_email(self):
        addr, name = gmail_store._extract_from_parts("Alice Smith <alice@example.com>")
        assert addr == "alice@example.com"
        assert name == "Alice Smith"

    def test_email_only(self):
        addr, name = gmail_store._extract_from_parts("alice@example.com")
        assert addr == "alice@example.com"
        assert name == ""

    def test_quoted_name(self):
        addr, name = gmail_store._extract_from_parts('"Bob Jones" <bob@test.com>')
        assert addr == "bob@test.com"
        assert name == "Bob Jones"


class TestDetectGmailCategory:
    def test_promotions(self):
        assert gmail_store._detect_gmail_category(["CATEGORY_PROMOTIONS"]) == "Promotions"

    def test_primary(self):
        assert gmail_store._detect_gmail_category(["INBOX", "CATEGORY_PERSONAL"]) == "Primary"

    def test_no_category(self):
        assert gmail_store._detect_gmail_category(["INBOX", "UNREAD"]) is None


class TestHasAttachments:
    def test_with_attachment(self):
        msg = {"payload": {"parts": [{"filename": "doc.pdf", "body": {}}]}}
        assert gmail_store._has_attachments(msg) is True

    def test_without_attachment(self):
        msg = {"payload": {"parts": [{"mimeType": "text/plain", "body": {}}]}}
        assert gmail_store._has_attachments(msg) is False

    def test_empty(self):
        assert gmail_store._has_attachments({}) is False


class TestSaveEmail:
    def test_upsert(self):
        conn = _mock_conn()
        msg = {
            "id": "msg1",
            "threadId": "t1",
            "snippet": "Hello",
            "labelIds": ["INBOX", "UNREAD"],
            "body": "Full email body text",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "To", "value": "bob@test.com"},
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "Date", "value": "Sat, 28 Mar 2026 10:00:00 +0000"},
                ],
                "parts": [],
            },
        }
        with patch("gmail_store.db.get_conn", return_value=conn):
            gmail_store.save_email(msg)
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO email_cache" in sql

    def test_skips_no_id(self):
        conn = _mock_conn()
        with patch("gmail_store.db.get_conn", return_value=conn):
            gmail_store.save_email({})
        conn.execute.assert_not_called()


class TestSearchEmails:
    def test_returns_results(self):
        mock_rows = [{"id": "m1", "subject": "Test", "timestamp": datetime.now()}]
        conn = _mock_conn()
        conn.execute.return_value.fetchall.return_value = mock_rows

        with patch("gmail_store.db.get_conn", return_value=conn), \
             patch("gmail_store.db.serialize_row",
                   side_effect=lambda r: {k: str(v) for k, v in r.items()}):
            results = gmail_store.search_emails("test")
        assert len(results) == 1


class TestGetUnclassified:
    def test_returns_unclassified(self):
        mock_rows = [{"id": "m1", "subject": "Unclassified"}]
        conn = _mock_conn()
        conn.execute.return_value.fetchall.return_value = mock_rows

        with patch("gmail_store.db.get_conn", return_value=conn), \
             patch("gmail_store.db.serialize_row",
                   side_effect=lambda r: {k: str(v) for k, v in r.items()}):
            results = gmail_store.get_unclassified()
        assert len(results) == 1


class TestClassificationStorage:
    def test_save_classification(self):
        conn = _mock_conn()
        with patch("gmail_store.db.get_conn", return_value=conn):
            gmail_store.save_classification("m1", "tier1_hard", "important", 1.0, "test")
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO email_classifications" in sql

    def test_get_classification(self):
        mock_row = {"email_id": "m1", "classification": "important"}
        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = mock_row

        with patch("gmail_store.db.get_conn", return_value=conn), \
             patch("gmail_store.db.serialize_row", return_value=mock_row):
            result = gmail_store.get_classification("m1")
        assert result["classification"] == "important"

    def test_get_classification_none(self):
        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None

        with patch("gmail_store.db.get_conn", return_value=conn):
            result = gmail_store.get_classification("m1")
        assert result is None


class TestEmailContext:
    def test_context_with_unread(self):
        with patch("gmail_store.get_unread_important",
                   return_value=[{"from_name": "Alice", "subject": "Urgent", "from_address": "a@b.com"}]), \
             patch("gmail_store.get_email_count",
                   return_value={"important": 1, "junk": 5}):
            ctx = gmail_store.get_email_context("2026-03-28")
        assert "Alice" in ctx
        assert "Urgent" in ctx

    def test_context_empty(self):
        with patch("gmail_store.get_unread_important", return_value=[]), \
             patch("gmail_store.get_email_count", return_value={}):
            ctx = gmail_store.get_email_context("2026-03-28")
        assert ctx == ""

    def test_briefing_context(self):
        with patch("gmail_store.get_email_count",
                   return_value={"important": 2, "routine": 10, "junk": 20}), \
             patch("gmail_store.get_unread_important",
                   return_value=[{"from_name": "Bob", "subject": "Meeting", "from_address": "b@c.com"}]):
            ctx = gmail_store.get_briefing_context()
        assert "Overnight email" in ctx
        assert "Bob" in ctx
