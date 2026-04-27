#!/usr/bin/env python3
"""One-shot boot notifier.

Fires once after the next reboot: waits for the ARIA daemon to become
healthy, prompts it to compose a "back online" message, then SMSes that
message to Adam via sms.send_to_owner. Removes its own flag file after
sending so it does not fire again on subsequent reboots.

Install:
  1. Script lives at /home/user/aria/scripts/boot_notify.py
  2. Flag file at /home/user/aria/data/boot_notify.flag (create to arm)
  3. Cron @reboot entry invokes this script
"""
import os
import sys
import time

sys.path.insert(0, "/home/user/aria")

FLAG = "/home/user/aria/data/boot_notify.flag"

if not os.path.exists(FLAG):
    # Not armed — silent exit.
    sys.exit(0)

import config  # noqa: E402
import sms     # noqa: E402
import httpx   # noqa: E402

base = f"http://127.0.0.1:{config.PORT}"
headers = {"Authorization": f"Bearer {config.AUTH_TOKEN}"}

# Wait up to 3 minutes for the daemon to report healthy.
healthy = False
for _ in range(36):
    try:
        r = httpx.get(f"{base}/health", timeout=5)
        if r.status_code == 200 and r.json().get("status") == "ok":
            healthy = True
            break
    except Exception:
        pass
    time.sleep(5)

if not healthy:
    # Daemon never came up — fall back to a plain SMS so Adam still hears
    # something. Leave the flag so the attempt can be retried manually.
    try:
        sms.send_to_owner(
            "Boot notify: ARIA daemon did not become healthy within 3 minutes "
            "after reboot. Check the service status."
        )
    except Exception:
        pass
    sys.exit(1)

# Prompt the model — channel sms so the response is composed in SMS style.
body = {
    "text": (
        "System just finished rebooting and your daemon is back up. "
        "Send Adam a brief SMS letting him know you're back online and ready. "
        "Keep it natural and in-character."
    ),
    "channel": "sms",
}

response_text = "ARIA is back online."
try:
    r = httpx.post(f"{base}/ask", json=body, headers=headers, timeout=120)
    if r.status_code == 200:
        response_text = r.json().get("response") or response_text
except Exception as e:
    response_text = f"ARIA is back online after reboot. (Prompt path errored: {e})"

# Deliver. send_to_owner handles segment splitting for long messages.
try:
    sms.send_to_owner(response_text)
finally:
    # One-shot: remove the flag so subsequent reboots are silent.
    try:
        os.remove(FLAG)
    except FileNotFoundError:
        pass
