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
MAX_CHARS_PER_TURN = 3000

# Strip processed ACTION blocks from history — they're persisted in the DB
# and accessible via tool calls, so they just waste tokens in conversation context
_ACTION_BLOCK = re.compile(r'<!--ACTION::.*?-->', re.DOTALL)


def get_recent_turns(n: int | None = None,
                     user_key: str = "adam") -> list[dict]:
    """Pull the last N conversation turns from request_log for a given user.

    user_key routing (based on the [sms:+N] channel prefix stored in input):
    - 'adam': everything EXCEPT Becky's SMS prefix. Adam's voice/file/CLI/
              SMS history all belong to him.
    - 'becky': ONLY Becky's SMS prefix.

    Returns a list of Anthropic API message dicts:
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]

    Filters out error entries, empty entries, and STT-only transcriptions.
    Strips channel prefixes ([voice], [sms:+N], [file:X]) from user input.
    Truncates very long responses to MAX_CHARS_PER_TURN.
    """
    if n is None:
        n = getattr(config, "ARIA_HISTORY_TURNS", 10)

    # Build user_key filter. We use config.BECKY_PHONE_NUMBER so a Visible
    # port (or any future number change) only requires updating config.
    becky_phone = getattr(config, "BECKY_PHONE_NUMBER", None)

    if user_key == "becky" and becky_phone:
        user_filter_sql = "AND input LIKE %s"
        user_filter_arg = (f"[sms:{becky_phone}]%",)
    elif user_key == "adam" and becky_phone:
        user_filter_sql = "AND input NOT LIKE %s"
        user_filter_arg = (f"[sms:{becky_phone}]%",)
    else:
        # No Becky configured or unknown user — fall back to all turns
        user_filter_sql = ""
        user_filter_arg = ()

    with db.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT timestamp, input, response FROM request_log
                WHERE status = 'ok'
                AND input IS NOT NULL AND input != ''
                AND response IS NOT NULL AND response != ''
                {user_filter_sql}
                ORDER BY timestamp DESC
                LIMIT %s""",
            user_filter_arg + (n,),
        ).fetchall()

    if not rows:
        return []

    messages = []
    for row in reversed(rows):  # chronological order (oldest first)
        user_text = row["input"] or ""
        assistant_text = row["response"] or ""

        # Strip ACTION blocks — already processed and persisted to DB
        assistant_text = _ACTION_BLOCK.sub('', assistant_text).strip()

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

        # Prepend timestamp so ARIA can see time gaps between messages
        ts = row.get("timestamp")
        if ts:
            if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
                ts = ts.astimezone().replace(tzinfo=None)
            if hasattr(ts, 'isoformat'):
                ts = ts.isoformat()
            user_text = f"[{ts}] {user_text}"

        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": assistant_text})

    return messages
