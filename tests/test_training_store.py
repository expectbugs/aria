"""Tests for training_store.py — tool traces, entity mentions, entity extraction.

SAFETY: All database access mocked. No real PostgreSQL connections.
"""

from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

import training_store


# ---------------------------------------------------------------------------
# log_tool_trace
# ---------------------------------------------------------------------------

class TestLogToolTrace:
    @patch("training_store.db.get_conn")
    def test_inserts_row(self, mock_get_conn):
        row = {"id": 1, "timestamp": datetime(2026, 3, 27),
               "tool_name": "query_health", "request_input": "test",
               "tool_input": "{}", "tool_output": "results",
               "was_correct": True, "correction": None}
        mc = MagicMock()
        mc.execute.return_value.fetchone.return_value = row
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.log_tool_trace("test", "query_health", "{}", "results")
        assert result is not None
        assert result["tool_name"] == "query_health"
        mc.execute.assert_called_once()

    @patch("training_store.db.get_conn", side_effect=Exception("DB down"))
    def test_db_error_returns_none(self, _):
        result = training_store.log_tool_trace("test", "query_health", "{}", "results")
        assert result is None

    @patch("training_store.db.get_conn")
    def test_none_row_returns_none(self, mock_get_conn):
        mc = MagicMock()
        mc.execute.return_value.fetchone.return_value = None
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.log_tool_trace("test", "query_health", "{}", "results")
        assert result is None


# ---------------------------------------------------------------------------
# log_entity_mention
# ---------------------------------------------------------------------------

class TestLogEntityMention:
    @patch("training_store.db.get_conn")
    def test_inserts_row(self, mock_get_conn):
        row = {"id": 1, "timestamp": datetime(2026, 3, 27),
               "source": "response", "entity_type": "person",
               "entity_value": "John Smith", "context_snippet": "saw John Smith",
               "source_id": "42"}
        mc = MagicMock()
        mc.execute.return_value.fetchone.return_value = row
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.log_entity_mention(
            "response", "person", "John Smith", "saw John Smith", "42"
        )
        assert result is not None
        assert result["entity_value"] == "John Smith"

    @patch("training_store.db.get_conn")
    def test_optional_fields_nullable(self, mock_get_conn):
        row = {"id": 2, "timestamp": datetime(2026, 3, 27),
               "source": "response", "entity_type": "place",
               "entity_value": "home", "context_snippet": None,
               "source_id": None}
        mc = MagicMock()
        mc.execute.return_value.fetchone.return_value = row
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.log_entity_mention("response", "place", "home")
        assert result is not None

    @patch("training_store.db.get_conn", side_effect=Exception("DB down"))
    def test_db_error_returns_none(self, _):
        result = training_store.log_entity_mention("response", "person", "Test")
        assert result is None


# ---------------------------------------------------------------------------
# log_interaction_quality
# ---------------------------------------------------------------------------

class TestLogInteractionQuality:
    @patch("training_store.db.get_conn")
    def test_inserts_row(self, mock_get_conn):
        row = {"id": 1, "timestamp": datetime(2026, 3, 27),
               "request_id": 42, "quality_signal": "correction",
               "details": "wrong date"}
        mc = MagicMock()
        mc.execute.return_value.fetchone.return_value = row
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.log_interaction_quality(42, "correction", "wrong date")
        assert result is not None
        assert result["quality_signal"] == "correction"

    @patch("training_store.db.get_conn")
    def test_null_request_id(self, mock_get_conn):
        row = {"id": 2, "timestamp": datetime(2026, 3, 27),
               "request_id": None, "quality_signal": "thank_you",
               "details": None}
        mc = MagicMock()
        mc.execute.return_value.fetchone.return_value = row
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.log_interaction_quality(None, "thank_you")
        assert result is not None


# ---------------------------------------------------------------------------
# get_tool_traces
# ---------------------------------------------------------------------------

class TestGetToolTraces:
    @patch("training_store.db.get_conn")
    def test_returns_list(self, mock_get_conn):
        rows = [{"id": 1, "timestamp": datetime(2026, 3, 27),
                 "tool_name": "query_health", "request_input": "test",
                 "tool_input": "{}", "tool_output": "ok",
                 "was_correct": True, "correction": None}]
        mc = MagicMock()
        mc.execute.return_value.fetchall.return_value = rows
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.get_tool_traces()
        assert len(result) == 1
        assert result[0]["tool_name"] == "query_health"

    @patch("training_store.db.get_conn")
    def test_empty_returns_empty(self, mock_get_conn):
        mc = MagicMock()
        mc.execute.return_value.fetchall.return_value = []
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.get_tool_traces(days=7)
        assert result == []

    @patch("training_store.db.get_conn", side_effect=Exception("DB down"))
    def test_db_error_returns_empty(self, _):
        result = training_store.get_tool_traces()
        assert result == []


