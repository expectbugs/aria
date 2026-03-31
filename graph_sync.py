"""Entity-to-graph sync pipeline for Neo4j.

Reads extraction results from PostgreSQL (ambient_conversations, commitments,
person_profiles) and syncs them to the Neo4j knowledge graph. Deduplication
is handled by MERGE in Cypher — safe to run repeatedly on the same data.
"""

import logging
from datetime import datetime, timedelta

import db
import neo4j_store
import ambient_store
import person_store as _person_store

log = logging.getLogger("aria.graph_sync")


def sync_conversation(conversation_id: int) -> bool:
    """Sync a single conversation and its relationships to Neo4j.

    Creates/updates: Conversation node, Person nodes, PARTICIPATED_IN rels,
    Topic DISCUSSED rels, KNOWS rels between co-participants.
    """
    conv = ambient_store.get_conversation(conversation_id)
    if not conv:
        return False

    # Create conversation node
    neo4j_store.add_conversation(
        conversation_id=conv["id"],
        title=conv.get("title"),
        summary=conv.get("summary"),
        started_at=conv.get("started_at"),
        location=conv.get("location"),
    )

    # Link participants
    speakers = conv.get("speakers") or []
    for speaker in speakers:
        if speaker and speaker not in ("unknown", "?"):
            name = "owner" if speaker == "owner" else speaker
            neo4j_store.upsert_person(name)
            neo4j_store.add_conversation_link(name, conv["id"])

    # Infer KNOWS relationships between co-participants
    if len(speakers) >= 2:
        neo4j_store.infer_knows_from_conversation(conv["id"])

    return True


def sync_commitments_for_conversation(conversation_id: int) -> int:
    """Sync commitments linked to a conversation to Neo4j.

    Returns count of commitments synced.
    """
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT id, who, what, to_whom, due_date
                   FROM commitments
                   WHERE conversation_id = %s""",
                (conversation_id,),
            ).fetchall()
    except Exception as e:
        log.error("Failed to fetch commitments for conversation %d: %s",
                  conversation_id, e)
        return 0

    count = 0
    for r in rows:
        due = r["due_date"]
        if hasattr(due, 'isoformat'):
            due = due.isoformat()
        if neo4j_store.add_commitment(
            commitment_id=r["id"],
            who=r["who"],
            what=r["what"],
            to_whom=r.get("to_whom"),
            due_date=due,
        ):
            count += 1
    return count


def sync_topics_for_conversation(conversation_id: int,
                                 topics: list[str]) -> int:
    """Link topics to a conversation in Neo4j."""
    count = 0
    for topic in topics:
        topic = topic.strip()
        if topic and len(topic) >= 2:
            if neo4j_store.add_topic_link(conversation_id, topic):
                count += 1
    return count


def sync_person_profiles() -> int:
    """Sync all person profiles from PostgreSQL to Neo4j.

    Uses MERGE so safe to run repeatedly.
    Returns count synced.
    """
    profiles = _person_store.get_all()
    count = 0
    for p in profiles:
        if neo4j_store.upsert_person(
            name=p["name"],
            relationship=p.get("relationship"),
            organization=p.get("organization"),
        ):
            count += 1
    return count


def sync_batch(since: str | None = None) -> dict:
    """Batch sync all new data since a timestamp.

    Returns {conversations: N, commitments: N, topics: N, persons: N}.
    """
    if since is None:
        since = (datetime.now() - timedelta(minutes=10)).isoformat()

    stats = {"conversations": 0, "commitments": 0, "topics": 0, "persons": 0}

    # Get recently created conversations
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT id FROM ambient_conversations
                   WHERE created_at > %s
                   ORDER BY started_at""",
                (since,),
            ).fetchall()

        for r in rows:
            conv_id = r["id"]
            if sync_conversation(conv_id):
                stats["conversations"] += 1
                stats["commitments"] += sync_commitments_for_conversation(conv_id)
    except Exception as e:
        log.error("Graph conversation sync failed: %s", e)

    # Sync person profiles (cheap — MERGE is idempotent)
    try:
        stats["persons"] = sync_person_profiles()
    except Exception as e:
        log.error("Graph person sync failed: %s", e)

    total = sum(stats.values())
    if total > 0:
        log.info("Graph sync: %s", stats)
    return stats
