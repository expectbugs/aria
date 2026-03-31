"""Neo4j knowledge graph store — relational memory for people and conversations.

Follows the graceful degradation pattern: all functions return empty results
if neo4j driver is not installed or the server is unreachable.

Graph schema:
  Nodes:  (:Person), (:Conversation), (:Topic), (:Commitment)
  Rels:   PARTICIPATED_IN, DISCUSSED, COMMITTED_TO, KNOWS
"""

import logging

import config

log = logging.getLogger("aria.neo4j")

_driver = None

NEO4J_URI = getattr(config, "NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = getattr(config, "NEO4J_USER", "neo4j")
NEO4J_PASSWORD = getattr(config, "NEO4J_PASSWORD", "")


def get_driver():
    """Get or create the Neo4j driver singleton. Returns None if unavailable."""
    global _driver
    if _driver is not None:
        return _driver

    try:
        from neo4j import GraphDatabase
        auth = (NEO4J_USER, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
        _driver = GraphDatabase.driver(NEO4J_URI, auth=auth)
        _driver.verify_connectivity()
        log.info("Neo4j connected at %s", NEO4J_URI)
        return _driver
    except ImportError:
        log.warning("neo4j driver not installed — knowledge graph disabled")
        return None
    except Exception as e:
        log.warning("Neo4j unreachable at %s: %s", NEO4J_URI, e)
        _driver = None
        return None


def close():
    """Close the Neo4j driver."""
    global _driver
    if _driver is not None:
        try:
            _driver.close()
        except Exception:
            pass
        _driver = None


def _run_query(query: str, params: dict | None = None) -> list[dict]:
    """Execute a Cypher query and return results as list of dicts."""
    driver = get_driver()
    if driver is None:
        return []

    try:
        with driver.session() as session:
            result = session.run(query, params or {})
            return [dict(record) for record in result]
    except Exception as e:
        log.error("Neo4j query failed: %s", e)
        return []


def _run_write(query: str, params: dict | None = None) -> bool:
    """Execute a write Cypher query. Returns True on success."""
    driver = get_driver()
    if driver is None:
        return False

    try:
        with driver.session() as session:
            session.run(query, params or {})
        return True
    except Exception as e:
        log.error("Neo4j write failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Person operations
# ---------------------------------------------------------------------------

def upsert_person(name: str, relationship: str | None = None,
                  organization: str | None = None) -> bool:
    """Create or update a Person node."""
    props = []
    params = {"name": name}
    if relationship:
        props.append("p.relationship = $relationship")
        params["relationship"] = relationship
    if organization:
        props.append("p.organization = $organization")
        params["organization"] = organization

    set_clause = ", ".join(props) if props else "p.name = p.name"  # no-op if nothing to set

    return _run_write(
        f"MERGE (p:Person {{name: $name}}) SET {set_clause}",
        params,
    )


def get_person_graph(name: str) -> dict:
    """Get a person and all their relationships.

    Returns {person: {...}, conversations: [...], commitments: [...], knows: [...]}.
    """
    result = {"person": None, "conversations": [], "commitments": [], "knows": []}

    # Get person node
    rows = _run_query(
        "MATCH (p:Person {name: $name}) RETURN p",
        {"name": name},
    )
    if rows:
        result["person"] = dict(rows[0]["p"]) if rows[0].get("p") else None

    # Conversations they participated in
    rows = _run_query(
        """MATCH (p:Person {name: $name})-[:PARTICIPATED_IN]->(c:Conversation)
           RETURN c ORDER BY c.started_at DESC LIMIT 20""",
        {"name": name},
    )
    result["conversations"] = [dict(r["c"]) for r in rows if r.get("c")]

    # Commitments involving them
    rows = _run_query(
        """MATCH (p:Person {name: $name})-[:COMMITTED_TO]->(cm:Commitment)
           RETURN cm ORDER BY cm.created_at DESC LIMIT 20""",
        {"name": name},
    )
    result["commitments"] = [dict(r["cm"]) for r in rows if r.get("cm")]

    # People they know (co-participated in conversations)
    rows = _run_query(
        """MATCH (p:Person {name: $name})-[:KNOWS]->(other:Person)
           RETURN other.name AS name, other.relationship AS relationship
           ORDER BY other.name""",
        {"name": name},
    )
    result["knows"] = [dict(r) for r in rows]

    return result


def get_shared_conversations(person_a: str, person_b: str,
                             limit: int = 10) -> list[dict]:
    """Get conversations two people both participated in."""
    rows = _run_query(
        """MATCH (a:Person {name: $a})-[:PARTICIPATED_IN]->(c:Conversation)<-[:PARTICIPATED_IN]-(b:Person {name: $b})
           RETURN c ORDER BY c.started_at DESC LIMIT $limit""",
        {"a": person_a, "b": person_b, "limit": limit},
    )
    return [dict(r["c"]) for r in rows if r.get("c")]


# ---------------------------------------------------------------------------
# Conversation operations
# ---------------------------------------------------------------------------

def add_conversation(conversation_id: int, title: str | None = None,
                     summary: str | None = None,
                     started_at: str | None = None,
                     location: str | None = None) -> bool:
    """Create or update a Conversation node."""
    return _run_write(
        """MERGE (c:Conversation {id: $id})
           SET c.title = $title, c.summary = $summary,
               c.started_at = $started_at, c.location = $location""",
        {"id": conversation_id, "title": title, "summary": summary,
         "started_at": started_at, "location": location},
    )


def add_conversation_link(person_name: str, conversation_id: int) -> bool:
    """Link a person to a conversation via PARTICIPATED_IN."""
    return _run_write(
        """MERGE (p:Person {name: $name})
           MERGE (c:Conversation {id: $conv_id})
           MERGE (p)-[:PARTICIPATED_IN]->(c)""",
        {"name": person_name, "conv_id": conversation_id},
    )


# ---------------------------------------------------------------------------
# Topic operations
# ---------------------------------------------------------------------------

def add_topic_link(conversation_id: int, topic: str) -> bool:
    """Link a conversation to a topic via DISCUSSED."""
    return _run_write(
        """MERGE (c:Conversation {id: $conv_id})
           MERGE (t:Topic {name: $topic})
           MERGE (c)-[:DISCUSSED]->(t)""",
        {"conv_id": conversation_id, "topic": topic},
    )


# ---------------------------------------------------------------------------
# Commitment operations
# ---------------------------------------------------------------------------

def add_commitment(commitment_id: int, who: str, what: str,
                   to_whom: str | None = None,
                   due_date: str | None = None) -> bool:
    """Create a Commitment node and link to the person who made it."""
    return _run_write(
        """MERGE (cm:Commitment {id: $id})
           SET cm.what = $what, cm.due_date = $due_date, cm.created_at = datetime()
           MERGE (p:Person {name: $who})
           MERGE (p)-[:COMMITTED_TO]->(cm)""",
        {"id": commitment_id, "what": what, "who": who,
         "due_date": due_date},
    )


# ---------------------------------------------------------------------------
# KNOWS relationship (inferred)
# ---------------------------------------------------------------------------

def infer_knows_from_conversation(conversation_id: int) -> int:
    """Create KNOWS relationships between all participants of a conversation.

    Returns count of relationships created.
    """
    rows = _run_query(
        """MATCH (a:Person)-[:PARTICIPATED_IN]->(c:Conversation {id: $conv_id})<-[:PARTICIPATED_IN]-(b:Person)
           WHERE a.name < b.name
           MERGE (a)-[:KNOWS]->(b)
           MERGE (b)-[:KNOWS]->(a)
           RETURN count(*) AS cnt""",
        {"conv_id": conversation_id},
    )
    return rows[0]["cnt"] if rows else 0


# ---------------------------------------------------------------------------
# Search / query
# ---------------------------------------------------------------------------

def search_by_relationship(person: str, rel_type: str,
                           limit: int = 20) -> list[dict]:
    """Search for nodes connected to a person by relationship type."""
    rows = _run_query(
        f"""MATCH (p:Person {{name: $name}})-[:{rel_type}]->(n)
            RETURN n LIMIT $limit""",
        {"name": person, "limit": limit},
    )
    return [dict(r["n"]) for r in rows if r.get("n")]


def get_topic_people(topic: str, limit: int = 20) -> list[dict]:
    """Get people who discussed a specific topic."""
    rows = _run_query(
        """MATCH (p:Person)-[:PARTICIPATED_IN]->(c:Conversation)-[:DISCUSSED]->(t:Topic {name: $topic})
           RETURN DISTINCT p.name AS name, p.relationship AS relationship
           LIMIT $limit""",
        {"topic": topic, "limit": limit},
    )
    return [dict(r) for r in rows]


def get_stats() -> dict | None:
    """Return graph statistics, or None if unavailable."""
    rows = _run_query(
        """MATCH (n) WITH labels(n) AS labs, count(*) AS cnt
           UNWIND labs AS label
           RETURN label, sum(cnt) AS count ORDER BY label""",
    )
    if not rows:
        return None
    return {r["label"]: r["count"] for r in rows}
