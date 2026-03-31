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
import gmail_store
import ambient_store
import commitment_store
import person_store


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


def format_email(results: list[dict]) -> str:
    if not results:
        return "No emails found matching the criteria."
    lines = [f"Email search ({len(results)} results):"]
    for r in results:
        sender = r.get("from_name") or r.get("from_address", "?")
        subject = r.get("subject", "(no subject)")
        ts = r.get("timestamp", "")
        date_str = ts[:10] if len(ts) >= 10 else ""
        lines.append(f"  [{date_str}] {sender}: {subject}")
    return "\n".join(lines)


def format_email_full(email: dict | None) -> str:
    """Format a single email with full body for --id lookup."""
    if not email:
        return "Email not found."
    lines = [f"Email ID: {email.get('id', '?')}"]
    lines.append(f"From: {email.get('from_name', '')} <{email.get('from_address', '?')}>")
    lines.append(f"To: {email.get('to_addresses', '?')}")
    lines.append(f"Subject: {email.get('subject', '(no subject)')}")
    lines.append(f"Date: {email.get('timestamp', '?')}")
    if email.get('has_attachments'):
        paths = email.get('attachment_paths') or []
        lines.append(f"Attachments: {len(paths)} file(s)")
        for p in paths:
            lines.append(f"  - {p}")
    lines.append(f"\n--- Body ---\n{email.get('body', '(no body)')}")
    return "\n".join(lines)


def cmd_email(args):
    if args.email_id:
        email = gmail_store.get_email(args.email_id)
        return format_email_full(email)
    elif args.search:
        results = gmail_store.search_emails(args.search, limit=args.limit)
    elif args.sender:
        results = gmail_store.search_emails(args.sender, limit=args.limit)
    else:
        results = gmail_store.get_recent(hours=args.days * 24, limit=args.limit)
    return format_email(results)


# ---------------------------------------------------------------------------
# Ambient / recall / commitments / people (Phase 6)
# ---------------------------------------------------------------------------

def format_transcripts(transcripts: list[dict]) -> str:
    if not transcripts:
        return "No ambient transcripts found."
    lines = [f"Ambient transcripts ({len(transcripts)} results):"]
    for t in transcripts:
        ts = t.get("started_at", "?")
        time_str = ts[11:16] if isinstance(ts, str) and len(ts) >= 16 else ts[:10] if ts else "?"
        text = t.get("quality_text") or t.get("text", "")
        dur = t.get("duration_s", 0) or 0
        lines.append(f"  [{time_str}] ({dur:.1f}s) {text[:200]}")
    return "\n".join(lines)


def format_commitments(commits: list[dict]) -> str:
    if not commits:
        return "No commitments found."
    lines = [f"Commitments ({len(commits)}):"]
    for c in commits:
        line = f"  [id={c['id']}] {c['who']} → {c['what']}"
        if c.get("to_whom"):
            line += f" (to {c['to_whom']})"
        if c.get("due_date"):
            line += f" [due {c['due_date']}]"
        line += f" [{c['status']}]"
        lines.append(line)
    return "\n".join(lines)


def format_people(profiles: list[dict]) -> str:
    if not profiles:
        return "No person profiles found."
    lines = [f"Person profiles ({len(profiles)}):"]
    for p in profiles:
        line = f"  {p['name']}"
        if p.get("relationship"):
            line += f" ({p['relationship']})"
        if p.get("organization"):
            line += f" at {p['organization']}"
        line += f" — {p['mention_count']} mentions"
        if p.get("last_mentioned"):
            line += f", last {p['last_mentioned'][:10]}"
        if p.get("notes"):
            line += f"\n    Notes: {p['notes'][:200]}"
        lines.append(line)
    return "\n".join(lines)


def format_ambient_conversations(convs: list[dict]) -> str:
    if not convs:
        return "No ambient conversations found."
    lines = [f"Ambient conversations ({len(convs)}):"]
    for c in convs:
        ts = c.get("started_at", "?")
        time_str = ts[11:16] if isinstance(ts, str) and len(ts) >= 16 else ts[:10] if ts else "?"
        dur = c.get("duration_s", 0) or 0
        speakers = ", ".join(c.get("speakers", []))
        summary = c.get("summary") or c.get("title") or "(no summary)"
        lines.append(f"  [{time_str}] ({dur/60:.0f}min) {speakers}: {summary}")
    return "\n".join(lines)


