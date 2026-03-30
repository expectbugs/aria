"""Tests for junk email auto-archival system.

Covers: gmail_batch_modify, gmail_modify_labels, archive_emails,
process_junk_archival, archive_junk.py query builder.
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


def _mock_conn():
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value.fetchone.return_value = None
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.rowcount = 0
    return conn


# ---------------------------------------------------------------------------
# gmail_store.archive_emails
# ---------------------------------------------------------------------------

class TestArchiveEmails:
    def test_archive_removes_inbox(self):
        import gmail_store
        conn = _mock_conn()
        conn.execute.return_value.rowcount = 3
        with patch("gmail_store.db.get_conn", return_value=conn):
            count = gmail_store.archive_emails(["m1", "m2", "m3"])
        assert count == 3
        sql = conn.execute.call_args[0][0]
        assert "array_remove" in sql
        assert "INBOX" in sql

    def test_archive_empty_list(self):
        import gmail_store
        assert gmail_store.archive_emails([]) == 0

    def test_archive_db_error(self):
        import gmail_store
        conn = _mock_conn()
        conn.execute.side_effect = Exception("DB down")
        with patch("gmail_store.db.get_conn", return_value=conn):
            count = gmail_store.archive_emails(["m1"])
        assert count == 0


# ---------------------------------------------------------------------------
# google_client.gmail_modify_labels
# ---------------------------------------------------------------------------

class TestGmailModifyLabels:
    @pytest.mark.asyncio
    async def test_modify_labels_add_remove(self):
        import google_client
        client = google_client.GoogleClient.__new__(google_client.GoogleClient)
        client._post = AsyncMock(return_value={"id": "msg1"})

        result = await client.gmail_modify_labels(
            "msg1", add_labels=["STARRED"], remove_labels=["INBOX"])
        client._post.assert_called_once()
        call_args = client._post.call_args
        body = call_args[1].get("json_body") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["json_body"]
        assert body["addLabelIds"] == ["STARRED"]
        assert body["removeLabelIds"] == ["INBOX"]

    @pytest.mark.asyncio
    async def test_modify_labels_remove_only(self):
        import google_client
        client = google_client.GoogleClient.__new__(google_client.GoogleClient)
        client._post = AsyncMock(return_value={})

        await client.gmail_modify_labels("msg1", remove_labels=["INBOX"])
        body = client._post.call_args[1].get("json_body", {})
        assert "addLabelIds" not in body
        assert body["removeLabelIds"] == ["INBOX"]


# ---------------------------------------------------------------------------
# google_client.gmail_batch_modify
# ---------------------------------------------------------------------------

class TestGmailBatchModify:
    @pytest.mark.asyncio
    async def test_batch_modify_single_chunk(self):
        import google_client
        client = google_client.GoogleClient.__new__(google_client.GoogleClient)
        client._post = AsyncMock(return_value={})

        ids = [f"msg{i}" for i in range(50)]
        count = await client.gmail_batch_modify(ids, remove_labels=["INBOX"])
        assert count == 50
        assert client._post.call_count == 1
        body = client._post.call_args[1]["json_body"]
        assert len(body["ids"]) == 50

    @pytest.mark.asyncio
    async def test_batch_modify_multiple_chunks(self):
        import google_client
        client = google_client.GoogleClient.__new__(google_client.GoogleClient)
        client._post = AsyncMock(return_value={})

        ids = [f"msg{i}" for i in range(2500)]
        count = await client.gmail_batch_modify(ids, remove_labels=["INBOX"])
        assert count == 2500
        # 2500 / 1000 = 3 calls
        assert client._post.call_count == 3

    @pytest.mark.asyncio
    async def test_batch_modify_empty(self):
        import google_client
        client = google_client.GoogleClient.__new__(google_client.GoogleClient)
        client._post = AsyncMock()

        count = await client.gmail_batch_modify([], remove_labels=["INBOX"])
        assert count == 0
        client._post.assert_not_called()


# ---------------------------------------------------------------------------
# process_junk_archival (tick.py)
# ---------------------------------------------------------------------------

class TestProcessJunkArchival:
    def test_archives_tier1_junk_only(self):
        from tick import process_junk_archival
        conn = _mock_conn()
        conn.execute.return_value.fetchall.return_value = [
            {"id": "m1"}, {"id": "m2"},
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"archived": 2}

        with patch("tick.db.get_conn", return_value=conn), \
             patch("httpx.post", return_value=mock_resp) as mock_post, \
             patch("tick.config") as mock_cfg:
            mock_cfg.JUNK_AUTO_ARCHIVE = True
            process_junk_archival()

        # Verify the DB query filters for tier1_hard junk in INBOX
        sql = conn.execute.call_args[0][0]
        assert "tier1_hard" in sql
        assert "INBOX" in sql
        assert "junk" in sql

        # Verify archive endpoint was called
        mock_post.assert_called_once()
        call_body = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
        assert call_body["message_ids"] == ["m1", "m2"]

    def test_disabled_by_config(self):
        from tick import process_junk_archival
        with patch("tick.config") as mock_cfg:
            mock_cfg.JUNK_AUTO_ARCHIVE = False
            with patch("tick.db.get_conn") as mock_db:
                process_junk_archival()
                mock_db.assert_not_called()

    def test_no_junk_no_action(self):
        from tick import process_junk_archival
        conn = _mock_conn()
        conn.execute.return_value.fetchall.return_value = []

        with patch("tick.db.get_conn", return_value=conn), \
             patch("httpx.post") as mock_post, \
             patch("tick.config") as mock_cfg:
            mock_cfg.JUNK_AUTO_ARCHIVE = True
            process_junk_archival()

        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# archive_junk.py query builder
# ---------------------------------------------------------------------------

class TestBuildSearchQueries:
    def test_builds_from_junk_domains(self):
        from archive_junk import build_search_queries
        rules = {
            "always_junk": {
                "senders": ["spam@junk.com"],
                "domains": ["doordash.com", "linkedin.com"],
            },
            "content_overrides": [],
        }
        queries = build_search_queries(rules)
        assert len(queries) >= 1
        assert "from:@doordash.com" in queries[0]
        assert "from:@linkedin.com" in queries[0]
        assert "from:spam@junk.com" in queries[0]
        assert "in:inbox" in queries[0]

    def test_splits_large_domain_lists(self):
        from archive_junk import build_search_queries
        rules = {
            "always_junk": {
                "senders": [],
                "domains": [f"domain{i}.com" for i in range(100)],
            },
            "content_overrides": [],
        }
        queries = build_search_queries(rules)
        assert len(queries) >= 3  # 100 / 40 = 3 chunks

    def test_includes_junk_content_overrides(self):
        from archive_junk import build_search_queries
        rules = {
            "always_junk": {"senders": [], "domains": []},
            "content_overrides": [
                {"sender_pattern": "lumosity", "classification": "junk"},
                {"sender_pattern": "paypal", "classification": "important"},  # NOT junk
            ],
        }
        queries = build_search_queries(rules)
        assert len(queries) == 1
        assert "lumosity" in queries[0]
        assert "paypal" not in queries[0]

    def test_empty_rules(self):
        from archive_junk import build_search_queries
        rules = {"always_junk": {"senders": [], "domains": []}, "content_overrides": []}
        queries = build_search_queries(rules)
        assert queries == []
