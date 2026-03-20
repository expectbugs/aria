"""Nutrition tracking store — structured per-item intake with daily totals.

Stores individual food items with full nutrient breakdown in PostgreSQL.
Daily totals computed via SQL aggregation. Integrates with Fitbit
calorie burn data for net energy balance.

All nutrient values stored PER SERVING as printed on the label.
Actual intake = label value × servings consumed.
"""

import logging
import uuid
from datetime import datetime, date, timedelta
from typing import Optional

import psycopg.types.json

import db
import fitbit_store

log = logging.getLogger("aria.nutrition")

# Daily targets from diet_reference.md — used for limit checking
DAILY_TARGETS = {
    "calories": {"min": 1600, "max": 1900, "unit": "kcal", "label": "Calories"},
    "protein_g": {"min": 100, "max": 130, "unit": "g", "label": "Protein"},
    "dietary_fiber_g": {"min": 25, "max": 35, "unit": "g", "label": "Fiber"},
    "added_sugars_g": {"min": 0, "max": 10, "warn": 25, "hard_limit": 36,
                        "unit": "g", "label": "Added sugar"},
    "sodium_mg": {"min": 1200, "max": 1800, "unit": "mg", "label": "Sodium"},
    "saturated_fat_g": {"min": 0, "max": 15, "unit": "g", "label": "Saturated fat"},
    "total_sugars_g": {"min": 0, "max": 36, "unit": "g", "label": "Total sugars"},
}

# All tracked nutrients (matches FDA label + omega-3 for NAFLD)
NUTRIENT_FIELDS = [
    "calories", "total_fat_g", "saturated_fat_g", "trans_fat_g",
    "cholesterol_mg", "sodium_mg", "total_carb_g", "dietary_fiber_g",
    "total_sugars_g", "added_sugars_g", "protein_g",
    "vitamin_d_mcg", "calcium_mg", "iron_mg", "potassium_mg",
    "omega3_mg",
]


def add_item(
    food_name: str,
    meal_type: str = "snack",
    nutrients: dict | None = None,
    servings: float = 1.0,
    serving_size: str = "",
    source: str = "label_photo",
    notes: str = "",
    entry_date: str | None = None,
    entry_time: str | None = None,
) -> dict:
    """Add a nutrition entry.

    nutrients: dict of nutrient_name -> value PER SERVING (as on label).
               Use None for unknown values, not 0.
    servings: how many servings actually consumed.
    """
    if servings <= 0:
        log.warning("Nutrition entry '%s' had servings=%s, defaulting to 1.0",
                     food_name, servings)
        servings = 1.0

    item_id = str(uuid.uuid4())[:8]
    now = datetime.now()
    d = entry_date or now.strftime("%Y-%m-%d")
    t = entry_time or now.strftime("%H:%M")

    with db.get_conn() as conn:
        row = conn.execute(
            """INSERT INTO nutrition_entries
               (id, date, time, meal_type, food_name, source, servings, serving_size, nutrients, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (item_id, d, t, meal_type, food_name, source, servings,
             serving_size, psycopg.types.json.Jsonb(nutrients or {}), notes),
        ).fetchone()
    log.info("Nutrition logged: %s (%s) — %s servings", food_name, meal_type, servings)
    return db.serialize_row(row)


def delete_item(item_id: str) -> bool:
    """Delete a nutrition entry by ID."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM nutrition_entries WHERE id = %s", (item_id,))
    return cur.rowcount > 0


def get_items(day: str | None = None, meal_type: str | None = None,
              days: int | None = None) -> list[dict]:
    """Get nutrition entries, newest first."""
    clauses = []
    params = []
    if day:
        clauses.append("date = %s")
        params.append(day)
    elif days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        clauses.append("date >= %s")
        params.append(cutoff)
    if meal_type:
        clauses.append("meal_type = %s")
        params.append(meal_type)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM nutrition_entries {where} ORDER BY date DESC, time DESC",
            params,
        ).fetchall()
    return [db.serialize_row(r) for r in rows]


