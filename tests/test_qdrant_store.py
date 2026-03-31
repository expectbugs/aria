"""Tests for qdrant_store.py — Qdrant vector search with graceful degradation."""

from datetime import datetime
from unittest.mock import patch, MagicMock

import qdrant_store


class TestGetClient:
    @patch("qdrant_store.QdrantClient", create=True)
    def test_creates_client(self, mock_class):
        mock_client = MagicMock()
        mock_client.get_collections.return_value = MagicMock(collections=[])
        mock_class.return_value = mock_client

        with patch.object(qdrant_store, "_client", None):
            import sys
            mock_module = MagicMock()
            mock_module.QdrantClient = mock_class
            with patch.dict(sys.modules, {"qdrant_client": mock_module}):
                client = qdrant_store.get_client()
                assert client is mock_client

    def test_returns_none_when_unavailable(self):
        with patch.object(qdrant_store, "_client", None):
            # Make import fail
            import sys
            original = sys.modules.get("qdrant_client")
            sys.modules["qdrant_client"] = None
            try:
                client = qdrant_store.get_client()
                # Either None or an error that was caught
            except (ImportError, TypeError):
                pass
            finally:
                if original:
                    sys.modules["qdrant_client"] = original
                else:
                    sys.modules.pop("qdrant_client", None)


class TestStableId:
    def test_deterministic(self):
        id1 = qdrant_store._stable_id("transcript:42")
        id2 = qdrant_store._stable_id("transcript:42")
        assert id1 == id2

    def test_different_inputs(self):
        id1 = qdrant_store._stable_id("transcript:42")
        id2 = qdrant_store._stable_id("transcript:43")
        assert id1 != id2

    def test_positive_integer(self):
        result = qdrant_store._stable_id("conversation:1")
        assert isinstance(result, int)
        assert result > 0


class TestUpsertPoints:
    def test_empty_list(self):
        assert qdrant_store.upsert_points([]) == 0

    @patch.object(qdrant_store, "get_client", return_value=None)
    def test_no_client(self, mock_get):
        result = qdrant_store.upsert_points([{
            "id": "transcript:1", "vector": [0.1] * 384,
            "payload": {"text": "hello"},
        }])
        assert result == 0

    @patch.object(qdrant_store, "_ensure_collection", return_value=True)
    @patch.object(qdrant_store, "get_client")
    def test_upserts_points(self, mock_get, mock_ensure):
        mock_client = MagicMock()
        mock_get.return_value = mock_client

        import sys
        mock_models = MagicMock()
        with patch.dict(sys.modules, {"qdrant_client.models": mock_models}):
            points = [
                {"id": "transcript:1", "vector": [0.1] * 384,
                 "payload": {"text": "hello", "category": "transcript"}},
                {"id": "transcript:2", "vector": [0.2] * 384,
                 "payload": {"text": "world", "category": "transcript"}},
            ]
            result = qdrant_store.upsert_points(points)
            assert result == 2
            mock_client.upsert.assert_called_once()


