"""Telnyx SMS/MMS integration for ARIA."""

import logging
import os
import re
import shutil
import textwrap
import uuid
from pathlib import Path

from telnyx import Telnyx

import config
import db

log = logging.getLogger("aria.sms")

# Directory for outbound MMS media (served via /mms_media endpoint)
MMS_OUTBOX = config.DATA_DIR / "mms_outbox"
MMS_OUTBOX.mkdir(parents=True, exist_ok=True)

# Lazy-initialized Telnyx client
_client: Telnyx | None = None


def get_client() -> Telnyx:
    """Get or create the Telnyx REST client."""
    global _client
    if _client is None:
        _client = Telnyx(
            api_key=config.TELNYX_API_KEY,
            public_key=getattr(config, "TELNYX_PUBLIC_KEY", None),
        )
    return _client


def stage_media(local_path: str) -> str:
    """Copy a local file to the MMS outbox and return the public URL.

    The file is served via the /mms_media endpoint through Tailscale Funnel,
    making it accessible to Telnyx for MMS delivery.
    """
    src = Path(local_path)
    if not src.exists():
        raise FileNotFoundError(f"Media file not found: {local_path}")

    # Use a unique name to avoid collisions
    media_id = str(uuid.uuid4())[:8]
    dest = MMS_OUTBOX / f"{media_id}_{src.name}"
    shutil.copy2(src, dest)

    # Build public URL via Tailscale Funnel
    public_url = f"{config.TELNYX_WEBHOOK_URL.rsplit('/sms', 1)[0]}/mms_media/{dest.name}"
    log.info("Staged MMS media: %s -> %s", local_path, public_url)
    return public_url


# --- Image rendering (used for MMS image delivery) ---

_FONT_REGULAR = "/usr/share/fonts/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"


