"""Twilio SMS/MMS integration for ARIA."""

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from twilio.rest import Client
from twilio.request_validator import RequestValidator

import config

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


def send_sms(to: str, body: str, media_url: str | None = None) -> str:
    """Send an SMS or MMS message. Returns the message SID."""
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
    outbound_log = config.DATA_DIR / "sms_outbound.jsonl"
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "to": to,
            "body": body,
            "media_url": media_url,
            "sid": message.sid,
        }
        with open(outbound_log, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.error("Failed to log outbound SMS: %s", e)

    return message.sid


def send_mms(to: str, body: str, local_path: str) -> str:
    """Send an MMS with a local file. Stages the file and sends via Twilio."""
    public_url = stage_media(local_path)
    return send_sms(to, body, media_url=public_url)


def send_to_owner(body: str, media_url: str | None = None) -> str:
    """Send an SMS/MMS to the owner's phone number."""
    return send_sms(config.OWNER_PHONE_NUMBER, body, media_url)


def validate_request(url: str, params: dict, signature: str) -> bool:
    """Validate that an incoming webhook request is genuinely from Twilio."""
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    return validator.validate(url, params, signature)
