#!/usr/bin/env python3
"""One-time backfill: add pantry micronutrient data to existing nutrition entries.

Only adds NEW JSONB keys — never overwrites existing values.
Dry-run by default. Use --apply to execute.

Usage:
    ./venv/bin/python backfill_micronutrients.py          # dry-run
    ./venv/bin/python backfill_micronutrients.py --apply   # execute
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db

# Pantry micronutrient data per serving.
# Keys are (pattern, exclude_pattern) tuples for ILIKE matching.
# Values are dicts of {nutrient_field: value_per_serving}.
# Source notes are for documentation — not used in code.
# IMPORTANT: Only match entries where the food is the PRIMARY item, not
# part of a composite entry. Composite entries (food_name contains "+")
# have combined nutrition values — per-ingredient micronutrients don't apply.
PANTRY_MICROS = [
    {
        "name": "Nature Made Multi Complete",
        "pattern": "%multi%vitamin%",
        "exclude": None,
        "source": "product label (photo verified)",
        "micros": {
            "vitamin_a_mcg": 750, "vitamin_c_mg": 180, "vitamin_k_mcg": 80,
            "thiamin_mg": 1.5, "riboflavin_mg": 1.7, "niacin_mg": 20,
            "vitamin_b6_mg": 4, "folate_mcg_dfe": 665, "vitamin_b12_mcg": 18,
            "vitamin_e_mg": 22.5, "magnesium_mg": 100, "zinc_mg": 15,
            "selenium_mcg": 70, "copper_mg": 2, "manganese_mg": 4,
        },
    },
    # Safe Catch salmon skipped — existing entries are composites (rice +
    # salmon + sauce) where per-3oz micronutrient values don't match the
    # full-can portion. Future entries will be logged with correct micros.
    {
        "name": "Magnesium supplement",
        "pattern": "%magnesium%supplement%",
        "exclude": None,
        "source": "product label (user-confirmed)",
        "micros": {
            "magnesium_mg": 100,
        },
    },
]

# Patterns that are too risky for automatic backfill (composite entries,
# multi-component names, variable portions). These should be handled
# manually or by ARIA going forward with the expanded nutrient fields.
#
# Skipped:
# - %egg%: Often in composites like "2 eggs + smoothie + Huel"
# - %huel%: Often in composites; "no Huel" entries false-positive
# - %broccoli%: Appears in Factor meals as minor component
# - %salmon%: Matches Factor Salmon Bowl (prepared meal, not raw salmon)
# Only safe matches are standalone entries (multivitamin, Safe Catch, supplements)


def find_matches(conn):
    """Find nutrition entries that match pantry items and need backfill."""
    matches = []

    for item in PANTRY_MICROS:
        pattern = item["pattern"]
        exclude = item["exclude"]

        if exclude:
            rows = conn.execute(
                """SELECT id, food_name, servings, nutrients
                   FROM nutrition_entries
                   WHERE food_name ILIKE %s AND food_name NOT ILIKE %s
                   ORDER BY date, time""",
                (pattern, exclude),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, food_name, servings, nutrients
                   FROM nutrition_entries
                   WHERE food_name ILIKE %s
                   ORDER BY date, time""",
                (pattern,),
            ).fetchall()

        for row in rows:
            nutrients = row["nutrients"] or {}
            # Find which micronutrients are missing from this entry
            delta = {}
            for field, value in item["micros"].items():
                if field not in nutrients or nutrients[field] is None:
                    delta[field] = value

            if delta:
                matches.append({
                    "id": row["id"],
                    "food_name": row["food_name"],
                    "pantry_item": item["name"],
                    "source": item["source"],
                    "delta": delta,
                })

    return matches


def apply_backfill(conn, matches):
    """Apply the backfill updates."""
    import psycopg.types.json

    updated = 0
    for match in matches:
        delta_jsonb = psycopg.types.json.Jsonb(match["delta"])
        # Use || to merge new keys. The delta only contains keys that are
        # absent from the current nutrients, so no overwrites occur.
        conn.execute(
            "UPDATE nutrition_entries SET nutrients = nutrients || %s WHERE id = %s",
            (delta_jsonb, match["id"]),
        )
        updated += 1

    return updated


def main():
    apply = "--apply" in sys.argv

    with db.get_conn() as conn:
        matches = find_matches(conn)

        if not matches:
            print("No entries need backfill.")
            return

        print(f"Found {len(matches)} entries to backfill:\n")
        for m in matches:
            fields = ", ".join(f"{k}={v}" for k, v in m["delta"].items())
            print(f"  [{m['id']}] {m['food_name']}")
            print(f"    → match: {m['pantry_item']} (source: {m['source']})")
            print(f"    → add: {fields}")
            print()

        if apply:
            count = apply_backfill(conn, matches)
            print(f"Applied {count} updates.")
        else:
            print("Dry run — no changes made. Use --apply to execute.")


if __name__ == "__main__":
    main()
