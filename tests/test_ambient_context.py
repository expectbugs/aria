"""Tests for Phase 6 ambient context injection and query.py subcommands."""

import asyncio
from datetime import datetime, date
from unittest.mock import patch, MagicMock

import context
import query


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tier 1: Always-inject (commitments + ambient status)
# ---------------------------------------------------------------------------

class TestAlwaysContextCommitments:
    @patch("commitment_store.get_overdue", return_value=[])
    @patch("commitment_store.get_open")
    @patch("ambient_store.get_today_count", return_value=0)
    def test_open_commitments_injected(self, mock_count, mock_open, mock_overdue):
        mock_open.return_value = [
            {"who": "self", "what": "Finish proposal", "to_whom": "Mike",
             "due_date": "2026-03-22", "status": "open"},
        ]
        ctx = context.gather_always_context()
        assert "Finish proposal" in ctx
        assert "Mike" in ctx

    @patch("commitment_store.get_overdue")
    @patch("commitment_store.get_open", return_value=[])
    @patch("ambient_store.get_today_count", return_value=0)
    def test_overdue_commitments_highlighted(self, mock_count, mock_open, mock_overdue):
        mock_overdue.return_value = [
            {"who": "self", "what": "Call inspector", "due_date": "2026-03-20"},
        ]
        ctx = context.gather_always_context()
        assert "OVERDUE" in ctx
        assert "Call inspector" in ctx

    @patch("commitment_store.get_overdue", return_value=[])
    @patch("commitment_store.get_open", return_value=[])
    @patch("ambient_store.get_today_duration", return_value=1800.0)
    @patch("ambient_store.get_today_count", return_value=47)
    def test_ambient_status_line(self, mock_count, mock_dur, mock_open, mock_overdue):
        ctx = context.gather_always_context()
        assert "47 segments" in ctx
        assert "30 min" in ctx


# ---------------------------------------------------------------------------
# Tier 2: Keyword-triggered ambient context
# ---------------------------------------------------------------------------

class TestKeywordTriggeredAmbient:
    @patch("commitment_store.get_recent", return_value=[])
    @patch("ambient_store.get_recent")
    def test_recall_keywords_trigger(self, mock_recent, mock_commit):
        mock_recent.return_value = [
            {"started_at": "2026-03-30T14:00:00", "text": "test proposal text",
             "quality_text": None},
        ]

        ctx = _run(context.build_request_context("what did I say about the proposal"))
        assert "test proposal text" in ctx

    @patch("commitment_store.get_recent", return_value=[])
    @patch("ambient_store.get_recent", return_value=[])
    @patch("person_store.get")
    @patch("person_store.get_names")
    def test_person_name_triggers(self, mock_names, mock_get, mock_recent, mock_commit):
        mock_names.return_value = ["Mike"]
        mock_get.return_value = {
            "name": "Mike", "relationship": "coworker",
            "organization": "Banker Wire", "mention_count": 12,
            "last_mentioned": "2026-03-30T14:00:00",
        }

        ctx = _run(context.build_request_context("tell me about Mike"))
        assert "Mike" in ctx
        assert "coworker" in ctx


# ---------------------------------------------------------------------------
# Query.py subcommands
# ---------------------------------------------------------------------------

class TestQueryAmbient:
    @patch("query.ambient_store")
    def test_ambient_search(self, mock_store):
        mock_store.search.return_value = [
            {"started_at": "2026-03-30T14:00:00", "text": "proposal discussion",
             "quality_text": None, "duration_s": 5.0},
        ]
        output = query.cmd_ambient(MagicMock(search="proposal", days=7, limit=20))
        assert "proposal discussion" in output

    @patch("query.ambient_store")
    def test_ambient_recent(self, mock_store):
        mock_store.get_recent.return_value = []
        output = query.cmd_ambient(MagicMock(search=None, days=1, limit=20))
        assert "No ambient transcripts" in output


class TestQueryCommitments:
    @patch("query.commitment_store")
    def test_open_commitments(self, mock_store):
        mock_store.get_open.return_value = [
            {"id": 1, "who": "self", "what": "Finish proposal",
             "to_whom": "Mike", "due_date": "2026-03-22", "status": "open"},
        ]
        output = query.cmd_commitments(MagicMock(status="open", person=None, days=30))
        assert "Finish proposal" in output
        assert "Mike" in output

    @patch("query.commitment_store")
    def test_commitments_by_person(self, mock_store):
        mock_store.get_by_person.return_value = []
        output = query.cmd_commitments(MagicMock(status=None, person="Mike", days=30))
        assert "No commitments" in output


class TestQueryPeople:
    @patch("person_store.get")
    def test_people_by_name(self, mock_get):
        mock_get.return_value = {
            "name": "Mike", "relationship": "coworker",
            "organization": "Banker Wire", "mention_count": 12,
            "last_mentioned": "2026-03-30T14:00:00", "notes": None,
            "aliases": [],
        }
        output = query.cmd_people(MagicMock(name="Mike", limit=20))
        assert "Mike" in output
        assert "coworker" in output

    @patch("person_store.get", return_value=None)
    @patch("person_store.search", return_value=[])
    @patch("person_store.get_all", return_value=[])
    def test_people_not_found(self, mock_all, mock_search, mock_get):
        output = query.cmd_people(MagicMock(name="Nobody", limit=20))
        assert "No person profiles" in output

    @patch("person_store.get_all", return_value=[])
    def test_people_all_empty(self, mock_all):
        output = query.cmd_people(MagicMock(name=None, limit=20))
        assert "No person profiles" in output


class TestQueryRecall:
    @patch("qdrant_store.search")
    def test_recall_search(self, mock_search):
        mock_search.return_value = [
            {"source_table": "ambient_transcripts", "source_id": 1,
             "text": "Budget meeting on Thursday", "timestamp": "2026-03-30T14:00:00",
             "category": "transcript", "score": 0.89},
        ]
        output = query.cmd_recall(MagicMock(query=["budget", "meeting"], days=30, limit=5))
        assert "Budget meeting" in output
        assert "0.89" in output

    def test_recall_empty_query(self):
        output = query.cmd_recall(MagicMock(query=[], days=30, limit=5))
        assert "Usage" in output


class TestQueryAmbientConversations:
    @patch("query.ambient_store")
    def test_conversations(self, mock_store):
        mock_store.get_conversations.return_value = [
            {"started_at": "2026-03-30T14:00:00", "duration_s": 300,
             "speakers": ["owner", "Mike"], "summary": "Discussed proposal",
             "title": None},
        ]
        output = query.cmd_ambient_conversations(MagicMock(days=1, limit=20))
        assert "Discussed proposal" in output
        assert "Mike" in output


# ---------------------------------------------------------------------------
# System prompt includes ambient docs
# ---------------------------------------------------------------------------

class TestSystemPromptAmbient:
    def test_ambient_section_present(self):
        import system_prompt
        prompt = system_prompt.build_primary_prompt()
        assert "AMBIENT AUDIO" in prompt
        assert "query.py ambient" in prompt
        assert "query.py recall" in prompt
        assert "query.py commitments" in prompt
        assert "query.py people" in prompt
        assert "wake word" in prompt.lower() or "ARIA" in prompt
