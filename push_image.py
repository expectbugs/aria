#!/usr/bin/env python3
"""Push an image to the phone via Tasker HTTP Server.

Usage:
    python push_image.py /path/to/image.png
    python push_image.py /path/to/image.png --caption "Here's your chart"

The phone's Tasker HTTP Server receives the image and displays it
using the Display Image action.
"""

import argparse
import sys
from pathlib import Path

import httpx

import config


def push_image(image_path: str, caption: str = "") -> bool:
    """POST an image to the phone's Tasker HTTP Server.

    Returns True on success, False on failure.
    """
    path = Path(image_path)
    if not path.exists():
        print(f"Error: {image_path} not found", file=sys.stderr)
        return False

    # Determine content type from extension
    suffix = path.suffix.lower()
    content_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }
    content_type = content_types.get(suffix, "image/png")

    url = f"http://{config.PHONE_IP}:{config.PHONE_PORT}/image"

    try:
        with open(path, "rb") as f:
            files = {"image": (path.name, f, content_type)}
            data = {}
            if caption:
                data["caption"] = caption

            resp = httpx.post(url, files=files, data=data, timeout=30)

        if resp.status_code == 200:
            print(f"Image pushed: {path.name}")
            return True
        else:
            print(f"Phone returned {resp.status_code}: {resp.text}", file=sys.stderr)
            return False

    except httpx.ConnectError:
        print("Error: phone unreachable (Tailscale down or Tasker HTTP Server not running)", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Push an image to the phone")
    parser.add_argument("image", help="Path to image file")
    parser.add_argument("--caption", default="", help="Optional caption to display with the image")
    args = parser.parse_args()

    success = push_image(args.image, args.caption)
    sys.exit(0 if success else 1)
