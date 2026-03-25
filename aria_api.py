"""ARIA Primary — Anthropic API client with tool call support.

Replaces the Claude Code CLI session for primary ARIA requests.
Stateless per call, with rolling conversation history from PostgreSQL.
Read-only data access tools provide Tier 3 historical query capability.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

import config
import db
import calendar_store
import health_store
import legal_store
import nutrition_store
import vehicle_store
from conversation_history import get_recent_turns
from system_prompt import build_primary_prompt

log = logging.getLogger("aria")

# --- API Client Singleton ---

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    """Get or create the Anthropic API client."""
    global _client
    if _client is None:
        api_key_path = Path(getattr(config, "ANTHROPIC_API_KEY_FILE",
                                     config.DATA_DIR / "api_key.txt"))
        if api_key_path.exists():
            api_key = api_key_path.read_text().strip()
        else:
            api_key = getattr(config, "ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("No Anthropic API key configured")
        _client = anthropic.Anthropic(
            api_key=api_key,
            timeout=600.0,  # 10 min — matches CLAUDE_TIMEOUT for extended thinking
        )
        log.info("Anthropic API client initialized")
    return _client


# --- Tool Definitions ---

TOOLS = [
    {
        "name": "query_health_log",
        "description": (
            "Search health log entries (pain, sleep, exercise, symptoms, medication, meals). "
            "Use for historical health queries when data is not in the injected context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of past days to search (e.g., 7, 14, 30). Omit for all entries.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional filter: pain, sleep, exercise, symptom, medication, meal, nutrition, general",
                },
            },
        },
    },
    {
        "name": "query_nutrition_log",
        "description": (
            "Look up nutrition tracking entries and daily totals for a specific date. "
            "Returns individual food items with nutrients and computed daily totals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date to query in YYYY-MM-DD format",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "query_vehicle_log",
        "description": (
            "Look up vehicle maintenance history (oil changes, tire rotations, brake service, etc.)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default 20)",
                },
            },
        },
    },
    {
        "name": "query_legal_log",
        "description": (
            "Look up legal case entries and upcoming court dates. "
            "SENSITIVE — only use when the user explicitly asks about legal matters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default 20)",
                },
            },
        },
    },
    {
        "name": "query_calendar",
        "description": (
            "Look up calendar events in a date range. "
            "Use for historical calendar queries or looking further ahead than the current week."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_conversations",
        "description": (
            "Search past ARIA conversations from the request log. "
            "Use when the user asks about something discussed previously that is not in the rolling history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of past days to search (default 7)",
                },
                "search_text": {
                    "type": "string",
                    "description": "Optional text to search for in conversation input/responses",
                },
            },
        },
    },
]

# Tools with cache_control on the last entry — Anthropic caches everything
# up to the last cache_control breakpoint, so system prompt + all tools get cached
CACHED_TOOLS = list(TOOLS)
CACHED_TOOLS[-1] = {**TOOLS[-1], "cache_control": {"type": "ephemeral"}}


# --- Simple Query Detection (skip extended thinking) ---

# Two match modes:
#   _SIMPLE_QUERY_STARTS — match if query starts with this phrase
#   _SIMPLE_QUERY_EXACT  — match ONLY if query is exactly this phrase
_SIMPLE_QUERY_STARTS = [
    "how are you",
    "how do you spell",
    "what time is it",
    "what is the date",
    "what is the weather",
    "what's the weather",
    "how's the weather",
    # Setting things
    "set a timer",
    "set a reminder",
    "set an alarm",
    "set an appointment",
    "set an event",
    "add a timer",
    "add a reminder",
    "add an alarm",
    "add an appointment",
    "add an event",
    "create a timer",
    "create a reminder",
    "create an appointment",
    "create an event",
    # Cancelling / deleting / removing things
    "cancel timer",
    "cancel the timer",
    "cancel a timer",
    "cancel reminder",
    "cancel the reminder",
    "cancel a reminder",
    "cancel appointment",
    "cancel the appointment",
    "cancel a appointment",
    "cancel alarm",
    "cancel the alarm",
    "cancel my alarm",
    "cancel event",
    "cancel the event",
    "delete timer",
    "delete the timer",
    "delete reminder",
    "delete the reminder",
    "delete appointment",
    "delete the appointment",
    "delete event",
    "delete the event",
    "remove timer",
    "remove the timer",
    "remove reminder",
    "remove the reminder",
    "remove appointment",
    "remove the appointment",
    "remove event",
    "remove the event",
    "quick question",
    "thank you",
    "thanks",
    "never mind",
    "nevermind",
    "hey aria",
]

_SIMPLE_QUERY_EXACT = [
    "hello",
    "good afternoon",
    "good evening",
]


def _is_simple_query(text: str) -> bool:
    """Check if a query is simple enough to skip extended thinking.

    Conservative: only matches specific phrases that are virtually certain
    to not require deep reasoning. Defaults to thinking enabled.
    """
    text_lower = text.lower().strip()
    # Exact-only matches (greetings that often precede longer prompts)
    if text_lower in _SIMPLE_QUERY_EXACT:
        return True
    # Starts-with matches for phrases that begin a simple command
    for pattern in _SIMPLE_QUERY_STARTS:
        if text_lower == pattern or text_lower.startswith(pattern + " "):
            return True
        if (text_lower.startswith(pattern)
                and len(text_lower) > len(pattern)
                and text_lower[len(pattern)] in ".,!?"):
            return True
    return False


# --- Tool Handlers ---

def _handle_tool_call(name: str, params: dict) -> str:
    """Execute a tool call and return the result as a formatted string.

    All handlers are read-only database queries — no side effects.
    """
    try:
        if name == "query_health_log":
            days = params.get("days")
            category = params.get("category")
            entries = health_store.get_entries(days=days, category=category)
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

        elif name == "query_nutrition_log":
            day = params["date"]
            items = nutrition_store.get_items(day=day)
            totals = nutrition_store.get_daily_totals(day)
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

        elif name == "query_vehicle_log":
            limit = params.get("limit", 20)
            entries = vehicle_store.get_entries(limit=limit)
            if not entries:
                return "No vehicle maintenance entries found."
            latest = vehicle_store.get_latest_by_type()
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

        elif name == "query_legal_log":
            limit = params.get("limit", 20)
            entries = legal_store.get_entries(limit=limit)
            upcoming = legal_store.get_upcoming_dates()
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

        elif name == "query_calendar":
            start = params["start_date"]
            end = params["end_date"]
            events = calendar_store.get_events(start=start, end=end)
            if not events:
                return f"No calendar events between {start} and {end}."
            lines = [f"Calendar events ({start} to {end}):"]
            for e in events:
                time_str = f" at {e['time']}" if e.get("time") else ""
                lines.append(f"  [id={e['id']}] {e['date']} {e['title']}{time_str}")
            return "\n".join(lines)

        elif name == "query_conversations":
            days = params.get("days", 7)
            search = params.get("search_text", "")
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
            if not rows:
                return f"No conversations found in the last {days} days" + (
                    f" matching '{search}'" if search else "") + "."
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

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        log.error("Tool call %s failed: %s", name, e)
        return f"Error executing {name}: {e}"


# --- Main Query Function ---

async def ask_aria(user_text: str, extra_context: str = "",
                   file_blocks: list[dict] | None = None) -> str:
    """Send a query to ARIA Primary via the Anthropic API.

    Same signature as ask_claude() for drop-in replacement.
    Includes rolling conversation history and read-only tool access.

    Returns the response text (may contain ACTION blocks).
    """
    client = _get_client()

    model = getattr(config, "ARIA_MODEL", "claude-opus-4-6-20250610")
    max_tokens = getattr(config, "ARIA_MAX_TOKENS", 16384)
    thinking_budget = getattr(config, "ARIA_THINKING_BUDGET", 64000)
    always_think = getattr(config, "ARIA_ALWAYS_THINK", False)

    # Build system prompt as cached static block + uncached dynamic context
    static_prompt = build_primary_prompt()
    system_blocks = [
        {"type": "text", "text": static_prompt,
         "cache_control": {"type": "ephemeral"}},
    ]
    if extra_context:
        system_blocks.append(
            {"type": "text",
             "text": f"[CONTEXT]\n{extra_context}\n[/CONTEXT]"})

    # Build conversation history + current user message
    history = get_recent_turns()
    messages = list(history)  # copy to avoid mutating

    # Current user message (text or multimodal with file blocks)
    if file_blocks:
        user_content = [{"type": "text", "text": user_text}] + file_blocks
    else:
        user_content = user_text

    messages.append({"role": "user", "content": user_content})

    # API call kwargs — system and tools are cached across tool-call rounds
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": messages,
        "tools": CACHED_TOOLS,
    }

    # Extended thinking — skip for simple queries to save cost
    if thinking_budget > 0 and (always_think or not _is_simple_query(user_text)):
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget,
        }

    thinking_mode = "thinking" if "thinking" in kwargs else "direct"

    # Tool call loop — keep calling until we get a final text response
    max_tool_rounds = 10  # safety limit
    for _round in range(max_tool_rounds):
        try:
            response = client.messages.create(**kwargs)
        except anthropic.APITimeoutError:
            raise RuntimeError("Anthropic API timed out")
        except anthropic.APIError as e:
            raise RuntimeError(f"Anthropic API error: {e}")

        # Log token usage and cache stats
        if hasattr(response, 'usage') and response.usage:
            u = response.usage
            cache_write = getattr(u, 'cache_creation_input_tokens', 0) or 0
            cache_read = getattr(u, 'cache_read_input_tokens', 0) or 0
            log.info("API tokens: in=%d out=%d cache_write=%d cache_read=%d",
                     u.input_tokens, u.output_tokens, cache_write, cache_read)

        # Check if we need to handle tool calls
        if response.stop_reason == "tool_use":
            # Extract tool use blocks and execute them
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    log.info("Tool call: %s(%s)", block.name,
                             str(block.input)[:100])
                    result_text = _handle_tool_call(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

            # Append assistant response + tool results, then continue
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            # Update kwargs with extended messages for next round
            kwargs["messages"] = messages
            continue

        # Final response — extract text content
        text_parts = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            # Skip thinking blocks — they're internal reasoning

        result = "\n".join(text_parts)
        log.info("API response: %d chars, %d tool rounds, model=%s, mode=%s",
                 len(result), _round, model, thinking_mode)
        return result

    # Safety: too many tool rounds
    log.error("Tool call loop exceeded %d rounds", max_tool_rounds)
    raise RuntimeError("Too many tool call rounds — possible loop")
