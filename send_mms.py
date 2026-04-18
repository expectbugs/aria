#!/usr/bin/env python3
"""Send an MMS (image) to a phone via Telnyx.

Usage:
    python send_mms.py /path/to/image.png
    python send_mms.py /path/to/image.png --body "Here's your chart"
    python send_mms.py /path/to/image.png --to +15551234567

By default sends to the owner's phone number. Stages the image to the
/mms_media/ endpoint (publicly accessible via Tailscale Funnel) and sends
via Telnyx. Works regardless of whether the phone is on local network.

This is the preferred delivery for user-initiated image requests (image
gen, charts, etc.) since it works on-network AND off-network. For automated
triggers (monitor alerts, bug reports, nudges), use push_image.py instead.
"""

import argparse
import sys
from pathlib import Path

import config
import sms


def main():
    parser = argparse.ArgumentParser(description="Send an image as MMS via Telnyx")
    parser.add_argument("image", help="Path to image file")
    parser.add_argument("--to", default=None,
                        help=f"Recipient phone number (default: {config.OWNER_PHONE_NUMBER})")
    parser.add_argument("--body", default="",
                        help="Optional text to include with the image")
    args = parser.parse_args()

    if not Path(args.image).exists():
        print(f"Error: {args.image} not found", file=sys.stderr)
        return 1

    to = args.to or config.OWNER_PHONE_NUMBER

    try:
        msg_id = sms.send_image_mms(to, args.image, body=args.body)
        print(f"MMS sent: {msg_id}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
