"""Qdrant vector search store — semantic recall for ambient transcripts.

Follows the graceful degradation pattern from redis_client.py: all functions
return empty results if qdrant-client is not installed or Qdrant is unreachable.
Never crashes the caller.
"""

import logging
import time
from datetime import datetime, timedelta

import config

log = logging.getLogger("aria.qdrant")

_client = None

COLLECTION = getattr(config, "QDRANT_COLLECTION", "aria_memory")
QDRANT_URL = getattr(config, "QDRANT_URL", "http://localhost:6333")


def get_client():
    """Get or create the Qdrant client singleton. Returns None if unavailable."""
    global _client
    if _client is not None:
        return _client

    try:
        from qdrant_client import QdrantClient
        _client = QdrantClient(url=QDRANT_URL, timeout=10)
        # Test connectivity
        _client.get_collections()
        log.info("Qdrant connected at %s", QDRANT_URL)
        return _client
    except ImportError:
        log.warning("qdrant-client not installed — vector search disabled")
        return None
    except Exception as e:
        log.warning("Qdrant unreachable at %s: %s", QDRANT_URL, e)
        _client = None
        return None


def _ensure_collection(dimension: int = 384):
    """Create the collection if it doesn't exist."""
    client = get_client()
    if client is None:
        return False

    try:
        from qdrant_client.models import Distance, VectorParams
        collections = [c.name for c in client.get_collections().collections]
        if COLLECTION not in collections:
            client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )
            log.info("Created Qdrant collection '%s' (dim=%d)", COLLECTION, dimension)
        return True
    except Exception as e:
        log.error("Failed to ensure Qdrant collection: %s", e)
        return False


def upsert_points(points: list[dict]) -> int:
    """Batch upsert points to Qdrant.

    Each point dict: {
        "id": "transcript:42" or "conversation:7",
        "vector": [float, ...],
        "payload": {
            "source_table": "ambient_transcripts",
            "source_id": 42,
            "text": "snippet...",
            "timestamp": "2026-03-30T14:00:00",
            "category": "transcript",
        }
    }

    Returns count of points upserted.
    """
    if not points:
        return 0

    client = get_client()
    if client is None:
        return 0

    try:
        from qdrant_client.models import PointStruct

        # Ensure collection exists (uses first point's vector dimension)
        dim = len(points[0]["vector"]) if points[0].get("vector") else 384
        _ensure_collection(dimension=dim)

        # Build PointStruct list — Qdrant needs numeric or UUID ids
        # Use a hash of the string id for stable numeric ids
        qdrant_points = []
        for p in points:
            # Generate stable numeric ID from string ID
            point_id = _stable_id(p["id"])
            qdrant_points.append(PointStruct(
                id=point_id,
                vector=p["vector"],
                payload={**p.get("payload", {}), "_aria_id": p["id"]},
            ))

        client.upsert(collection_name=COLLECTION, points=qdrant_points)
        return len(qdrant_points)

    except Exception as e:
        log.error("Qdrant upsert failed: %s", e)
        return 0


def _stable_id(string_id: str) -> int:
    """Convert a string ID like 'transcript:42' to a stable positive integer."""
    import hashlib
    h = hashlib.sha256(string_id.encode()).hexdigest()[:15]
    return int(h, 16)


def search(query: str, limit: int = 5, category: str | None = None,
           days: int | None = None) -> list[dict]:
    """Semantic search over the collection.

    Returns list of {source_table, source_id, text, timestamp, category, score}.
    """
    client = get_client()
    if client is None:
        return []

    try:
        import embedding_engine
        query_vector = embedding_engine.embed_single(query)
        if not query_vector:
            return []

        # Category filter via Qdrant; date filtering done post-query
        # (Qdrant Range requires numeric, timestamps are ISO strings)
        filters = None
        if category:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            filters = Filter(must=[
                FieldCondition(key="category", match=MatchValue(value=category)),
            ])

        # Fetch more results if date filtering (post-filter will trim)
        fetch_limit = limit * 3 if days else limit

        results = client.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            query_filter=filters,
            limit=fetch_limit,
        ).points

        # Post-filter by date
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            results = [r for r in results
                       if (r.payload.get("timestamp") or "") >= cutoff]

        return [
            {
                "source_table": r.payload.get("source_table"),
                "source_id": r.payload.get("source_id"),
                "text": r.payload.get("text", ""),
                "timestamp": r.payload.get("timestamp"),
                "category": r.payload.get("category"),
                "score": round(r.score, 4),
            }
            for r in results[:limit]
        ]

    except Exception as e:
        log.error("Qdrant search failed: %s", e)
        return []


