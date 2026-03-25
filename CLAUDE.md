# ARIA — Claude Code Rules

## RULE ZERO — DO NOT ACT WITHOUT PERMISSION

When the user asks you to investigate, analyze, research, propose, plan, suggest, review, explain, or look into something: present your findings and STOP. Do NOT proceed to implementation. Do NOT write code. Do NOT edit files. Do NOT create files.

Wait for EXPLICIT approval before making any changes. "Go ahead", "do it", "yes", "implement it" = permission. Anything else = no permission.

This applies even when:
- You are confident in the solution
- The fix seems obvious or small
- You have momentum from investigation
- You think you're saving the user time

When you ask the user a question ("Want me to do X?"), WAIT for their answer. Do not answer your own question by doing X.

Proposing ≠ permission. Investigating ≠ permission. Understanding ≠ permission.

## Verify Before Execute

NEVER run commands based on guesses or assumptions. Before any database query, read `schema.sql` or the relevant `*_store.py` for correct column names. Before any system command, verify correct flags. Before any API call, read the endpoint code. One correct command beats three failed attempts. Wrong write commands can destroy data.

## Integrity

Never present a guess as fact. If unsure, say "I think" or "I'm not sure." Never fabricate explanations for failures — say "I don't know" rather than invent a plausible-sounding cause. Verify claims with actual evidence (code, logs, data) before asserting them.

## System Environment

- Gentoo Linux, OpenRC (NOT systemd)
- Python 3.13 — always use `./venv/bin/python` or `./venv/bin/pytest` (no system pip)
- PostgreSQL 17: `postgresql://aria@/aria` (Unix socket, trust auth)
- `config.py` is gitignored — contains secrets, never commit or display its contents
- Tests: `./venv/bin/pytest tests/ -v` (uses disposable `aria_test` database)
- Web page fetching: prefer `curl -s` or `lynx -dump -nolist` for speed (~0.3s). When those return empty or garbled content (JS-rendered pages, SPAs, dynamic content), fall back to `./venv/bin/python fetch_page.py "URL"` which renders JavaScript via headless Chromium (~2-3s). fetch_page.py works on sites that block simple HTTP requests (Reddit, Wikipedia, Amazon, news sites, SPAs).

## Architecture Constraints

- All data stores use PostgreSQL — never create new JSON file stores
- All request paths use unified `build_request_context()` in context.py — never add context inline in individual endpoints
- ACTION blocks (`<!--ACTION::{}-->`) are the ONLY persistent storage — Claude session memory is ephemeral, cleared on daemon restart
- Delivery routing uses `set_delivery` ACTION block + handler enforcement — never keyword-parse user intent for critical decisions (keywords are fine for context injection where failure is benign)
- New stores: table in `schema.sql`, `db.get_conn()`, `serialize_row()` dicts, function signatures return `list[dict]` with string dates

## Testing Safety

CRITICAL: Tests that mock locally-imported modules MUST patch at the MODULE level. Use `patch("httpx.post")`, NOT `patch("tick.httpx")`. Incorrect patching caused a test to hit the live daemon and push audio to the user's phone.

- Mock stores in the module where the function LIVES: `actions.X_store` for process_actions(), `context.X_store` for context functions, `daemon.X` for daemon endpoints
- Never pollute production data with test data — always clean up after smoke tests
- When told to STOP, actually STOP — do not attempt fixes or re-runs

## External API Data

Never trust types from external APIs. Always cast with `int()` or `float()` before comparisons. Fitbit has returned string values where ints were documented, causing runtime TypeErrors.

## Documentation Checklist

When making code changes, update ALL relevant documentation before committing. Check each of these:

- `CHANGELOG.md` — add entry under current version (or create new version section)
- `MEMORY.md` — update version, architecture, test count, or any section affected by the change
- `data/projects/aria.md` — update status, "What's Working", and "Next Steps" if features changed
- `CODE_REVIEW.md` — update if the change resolves or relates to a tracked issue
- `system_prompt.py` — update if ACTION blocks, tools, or behavioral rules changed
- `config.example.py` — update if new config values were added
- `schema.sql` — update if database tables or indexes changed
- `tests/` — add or update tests for changed functionality
