"""Twilio SMS/MMS integration for ARIA."""

import logging
from twilio.rest import Client
from twilio.request_validator import RequestValidator

import config

log = logging.getLogger("aria.sms")

# Lazy-initialized Twilio client
_client: Client | None = None


def get_client() -> Client:
    """Get or create the Twilio REST client."""
    global _client
    if _client is None:
        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


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
    return message.sid


def send_to_owner(body: str, media_url: str | None = None) -> str:
    """Send an SMS/MMS to the owner's phone number."""
    return send_sms(config.OWNER_PHONE_NUMBER, body, media_url)


def validate_request(url: str, params: dict, signature: str) -> bool:
    """Validate that an incoming webhook request is genuinely from Twilio."""
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    return validator.validate(url, params, signature)