# ---------------------------------------------------------------------------
# get_entity_mentions
# ---------------------------------------------------------------------------

class TestGetEntityMentions:
    @patch("training_store.db.get_conn")
    def test_returns_filtered(self, mock_get_conn):
        rows = [{"id": 1, "timestamp": datetime(2026, 3, 27),
                 "source": "response", "entity_type": "person",
                 "entity_value": "John", "context_snippet": None,
                 "source_id": None}]
        mc = MagicMock()
        mc.execute.return_value.fetchall.return_value = rows
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.get_entity_mentions(entity_type="person")
        assert len(result) == 1

    @patch("training_store.db.get_conn")
    def test_no_filter(self, mock_get_conn):
        mc = MagicMock()
        mc.execute.return_value.fetchall.return_value = []
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mc)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        result = training_store.get_entity_mentions()
        assert result == []


# ---------------------------------------------------------------------------
# extract_entities
# ---------------------------------------------------------------------------

class TestExtractEntities:
    def test_empty_text(self):
        assert training_store.extract_entities("", "response") == []

    def test_extracts_capitalized_names(self):
        results = training_store.extract_entities(
            "I talked to John Smith about the project.", "response"
        )
        persons = [r for r in results if r[0] == "person"]
        assert len(persons) == 1
        assert persons[0][1] == "John Smith"

    def test_filters_day_month_names(self):
        results = training_store.extract_entities(
            "Good Morning, today is Monday March 15.", "response"
        )
        persons = [r for r in results if r[0] == "person"]
        assert len(persons) == 0

    @patch("training_store.config")
    def test_extracts_known_places(self, mock_config):
        mock_config.KNOWN_PLACES = {"home": "123 Main St", "work": "456 Oak Ave"}
        results = training_store.extract_entities(
            "I'm heading home after work today.", "response"
        )
        places = [r for r in results if r[0] == "place"]
        assert len(places) == 2
        place_names = {p[1] for p in places}
        assert "home" in place_names
        assert "work" in place_names

    def test_extracts_health_topics(self):
        results = training_store.extract_entities(
            "My back pain has been worse since yesterday.", "response"
        )
        topics = [r for r in results if r[0] == "topic"]
        assert any(t[1] == "health" for t in topics)

    def test_extracts_nutrition_topics(self):
        results = training_store.extract_entities(
            "You consumed 1,450 calories with 120g protein today.", "response"
        )
        topics = [r for r in results if r[0] == "topic"]
        assert any(t[1] == "nutrition" for t in topics)

    def test_extracts_legal_topics(self):
        results = training_store.extract_entities(
            "Your court date is next Thursday.", "response"
        )
        topics = [r for r in results if r[0] == "topic"]
        assert any(t[1] == "legal" for t in topics)

    def test_extracts_vehicle_topics(self):
        results = training_store.extract_entities(
            "The last oil change was at 145,000 miles.", "response"
        )
        topics = [r for r in results if r[0] == "topic"]
        assert any(t[1] == "vehicle" for t in topics)

    def test_deduplicates_within_text(self):
        results = training_store.extract_entities(
            "John Smith met with John Smith again.", "response"
        )
        persons = [r for r in results if r[0] == "person"]
        assert len(persons) == 1

    def test_multiple_entity_types(self):
        results = training_store.extract_entities(
            "John Smith has court on Thursday about his back pain.",
            "response"
        )
        types = {r[0] for r in results}
        values = {r[1] for r in results}
        assert "person" in types
        assert "topic" in types
        # Both health and legal topics detected
        assert "health" in values
        assert "legal" in values
        assert "John Smith" in values

    def test_context_snippet_included(self):
        results = training_store.extract_entities(
            "I talked to John Smith yesterday.", "response"
        )
        persons = [r for r in results if r[0] == "person"]
        assert len(persons) == 1
        assert "John Smith" in persons[0][2]  # snippet contains the name

    @patch("training_store.config")
    def test_no_known_places_graceful(self, mock_config):
        mock_config.KNOWN_PLACES = {}
        results = training_store.extract_entities(
            "I'm at some unknown location.", "response"
        )
        places = [r for r in results if r[0] == "place"]
        assert len(places) == 0
