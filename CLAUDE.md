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
- **Multi-user threading (v0.9.5+):** when adding/changing any function that might be multi-user aware (auth, per-user state, data writes, delivery routing, context building), add a `user_key: str = "adam"` parameter and thread it through. NEVER hardcode "adam" assumptions. The chain today: `webhook_sms` → `_process_sms` → `_check_pending_confirmation` / `_route_query` / `_verify_and_maybe_retry` / `process_actions` / `execute_delivery` / `_get_context_for_text` / `build_request_context` / `gather_always_context` / `get_recent_turns`. Session pools + Action Arias are REGISTRIES keyed by user_key — never revert to singletons. `TRUSTED_USERS` in config is the auth source of truth; adding another user = adding another dict entry + (optionally) spawning their pool in lifespan. Adam-exclusive writes are enforced in `actions._ADAM_EXCLUSIVE_ACTIONS`.
- ACTION blocks (`<!--ACTION::{}-->`) are the ONLY persistent storage — Claude session memory is ephemeral, cleared on daemon restart
- Delivery routing uses `set_delivery` ACTION block + handler enforcement — never keyword-parse user intent for critical decisions (keywords are fine for context injection where failure is benign)
- **Telnyx SMS/MMS (v0.9.2+):** SMS/MMS via Telnyx SDK (`sms.py`). Webhook uses ED25519 signature verification via PyNaCl (headers: `webhook-signature`, `webhook-timestamp`). Webhook always returns 200 (even on invalid sig/malformed JSON) to prevent retry storms. Idempotency via atomic `INSERT ON CONFLICT` on `processed_webhooks`. Image delivery split by source (v0.9.4): automated triggers (nudges, monitor, bug alerts) push via Tasker `push_image.py` (free); user-initiated (SMS/voice/file/CLI) use `send_image_mms()` or `send_mms.py` CLI (Telnyx MMS, works off-network). **GSM-7 normalization (v0.9.7):** `sms._normalize_for_sms()` substitutes non-GSM-7 chars (em-dash, smart quotes, backticks, bullets, ©/®/™, arrows, zero-width) with ASCII equivalents before send. `split_sms` picks 1500 chars for GSM-7, 600 for UCS-2. Prevents silent 22-segment 40302 drops. Never mention carrier brand names (Verizon, T-Mobile, Visible, Fi) in MMS content — filtered as impersonation.
- **Prompt caching (v0.4.28):** System prompt passed as list of content blocks (static cached + dynamic context). Tools cached via `CACHED_TOOLS` with `cache_control` on last entry. Never pass system prompt as a plain string — always use the block-based format in `aria_api.py`.
- **Extended thinking bypass (v0.4.28):** Simple queries (timers, weather, greetings) skip thinking via `_is_simple_query()`. Add new patterns to `_SIMPLE_QUERY_STARTS` or `_SIMPLE_QUERY_EXACT` in `aria_api.py`. Defaults to thinking ON.
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
