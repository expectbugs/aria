"""Tests for graph_sync.py — entity-to-graph sync pipeline."""

from unittest.mock import patch, MagicMock
from conftest import patch_db

import graph_sync


class TestSyncConversation:
    @patch("graph_sync.neo4j_store")
    @patch("graph_sync.ambient_store")
    def test_syncs_conversation_and_participants(self, mock_ambient, mock_neo4j):
        mock_ambient.get_conversation.return_value = {
            "id": 1, "title": "Work chat", "summary": "Discussed budget",
            "started_at": "2026-03-30T14:00:00", "location": "Banker Wire",
            "speakers": ["owner", "Mike", "Dave"],
            "segments": [],
        }
        mock_neo4j.add_conversation.return_value = True
        mock_neo4j.upsert_person.return_value = True
        mock_neo4j.add_conversation_link.return_value = True
        mock_neo4j.infer_knows_from_conversation.return_value = 1

        result = graph_sync.sync_conversation(1)
        assert result is True
        mock_neo4j.add_conversation.assert_called_once()
        # 3 speakers (owner, Mike, Dave) → 3 upsert + 3 link calls
        assert mock_neo4j.upsert_person.call_count == 3
        assert mock_neo4j.add_conversation_link.call_count == 3
        mock_neo4j.infer_knows_from_conversation.assert_called_once_with(1)

    @patch("graph_sync.ambient_store")
    def test_returns_false_if_not_found(self, mock_ambient):
        mock_ambient.get_conversation.return_value = None
        assert graph_sync.sync_conversation(999) is False

    @patch("graph_sync.neo4j_store")
    @patch("graph_sync.ambient_store")
    def test_skips_unknown_speakers(self, mock_ambient, mock_neo4j):
        mock_ambient.get_conversation.return_value = {
            "id": 1, "title": None, "summary": None,
            "started_at": "2026-03-30T14:00:00", "location": None,
            "speakers": ["unknown", "?", "Mike"],
            "segments": [],
        }
        mock_neo4j.add_conversation.return_value = True
        mock_neo4j.upsert_person.return_value = True
        mock_neo4j.add_conversation_link.return_value = True
        mock_neo4j.infer_knows_from_conversation.return_value = 0

        graph_sync.sync_conversation(1)
        # Only "Mike" should be upserted (unknown and ? filtered)
        assert mock_neo4j.upsert_person.call_count == 1


class TestSyncCommitmentsForConversation:
    def test_syncs_commitments(self):
        mc, p = patch_db("graph_sync")
        try:
            mc.execute.return_value.fetchall.return_value = [
                {"id": 1, "who": "self", "what": "finish proposal",
                 "to_whom": "Mike", "due_date": "2026-03-22"},
            ]
            with patch("graph_sync.neo4j_store") as mock_neo4j:
                mock_neo4j.add_commitment.return_value = True
                count = graph_sync.sync_commitments_for_conversation(1)
                assert count == 1
                mock_neo4j.add_commitment.assert_called_once()
        finally:
            p.stop()

    def test_handles_empty(self):
        mc, p = patch_db("graph_sync")
        try:
            mc.execute.return_value.fetchall.return_value = []
            count = graph_sync.sync_commitments_for_conversation(1)
            assert count == 0
        finally:
            p.stop()


class TestSyncTopics:
    @patch("graph_sync.neo4j_store")
    def test_syncs_topics(self, mock_neo4j):
        mock_neo4j.add_topic_link.return_value = True
        count = graph_sync.sync_topics_for_conversation(1, ["budget", "schedule"])
        assert count == 2

    @patch("graph_sync.neo4j_store")
    def test_filters_short_topics(self, mock_neo4j):
        mock_neo4j.add_topic_link.return_value = True
        count = graph_sync.sync_topics_for_conversation(1, ["a", "", "budget"])
        assert count == 1  # only "budget" passes

    def test_empty_topics(self):
        count = graph_sync.sync_topics_for_conversation(1, [])
        assert count == 0


class TestSyncPersonProfiles:
    @patch("graph_sync.neo4j_store")
    @patch("graph_sync._person_store")
    def test_syncs_all_profiles(self, mock_ps, mock_neo4j):
        mock_ps.get_all.return_value = [
            {"name": "Mike", "relationship": "coworker", "organization": "Banker Wire"},
            {"name": "Dave", "relationship": "friend", "organization": None},
        ]
        mock_neo4j.upsert_person.return_value = True

        count = graph_sync.sync_person_profiles()
        assert count == 2
        assert mock_neo4j.upsert_person.call_count == 2


class TestSyncBatch:
    @patch("graph_sync.sync_person_profiles", return_value=3)
    @patch("graph_sync.sync_commitments_for_conversation", return_value=1)
    @patch("graph_sync.sync_conversation", return_value=True)
    def test_batch_sync(self, mock_conv, mock_commit, mock_persons):
        mc, p = patch_db("graph_sync")
        try:
            mc.execute.return_value.fetchall.return_value = [
                {"id": 1}, {"id": 2},
            ]
            stats = graph_sync.sync_batch(since="2026-03-30T13:00:00")
            assert stats["conversations"] == 2
            assert stats["commitments"] == 2  # 1 per conversation
            assert stats["persons"] == 3
        finally:
            p.stop()

    @patch("graph_sync.sync_person_profiles", return_value=0)
    def test_batch_empty(self, mock_persons):
        mc, p = patch_db("graph_sync")
        try:
            mc.execute.return_value.fetchall.return_value = []
            stats = graph_sync.sync_batch()
            assert stats["conversations"] == 0
        finally:
            p.stop()


class TestTickJob:
    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    @patch("graph_sync.sync_batch", return_value={"conversations": 2, "commitments": 1, "topics": 0, "persons": 3})
    def test_graph_sync_job(self, mock_sync, mock_load, mock_save):
        import tick
        with patch.object(tick.config, "AMBIENT_ENABLED", True, create=True):
            tick.process_graph_sync()
            mock_sync.assert_called_once()

    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    def test_graph_sync_skips_when_disabled(self, mock_load, mock_save):
        import tick
        with patch.object(tick.config, "AMBIENT_ENABLED", False, create=True):
            tick.process_graph_sync()
            mock_save.assert_not_called()
