"""Rolling conversation history from PostgreSQL request_log.

Provides recent conversation turns formatted as Anthropic API messages
for ARIA Primary's stateless per-call context. Each API call gets the
last N turns so ARIA maintains conversational continuity.
"""

import logging
import re

import db
import config

log = logging.getLogger("aria")

# Channel tag prefixes to strip from stored input text
_CHANNEL_PREFIX = re.compile(
    r'^\[(voice|stt|sms:[+\d]+|file:[^\]]+)\]\s*', re.IGNORECASE
)

# Max characters per turn to prevent a single long response from
# dominating the history window
MAX_CHARS_PER_TURN = 4000


def get_recent_turns(n: int | None = None) -> list[dict]:
    """Pull the last N conversation turns from request_log.

    Returns a list of Anthropic API message dicts:
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]

    Filters out error entries, empty entries, and STT-only transcriptions.
    Strips channel prefixes ([voice], [sms:+N], [file:X]) from user input.
    Truncates very long responses to MAX_CHARS_PER_TURN.
    """
    if n is None:
        n = getattr(config, "ARIA_HISTORY_TURNS", 25)

    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT input, response FROM request_log
               WHERE status = 'ok'
               AND input IS NOT NULL AND input != ''
               AND response IS NOT NULL AND response != ''
               ORDER BY timestamp DESC
               LIMIT %s""",
            (n,),
        ).fetchall()

    if not rows:
        return []

    messages = []
    for row in reversed(rows):  # chronological order (oldest first)
        user_text = row["input"] or ""
        assistant_text = row["response"] or ""

        # Skip STT-only entries (no Claude response)
        if user_text.startswith("[stt]"):
            continue

        # Strip channel prefixes — keep the text, drop the tag
        user_text = _CHANNEL_PREFIX.sub("", user_text).strip()

        if not user_text or not assistant_text:
            continue

        # Truncate very long responses
        if len(assistant_text) > MAX_CHARS_PER_TURN:
            assistant_text = assistant_text[:MAX_CHARS_PER_TURN] + "..."

        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": assistant_text})

    return messages