def get_daily_totals(day: str | None = None) -> dict:
    """Compute nutrient totals for a day via SQL aggregation.

    Returns: {nutrient_name: total_value, ...} with values multiplied
    by servings consumed. None values are excluded from sums.
    """
    if not day:
        day = date.today().isoformat()

    # Build SQL SUM expressions for each nutrient
    sums = ", ".join(
        f"COALESCE(SUM(CASE WHEN nutrients->>'{f}' IS NOT NULL "
        f"THEN (nutrients->>'{f}')::float * servings END), 0) AS {f}"
        for f in NUTRIENT_FIELDS
    )

    with db.get_conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS item_count, {sums} FROM nutrition_entries WHERE date = %s",
            (day,),
        ).fetchone()

    totals = dict(row)
    # Round for readability
    for field in NUTRIENT_FIELDS:
        totals[field] = round(totals[field], 1)
    return totals


def get_net_calories(day: str | None = None) -> dict:
    """Compute net calorie balance for a day.

    Returns: {consumed, burned, net, target_deficit, on_track}
    Burned comes from Fitbit activity data (includes BMR).
    """
    if not day:
        day = date.today().isoformat()

    totals = get_daily_totals(day)
    consumed = totals.get("calories", 0)

    # Get Fitbit calorie burn
    activity = fitbit_store.get_activity_summary(day)
    burned = activity.get("calories_total", 0) if activity else 0

    net = consumed - burned
    target_deficit_min = 500
    target_deficit_max = 1000

    return {
        "consumed": round(consumed),
        "burned": burned,
        "net": round(net),
        "target_deficit_min": target_deficit_min,
        "target_deficit_max": target_deficit_max,
        "on_track": net <= -target_deficit_min if burned > 0 else None,
    }


def check_limits(day: str | None = None) -> list[str]:
    """Check daily totals against targets. Returns list of warnings/status strings."""
    if not day:
        day = date.today().isoformat()

    totals = get_daily_totals(day)
    warnings = []

    if totals["item_count"] == 0:
        return []

    for nutrient, target in DAILY_TARGETS.items():
        value = totals.get(nutrient, 0)
        label = target["label"]
        unit = target["unit"]

        hard_limit = target.get("hard_limit")
        if hard_limit and value >= hard_limit:
            warnings.append(f"OVER LIMIT: {label} at {value}{unit} — hard limit is {hard_limit}{unit}")
        elif target.get("warn") and value >= target["warn"]:
            warnings.append(f"WARNING: {label} at {value}{unit} — approaching limit of {target.get('hard_limit', target['max'])}{unit}")
        elif value > target["max"]:
            warnings.append(f"{label} at {value}{unit} — above target max of {target['max']}{unit}")

    # Check calorie balance
    net = get_net_calories(day)
    if net["burned"] > 0:
        if net["net"] > 0:
            warnings.append(
                f"Calorie surplus: {net['consumed']} consumed - {net['burned']} burned = "
                f"+{net['net']} net (target: -{net['target_deficit_min']} to -{net['target_deficit_max']})"
            )

    # Positive notes
    if totals.get("dietary_fiber_g", 0) >= 25:
        warnings.append(f"Fiber on track: {totals['dietary_fiber_g']}g (target 25-35g)")
    if totals.get("protein_g", 0) >= 100:
        warnings.append(f"Protein on track: {totals['protein_g']}g (target 100-130g)")

    return warnings


