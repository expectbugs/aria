"""Twilio SMS/MMS integration for ARIA."""

import logging
import os
import re
import shutil
import textwrap
import uuid
from pathlib import Path

from twilio.rest import Client
from twilio.request_validator import RequestValidator

import config
import db

log = logging.getLogger("aria.sms")

# Directory for outbound MMS media (served via /mms_media endpoint)
MMS_OUTBOX = config.DATA_DIR / "mms_outbox"
MMS_OUTBOX.mkdir(parents=True, exist_ok=True)

# Lazy-initialized Twilio client
_client: Client | None = None


def get_client() -> Client:
    """Get or create the Twilio REST client."""
    global _client
    if _client is None:
        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def stage_media(local_path: str) -> str:
    """Copy a local file to the MMS outbox and return the public URL.

    The file is served via the /mms_media endpoint through Tailscale Funnel,
    making it accessible to Twilio for MMS delivery.
    """
    src = Path(local_path)
    if not src.exists():
        raise FileNotFoundError(f"Media file not found: {local_path}")

    # Use a unique name to avoid collisions
    media_id = str(uuid.uuid4())[:8]
    dest = MMS_OUTBOX / f"{media_id}_{src.name}"
    shutil.copy2(src, dest)

    # Build public URL via Tailscale Funnel
    public_url = f"{config.TWILIO_WEBHOOK_URL.rsplit('/sms', 1)[0]}/mms_media/{dest.name}"
    log.info("Staged MMS media: %s -> %s", local_path, public_url)
    return public_url


# --- SMS → Image Redirect (temporary — while A2P 10DLC is pending) ---

_FONT_REGULAR = "/usr/share/fonts/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"


def _render_sms_image(body: str, header: str = "ARIA") -> str:
    """Render SMS text as a phone-readable PNG image.

    Returns path to a temp PNG file. Caller must delete after use.
    """
    from datetime import datetime

    from PIL import Image, ImageDraw, ImageFont

    WIDTH = 540
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


def _redirect_to_image(to: str, body: str, media_url: str | None = None) -> str:
    """Redirect an outbound SMS to an image push. Returns a fake SID."""
    fake_sid = f"IMG_{uuid.uuid4().hex[:8]}"

    if not body or not body.strip():
        log.warning("SMS redirect: empty body to %s, skipping image push", to)
    else:
        try:
            img_path = _render_sms_image(body)
            try:
                import push_image
                success = push_image.push_image(img_path, caption="ARIA")
                if not success:
                    log.error("SMS redirect: push_image failed for message to %s", to)
            finally:
                try:
                    os.unlink(img_path)
                except OSError:
                    pass
        except Exception as e:
            log.error("SMS redirect: failed to render/push image for %s: %s", to, e)

    # Log to sms_outbound for audit trail
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO sms_outbound (to_number, body, media_url, sid)
                   VALUES (%s, %s, %s, %s)""",
                (to, body, media_url, fake_sid),
            )
    except Exception as e:
        log.error("Failed to log redirected SMS: %s", e)

    log.info("SMS redirected to image push (to=%s, sid=%s)", to, fake_sid)
    return fake_sid


def send_sms(to: str, body: str, media_url: str | None = None) -> str:
    """Send an SMS or MMS message. Returns the message SID."""
    if getattr(config, "SMS_REDIRECT_TO_IMAGE", False):
        return _redirect_to_image(to, body, media_url)

    client = get_client()
    kwargs = {
        "messaging_service_sid": config.TWILIO_MESSAGING_SID,
        "to": to,
        "body": body,
    }
    if media_url:
        kwargs["media_url"] = [media_url]

    message = client.messages.create(**kwargs)
    log.info("SMS sent to %s (sid=%s)", to, message.sid)

    # Log every outbound message with its exact text
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO sms_outbound (to_number, body, media_url, sid)
                   VALUES (%s, %s, %s, %s)""",
                (to, body, media_url, message.sid),
            )
    except Exception as e:
        log.error("Failed to log outbound SMS: %s", e)

    return message.sid


def split_sms(body: str, max_length: int = 1500) -> list[str]:
    """Split a long message into chunks at natural break points.

    Splitting priority: paragraph boundaries > sentence boundaries >
    word boundaries > hard cut.
    """
    if not body:
        return [""]
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
    """Send a potentially long SMS as multiple messages. Returns list of SIDs."""
    if getattr(config, "SMS_REDIRECT_TO_IMAGE", False):
        # No splitting — render full message as one image
        return [send_sms(to, body, media_url)]

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
    """Send an MMS with a local file. Stages the file and sends via Twilio."""
    if getattr(config, "SMS_REDIRECT_TO_IMAGE", False):
        import push_image as _pi
        _pi.push_image(local_path, caption="Attachment")
        return _redirect_to_image(to, body)

    public_url = stage_media(local_path)
    return send_sms(to, body, media_url=public_url)


def send_to_owner(body: str, media_url: str | None = None) -> str:
    """Send an SMS/MMS to the owner's phone number."""
    return send_sms(config.OWNER_PHONE_NUMBER, body, media_url)


def validate_request(url: str, params: dict, signature: str) -> bool:
    """Validate that an incoming webhook request is genuinely from Twilio."""
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    return validator.validate(url, params, signature)