def format_recall(results: list[dict]) -> str:
    if not results:
        return "No matching memories found."
    lines = [f"Recall results ({len(results)} matches):"]
    for r in results:
        ts = r.get("timestamp", "?")
        date_str = ts[:10] if ts else "?"
        lines.append(
            f"  [{date_str}] ({r.get('category', '?')}, score={r.get('score', 0)}) "
            f"{r.get('text', '')[:200]}"
        )
    return "\n".join(lines)


def cmd_ambient(args):
    if args.search:
        results = ambient_store.search(args.search, days=args.days, limit=args.limit)
    else:
        results = ambient_store.get_recent(hours=args.days * 24, limit=args.limit)
    return format_transcripts(results)


def cmd_recall(args):
    query = " ".join(args.query) if args.query else ""
    if not query:
        return "Usage: query.py recall \"what was that conversation about...\""
    try:
        import qdrant_store
        results = qdrant_store.search(query, limit=args.limit, days=args.days)
        return format_recall(results)
    except Exception as e:
        return f"Recall search unavailable: {e}"


def cmd_commitments(args):
    if args.person:
        results = commitment_store.get_by_person(args.person, status=args.status)
    elif args.status:
        if args.status == "open":
            results = commitment_store.get_open(limit=50)
        elif args.status == "overdue":
            results = commitment_store.get_overdue()
        else:
            results = commitment_store.get_recent(days=30, limit=50)
            results = [c for c in results if c.get("status") == args.status]
    else:
        results = commitment_store.get_recent(days=args.days, limit=50)
    return format_commitments(results)


def cmd_people(args):
    if args.name:
        profile = person_store.get(args.name)
        if profile:
            return format_people([profile])
        # Try search
        results = person_store.search(args.name)
        return format_people(results)
    return format_people(person_store.get_all(limit=args.limit))


def cmd_ambient_conversations(args):
    convs = ambient_store.get_conversations(days=args.days, limit=args.limit)
    return format_ambient_conversations(convs)


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

    # email
    p_email = subparsers.add_parser("email", help="Search emails")
    p_email.add_argument("--id", dest="email_id", type=str, default=None,
                         help="Get full email by message ID")
    p_email.add_argument("--search", type=str, default=None)
    p_email.add_argument("--from", dest="sender", type=str, default=None)
    p_email.add_argument("--days", type=int, default=7)
    p_email.add_argument("--limit", type=int, default=20)

    # Phase 6 — Ambient audio pipeline
    p_ambient = subparsers.add_parser("ambient", help="Search ambient transcripts")
    p_ambient.add_argument("--search", type=str, default=None)
    p_ambient.add_argument("--days", type=int, default=7)
    p_ambient.add_argument("--limit", type=int, default=20)

    p_recall = subparsers.add_parser("recall", help="Semantic recall search")
    p_recall.add_argument("query", nargs="*", help="Natural language recall query")
    p_recall.add_argument("--days", type=int, default=30)
    p_recall.add_argument("--limit", type=int, default=5)

    p_commits = subparsers.add_parser("commitments", help="Query commitments/promises")
    p_commits.add_argument("--status", type=str, default=None,
                           help="Filter by status: open, done, overdue")
    p_commits.add_argument("--person", type=str, default=None)
    p_commits.add_argument("--days", type=int, default=30)

    p_people = subparsers.add_parser("people", help="Query person profiles")
    p_people.add_argument("--name", type=str, default=None)
    p_people.add_argument("--limit", type=int, default=20)

    p_aconv = subparsers.add_parser("ambient-conversations",
                                     help="List ambient conversations")
    p_aconv.add_argument("--days", type=int, default=7)
    p_aconv.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)

    handlers = {
        "health": cmd_health,
        "nutrition": cmd_nutrition,
        "vehicle": cmd_vehicle,
        "legal": cmd_legal,
        "calendar": cmd_calendar,
        "conversations": cmd_conversations,
        "email": cmd_email,
        "ambient": cmd_ambient,
        "recall": cmd_recall,
        "commitments": cmd_commitments,
        "people": cmd_people,
        "ambient-conversations": cmd_ambient_conversations,
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
