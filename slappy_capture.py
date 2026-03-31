#!/usr/bin/env python3
"""Ambient audio capture daemon for slappy (laptop).

Captures audio from DJI Mic 3 via Bluetooth/PipeWire, runs VAD segmentation,
transcribes locally with faster-whisper (base model, CPU int8), and relays
transcripts to beardos. Audio files saved for quality re-processing.

Standalone: no FastAPI, no PostgreSQL. Uses only config.py, whisper_engine.py,
numpy, sounddevice, and httpx.

Usage:
    ./venv/bin/python slappy_capture.py
    # Or via OpenRC: rc-service aria-capture start
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import config
from whisper_engine import EnergyVAD, WhisperEngine, resample

log = logging.getLogger("aria.capture")

# ---------------------------------------------------------------------------
# Configuration (from config.py, with defaults)
# ---------------------------------------------------------------------------

BEARDOS_URL = getattr(config, "BEARDOS_URL", "http://100.107.139.121:8450")
AUTH_TOKEN = getattr(config, "AUTH_TOKEN", "")
CAPTURE_DEVICE = getattr(config, "AMBIENT_CAPTURE_DEVICE", None)
WHISPER_MODEL = getattr(config, "AMBIENT_WHISPER_MODEL", "base")
WHISPER_DEVICE = getattr(config, "WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = getattr(config, "WHISPER_COMPUTE_TYPE", "int8")
VAD_SILENCE_S = getattr(config, "AMBIENT_VAD_SILENCE_S", 2.0)
VAD_MIN_SPEECH_S = getattr(config, "AMBIENT_VAD_MIN_SPEECH_S", 1.0)

# Audio capture settings
SAMPLE_RATE = 48000       # DJI Mic 3 native rate
TARGET_RATE = 16000       # Whisper expects 16kHz
CHANNELS = 1              # mono
CHUNK_DURATION_S = 0.1    # 100ms chunks for VAD
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_DURATION_S)

# Offline queue
QUEUE_DIR = Path(getattr(config, "DATA_DIR", Path(__file__).parent / "data")) / "capture_queue"
QUEUE_MAX_FILES = 1000    # ~8 hours of speech segments

# Audio save directory (for quality pass uploads)
AUDIO_DIR = Path(getattr(config, "DATA_DIR", Path(__file__).parent / "data")) / "ambient_local"

# Reconnect settings
RECONNECT_POLL_S = 5      # seconds between reconnect attempts
RELAY_TIMEOUT_S = 10      # HTTP timeout for transcript relay

# Shutdown flag
_running = True


def _signal_handler(signum, frame):
    global _running
    log.info("Received signal %d, shutting down...", signum)
    _running = False


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ---------------------------------------------------------------------------
# Offline queue (file-based JSON)
# ---------------------------------------------------------------------------

def _queue_transcript(payload: dict) -> bool:
    """Write a transcript payload to the offline queue."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    # Check queue size
    existing = list(QUEUE_DIR.glob("*.json"))
    if len(existing) >= QUEUE_MAX_FILES:
        log.warning("Queue full (%d files), dropping oldest", len(existing))
        oldest = sorted(existing)[0]
        oldest.unlink()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = QUEUE_DIR / f"{ts}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    log.debug("Queued transcript: %s", path.name)
    return True


def _drain_queue() -> int:
    """Send queued transcripts to beardos. Returns count sent."""
    if not QUEUE_DIR.exists():
        return 0

    files = sorted(QUEUE_DIR.glob("*.json"))
    if not files:
        return 0

    sent = 0
    for f in files:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            if _relay_to_beardos(payload):
                f.unlink()
                sent += 1
            else:
                break  # beardos still unreachable, stop draining
        except Exception as e:
            log.warning("Failed to drain %s: %s", f.name, e)
            break

    if sent > 0:
        log.info("Drained %d queued transcripts", sent)
    return sent


# ---------------------------------------------------------------------------
# HTTP relay to beardos
# ---------------------------------------------------------------------------

