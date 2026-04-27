#!/usr/bin/env python3
"""ARIA CLI — talk to ARIA from the terminal.

Usage:
    ./venv/bin/python aria_cli.py [--audio] [--debug] [--host URL]

Interactive commands:
    /file <path> [caption]  — send a file (image, PDF, etc.)
    /audio                  — toggle audio playback on/off
    /debug                  — toggle debug trace on/off
    /quit or Ctrl+D         — exit
"""

import argparse
import base64
import json
import os
import readline
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import httpx

# Import auth token from config (lightweight — just assignments + pathlib)
sys.path.insert(0, str(Path(__file__).parent))
import config

HISTORY_FILE = Path.home() / ".aria_cli_history"
POLL_INTERVAL = 0.5  # seconds between status polls

# ANSI colors
DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _headers():
    return {"Authorization": f"Bearer {config.AUTH_TOKEN}"}


def _play_audio_bytes(audio_bytes: bytes):
    """Play WAV audio via aplay."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp = f.name
    try:
        subprocess.run(["aplay", "-q", tmp], check=False)
    finally:
        os.unlink(tmp)


def _print_trace(entry: dict, debug: bool):
    """Print a single trace entry."""
    t = entry.get("t", 0.0)
    event = entry.get("event", "?")
    detail = entry.get("detail", "")

    timestamp = f"{DIM}[{t:5.1f}s]{RESET}"

    if event == "start":
        label = f"{CYAN}start{RESET}"
        if debug:
            print(f"  {timestamp} {label}: {detail}")
        else:
            summary = detail[:1000] + ("..." if len(detail) > 1000 else "")
            print(f"  {timestamp} {label}: {summary}")

    elif event == "context":
        label = f"{CYAN}context{RESET}"
        if debug:
            print(f"  {timestamp} {label}:")
            for line in detail.split("\n"):
                print(f"    {DIM}{line}{RESET}")
        else:
            print(f"  {timestamp} {label}: {len(detail):,} chars")

    elif event == "route":
        print(f"  {timestamp} {CYAN}route{RESET}: {detail}")

    elif event == "raw_response":
        label = f"{CYAN}raw_response{RESET}"
        action_count = detail.count("<!--ACTION::")
        if debug:
            print(f"  {timestamp} {label} ({len(detail):,} chars, {action_count} ACTION blocks):")
            for line in detail.split("\n"):
                print(f"    {DIM}{line}{RESET}")
        else:
            print(f"  {timestamp} {label}: {len(detail):,} chars, {action_count} ACTION blocks")

    elif event == "actions":
        label = f"{YELLOW}actions{RESET}"
        try:
            data = json.loads(detail)
        except (json.JSONDecodeError, TypeError):
            data = {"types": [], "failures": [], "warnings": []}
        types = data.get("types", [])
        failures = data.get("failures", [])
        found = data.get("found", [])
        warnings = data.get("warnings", [])
        n_ok = len(types) - len(failures)
        if debug:
            print(f"  {timestamp} {label}: {len(types)} found, {n_ok} ok, {len(failures)} failed")
            for action in found:
                act = action.get("action", "?")
                print(f"    {GREEN}-> {act}{RESET}: {json.dumps(action, default=str)}")
            for f in failures:
                print(f"    {RED}FAIL: {f}{RESET}")
            for w in warnings:
                print(f"    {YELLOW}WARN: {w}{RESET}")
        else:
            if types:
                type_str = ", ".join(types)
                print(f"  {timestamp} {label}: {type_str} ({n_ok} ok, {len(failures)} failed)")
            else:
                print(f"  {timestamp} {label}: none")

    elif event == "verification":
        color = GREEN if detail == "ok" else YELLOW
        print(f"  {timestamp} {CYAN}verification{RESET}: {color}{detail}{RESET}")

    elif event == "clean_response":
        label = f"{CYAN}clean_response{RESET}"
        if debug:
            print(f"  {timestamp} {label}:")
            for line in detail.split("\n"):
                print(f"    {DIM}{line}{RESET}")
        else:
            print(f"  {timestamp} {label}: {len(detail):,} chars")

    elif event == "delivery":
        print(f"  {timestamp} {CYAN}delivery{RESET}: {detail}")

    elif event == "tts":
        print(f"  {timestamp} {CYAN}tts{RESET}: {detail}")

    elif event == "tool_calls":
        print(f"  {timestamp} {YELLOW}tools used{RESET}: {detail}")

    elif event == "assistant_text":
        label = f"{CYAN}aria says{RESET}"
        try:
            data = json.loads(detail)
            text_val = data.get("text", detail)
        except (json.JSONDecodeError, TypeError):
            text_val = detail
        if debug:
            print(f"  {timestamp} {label}:")
            for line in str(text_val).split("\n"):
                print(f"    {DIM}{line}{RESET}")
        else:
            snippet = str(text_val)[:1000]
            print(f"  {timestamp} {label}: {snippet}")

    elif event == "tool_call":
        label = f"{YELLOW}tool call{RESET}"
        try:
            data = json.loads(detail)
            tool_name = data.get("tool", "?")
            tool_input = data.get("input", {})
        except (json.JSONDecodeError, TypeError):
            tool_name, tool_input = "?", detail
        if debug:
            input_str = json.dumps(tool_input, default=str)
            if len(input_str) > 1500:
                input_str = input_str[:1500] + "..."
            print(f"  {timestamp} {label}: {BOLD}{tool_name}{RESET}")
            print(f"    {DIM}input: {input_str}{RESET}")
        else:
            print(f"  {timestamp} {label}: {tool_name}")

    elif event == "tool_result":
        label = f"{GREEN}tool result{RESET}"
        try:
            data = json.loads(detail)
            content = data.get("content", detail)
        except (json.JSONDecodeError, TypeError):
            content = detail
        if debug:
            content_str = str(content)
            if len(content_str) > 1800:
                content_str = content_str[:1800] + "..."
            print(f"  {timestamp} {label}:")
            for line in content_str.split("\n")[:20]:
                print(f"    {DIM}{line}{RESET}")
        else:
            print(f"  {timestamp} {label}: {len(str(content)):,} chars")

    elif event == "confirmation_shortcut":
        print(f"  {timestamp} {GREEN}confirmed{RESET}: {detail}")

    elif event == "done":
        print(f"  {timestamp} {GREEN}{BOLD}done{RESET}: {detail}")

    else:
        print(f"  {timestamp} {event}: {detail[:200]}")


def _start_task(host: str, url: str, **kwargs) -> str | None:
    """POST to start an async task. Returns task_id or None."""
    try:
        resp = httpx.post(url, headers=_headers(), timeout=60, **kwargs)
    except httpx.ConnectError:
        print(f"{RED}Error: daemon unreachable{RESET}", file=sys.stderr)
        return None
    if resp.status_code != 200:
        print(f"{RED}Error: {resp.status_code} — {resp.text}{RESET}", file=sys.stderr)
        return None
    return resp.json().get("task_id")


def _poll_task(host: str, task_id: str, audio: bool,
               debug: bool) -> str | None:
    """Poll /ask/status until done, display trace, print response."""
    last_trace_idx = 0
    while True:
        try:
            resp = httpx.get(
                f"{host}/ask/status/{task_id}",
                headers=_headers(),
                timeout=30,
            )
        except httpx.ConnectError:
            print(f"{RED}Error: daemon unreachable{RESET}", file=sys.stderr)
            return None

        if resp.status_code in (200, 202):
            data = resp.json()
        elif resp.status_code == 500:
            data = resp.json() if "json" in resp.headers.get("content-type", "") else {}
            print(f"{RED}Error: {data.get('error', resp.text)}{RESET}", file=sys.stderr)
            return None
        else:
            print(f"{RED}Error: {resp.status_code} — {resp.text}{RESET}", file=sys.stderr)
            return None

        # Display new trace entries
        trace = data.get("trace", [])
        for entry in trace[last_trace_idx:]:
            _print_trace(entry, debug)
        last_trace_idx = len(trace)

        status = data.get("status")
        if status == "done":
            response = data.get("response", "(no response text)")
            print(f"\n{BOLD}ARIA>{RESET} {response}\n")
            # Fetch audio if enabled
            if audio:
                try:
                    audio_resp = httpx.get(
                        f"{host}/ask/result/{task_id}",
                        headers=_headers(),
                        timeout=60,
                    )
                    if audio_resp.status_code == 200 and len(audio_resp.content) > 0:
                        _play_audio_bytes(audio_resp.content)
                except Exception:
                    pass
            return response
        elif status == "error":
            print(f"{RED}Error: {data.get('error', 'Unknown')}{RESET}", file=sys.stderr)
            return None

        time.sleep(POLL_INTERVAL)


def ask_text(host: str, text: str, audio: bool, debug: bool) -> str | None:
    """Send a text query via async /ask/start + polling."""
    task_id = _start_task(
        host, f"{host}/ask/start",
        json={"text": text, "channel": "cli"},
    )
    if not task_id:
        return None
    return _poll_task(host, task_id, audio, debug)


def send_file(host: str, path: str, caption: str, audio: bool,
              debug: bool) -> str | None:
    """Send a file via POST /ask/file + polling."""
    file_path = Path(path).expanduser()
    if not file_path.exists():
        print(f"{RED}Error: {path} not found{RESET}", file=sys.stderr)
        return None

    try:
        with open(file_path, "rb") as f:
            task_id = _start_task(
                host, f"{host}/ask/file",
                params={"channel": "cli"},
                files={"file": (file_path.name, f)},
                data={"text": caption} if caption else {},
            )
    except httpx.ConnectError:
        print(f"{RED}Error: daemon unreachable{RESET}", file=sys.stderr)
        return None

    if not task_id:
        return None
    return _poll_task(host, task_id, audio, debug)


def main():
    parser = argparse.ArgumentParser(description="ARIA CLI — talk to ARIA from the terminal")
    parser.add_argument("--audio", action="store_true", help="Enable audio playback of responses")
    parser.add_argument("--debug", action="store_true", help="Show full debug trace (context, raw response, actions)")
    parser.add_argument("--host", default=f"http://localhost:{config.PORT}", help="Daemon URL")
    args = parser.parse_args()

    audio_enabled = args.audio
    debug_enabled = args.debug
    host = args.host.rstrip("/")

    # Readline history
    try:
        readline.read_history_file(str(HISTORY_FILE))
    except FileNotFoundError:
        pass
    readline.set_history_length(1000)

    mode_parts = []
    if audio_enabled:
        mode_parts.append("audio")
    if debug_enabled:
        mode_parts.append("debug")
    mode = ", ".join(mode_parts) if mode_parts else "text"
    print(f"{BOLD}ARIA CLI{RESET} — {mode} mode")
    print(f"Commands: /file <path> [caption], /audio, /debug, /quit\n")

    try:
        while True:
            try:
                line = input(f"{BOLD}You>{RESET} ").strip()
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
            elif line == "/debug":
                debug_enabled = not debug_enabled
                print(f"Debug {'enabled' if debug_enabled else 'disabled'}")
                continue
            elif line.startswith("/file "):
                parts = line[6:].strip().split(None, 1)
                if not parts:
                    print("Usage: /file <path> [caption]")
                    continue
                path = parts[0]
                caption = parts[1] if len(parts) > 1 else ""
                send_file(host, path, caption, audio_enabled, debug_enabled)
            else:
                ask_text(host, line, audio_enabled, debug_enabled)
    except KeyboardInterrupt:
        print()
    finally:
        readline.write_history_file(str(HISTORY_FILE))


if __name__ == "__main__":
    main()
