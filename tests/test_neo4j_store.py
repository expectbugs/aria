"""Tests for neo4j_store.py — Neo4j knowledge graph with graceful degradation."""

from unittest.mock import patch, MagicMock

import neo4j_store


class TestGetDriver:
    def test_returns_none_when_unavailable(self):
        with patch.object(neo4j_store, "_driver", None):
            # Simulate import failure
            import sys
            orig = sys.modules.get("neo4j")
            sys.modules["neo4j"] = None
            try:
                driver = neo4j_store.get_driver()
            except (ImportError, TypeError):
                driver = None
            finally:
                if orig:
                    sys.modules["neo4j"] = orig
                else:
                    sys.modules.pop("neo4j", None)


class TestRunQuery:
    @patch.object(neo4j_store, "get_driver", return_value=None)
    def test_no_driver_returns_empty(self, mock_get):
        assert neo4j_store._run_query("MATCH (n) RETURN n") == []

    @patch.object(neo4j_store, "get_driver")
    def test_executes_query(self, mock_get):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            {"name": "Mike", "relationship": "coworker"},
        ]))
        mock_session.run.return_value = mock_result
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_get.return_value = mock_driver

        results = neo4j_store._run_query("MATCH (p:Person) RETURN p.name AS name")
        assert len(results) == 1
        mock_session.run.assert_called_once()


class TestRunWrite:
    @patch.object(neo4j_store, "get_driver", return_value=None)
    def test_no_driver_returns_false(self, mock_get):
        assert neo4j_store._run_write("CREATE (n:Test)") is False

    @patch.object(neo4j_store, "get_driver")
    def test_executes_write(self, mock_get):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_get.return_value = mock_driver

        assert neo4j_store._run_write("CREATE (n:Test)") is True
        mock_session.run.assert_called_once()


class TestUpsertPerson:
    @patch.object(neo4j_store, "_run_write", return_value=True)
    def test_basic_upsert(self, mock_write):
        assert neo4j_store.upsert_person("Mike") is True
        cypher = mock_write.call_args[0][0]
        assert "MERGE" in cypher
        assert "Person" in cypher

    @patch.object(neo4j_store, "_run_write", return_value=True)
    def test_with_relationship(self, mock_write):
        neo4j_store.upsert_person("Mike", relationship="coworker",
                                   organization="Banker Wire")
        params = mock_write.call_args[0][1]
        assert params["relationship"] == "coworker"
        assert params["organization"] == "Banker Wire"


class TestGetPersonGraph:
    @patch.object(neo4j_store, "_run_query")
    def test_returns_structure(self, mock_query):
        # Person query
        mock_query.side_effect = [
            [{"p": {"name": "Mike", "relationship": "coworker"}}],  # person
            [{"c": {"id": 1, "title": "Work chat"}}],                # conversations
            [],                                                        # commitments
            [{"name": "Dave", "relationship": "coworker"}],           # knows
        ]

        result = neo4j_store.get_person_graph("Mike")
        assert result["person"]["name"] == "Mike"
        assert len(result["conversations"]) == 1
        assert len(result["knows"]) == 1
        assert result["knows"][0]["name"] == "Dave"

    @patch.object(neo4j_store, "_run_query", return_value=[])
    def test_person_not_found(self, mock_query):
        result = neo4j_store.get_person_graph("Nobody")
        assert result["person"] is None
        assert result["conversations"] == []


class TestConversationOps:
    @patch.object(neo4j_store, "_run_write", return_value=True)
    def test_add_conversation(self, mock_write):
        assert neo4j_store.add_conversation(42, title="Work chat") is True
        params = mock_write.call_args[0][1]
        assert params["id"] == 42
        assert params["title"] == "Work chat"

    @patch.object(neo4j_store, "_run_write", return_value=True)
    def test_add_conversation_link(self, mock_write):
        assert neo4j_store.add_conversation_link("Mike", 42) is True
        cypher = mock_write.call_args[0][0]
        assert "PARTICIPATED_IN" in cypher


class TestTopicOps:
    @patch.object(neo4j_store, "_run_write", return_value=True)
    def test_add_topic_link(self, mock_write):
        assert neo4j_store.add_topic_link(42, "budget") is True
        cypher = mock_write.call_args[0][0]
        assert "DISCUSSED" in cypher
        assert "Topic" in cypher


class TestCommitmentOps:
    @patch.object(neo4j_store, "_run_write", return_value=True)
    def test_add_commitment(self, mock_write):
        assert neo4j_store.add_commitment(1, "self", "finish proposal",
                                           to_whom="Mike") is True
        cypher = mock_write.call_args[0][0]
        assert "COMMITTED_TO" in cypher
        assert "Commitment" in cypher


class TestInferKnows:
    @patch.object(neo4j_store, "_run_query")
    def test_infers_relationships(self, mock_query):
        mock_query.return_value = [{"cnt": 2}]
        count = neo4j_store.infer_knows_from_conversation(42)
        assert count == 2
        cypher = mock_query.call_args[0][0]
        assert "KNOWS" in cypher


class TestSearch:
    @patch.object(neo4j_store, "_run_query")
    def test_search_by_relationship(self, mock_query):
        mock_query.return_value = [{"n": {"id": 1, "title": "chat"}}]
        results = neo4j_store.search_by_relationship("Mike", "PARTICIPATED_IN")
        assert len(results) == 1

    @patch.object(neo4j_store, "_run_query")
    def test_get_topic_people(self, mock_query):
        mock_query.return_value = [
            {"name": "Mike", "relationship": "coworker"},
        ]
        results = neo4j_store.get_topic_people("budget")
        assert len(results) == 1
        assert results[0]["name"] == "Mike"


class TestGetStats:
    @patch.object(neo4j_store, "_run_query")
    def test_returns_stats(self, mock_query):
        mock_query.return_value = [
            {"label": "Person", "count": 5},
            {"label": "Conversation", "count": 10},
        ]
        stats = neo4j_store.get_stats()
        assert stats["Person"] == 5
        assert stats["Conversation"] == 10

    @patch.object(neo4j_store, "_run_query", return_value=[])
    def test_empty_graph(self, mock_query):
        assert neo4j_store.get_stats() is None
