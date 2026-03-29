#!/usr/bin/env python3
"""ARIA CLI — talk to ARIA from the terminal.

Usage:
    ./venv/bin/python aria_cli.py [--audio] [--host URL]

Interactive commands:
    /file <path> [caption]  — send a file (image, PDF, etc.)
    /audio                  — toggle audio playback on/off
    /quit or Ctrl+D         — exit
"""

import argparse
import base64
import os
import readline
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

# Import auth token from config (lightweight — just assignments + pathlib)
sys.path.insert(0, str(Path(__file__).parent))
import config

HISTORY_FILE = Path.home() / ".aria_cli_history"
POLL_INTERVAL = 1.0  # seconds between status polls


def _headers():
    return {"Authorization": f"Bearer {config.AUTH_TOKEN}"}


def _play_audio(audio_b64: str):
    """Decode base64 WAV and play via aplay."""
    audio_bytes = base64.b64decode(audio_b64)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp = f.name
    try:
        subprocess.run(["aplay", "-q", tmp], check=False)
    finally:
        os.unlink(tmp)


def ask_text(host: str, text: str, audio: bool) -> str | None:
    """Send a text query via POST /ask. Returns response text."""
    try:
        resp = httpx.post(
            f"{host}/ask",
            json={"text": text, "channel": "cli", "include_audio": audio},
            headers=_headers(),
            timeout=300,
        )
    except httpx.ConnectError:
        print("Error: daemon unreachable", file=sys.stderr)
        return None
    except httpx.ReadTimeout:
        print("Error: request timed out (5 min)", file=sys.stderr)
        return None

    if resp.status_code != 200:
        print(f"Error: {resp.status_code} — {resp.text}", file=sys.stderr)
        return None

    data = resp.json()
    response = data.get("response", "")
    print(f"\nARIA> {response}\n")

    if audio and data.get("audio"):
        _play_audio(data["audio"])

    return response


def send_file(host: str, path: str, caption: str, audio: bool) -> str | None:
    """Send a file via POST /ask/file, poll for result."""
    file_path = Path(path).expanduser()
    if not file_path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        return None

    try:
        with open(file_path, "rb") as f:
            resp = httpx.post(
                f"{host}/ask/file",
                params={"channel": "cli"},
                files={"file": (file_path.name, f)},
                data={"text": caption} if caption else {},
                headers=_headers(),
                timeout=60,
            )
    except httpx.ConnectError:
        print("Error: daemon unreachable", file=sys.stderr)
        return None

    if resp.status_code != 200:
        print(f"Error: {resp.status_code} — {resp.text}", file=sys.stderr)
        return None

    task_id = resp.json().get("task_id")
    if not task_id:
        print("Error: no task_id returned", file=sys.stderr)
        return None

    print(f"Processing file... (task {task_id})")
    return _poll_task(host, task_id, audio)


def _poll_task(host: str, task_id: str, audio: bool) -> str | None:
    """Poll /ask/status until done, then print response and optionally play audio."""
    while True:
        try:
            resp = httpx.get(
                f"{host}/ask/status/{task_id}",
                headers=_headers(),
                timeout=30,
            )
        except httpx.ConnectError:
            print("Error: daemon unreachable during poll", file=sys.stderr)
            return None

        if resp.status_code == 202:
            time.sleep(POLL_INTERVAL)
            continue
        elif resp.status_code == 200:
            data = resp.json()
            response = data.get("response", "(no response text)")
            print(f"\nARIA> {response}\n")
            if audio:
                # Fetch audio from /ask/result
                try:
                    audio_resp = httpx.get(
                        f"{host}/ask/result/{task_id}",
                        headers=_headers(),
                        timeout=60,
                    )
                    if audio_resp.status_code == 200:
                        audio_b64 = base64.b64encode(audio_resp.content).decode()
                        _play_audio(audio_b64)
                except Exception:
                    pass  # Audio playback is best-effort
            return response
        else:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            print(f"Error: {data.get('error', resp.text)}", file=sys.stderr)
            return None


def main():
    parser = argparse.ArgumentParser(description="ARIA CLI — talk to ARIA from the terminal")
    parser.add_argument("--audio", action="store_true", help="Enable audio playback of responses")
    parser.add_argument("--host", default=f"http://localhost:{config.PORT}", help="Daemon URL")
    args = parser.parse_args()

    audio_enabled = args.audio
    host = args.host.rstrip("/")

    # Readline history
    try:
        readline.read_history_file(str(HISTORY_FILE))
    except FileNotFoundError:
        pass
    readline.set_history_length(1000)

    mode = "audio" if audio_enabled else "text"
    print(f"ARIA CLI v{config.__dict__.get('__version__', '?')} — {mode} mode")
    print("Commands: /file <path> [caption], /audio, /quit\n")

    try:
        while True:
            try:
                line = input("You> ").strip()
            except EOFError:
                print()
                break

            if not line:
                continue

            if line in ("/quit", "/exit", "/q"):
                break
            elif line == "/audio":
                audio_enabled = not audio_enabled
                print(f"Audio {'enabled' if audio_enabled else 'disabled'}")
                continue
            elif line.startswith("/file "):
                parts = line[6:].strip().split(None, 1)
                if not parts:
                    print("Usage: /file <path> [caption]")
                    continue
                path = parts[0]
                caption = parts[1] if len(parts) > 1 else ""
                send_file(host, path, caption, audio_enabled)
            else:
                ask_text(host, line, audio_enabled)
    except KeyboardInterrupt:
        print()
    finally:
        readline.write_history_file(str(HISTORY_FILE))


if __name__ == "__main__":
    main()
