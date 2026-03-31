"""Tests for embedding_engine.py — sentence-transformers lazy singleton."""

from unittest.mock import patch, MagicMock

import embedding_engine


class TestGetModel:
    @patch("embedding_engine.SentenceTransformer", create=True)
    def test_lazy_loads_model(self, mock_st_class):
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_st_class.return_value = mock_model

        with patch.object(embedding_engine, "_model", None), \
             patch.object(embedding_engine, "_model_name", None):
            # Patch the import inside get_model
            import sys
            mock_module = MagicMock()
            mock_module.SentenceTransformer = mock_st_class
            with patch.dict(sys.modules, {"sentence_transformers": mock_module}):
                model = embedding_engine.get_model()
                assert model is mock_model


class TestEmbed:
    @patch.object(embedding_engine, "get_model")
    def test_batch_embed(self, mock_get):
        import numpy as np
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([
            [0.1, 0.2, 0.3],
            [0.4, 0.5, 0.6],
        ])
        mock_get.return_value = mock_model

        result = embedding_engine.embed(["hello", "world"])
        assert len(result) == 2
        assert len(result[0]) == 3
        assert isinstance(result[0][0], float)
        mock_model.encode.assert_called_once_with(
            ["hello", "world"], show_progress_bar=False, convert_to_numpy=True,
        )

    @patch.object(embedding_engine, "get_model")
    def test_empty_input(self, mock_get):
        result = embedding_engine.embed([])
        assert result == []
        mock_get.assert_not_called()


class TestEmbedSingle:
    @patch.object(embedding_engine, "get_model")
    def test_single_embed(self, mock_get):
        import numpy as np
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1, 0.2, 0.3]])
        mock_get.return_value = mock_model

        result = embedding_engine.embed_single("hello")
        assert len(result) == 3


class TestGetDimension:
    @patch.object(embedding_engine, "get_model")
    def test_returns_dimension(self, mock_get):
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_get.return_value = mock_model

        assert embedding_engine.get_dimension() == 384