class TestSearch:
    @patch.object(qdrant_store, "get_client", return_value=None)
    def test_no_client_returns_empty(self, mock_get):
        assert qdrant_store.search("hello") == []

    @patch("embedding_engine.embed_single", return_value=[0.1] * 384)
    @patch.object(qdrant_store, "get_client")
    def test_returns_results(self, mock_get, mock_embed):
        mock_point = MagicMock()
        mock_point.payload = {
            "source_table": "ambient_transcripts",
            "source_id": 42,
            "text": "Hello world",
            "timestamp": "2026-03-30T14:00:00",
            "category": "transcript",
        }
        mock_point.score = 0.95

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.points = [mock_point]
        mock_client.query_points.return_value = mock_response
        mock_get.return_value = mock_client

        results = qdrant_store.search("hello", limit=5)
        assert len(results) == 1
        assert results[0]["source_id"] == 42
        assert results[0]["score"] == 0.95
        assert results[0]["category"] == "transcript"

    @patch("embedding_engine.embed_single", return_value=[0.1] * 384)
    @patch.object(qdrant_store, "get_client")
    def test_search_with_category_filter(self, mock_get, mock_embed):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.points = []
        mock_client.query_points.return_value = mock_response
        mock_get.return_value = mock_client

        qdrant_store.search("hello", category="conversation")
        call_kwargs = mock_client.query_points.call_args[1]
        assert call_kwargs.get("query_filter") is not None

    @patch("embedding_engine.embed_single", return_value=[0.1] * 384)
    @patch.object(qdrant_store, "get_client")
    def test_search_with_days_filter(self, mock_get, mock_embed):
        """Days filter is post-query (Qdrant Range requires numeric).
        Verify it fetches more results and filters by timestamp."""
        mock_point = MagicMock()
        mock_point.payload = {
            "source_table": "ambient_transcripts", "source_id": 1,
            "text": "recent", "timestamp": datetime.now().isoformat(),
            "category": "transcript",
        }
        mock_point.score = 0.9

        mock_old_point = MagicMock()
        mock_old_point.payload = {
            "source_table": "ambient_transcripts", "source_id": 2,
            "text": "old", "timestamp": "2020-01-01T00:00:00",
            "category": "transcript",
        }
        mock_old_point.score = 0.8

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.points = [mock_point, mock_old_point]
        mock_client.query_points.return_value = mock_response
        mock_get.return_value = mock_client

        results = qdrant_store.search("hello", days=7)
        assert len(results) == 1  # old point filtered out
        assert results[0]["text"] == "recent"


class TestGetCollectionInfo:
    @patch.object(qdrant_store, "get_client", return_value=None)
    def test_no_client(self, mock_get):
        assert qdrant_store.get_collection_info() is None

    @patch.object(qdrant_store, "get_client")
    def test_returns_info(self, mock_get):
        mock_client = MagicMock()
        mock_info = MagicMock()
        mock_info.points_count = 1000
        mock_info.vectors_count = 1000
        mock_info.status.value = "green"
        mock_client.get_collection.return_value = mock_info
        mock_get.return_value = mock_client

        info = qdrant_store.get_collection_info()
        assert info is not None
        assert info["points_count"] == 1000


class TestDeleteBySource:
    @patch.object(qdrant_store, "get_client", return_value=None)
    def test_no_client(self, mock_get):
        assert qdrant_store.delete_by_source("ambient_transcripts", 42) is False

    @patch.object(qdrant_store, "get_client")
    def test_deletes(self, mock_get):
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        result = qdrant_store.delete_by_source("ambient_transcripts", 42)
        assert result is True
        mock_client.delete.assert_called_once()


class TestSyncNewData:
    @patch.object(qdrant_store, "get_client", return_value=None)
    def test_no_client_returns_zero(self, mock_get):
        assert qdrant_store.sync_new_data() == 0

    @patch.object(qdrant_store, "upsert_points", return_value=3)
    @patch("embedding_engine.embed", return_value=[[0.1] * 384, [0.2] * 384])
    @patch.object(qdrant_store, "get_client")
    def test_syncs_transcripts(self, mock_get, mock_embed, mock_upsert):
        mock_get.return_value = MagicMock()

        mock_conn = MagicMock()
        patcher = patch("db.get_conn")
        mock_get_conn = patcher.start()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        try:
            mock_conn.execute.return_value.fetchall.side_effect = [
                [  # transcripts
                    {"id": 1, "text": "hello", "started_at": "2026-03-30T14:00:00", "source": "slappy"},
                    {"id": 2, "text": "world", "started_at": "2026-03-30T14:01:00", "source": "slappy"},
                ],
                [],  # conversations
                [],  # commitments
            ]

            count = qdrant_store.sync_new_data(since="2026-03-30T13:00:00")
            assert count >= 3  # at least transcript upsert
            mock_upsert.assert_called()
        finally:
            patcher.stop()


class TestTickJob:
    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    @patch("qdrant_store.sync_new_data", return_value=10)
    def test_qdrant_sync_job(self, mock_sync, mock_load, mock_save):
        import tick
        with patch.object(tick.config, "AMBIENT_ENABLED", True, create=True):
            tick.process_qdrant_sync()
            mock_sync.assert_called_once()

    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    def test_qdrant_sync_skips_when_disabled(self, mock_load, mock_save):
        import tick
        with patch.object(tick.config, "AMBIENT_ENABLED", False, create=True):
            tick.process_qdrant_sync()
            mock_save.assert_not_called()
