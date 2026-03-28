#!/usr/bin/env python3
"""ARIA data query helper — CLI replacement for API tool definitions.

Called by ARIA's CLI sessions via the Bash tool during response generation.
Output format is identical to the API tool handlers in aria_api.py.

Usage:
    ./venv/bin/python query.py health --days 7 --category pain
    ./venv/bin/python query.py nutrition --date 2026-03-25
    ./venv/bin/python query.py vehicle --limit 10
    ./venv/bin/python query.py legal --limit 10
    ./venv/bin/python query.py calendar --start 2026-03-25 --end 2026-04-01
    ./venv/bin/python query.py conversations --days 7 --search "salmon"
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Set up path so we can import project modules (same pattern as tick.py)
sys.path.insert(0, str(Path(__file__).parent))

import config
import db
import calendar_store
import health_store
import legal_store
import nutrition_store
import vehicle_store


# ---------------------------------------------------------------------------
# Format functions — output identical to aria_api._handle_tool_call()
# ---------------------------------------------------------------------------

def format_health(entries: list[dict]) -> str:
    if not entries:
        return "No health entries found for the specified criteria."
    lines = []
    for h in entries:
        line = f"[id={h['id']}] {h['date']} {h['category']}: {h['description']}"
        if h.get("severity"):
            line += f" (severity {h['severity']}/10)"
        if h.get("sleep_hours"):
            line += f" ({h['sleep_hours']}h sleep)"
        if h.get("meal_type"):
            line += f" [{h['meal_type']}]"
        lines.append(line)
    return f"Health log ({len(entries)} entries):\n" + "\n".join(lines)


def format_nutrition(items: list[dict], totals: dict, day: str) -> str:
    if not items and totals.get("item_count", 0) == 0:
        return f"No nutrition entries for {day}."
    lines = [f"Nutrition for {day} ({totals.get('item_count', 0)} items):"]
    for item in reversed(items):  # chronological
        servings = item.get("servings", 1)
        cal = item.get("nutrients", {}).get("calories")
        cal_str = f" — {round(cal * servings)} cal" if cal else ""
        srv_str = f" ({servings} servings)" if servings != 1 else ""
        lines.append(f"  [id={item['id']}] {item.get('time', '')} "
                     f"{item.get('meal_type', '')}: {item['food_name']}"
                     f"{srv_str}{cal_str}")
    lines.append(f"\nDaily totals:")
    lines.append(f"  Calories: {totals.get('calories', 0):.0f}")
    lines.append(f"  Protein: {totals.get('protein_g', 0):.0f}g")
    lines.append(f"  Fiber: {totals.get('dietary_fiber_g', 0):.0f}g")
    lines.append(f"  Added sugar: {totals.get('added_sugars_g', 0):.0f}g")
    lines.append(f"  Sodium: {totals.get('sodium_mg', 0):.0f}mg")
    if totals.get("omega3_mg", 0) > 0:
        lines.append(f"  Omega-3: {totals['omega3_mg']:.0f}mg")
    for field, label, unit in [
        ("choline_mg", "Choline", "mg"), ("magnesium_mg", "Magnesium", "mg"),
        ("zinc_mg", "Zinc", "mg"), ("vitamin_c_mg", "Vitamin C", "mg"),
        ("selenium_mcg", "Selenium", "mcg"), ("vitamin_k_mcg", "Vitamin K", "mcg"),
    ]:
        if totals.get(field, 0) > 0:
            lines.append(f"  {label}: {totals[field]:.0f}{unit}")
    return "\n".join(lines)


def format_vehicle(entries: list[dict], latest: dict) -> str:
    if not entries:
        return "No vehicle maintenance entries found."
    lines = [f"Vehicle log ({len(entries)} entries):"]
    for v in entries:
        line = f"  [id={v['id']}] {v['date']} {v['event_type']}: {v['description']}"
        if v.get("mileage"):
            line += f" ({v['mileage']} mi)"
        if v.get("cost"):
            line += f" (${v['cost']:.2f})"
        lines.append(line)
    if latest:
        lines.append("\nLatest per service type:")
        for t, e in latest.items():
            line = f"  {t}: {e['date']}"
            if e.get("mileage"):
                line += f" at {e['mileage']} mi"
            lines.append(line)
    return "\n".join(lines)


def format_legal(entries: list[dict], upcoming: list[dict]) -> str:
    if not entries and not upcoming:
        return "No legal case entries found."
    lines = []
    if entries:
        lines.append(f"Legal case log ({len(entries)} entries):")
        for l in entries:
            lines.append(f"  [id={l['id']}] {l['date']} {l['entry_type']}: "
                         f"{l['description']}")
    if upcoming:
        lines.append("\nUpcoming legal dates:")
        for u in upcoming:
            lines.append(f"  {u['date']}: {u['description']}")
    return "\n".join(lines)


def format_calendar(events: list[dict], start: str, end: str) -> str:
    if not events:
        return f"No calendar events between {start} and {end}."
    lines = [f"Calendar events ({start} to {end}):"]
    for e in events:
        time_str = f" at {e['time']}" if e.get("time") else ""
        lines.append(f"  [id={e['id']}] {e['date']} {e['title']}{time_str}")
    return "\n".join(lines)


def format_conversations(rows: list[dict], days: int, search: str) -> str:
    if not rows:
        msg = f"No conversations found in the last {days} days"
        if search:
            msg += f" matching '{search}'"
        return msg + "."
    lines = [f"Past conversations ({len(rows)} found):"]
    for r in rows:
        ts = db.serialize_row(r).get("timestamp", "")
        time_str = ts[11:16] if len(ts) >= 16 else ""
        date_str = ts[:10] if len(ts) >= 10 else ""
        inp = (r.get("input") or "")[:120]
        resp = (r.get("response") or "")[:200]
        lines.append(f"  [{date_str} {time_str}] User: {inp}")
        lines.append(f"    ARIA: {resp}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_health(args):
    entries = health_store.get_entries(days=args.days, category=args.category)
    return format_health(entries)


def cmd_nutrition(args):
    items = nutrition_store.get_items(day=args.date)
    totals = nutrition_store.get_daily_totals(args.date)
    return format_nutrition(items, totals, args.date)


def cmd_vehicle(args):
    entries = vehicle_store.get_entries(limit=args.limit)
    latest = vehicle_store.get_latest_by_type()
    return format_vehicle(entries, latest)


def cmd_legal(args):
    entries = legal_store.get_entries(limit=args.limit)
    upcoming = legal_store.get_upcoming_dates()
    return format_legal(entries, upcoming)


def cmd_calendar(args):
    events = calendar_store.get_events(start=args.start, end=args.end)
    return format_calendar(events, args.start, args.end)


def cmd_conversations(args):
    days = args.days
    search = args.search or ""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        if search:
            rows = conn.execute(
                """SELECT timestamp, input, response FROM request_log
                   WHERE timestamp >= %s AND status = 'ok'
                   AND (input ILIKE %s OR response ILIKE %s)
                   ORDER BY timestamp DESC LIMIT 50""",
                (cutoff, f"%{search}%", f"%{search}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT timestamp, input, response FROM request_log
                   WHERE timestamp >= %s AND status = 'ok'
                   ORDER BY timestamp DESC LIMIT 30""",
                (cutoff,),
            ).fetchall()
    return format_conversations(rows, days, search)


