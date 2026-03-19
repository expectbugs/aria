#!/usr/bin/env python3
"""Push audio to the phone via Tasker HTTP Server.

Usage:
    python push_audio.py /path/to/audio.wav
    python push_audio.py /path/to/audio.wav --caption "Timer: laundry done"

Tasker setup:
    1. Profile: Event → Net → HTTP Request
       Port: 8451, Method: POST, Path: /audio
    2. Task "ARIA Audio Push":
       Step 1: Variable Set  %file  to  %http_request_file_name
       Step 2: Copy File  From: ARIA/received_audio.wav  To: ARIA/push_audio.wav
       Step 3: HTTP Response  Code: 200
       Step 4: Media → Play File  File: ARIA/push_audio.wav
"""

import argparse
import sys
from pathlib import Path

import httpx

import config


def push_audio(audio_path: str, caption: str = "") -> bool:
    """POST audio to the phone's Tasker HTTP Server.

    Returns True on success, False on failure.
    """
    path = Path(audio_path)
    if not path.exists():
        print(f"Error: {audio_path} not found", file=sys.stderr)
        return False

    url = f"http://{config.PHONE_IP}:{config.PHONE_PORT}/audio"

    try:
        with open(path, "rb") as f:
            files = {"audio": (path.name, f, "audio/wav")}
            data = {}
            if caption:
                data["caption"] = caption

            resp = httpx.post(url, files=files, data=data, timeout=30)

        if resp.status_code == 200:
            print(f"Audio pushed: {path.name}")
            return True
        else:
            print(f"Phone returned {resp.status_code}: {resp.text}", file=sys.stderr)
            return False

    except httpx.ConnectError:
        print("Error: phone unreachable (Tailscale down or Tasker HTTP Server not running)",
              file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Push audio to the phone")
    parser.add_argument("audio", help="Path to audio file")
    parser.add_argument("--caption", default="", help="Optional caption")
    args = parser.parse_args()

    success = push_audio(args.audio, args.caption)
    sys.exit(0 if success else 1)
