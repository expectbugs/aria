#!/usr/bin/env python3
"""Bulk delete junk calendar test entries — uses calendar_store.delete_event() for Google sync."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import db
import calendar_store

JUNK_TITLES = {"Test", "X", "A", "B", "Real Event", "Birthday Party 🎂✨"}

async def bulk_delete_junk():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, date FROM events WHERE title = ANY(%s) ORDER BY date, title",
            (list(JUNK_TITLES),),
        ).fetchall()

    total = len(rows)
    print(f"Found {total} junk entries to delete.")
    if total == 0:
        return

    deleted = 0
    failed = 0
    for i, row in enumerate(rows, 1):
        try:
            success = await calendar_store.delete_event(row["id"])
            if success:
                deleted += 1
            else:
                failed += 1
                print(f"  FAILED (not found): {row['id']} - {row['title']} ({row['date']})")
        except Exception as e:
            failed += 1
            print(f"  ERROR: {row['id']} - {row['title']} ({row['date']}): {e}")

        if i % 50 == 0:
            print(f"  ...{i}/{total} processed ({deleted} deleted, {failed} failed)")

    print(f"\nDone: {deleted} deleted, {failed} failed out of {total} total.")

if __name__ == "__main__":
    asyncio.run(bulk_delete_junk())