def _relay_to_beardos(payload: dict) -> bool:
    """POST a transcript to beardos /ambient/transcript. Returns success."""
    import httpx

    url = f"{BEARDOS_URL}/ambient/transcript"
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    try:
        resp = httpx.post(url, json=payload, headers=headers,
                          timeout=RELAY_TIMEOUT_S)
        if resp.status_code == 200:
            data = resp.json()
            wake = data.get("has_wake_word", False)
            if wake:
                log.info("Wake word detected by beardos for transcript %s",
                         data.get("id"))
            return True
        else:
            log.warning("Relay failed: HTTP %d — %s",
                        resp.status_code, resp.text[:200])
            return False
    except httpx.ConnectError:
        log.debug("beardos unreachable (connect error)")
        return False
    except httpx.TimeoutException:
        log.debug("beardos unreachable (timeout)")
        return False
    except Exception as e:
        log.warning("Relay error: %s", e)
        return False


def _upload_audio_to_beardos(audio_path: str, started_at: str) -> bool:
    """Upload an audio file to beardos for quality pass (lower priority)."""
    import httpx

    url = f"{BEARDOS_URL}/ambient/upload"
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    try:
        audio_bytes = Path(audio_path).read_bytes()
        files = {"audio": ("segment.wav", audio_bytes, "audio/wav")}
        data = {"source": "slappy", "started_at": started_at}
        resp = httpx.post(url, files=files, data=data, headers=headers,
                          timeout=30)
        return resp.status_code == 200
    except Exception as e:
        log.debug("Audio upload failed (non-critical): %s", e)
        return False


# ---------------------------------------------------------------------------
# Audio file management
# ---------------------------------------------------------------------------

def _save_audio_locally(pcm_16k: np.ndarray, started_at: datetime,
                        duration_s: float) -> str | None:
    """Save a PCM segment as WAV locally for quality pass upload."""
    try:
        import soundfile as sf

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        day_dir = AUDIO_DIR / started_at.strftime("%Y-%m-%d")
        day_dir.mkdir(exist_ok=True)

        filename = f"seg_{started_at.strftime('%H%M%S')}_{duration_s:.1f}s.wav"
        path = day_dir / filename
        if path.exists():
            path = day_dir / f"seg_{started_at.strftime('%H%M%S')}_{os.getpid()}.wav"

        sf.write(str(path), pcm_16k, TARGET_RATE)
        return str(path)
    except Exception as e:
        log.warning("Failed to save audio locally: %s", e)
        return None


# ---------------------------------------------------------------------------
# Audio device discovery
# ---------------------------------------------------------------------------

def _find_capture_device() -> int | str | None:
    """Find the DJI Mic 3 or configured capture device.

    Returns a sounddevice device index/name, or None if not found.
    """
    import sounddevice as sd

    if CAPTURE_DEVICE is not None:
        return CAPTURE_DEVICE

    # Auto-detect: look for DJI in device names
    try:
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            name = dev.get("name", "").lower()
            if dev.get("max_input_channels", 0) > 0:
                if "dji" in name or "mic 3" in name:
                    log.info("Auto-detected DJI Mic 3: device %d (%s)", i, dev["name"])
                    return i
    except Exception as e:
        log.warning("Device enumeration failed: %s", e)

    # Fall back to default input device
    try:
        default = sd.query_devices(kind="input")
        if default:
            log.info("Using default input device: %s", default.get("name", "unknown"))
            return None  # sounddevice uses default when device=None
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Main capture loop
# ---------------------------------------------------------------------------

