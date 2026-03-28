"""ARIA Delivery Intelligence Engine — smart routing based on user state.

Pure decision function: evaluate() returns a DeliveryDecision telling the
caller WHAT to do, not doing it for them. Callers keep their existing
delivery mechanics unchanged.

Forward-looking: device_state table tracks phone, glasses, watch, mic.
Routing rules include all devices — when Phase 5-7 implement push modules,
the engine automatically upgrades delivery choices.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import config
import db
import location_store

log = logging.getLogger("aria.delivery")


@dataclass
class UserState:
    """Inferred user state from location, time, and device data."""
    location: str       # home, work, court, driving, unknown
    activity: str       # available, working, sleeping, exercising, driving
    channels: list      # available delivery channels
    battery: int | None
    location_fresh: bool  # True if location data < stale threshold


@dataclass
class DeliveryDecision:
    """What delivery method to use and why."""
    method: str    # voice, image, sms, glasses, defer
    reason: str    # human-readable explanation for logging


def _get_device_states() -> dict[str, dict]:
    """Read all device states from the database."""
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT * FROM device_state").fetchall()
        result = {}
        for row in rows:
            r = db.serialize_row(row)
            result[r["device"]] = r
        return result
    except Exception as e:
        log.warning("[DELIVERY] Failed to read device_state: %s", e)
        return {}


def _is_device_connected(device: dict) -> bool:
    """Check if a device is considered connected (seen recently)."""
    if not device.get("connected"):
        return False
    last_seen = device.get("last_seen", "")
    if not last_seen:
        return False
    try:
        ts = datetime.fromisoformat(last_seen)
        stale_min = getattr(config, "DELIVERY_LOCATION_STALE_MINUTES", 30)
        return (datetime.now() - ts).total_seconds() < stale_min * 60
    except (ValueError, TypeError):
        return False


def get_user_state() -> UserState:
    """Infer current user state from location + time + Fitbit + devices."""
    now = datetime.now()
    loc = location_store.get_latest()

    # --- Location classification ---
    location = "unknown"
    location_fresh = False

    if loc and loc.get("location"):
        loc_str = loc["location"].lower()
        known = getattr(config, "KNOWN_PLACES", {})
        for name, addr in known.items():
            addr_lower = addr.lower()
            if addr_lower in loc_str or loc_str in addr_lower:
                location = name
                break

        # Check freshness
        try:
            loc_ts = datetime.fromisoformat(loc.get("timestamp", ""))
            stale_min = getattr(config, "DELIVERY_LOCATION_STALE_MINUTES", 30)
            location_fresh = (now - loc_ts).total_seconds() < stale_min * 60
        except (ValueError, TypeError):
            pass

    # --- Activity inference (priority order) ---
    activity = "available"

    # 1. Exercise mode (explicit state, highest priority)
    try:
        import fitbit_store
        exercise = fitbit_store.get_exercise_state()
        if exercise:
            activity = "exercising"
    except Exception:
        pass

    # 2. Driving (speed data)
    if activity == "available" and loc:
        speed = loc.get("speed_mps")
        if speed is not None:
            try:
                if float(speed) > 5.0:  # ~11 mph
                    activity = "driving"
                    location = "driving"
            except (ValueError, TypeError):
                pass

    # 3. Sleeping (quiet hours)
    if activity == "available":
        quiet_start = getattr(config, "QUIET_HOURS_START", 0)
        quiet_end = getattr(config, "QUIET_HOURS_END", 7)
        hour = now.hour
        if quiet_start <= quiet_end:
            is_quiet = quiet_start <= hour < quiet_end
        else:
            is_quiet = hour >= quiet_start or hour < quiet_end
        if is_quiet:
            activity = "sleeping"

    # 4. Working (location + time)
    if activity == "available" and location == "work":
        # Second shift: 1:30pm-11pm (13:30-23:00)
        if 13 <= now.hour <= 23:
            activity = "working"

    # --- Available channels (from device_state) ---
    devices = _get_device_states()
    channels = []

    phone = devices.get("phone", {})
    if _is_device_connected(phone):
        channels.extend(["voice", "image", "sms"])
    else:
        channels.append("sms")  # SMS doesn't need phone connectivity

    glasses = devices.get("glasses", {})
    if _is_device_connected(glasses):
        channels.append("glasses")

    watch = devices.get("watch", {})
    if _is_device_connected(watch):
        channels.append("watch")

    # Filter by activity: remove voice in restricted environments
    if activity in ("working", "sleeping"):
        channels = [c for c in channels if c != "voice"]
    if location == "court":
        channels = [c for c in channels if c not in ("voice", "sms")]

    battery = loc.get("battery_pct") if loc else None

    return UserState(
        location=location,
        activity=activity,
        channels=channels,
        battery=battery,
        location_fresh=location_fresh,
    )


def evaluate(content_type: str = "response",
             priority: str = "normal",
             source: str = "voice",
             hint: str | None = None,
             _state: UserState | None = None) -> DeliveryDecision:
    """Decide how to deliver content based on user state.

    Pure function — no side effects. Callers handle actual delivery.

    Args:
        content_type: response, nudge, timer, monitor_finding, task_completion
        priority: urgent, normal, gentle
        source: voice, sms, file, timer, nudge, monitor_finding, watch
        hint: ARIA's set_delivery preference (may be overridden for safety)
        _state: pre-fetched UserState (avoids redundant DB queries when caller
                also needs the state for logging)
    """
    if not getattr(config, "DELIVERY_ENGINE_ENABLED", True):
        # Engine disabled — return source-default or hint
        return DeliveryDecision(
            method=hint or ("sms" if source == "sms" else "voice"),
            reason="delivery engine disabled — using default",
        )

    state = _state if _state is not None else get_user_state()

    # --- Sleeping ---
    if state.activity == "sleeping":
        if priority == "urgent":
            return DeliveryDecision("image", "urgent during sleep — image only")
        return DeliveryDecision("defer", "sleeping — queued for morning")

    # --- Court ---
    if state.location == "court":
        if priority == "urgent":
            if "glasses" in state.channels:
                return DeliveryDecision("glasses", "in court — urgent stealth via glasses")
            return DeliveryDecision("image", "in court — urgent image only")
        return DeliveryDecision("defer", "in court — deferred")

    # --- Working ---
    if state.activity == "working":
        if "glasses" in state.channels:
            return DeliveryDecision("glasses", "at work — stealth via glasses")
        return DeliveryDecision("image", "at work — voice blocked, using image")

    # --- Driving ---
    if state.activity == "driving":
        if "voice" in state.channels:
            return DeliveryDecision("voice", "driving — voice for safety")
        return DeliveryDecision("defer", "driving, no voice channel — deferred")

    # --- Exercising ---
    if state.activity == "exercising":
        return DeliveryDecision("voice", "exercising — voice coaching")

    # --- Available (home or unknown location) ---

    # Respect ARIA's hint if the channel is available and safe
    if hint and hint in state.channels:
        return DeliveryDecision(hint, f"ARIA requested {hint}")
    if hint == "glasses" and "glasses" not in state.channels:
        # Glasses requested but not connected — fall back to image
        return DeliveryDecision("image", "ARIA requested glasses, not connected — image fallback")

    # Default by source channel
    if source == "sms":
        return DeliveryDecision("sms", "SMS request — SMS response")
    if source == "watch":
        if "voice" in state.channels:
            return DeliveryDecision("voice", "watch request — voice response")
        return DeliveryDecision("image", "watch request, no voice — image fallback")
    if source in ("voice", "file"):
        return DeliveryDecision("voice", f"{source} request — voice response")

    # Proactive content (timers, nudges, findings) defaults to image
    if source in ("timer", "nudge", "monitor_finding", "task_completion"):
        if "voice" in state.channels:
            return DeliveryDecision("voice", f"proactive {source} — voice")
        return DeliveryDecision("image", f"proactive {source} — image")

    return DeliveryDecision("voice", "default — voice")


def log_decision(decision: DeliveryDecision, content_type: str,
                 source: str, hint: str | None,
                 _state: UserState | None = None):
    """Log a delivery decision to the delivery_log table."""
    if not getattr(config, "DELIVERY_LOG_ENABLED", True):
        return
    try:
        state = _state if _state is not None else get_user_state()
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO delivery_log
                   (content_type, source_channel, hint, chosen_method,
                    reason, user_location, user_activity)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (content_type, source, hint, decision.method,
                 decision.reason, state.location, state.activity),
            )
    except Exception as e:
        log.warning("[DELIVERY] Failed to log decision: %s", e)


def queue_deferred(content: str, content_type: str, priority: str,
                   source: str, reason: str):
    """Store content for later delivery when user state changes.

    Deferred items expire after DEFERRED_DELIVERY_EXPIRES_HOURS (default 12h).
    """
    expires_hours = getattr(config, "DEFERRED_DELIVERY_EXPIRES_HOURS", 12)
    expires_at = (datetime.now() + timedelta(hours=expires_hours)).isoformat()
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO deferred_deliveries
                   (content, content_type, priority, source, reason, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (content, content_type, priority, source, reason, expires_at),
            )
        log.info("[DELIVERY] Queued deferred: %s (%s) — %s",
                 content_type, source, reason)
    except Exception as e:
        log.error("[DELIVERY] Failed to queue deferred delivery: %s", e)


def get_pending_deferred() -> list[dict]:
    """Query undelivered, non-expired deferred items."""
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM deferred_deliveries
                   WHERE delivered = FALSE AND expires_at > NOW()
                   ORDER BY created_at"""
            ).fetchall()
        return [db.serialize_row(r) for r in rows]
    except Exception as e:
        log.warning("[DELIVERY] Failed to query deferred: %s", e)
        return []


