#!/usr/bin/env python3
"""One-time cleanup: deduplicate nutrition_entries and health_entries, backfill content_hash.

Run once: ./venv/bin/python cleanup_duplicates.py

Safe to re-run (idempotent). Does NOT create unique indexes — that's done
after this script confirms no duplicates remain.
"""

import hashlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cleanup")


def compute_nutrition_hash(food_name: str, date: str, meal_type: str, servings: float) -> str:
    """Compute content hash for a nutrition entry."""
    key = f"{food_name.lower().strip()}|{date}|{meal_type}|{servings}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def compute_health_hash(date: str, category: str, description: str, meal_type: str | None) -> str:
    """Compute content hash for a health entry."""
    key = f"{date}|{category}|{description[:100].lower().strip()}|{meal_type or ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def dedup_nutrition():
    """Remove duplicate nutrition entries, keeping the earliest created."""
    with db.get_conn() as conn:
        # Find duplicates by (date, food_name, meal_type, servings)
        dupes = conn.execute("""
            SELECT date, food_name, meal_type, servings, COUNT(*) as cnt,
                   array_agg(id ORDER BY created ASC) as ids
            FROM nutrition_entries
            GROUP BY date, food_name, meal_type, servings
            HAVING COUNT(*) > 1
        """).fetchall()

        total_removed = 0
        for d in dupes:
            ids = d["ids"]
            keep_id = ids[0]  # earliest
            remove_ids = ids[1:]
            log.info("Dedup nutrition: keeping %s, removing %s (%s on %s)",
                     keep_id, remove_ids, d["food_name"][:40], d["date"])
            for rid in remove_ids:
                conn.execute("DELETE FROM nutrition_entries WHERE id = %s", (rid,))
                total_removed += 1

        log.info("Nutrition dedup: removed %d duplicate entries from %d groups",
                 total_removed, len(dupes))
        return total_removed


def dedup_health():
    """Remove duplicate health entries, keeping the earliest created."""
    with db.get_conn() as conn:
        dupes = conn.execute("""
            SELECT date, category, description, meal_type, COUNT(*) as cnt,
                   array_agg(id ORDER BY created ASC) as ids
            FROM health_entries
            GROUP BY date, category, description, meal_type
            HAVING COUNT(*) > 1
        """).fetchall()

        total_removed = 0
        for d in dupes:
            ids = d["ids"]
            keep_id = ids[0]
            remove_ids = ids[1:]
            log.info("Dedup health: keeping %s, removing %s (%s on %s)",
                     keep_id, remove_ids, d["description"][:40], d["date"])
            for rid in remove_ids:
                conn.execute("DELETE FROM health_entries WHERE id = %s", (rid,))
                total_removed += 1

        log.info("Health dedup: removed %d duplicate entries from %d groups",
                 total_removed, len(dupes))
        return total_removed


def backfill_nutrition_hashes():
    """Backfill content_hash on all nutrition entries that don't have one."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, date, food_name, meal_type, servings FROM nutrition_entries WHERE content_hash IS NULL"
        ).fetchall()

        for r in rows:
            h = compute_nutrition_hash(
                str(r["food_name"]), str(r["date"]), str(r["meal_type"]), float(r["servings"])
            )
            conn.execute(
                "UPDATE nutrition_entries SET content_hash = %s WHERE id = %s",
                (h, r["id"]),
            )

        log.info("Backfilled content_hash on %d nutrition entries", len(rows))
        return len(rows)


def backfill_health_hashes():
    """Backfill content_hash on all health entries that don't have one."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, date, category, description, meal_type FROM health_entries WHERE content_hash IS NULL"
        ).fetchall()

        for r in rows:
            h = compute_health_hash(
                str(r["date"]), str(r["category"]),
                str(r["description"]), r.get("meal_type"),
            )
            conn.execute(
                "UPDATE health_entries SET content_hash = %s WHERE id = %s",
                (h, r["id"]),
            )

        log.info("Backfilled content_hash on %d health entries", len(rows))
        return len(rows)


def verify_no_hash_dupes():
    """Check for any remaining duplicate content hashes."""
    with db.get_conn() as conn:
        n_dupes = conn.execute("""
            SELECT content_hash, COUNT(*) FROM nutrition_entries
            WHERE content_hash IS NOT NULL
            GROUP BY content_hash HAVING COUNT(*) > 1
        """).fetchall()

        h_dupes = conn.execute("""
            SELECT content_hash, COUNT(*) FROM health_entries
            WHERE content_hash IS NOT NULL
            GROUP BY content_hash HAVING COUNT(*) > 1
        """).fetchall()

    if n_dupes:
        log.warning("Nutrition still has %d duplicate content_hash groups!", len(n_dupes))
        for d in n_dupes:
            log.warning("  hash=%s count=%d", d["content_hash"], d["count"])
    if h_dupes:
        log.warning("Health still has %d duplicate content_hash groups!", len(h_dupes))
        for d in h_dupes:
            log.warning("  hash=%s count=%d", d["content_hash"], d["count"])

    return len(n_dupes), len(h_dupes)


def main():
    log.info("=== ARIA Duplicate Cleanup ===")

    # Step 1: Remove row-level duplicates
    n_removed = dedup_nutrition()
    h_removed = dedup_health()

    # Step 2: Backfill content hashes
    n_hashed = backfill_nutrition_hashes()
    h_hashed = backfill_health_hashes()

    # Step 3: Verify no remaining hash collisions
    n_hash_dupes, h_hash_dupes = verify_no_hash_dupes()

    # If hash-level duplicates exist after row-level dedup, they are
    # entries with slightly different descriptions but same logical content.
    # Remove the newer ones.
    if n_hash_dupes or h_hash_dupes:
        log.info("Cleaning up hash-level duplicates...")
        with db.get_conn() as conn:
            if n_hash_dupes:
                conn.execute("""
                    DELETE FROM nutrition_entries a
                    USING nutrition_entries b
                    WHERE a.content_hash = b.content_hash
                      AND a.content_hash IS NOT NULL
                      AND a.created > b.created
                """)
                log.info("Cleaned nutrition hash dupes")
            if h_hash_dupes:
                conn.execute("""
                    DELETE FROM health_entries a
                    USING health_entries b
                    WHERE a.content_hash = b.content_hash
                      AND a.content_hash IS NOT NULL
                      AND a.created > b.created
                """)
                log.info("Cleaned health hash dupes")

        # Re-verify
        n_hash_dupes, h_hash_dupes = verify_no_hash_dupes()

    log.info("=== Summary ===")
    log.info("Nutrition: %d row dupes removed, %d hashes backfilled", n_removed, n_hashed)
    log.info("Health: %d row dupes removed, %d hashes backfilled", h_removed, h_hashed)

    if n_hash_dupes == 0 and h_hash_dupes == 0:
        log.info("No remaining duplicates — safe to create unique indexes")
    else:
        log.error("STILL HAVE DUPLICATES — do not create unique indexes yet!")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