def _render_sms_image(body: str, header: str = "ARIA") -> str:
    """Render SMS text as a phone-readable PNG image.

    Returns path to a temp PNG file. Caller must delete after use.
    """
    from datetime import datetime

    from PIL import Image, ImageDraw, ImageFont

    WIDTH = 470
    PADDING = 24
    TEXT_WIDTH = WIDTH - 2 * PADDING
    BG_COLOR = "#FFFFFF"
    HEADER_COLOR = "#1a1a2e"
    TIME_COLOR = "#888888"
    BODY_COLOR = "#333333"
    LINE_COLOR = "#E0E0E0"

    # Load fonts with fallback
    try:
        font_header = ImageFont.truetype(_FONT_BOLD, 26)
        font_time = ImageFont.truetype(_FONT_REGULAR, 15)
        font_body = ImageFont.truetype(_FONT_REGULAR, 20)
    except OSError:
        log.warning("DejaVu fonts not found, using default")
        font_header = ImageFont.load_default()
        font_time = ImageFont.load_default()
        font_body = ImageFont.load_default()

    # Calculate chars per line from font metrics
    avg_char_width = font_body.getlength("x")
    chars_per_line = max(20, int(TEXT_WIDTH / avg_char_width))

    # Wrap text: preserve existing newlines, wrap each paragraph
    wrapped_lines = []
    for line in (body or "").split("\n"):
        if line.strip():
            wrapped_lines.extend(
                textwrap.wrap(line, width=chars_per_line, break_long_words=True)
            )
        else:
            wrapped_lines.append("")

    if not wrapped_lines:
        wrapped_lines = [""]

    # Calculate dimensions
    body_line_height = 26
    header_block = 80  # header + timestamp + separator + spacing
    body_height = len(wrapped_lines) * body_line_height
    total_height = PADDING + header_block + body_height + PADDING

    # Create image
    img = Image.new("RGB", (WIDTH, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = PADDING
    # Header
    draw.text((PADDING, y), header, font=font_header, fill=HEADER_COLOR)
    y += 34
    # Timestamp
    timestamp = datetime.now().strftime("%I:%M %p \u00b7 %b %d, %Y")
    draw.text((PADDING, y), timestamp, font=font_time, fill=TIME_COLOR)
    y += 24
    # Separator
    draw.line([(PADDING, y), (WIDTH - PADDING, y)], fill=LINE_COLOR, width=1)
    y += 20
    # Body
    for line in wrapped_lines:
        draw.text((PADDING, y), line, font=font_body, fill=BODY_COLOR)
        y += body_line_height

    # Save to temp file
    tmp_path = config.DATA_DIR / f"sms_img_{uuid.uuid4().hex[:8]}.png"
    img.save(str(tmp_path), "PNG")
    return str(tmp_path)


# --- GSM-7 encoding normalization ---
#
# SMS has two encodings: GSM-7 (160 chars/single segment, 153 chars/concat
# segment) and UCS-2 (70/67). Any single non-GSM-7 char in the message forces
# UCS-2 for the whole message, shrinking capacity ~2.3x. Telnyx rejects any
# single API call that would produce >10 segments (error 40302), so a UCS-2
# message over ~670 chars fails entirely unless split.
#
# We aggressively substitute common typography/symbol chars with ASCII
# equivalents to keep messages in GSM-7 when possible. Anything that survives
# (emoji, CJK text, rare symbols) legitimately belongs in UCS-2, and split_sms
# uses a smaller chunk size in that case.

# The GSM-7 default alphabet + extension table (right side). Extension chars
# cost 2 septets each but are still GSM-7 (don't force UCS-2).
_GSM7_CHARSET = frozenset(
    "@\n\r "
    "!\"#$%&'()*+,-./0123456789:;<=>?"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "£¥èéùìòÇØøÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ"
    "¡¿ÄÖÑÜ§äöñüà¤"
    "\f^{}\\[~]|€"
)

_GSM7_SUBSTITUTIONS = {
    # Dashes
    "\u2014": "-",    # em dash (—)
    "\u2013": "-",    # en dash (–)
    "\u2212": "-",    # minus sign (−)
    # Smart quotes → straight
    "\u2018": "'",    # ‘
    "\u2019": "'",    # ’
    "\u201A": "'",    # ‚
    "\u201B": "'",    # ‛
    "\u201C": '"',    # “
    "\u201D": '"',    # ”
    "\u201E": '"',    # „
    "\u201F": '"',    # ‟
    "\u00AB": '"',    # «
    "\u00BB": '"',    # »
    # Typography
    "\u2026": "...",  # ellipsis …
    "\u2022": "*",    # bullet •
    "\u00B7": ".",    # middle dot ·
    "\u2032": "'",    # prime ′
    "\u2033": '"',    # double prime ″
    # Backtick — ASCII 0x60 but NOT in GSM-7
    "`": "'",
    # Spaces → regular space (or stripped for zero-width)
    "\u00A0": " ",    # non-breaking space
    "\u2009": " ",    # thin space
    "\u202F": " ",    # narrow no-break space
    "\u2002": " ",    # en space
    "\u2003": " ",    # em space
    "\u2007": " ",    # figure space
    "\u200B": "",     # zero-width space
    "\u200C": "",     # zero-width non-joiner
    "\u200D": "",     # zero-width joiner
    "\uFEFF": "",     # BOM / zero-width no-break
    # Common symbols
    "\u00A9": "(c)",  # ©
    "\u00AE": "(R)",  # ®
    "\u2122": "(TM)", # ™
    "\u00B0": "deg",  # °
    # Math
    "\u00D7": "x",    # ×
    "\u00F7": "/",    # ÷
    "\u00B1": "+/-",  # ±
    # Arrows
    "\u2192": "->",   # →
    "\u2190": "<-",   # ←
    "\u2194": "<->",  # ↔
    "\u21D2": "=>",   # ⇒
    "\u21D0": "<=",   # ⇐
    # Checkmarks
    "\u2713": "[x]",  # ✓
    "\u2714": "[x]",  # ✔
    "\u2717": "[ ]",  # ✗
    "\u2718": "[ ]",  # ✘
    # Bullets/shapes
    "\u25CF": "*",    # ●
    "\u25E6": "o",    # ◦
    "\u25A0": "*",    # ■
    "\u25A1": "[]",   # □
}


def _normalize_for_sms(text: str) -> tuple[str, bool]:
    """Substitute non-GSM-7 chars with ASCII equivalents where possible.

    Returns: (normalized_text, is_fully_gsm7)

    If is_fully_gsm7 is True, the message can use the larger 1500-char
    split size (10 concat segments). If False, anything survived that can't
    be substituted (emoji, CJK text, etc.) and we must use the 600-char
    UCS-2-safe split size.
    """
    for src, dst in _GSM7_SUBSTITUTIONS.items():
        if src in text:
            text = text.replace(src, dst)
    is_gsm7 = all(c in _GSM7_CHARSET for c in text)
    return text, is_gsm7


def send_sms(to: str, body: str, media_url: str | None = None) -> str:
    """Send an SMS or MMS message via Telnyx. Returns the message ID.

    Body is normalized for GSM-7 before sending (em dash → hyphen, smart
    quotes → straight, backticks → apostrophes, etc.) to avoid triggering
    UCS-2 encoding that would require shorter segments or reject at >10.
    """
    body, _ = _normalize_for_sms(body)
    client = get_client()
    kwargs: dict = {
        "from_": config.TELNYX_PHONE_NUMBER,
        "to": to,
        "text": body,
    }
    if media_url:
        kwargs["media_urls"] = [media_url]

    response = client.messages.send(**kwargs)
    message_id = response.data.id if response.data else "unknown"
    log.info("SMS sent to %s (id=%s)", to, message_id)

    # Log every outbound message with its exact text
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO sms_outbound (to_number, body, media_url, sid)
                   VALUES (%s, %s, %s, %s)""",
                (to, body, media_url, message_id),
            )
    except Exception as e:
        log.error("Failed to log outbound SMS: %s", e)

    return message_id


def split_sms(body: str, max_length: int | None = None) -> list[str]:
    """Split a long message into chunks at natural break points.

    Normalizes non-GSM-7 chars first (em dashes → hyphens, etc.), then picks
    a chunk size based on the resulting encoding:
    - GSM-7 (all chars substituted or native): 1500 chars (≤10 segments × 153)
    - UCS-2 (unicode survived substitution, e.g. emoji): 600 chars (≤9 × 67)

    Either stays under Telnyx's 10-segment-per-API-call limit.

    If max_length is passed explicitly, normalization still happens but the
    caller's ceiling is respected (used by tests and callers with specific
    size constraints).

    Splitting priority: paragraph boundaries > sentence boundaries >
    word boundaries > hard cut.
    """
    if not body:
        return [""]

    body, is_gsm7 = _normalize_for_sms(body)
    if max_length is None:
        max_length = 1500 if is_gsm7 else 600

    if len(body) <= max_length:
        return [body]

    chunks = []
    remaining = body

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try paragraph boundary (double newline)
        cut = _find_break(remaining, max_length, "\n\n")
        if cut == -1:
            # Try sentence boundary (. ! ? followed by space)
            cut = _find_sentence_break(remaining, max_length)
        if cut == -1:
            # Try word boundary (space)
            cut = _find_break(remaining, max_length, " ")
        if cut == -1:
            # Hard cut
            cut = max_length

        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    return chunks


def _find_break(text: str, max_length: int, delimiter: str) -> int:
    """Find the last occurrence of delimiter within max_length."""
    segment = text[:max_length]
    pos = segment.rfind(delimiter)
    if pos > 0:
        return pos + len(delimiter)
    return -1


def _find_sentence_break(text: str, max_length: int) -> int:
    """Find the last sentence boundary within max_length."""
    segment = text[:max_length]
    # Match . ! ? followed by a space (end of sentence)
    matches = list(re.finditer(r'[.!?]\s', segment))
    if matches:
        last = matches[-1]
        return last.end()
    return -1


def send_long_sms(to: str, body: str, media_url: str | None = None) -> list[str]:
    """Send a potentially long SMS as multiple messages. Returns list of message IDs."""
    parts = split_sms(body)
    sids = []
    for i, part in enumerate(parts):
        # Only attach media to the first message
        url = media_url if i == 0 else None
        sids.append(send_sms(to, part, media_url=url))
    return sids


def send_long_to_owner(body: str, media_url: str | None = None) -> list[str]:
    """Send a potentially long SMS/MMS to the owner's phone number."""
    return send_long_sms(config.OWNER_PHONE_NUMBER, body, media_url)


def send_mms(to: str, body: str, local_path: str) -> str:
    """Send an MMS with a local file. Stages the file and sends via Telnyx."""
    public_url = stage_media(local_path)
    return send_sms(to, body, media_url=public_url)


def send_to_owner(body: str, media_url: str | None = None) -> str:
    """Send an SMS/MMS to the owner's phone number."""
    return send_sms(config.OWNER_PHONE_NUMBER, body, media_url)


def validate_request(payload: str, headers: dict) -> bool:
    """Validate that an incoming webhook request is genuinely from Telnyx.

    Uses ED25519 signature verification. Telnyx signs "{timestamp}|{payload}"
    with their private key; we verify with the public key from Mission Control.
    Headers: webhook-id, webhook-timestamp, webhook-signature.
    """
    import base64
    import time as _time

    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError

        signature_b64 = headers.get("webhook-signature", "")
        timestamp = headers.get("webhook-timestamp", "")
        if not signature_b64 or not timestamp:
            log.warning("Webhook missing signature or timestamp headers")
            return False

        # Reject timestamps older than 5 minutes (replay protection)
        try:
            ts_int = int(timestamp)
            if abs(_time.time() - ts_int) > 300:
                log.warning("Webhook timestamp too old/new: %s", timestamp)
                return False
        except ValueError:
            log.warning("Webhook timestamp not an integer: %s", timestamp)
            return False

        # Verify ED25519 signature over "{timestamp}|{payload}"
        public_key_bytes = base64.b64decode(config.TELNYX_PUBLIC_KEY)
        verify_key = VerifyKey(public_key_bytes)
        signed_payload = f"{timestamp}|{payload}".encode("utf-8")
        signature_bytes = base64.b64decode(signature_b64)
        verify_key.verify(signed_payload, signature_bytes)
        return True
    except BadSignatureError:
        log.warning("Webhook ED25519 signature mismatch")
        return False
    except Exception as e:
        log.warning("Webhook validation failed: %s", e)
        return False


def send_image_mms(to: str, image_path: str, body: str = "") -> str:
    """Send a local image as MMS. Stages to public URL, sends via Telnyx.

    Returns the message ID.
    """
    public_url = stage_media(image_path)
    return send_sms(to, body, media_url=public_url)