def run():
    """Main capture loop. Blocks until shutdown signal."""
    import sounddevice as sd

    log.info("Starting ambient capture daemon")
    log.info("  Model: %s (%s/%s)", WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE)
    log.info("  beardos: %s", BEARDOS_URL)
    log.info("  VAD: silence=%.1fs, min_speech=%.1fs", VAD_SILENCE_S, VAD_MIN_SPEECH_S)

    # Initialize Whisper
    engine = WhisperEngine(WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE)

    # Initialize VAD with ambient-tuned thresholds
    vad = EnergyVAD(sample_rate=TARGET_RATE)
    vad.SILENCE_DURATION = VAD_SILENCE_S
    vad.MIN_SPEECH_DURATION = VAD_MIN_SPEECH_S

    device = _find_capture_device()
    segments_processed = 0
    last_queue_drain = 0

    while _running:
        try:
            log.info("Opening audio stream (device=%s)...", device)
            with sd.InputStream(device=device, samplerate=SAMPLE_RATE,
                                channels=CHANNELS, dtype="float32",
                                blocksize=CHUNK_FRAMES) as stream:
                log.info("Audio stream open — capturing")

                while _running:
                    # Read audio chunk
                    chunk, overflowed = stream.read(CHUNK_FRAMES)
                    if overflowed:
                        log.debug("Audio overflow (buffer underrun)")

                    # Convert to mono float32 and resample to 16kHz
                    pcm = chunk[:, 0] if chunk.ndim > 1 else chunk.flatten()
                    pcm_16k = resample(pcm, SAMPLE_RATE, TARGET_RATE)

                    # Feed VAD
                    utterance = vad.process_chunk(pcm_16k)
                    if utterance is None:
                        # Periodically drain queue during silence
                        now = time.time()
                        if now - last_queue_drain > 30:
                            _drain_queue()
                            last_queue_drain = now
                        continue

                    # Got a complete utterance — transcribe
                    started_at = datetime.now()
                    duration_s = len(utterance) / TARGET_RATE

                    result = engine.transcribe_numpy(utterance, sample_rate=TARGET_RATE)
                    text = result.text.strip()

                    if not text:
                        continue

                    segments_processed += 1
                    log.info("[%d] %.1fs → %s", segments_processed,
                             duration_s, text[:80])

                    # Save audio locally for quality pass
                    audio_path = _save_audio_locally(utterance, started_at, duration_s)

                    # Build relay payload
                    payload = {
                        "text": text,
                        "source": "slappy",
                        "started_at": started_at.isoformat(),
                        "ended_at": (started_at.__class__(
                            started_at.year, started_at.month, started_at.day,
                            started_at.hour, started_at.minute, started_at.second,
                        )).isoformat(),  # approximate
                        "duration_s": round(duration_s, 2),
                        "confidence": result.language_probability,
                    }

                    # Try to relay to beardos
                    if _relay_to_beardos(payload):
                        # Also try to drain queue while we have connectivity
                        _drain_queue()
                    else:
                        _queue_transcript(payload)

        except Exception as e:
            if not _running:
                break
            error_name = type(e).__name__
            log.warning("Audio stream error (%s): %s — reconnecting in %ds",
                        error_name, e, RECONNECT_POLL_S)

            # Flush any accumulated VAD speech
            remaining = vad.flush()
            if remaining is not None and len(remaining) > TARGET_RATE * VAD_MIN_SPEECH_S:
                log.info("Flushing VAD buffer on disconnect")
                result = engine.transcribe_numpy(remaining, sample_rate=TARGET_RATE)
                if result.text.strip():
                    payload = {
                        "text": result.text.strip(),
                        "source": "slappy",
                        "started_at": datetime.now().isoformat(),
                        "duration_s": round(len(remaining) / TARGET_RATE, 2),
                        "confidence": result.language_probability,
                    }
                    if not _relay_to_beardos(payload):
                        _queue_transcript(payload)

            # Wait before reconnecting
            for _ in range(RECONNECT_POLL_S * 10):
                if not _running:
                    break
                time.sleep(0.1)

    # Final flush on shutdown
    remaining = vad.flush()
    if remaining is not None:
        result = engine.transcribe_numpy(remaining, sample_rate=TARGET_RATE)
        if result.text.strip():
            payload = {
                "text": result.text.strip(),
                "source": "slappy",
                "started_at": datetime.now().isoformat(),
                "duration_s": round(len(remaining) / TARGET_RATE, 2),
                "confidence": result.language_probability,
            }
            if not _relay_to_beardos(payload):
                _queue_transcript(payload)

    # Final queue drain attempt
    _drain_queue()
    log.info("Capture daemon stopped (processed %d segments)", segments_processed)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if not getattr(config, "AMBIENT_CAPTURE_ENABLED", False):
        log.error("AMBIENT_CAPTURE_ENABLED is not set in config.py — exiting")
        sys.exit(1)
    run()
