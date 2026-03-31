"""Embedding engine — lazy singleton for sentence-transformers.

Loads the model on first use and keeps it resident (same pattern as
WhisperEngine and Kokoro TTS). Runs on CPU — fast enough for batch
embedding of transcript chunks.
"""

import logging
import time

import config

log = logging.getLogger("aria.embedding")

_model = None
_model_name = None


def get_model():
    """Get or create the sentence-transformers model (lazy singleton)."""
    global _model, _model_name
    target = getattr(config, "EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    if _model is None or _model_name != target:
        from sentence_transformers import SentenceTransformer
        log.info("Loading embedding model: %s", target)
        start = time.time()
        _model = SentenceTransformer(target)
        _model_name = target
        log.info("Embedding model loaded in %.1fs (dim=%d)",
                 time.time() - start, _model.get_sentence_embedding_dimension())
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns list of float vectors.

    Uses the lazy-loaded model. Empty input returns empty list.
    """
    if not texts:
        return []

    model = get_model()
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return embeddings.tolist()


def embed_single(text: str) -> list[float]:
    """Embed a single text string. Returns a float vector."""
    results = embed([text])
    return results[0] if results else []


def get_dimension() -> int:
    """Return the embedding dimension for the current model."""
    model = get_model()
    return model.get_sentence_embedding_dimension()