def get_collection_info() -> dict | None:
    """Return collection stats, or None if unavailable."""
    client = get_client()
    if client is None:
        return None

    try:
        info = client.get_collection(COLLECTION)
        return {
            "name": COLLECTION,
            "points_count": info.points_count,
            "vectors_count": info.vectors_count,
            "status": info.status.value if info.status else "unknown",
        }
    except Exception as e:
        log.debug("Qdrant collection info failed: %s", e)
        return None


def delete_by_source(source_table: str, source_id: int) -> bool:
    """Delete a point by its source table and ID."""
    client = get_client()
    if client is None:
        return False

    try:
        point_id = _stable_id(f"{source_table}:{source_id}")
        client.delete(collection_name=COLLECTION, points_selector=[point_id])
        return True
    except Exception as e:
        log.warning("Qdrant delete failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Incremental sync (called from tick.py)
# ---------------------------------------------------------------------------

def sync_new_data(since: str | None = None) -> int:
    """Sync new transcripts, conversations, and commitments to Qdrant.

    Fetches rows with created_at > since, embeds, and upserts.
    Returns total points synced.
    """
    client = get_client()
    if client is None:
        return 0

    import embedding_engine
    import ambient_store
    import commitment_store

    if since is None:
        since = (datetime.now() - timedelta(minutes=10)).isoformat()

    total = 0

    # Sync transcripts
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT id, COALESCE(quality_text, text) AS text, started_at, source
                   FROM ambient_transcripts
                   WHERE created_at > %s AND text != ''
                   ORDER BY started_at
                   LIMIT 200""",
                (since,),
            ).fetchall()

        if rows:
            texts = [r["text"] for r in rows]
            vectors = embedding_engine.embed(texts)
            points = []
            for r, vec in zip(rows, vectors):
                ts = r["started_at"]
                if hasattr(ts, 'isoformat'):
                    ts = ts.isoformat()
                points.append({
                    "id": f"transcript:{r['id']}",
                    "vector": vec,
                    "payload": {
                        "source_table": "ambient_transcripts",
                        "source_id": r["id"],
                        "text": r["text"][:500],
                        "timestamp": ts,
                        "category": "transcript",
                    },
                })
            total += upsert_points(points)
    except Exception as e:
        log.error("Qdrant transcript sync failed: %s", e)

    # Sync conversations (summaries)
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT id, summary, title, started_at
                   FROM ambient_conversations
                   WHERE created_at > %s AND summary IS NOT NULL
                   ORDER BY started_at
                   LIMIT 100""",
                (since,),
            ).fetchall()

        if rows:
            texts = [r["summary"] or r["title"] or "" for r in rows]
            texts = [t for t in texts if t]  # filter empty
            if texts:
                vectors = embedding_engine.embed(texts)
                points = []
                for r, vec in zip([r for r in rows if r["summary"] or r["title"]], vectors):
                    ts = r["started_at"]
                    if hasattr(ts, 'isoformat'):
                        ts = ts.isoformat()
                    points.append({
                        "id": f"conversation:{r['id']}",
                        "vector": vec,
                        "payload": {
                            "source_table": "ambient_conversations",
                            "source_id": r["id"],
                            "text": (r["summary"] or r["title"] or "")[:500],
                            "timestamp": ts,
                            "category": "conversation",
                        },
                    })
                total += upsert_points(points)
    except Exception as e:
        log.error("Qdrant conversation sync failed: %s", e)

    # Sync commitments
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT id, who, what, to_whom, created_at
                   FROM commitments
                   WHERE created_at > %s
                   ORDER BY created_at
                   LIMIT 100""",
                (since,),
            ).fetchall()

        if rows:
            texts = [f"{r['who']} committed to: {r['what']}" +
                     (f" (to {r['to_whom']})" if r.get("to_whom") else "")
                     for r in rows]
            vectors = embedding_engine.embed(texts)
            points = []
            for r, vec in zip(rows, vectors):
                ts = r["created_at"]
                if hasattr(ts, 'isoformat'):
                    ts = ts.isoformat()
                points.append({
                    "id": f"commitment:{r['id']}",
                    "vector": vec,
                    "payload": {
                        "source_table": "commitments",
                        "source_id": r["id"],
                        "text": texts[rows.index(r)][:500],
                        "timestamp": ts,
                        "category": "commitment",
                    },
                })
            total += upsert_points(points)
    except Exception as e:
        log.error("Qdrant commitment sync failed: %s", e)

    if total > 0:
        log.info("Qdrant sync: %d points upserted", total)
    return total