# ---------------------------------------------------------------------------
# Self-reporting for tool traces
# ---------------------------------------------------------------------------

def _log_trace(tool_name: str, tool_input: dict, output: str):
    """Log this query invocation to tool_traces for LoRA training data."""
    if not getattr(config, "COLLECT_TOOL_TRACES", False):
        return
    try:
        import training_store
        training_store.log_tool_trace(
            request_input="[cli-self-report]",
            tool_name=tool_name,
            tool_input=json.dumps(tool_input),
            tool_output=output[:2000],  # cap output size
        )
    except Exception:
        pass  # non-fatal


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ARIA data query helper",
        prog="query.py",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # health
    p_health = subparsers.add_parser("health", help="Query health log")
    p_health.add_argument("--days", type=int, default=None)
    p_health.add_argument("--category", type=str, default=None)

    # nutrition
    p_nutr = subparsers.add_parser("nutrition", help="Query nutrition log")
    p_nutr.add_argument("--date", type=str, required=True)

    # vehicle
    p_veh = subparsers.add_parser("vehicle", help="Query vehicle log")
    p_veh.add_argument("--limit", type=int, default=20)

    # legal
    p_legal = subparsers.add_parser("legal", help="Query legal log")
    p_legal.add_argument("--limit", type=int, default=20)

    # calendar
    p_cal = subparsers.add_parser("calendar", help="Query calendar events")
    p_cal.add_argument("--start", type=str, required=True)
    p_cal.add_argument("--end", type=str, required=True)

    # conversations
    p_conv = subparsers.add_parser("conversations", help="Search conversations")
    p_conv.add_argument("--days", type=int, default=7)
    p_conv.add_argument("--search", type=str, default=None)

    args = parser.parse_args(argv)

    handlers = {
        "health": cmd_health,
        "nutrition": cmd_nutrition,
        "vehicle": cmd_vehicle,
        "legal": cmd_legal,
        "calendar": cmd_calendar,
        "conversations": cmd_conversations,
    }

    try:
        output = handlers[args.command](args)
        print(output)
        _log_trace(f"query_{args.command}", vars(args), output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