def get_context(day: str | None = None) -> str:
    """Build nutrition context string for ARIA injection."""
    if not day:
        day = date.today().isoformat()

    totals = get_daily_totals(day)
    if totals["item_count"] == 0:
        return ""

    # Today's items
    items = get_items(day=day)
    parts = [f"Nutrition today ({totals['item_count']} items logged):"]

    for item in reversed(items):  # chronological order
        servings = item.get("servings", 1)
        cal = item.get("nutrients", {}).get("calories")
        cal_str = f" — {round(cal * servings)} cal" if cal else ""
        srv_str = f" ({servings} servings)" if servings != 1 else ""
        parts.append(f"  - [id={item['id']}] {item['time']} {item['meal_type']}: "
                      f"{item['food_name']}{srv_str}{cal_str}")

    # Daily totals
    parts.append(f"\nDaily totals:")
    parts.append(f"  Calories: {totals['calories']:.0f} / 1,600-1,900 target")
    parts.append(f"  Protein: {totals['protein_g']:.0f}g / 100-130g")
    parts.append(f"  Fiber: {totals['dietary_fiber_g']:.0f}g / 25-35g")
    parts.append(f"  Added sugar: {totals['added_sugars_g']:.0f}g / <10g (hard limit 36g)")
    parts.append(f"  Saturated fat: {totals['saturated_fat_g']:.0f}g / <15g")
    parts.append(f"  Sodium: {totals['sodium_mg']:.0f}mg / 1,200-1,800mg")
    parts.append(f"  Total fat: {totals['total_fat_g']:.0f}g")
    parts.append(f"  Total carbs: {totals['total_carb_g']:.0f}g")

    if totals.get("omega3_mg", 0) > 0:
        parts.append(f"  Omega-3: {totals['omega3_mg']:.0f}mg")

    # Net calorie balance
    net = get_net_calories(day)
    if net["burned"] > 0:
        parts.append(f"\nCalorie balance: {net['consumed']} consumed - {net['burned']} burned = {net['net']} net")
        if net["on_track"] is True:
            parts.append(f"  On track for target deficit")
        elif net["on_track"] is False:
            if net["net"] > 0:
                parts.append(f"  Surplus: +{net['net']} cal (target: deficit of 500-1,000)")
            else:
                parts.append(f"  Deficit: {-net['net']} cal (target: 500-1,000)")

    # Warnings
    warnings = check_limits(day)
    if warnings:
        parts.append(f"\nAlerts:")
        for w in warnings:
            parts.append(f"  - {w}")

    return "\n".join(parts)


def get_weekly_summary() -> str:
    """Build a weekly nutrition summary for briefings."""
    week_start = (date.today() - timedelta(days=6)).isoformat()

    # Single query for all days
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT date,
                COUNT(*) AS item_count,
                COALESCE(SUM(CASE WHEN nutrients->>'calories' IS NOT NULL
                    THEN (nutrients->>'calories')::float * servings END), 0) AS calories,
                COALESCE(SUM(CASE WHEN nutrients->>'protein_g' IS NOT NULL
                    THEN (nutrients->>'protein_g')::float * servings END), 0) AS protein_g,
                COALESCE(SUM(CASE WHEN nutrients->>'dietary_fiber_g' IS NOT NULL
                    THEN (nutrients->>'dietary_fiber_g')::float * servings END), 0) AS dietary_fiber_g,
                COALESCE(SUM(CASE WHEN nutrients->>'added_sugars_g' IS NOT NULL
                    THEN (nutrients->>'added_sugars_g')::float * servings END), 0) AS added_sugars_g,
                COALESCE(SUM(CASE WHEN nutrients->>'omega3_mg' IS NOT NULL
                    THEN (nutrients->>'omega3_mg')::float * servings END), 0) AS omega3_mg
            FROM nutrition_entries
            WHERE date >= %s
            GROUP BY date""",
            (week_start,),
        ).fetchall()

    if not rows:
        return ""

    days_logged = len(rows)
    cal_totals = [r["calories"] for r in rows]
    protein_totals = [r["protein_g"] for r in rows]
    fiber_totals = [r["dietary_fiber_g"] for r in rows]
    sugar_totals = [r["added_sugars_g"] for r in rows]
    omega_days = sum(1 for r in rows if r["omega3_mg"] > 0)

    parts = [f"Nutrition summary (last 7 days, {days_logged} days logged):"]
    if cal_totals:
        parts.append(f"  Avg calories: {sum(cal_totals)/len(cal_totals):.0f} / 1,600-1,900 target")
    if protein_totals:
        parts.append(f"  Avg protein: {sum(protein_totals)/len(protein_totals):.0f}g / 100-130g")
    if fiber_totals:
        parts.append(f"  Avg fiber: {sum(fiber_totals)/len(fiber_totals):.0f}g / 25-35g")
    if sugar_totals:
        parts.append(f"  Avg added sugar: {sum(sugar_totals)/len(sugar_totals):.0f}g / <10g")

    if omega_days:
        parts.append(f"  Omega-3 days: {omega_days}/7 (target: 3-4)")
    else:
        parts.append(f"  No omega-3 logged this week (target: 3-4 fish meals)")

    return "\n".join(parts)