def mark_deferred_delivered(item_id: int, method: str):
    """Mark a deferred item as delivered."""
    try:
        with db.get_conn() as conn:
            conn.execute(
                """UPDATE deferred_deliveries
                   SET delivered = TRUE, delivered_at = NOW(), delivery_method = %s
                   WHERE id = %s""",
                (method, item_id),
            )
    except Exception as e:
        log.error("[DELIVERY] Failed to mark deferred delivered: %s", e)


def cleanup_expired_deferred():
    """Delete expired undelivered deferred items."""
    try:
        with db.get_conn() as conn:
            result = conn.execute(
                "DELETE FROM deferred_deliveries WHERE expires_at < NOW() AND delivered = FALSE"
            )
            if result.rowcount:
                log.info("[DELIVERY] Cleaned up %d expired deferred items", result.rowcount)
    except Exception as e:
        log.error("[DELIVERY] Deferred cleanup failed: %s", e)


def update_device_state(device: str, connected: bool,
                        battery_pct: int | None = None):
    """Update a device's connectivity state."""
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO device_state (device, connected, battery_pct, last_seen)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (device) DO UPDATE
                   SET connected = EXCLUDED.connected,
                       battery_pct = EXCLUDED.battery_pct,
                       last_seen = NOW()""",
                (device, connected, battery_pct),
            )
    except Exception as e:
        log.error("[DELIVERY] Failed to update device state for %s: %s", device, e)


# --- Delivery Execution ---

async def execute_delivery(
    response_text: str,
    content_type: str = "response",
    priority: str = "normal",
    source: str = "voice",
    hint: str | None = None,
    sms_target: str | None = None,
    push_voice: bool = True,
) -> dict:
    """Evaluate delivery, execute it, log decision. Single get_user_state() call.

    Encapsulates the full delivery cycle: decide → execute → log. Uses lazy
    imports for TTS/push modules to keep the module lightweight at import time.

    Args:
        response_text: the text to deliver
        content_type: response, nudge, timer, monitor_finding, task_completion
        priority: urgent, normal, gentle
        source: voice, sms, file, timer, nudge, monitor_finding, watch
        hint: ARIA's set_delivery preference (may be overridden for safety)
        sms_target: phone number for SMS delivery (None → owner phone)
        push_voice: True = push audio to phone (proactive/SMS flows)
                    False = return audio bytes only (task flows where phone polls)

    Returns: {"method": str, "audio": bytes, "reason": str}
    """
    import os
    import uuid

    # Decide — single get_user_state() call shared with log
    state = get_user_state()
    decision = evaluate(content_type, priority, source, hint, _state=state)
    log_decision(decision, content_type, source, hint, _state=state)

    method = decision.method
    audio = b""

    if method == "defer":
        queue_deferred(response_text, content_type, priority, source, decision.reason)

    elif method == "sms":
        try:
            import sms as _sms
            target = sms_target or config.OWNER_PHONE_NUMBER
            _sms.send_long_sms(target, response_text)
        except Exception as e:
            log.error("[DELIVERY] SMS delivery failed: %s", e)

    elif method == "image":
        try:
            from sms import _render_sms_image
            import push_image as _pi
            img_path = _render_sms_image(response_text, header="ARIA")
            _pi.push_image(img_path, caption="ARIA")
            os.unlink(img_path)
        except Exception as e:
            log.error("[DELIVERY] Image delivery failed: %s", e)

    elif method in ("voice", "glasses"):
        try:
            from tts import _generate_tts
            audio = await _generate_tts(response_text)

            if push_voice:
                # Save to UUID temp file and push to phone
                wav_path = config.DATA_DIR / f"voice_{uuid.uuid4().hex[:8]}.wav"
                wav_path.write_bytes(audio)
                try:
                    import push_audio as _pa
                    if not _pa.push_audio(str(wav_path)):
                        # Voice push failed — fall back to SMS
                        log.warning("[DELIVERY] Voice push failed, falling back to SMS")
                        try:
                            import sms as _sms
                            target = sms_target or config.OWNER_PHONE_NUMBER
                            _sms.send_long_sms(target, response_text)
                            method = "sms"
                        except Exception as se:
                            log.error("[DELIVERY] SMS fallback also failed: %s", se)
                finally:
                    try:
                        os.unlink(str(wav_path))
                    except OSError:
                        pass
            # When push_voice=False, audio bytes are returned for the caller
            # to store in the _tasks dict (phone polls /ask/result for them)
        except Exception as e:
            log.error("[DELIVERY] Voice delivery failed: %s", e)

    return {"method": method, "audio": audio, "reason": decision.reason}
