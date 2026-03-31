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
import subprocess
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
VAD_SPEECH_THRESHOLD = getattr(config, "AMBIENT_VAD_SPEECH_THRESHOLD", 0.015)

# Audio capture settings
TARGET_RATE = 16000       # Whisper expects 16kHz
CHANNELS = 1              # mono
CHUNK_DURATION_S = 0.1    # 100ms chunks for VAD

# Sliding window (real-time streaming for glasses context)
WINDOW_S = getattr(config, "AMBIENT_WINDOW_S", 5.0)          # transcribe last N seconds
WINDOW_INTERVAL_S = getattr(config, "AMBIENT_WINDOW_INTERVAL_S", 1.0)  # every N seconds

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

def _relay_to_beardos(payload: dict, stream: bool = False) -> bool:
    """POST a transcript to beardos /ambient/transcript. Returns success.

    stream=True sends as a streaming partial (cached in Redis only, not stored in DB).
    stream=False sends as a completed segment (stored permanently).
    """
    import httpx

    url = f"{BEARDOS_URL}/ambient/transcript"
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    if stream:
        payload = {**payload, "stream": True}

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
            if not stream:  # don't warn on stream failures, they're non-critical
                log.warning("Relay failed: HTTP %d — %s",
                            resp.status_code, resp.text[:200])
            return False
    except httpx.ConnectError:
        if not stream:
            log.debug("beardos unreachable (connect error)")
        return False
    except httpx.TimeoutException:
        if not stream:
            log.debug("beardos unreachable (timeout)")
        return False
    except Exception as e:
        if not stream:
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

def _find_pulseaudio_source() -> str | None:
    """Find the Bluetooth audio source name via pactl."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if "bluez_input" in line:
                return line.split("\t")[1]
    except Exception as e:
        log.warning("Failed to find PulseAudio Bluetooth source: %s", e)
    return None


def run():
    """Main capture loop. Blocks until shutdown signal.

    Uses parecord for Bluetooth sources (direct PipeWire capture at correct
    levels) and falls back to sounddevice for USB/analog sources.
    """
    log.info("Starting ambient capture daemon")
    log.info("  Model: %s (%s/%s)", WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE)
    log.info("  beardos: %s", BEARDOS_URL)
    log.info("  VAD: silence=%.1fs, min_speech=%.1fs, threshold=%.4f",
             VAD_SILENCE_S, VAD_MIN_SPEECH_S, VAD_SPEECH_THRESHOLD)

    # Initialize Whisper
    engine = WhisperEngine(WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE)

    # Initialize VAD with ambient-tuned thresholds
    vad = EnergyVAD(sample_rate=TARGET_RATE)
    vad.SPEECH_THRESHOLD = VAD_SPEECH_THRESHOLD
    vad.SILENCE_DURATION = VAD_SILENCE_S
    vad.MIN_SPEECH_DURATION = VAD_MIN_SPEECH_S

    # Detect capture method: Bluetooth (parecord) or USB/analog (sounddevice)
    bt_source = _find_pulseaudio_source()
    use_parecord = bt_source is not None

    segments_processed = 0
    last_queue_drain = 0
    chunk_samples = int(TARGET_RATE * CHUNK_DURATION_S)  # 1600 samples at 16kHz

    # Sliding window state
    window_max_samples = int(TARGET_RATE * WINDOW_S)
    rolling_buffer: list[np.ndarray] = []
    rolling_samples = 0
    last_window_time = 0.0
    last_window_text = ""

    def _process_utterance(utterance):
        """Transcribe an utterance and relay to beardos. Returns True if processed."""
        nonlocal segments_processed, last_window_text

        started_at = datetime.now()
        duration_s = len(utterance) / TARGET_RATE

        result = engine.transcribe_numpy(utterance, sample_rate=TARGET_RATE)
        text = result.text.strip()
        if not text:
            return False

        segments_processed += 1
        log.info("[%d] %.1fs → %s", segments_processed, duration_s, text[:80])

        audio_path = _save_audio_locally(utterance, started_at, duration_s)

        payload = {
            "text": text,
            "source": "slappy",
            "started_at": started_at.isoformat(),
            "duration_s": round(duration_s, 2),
            "confidence": result.language_probability,
        }

        if _relay_to_beardos(payload):
            _drain_queue()
        else:
            _queue_transcript(payload)

        # Reset sliding window after a completed segment
        last_window_text = ""
        return True

    def _process_chunk(pcm_16k: np.ndarray):
        """Feed a chunk to VAD and sliding window. Called from both capture paths."""
        nonlocal rolling_samples, last_window_time, last_window_text, last_queue_drain

        # --- VAD path (segment completion → permanent storage) ---
        utterance = vad.process_chunk(pcm_16k)
        if utterance is not None:
            _process_utterance(utterance)
            # Clear rolling buffer — segment was captured completely by VAD
            rolling_buffer.clear()
            rolling_samples = 0
            return

        # --- Sliding window path (real-time context for glasses) ---
        rolling_buffer.append(pcm_16k)
        rolling_samples += len(pcm_16k)

        # Trim buffer to max window size
        while rolling_samples > window_max_samples and rolling_buffer:
            removed = rolling_buffer.pop(0)
            rolling_samples -= len(removed)

        now = time.time()

        # Periodic queue drain during silence
        if now - last_queue_drain > 30:
            _drain_queue()
            last_queue_drain = now

        # Sliding window transcription at configured interval
        if now - last_window_time < WINDOW_INTERVAL_S:
            return
        if rolling_samples < TARGET_RATE:  # need at least 1 second of audio
            return

        last_window_time = now
        window_audio = np.concatenate(rolling_buffer)

        # Quick check: is there any speech in the window?
        rms = float(np.sqrt(np.mean(window_audio ** 2)))
        if rms < VAD_SPEECH_THRESHOLD:
            return  # all silence, skip transcription

        result = engine.transcribe_numpy(window_audio, sample_rate=TARGET_RATE)
        text = result.text.strip()

        if not text or text == last_window_text:
            return

        last_window_text = text
        _relay_to_beardos({
            "text": text,
            "source": "slappy",
            "started_at": datetime.now().isoformat(),
            "duration_s": round(rolling_samples / TARGET_RATE, 2),
            "confidence": result.language_probability,
        }, stream=True)

    while _running:
        try:
            if use_parecord:
                log.info("Opening parecord stream (source=%s, 16kHz mono)...", bt_source)
                proc = subprocess.Popen(
                    ["parecord", "--channels=1", "--rate=16000",
                     "--format=s16le", f"--device={bt_source}", "--raw"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                log.info("parecord stream open — capturing (window=%.0fs/%.0fs)",
                         WINDOW_S, WINDOW_INTERVAL_S)
                try:
                    while _running:
                        raw = proc.stdout.read(chunk_samples * 2)
                        if not raw:
                            break
                        pcm_16k = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                        _process_chunk(pcm_16k)
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()

            else:
                import sounddevice as sd
                device = _find_capture_device()
                try:
                    dev_info = sd.query_devices(device, kind="input")
                    sample_rate = int(dev_info["default_samplerate"])
                except Exception:
                    sample_rate = 48000
                needs_resample = (sample_rate != TARGET_RATE)
                sd_chunk = int(sample_rate * CHUNK_DURATION_S)
                log.info("Opening sounddevice stream (device=%s, rate=%d, window=%.0fs/%.0fs)...",
                         device, sample_rate, WINDOW_S, WINDOW_INTERVAL_S)

                with sd.InputStream(device=device, samplerate=sample_rate,
                                    channels=CHANNELS, dtype="float32",
                                    blocksize=sd_chunk) as stream:
                    log.info("sounddevice stream open — capturing")
                    while _running:
                        chunk, overflowed = stream.read(sd_chunk)
                        if overflowed:
                            log.debug("Audio overflow")
                        pcm = chunk[:, 0] if chunk.ndim > 1 else chunk.flatten()
                        pcm_16k = resample(pcm, sample_rate, TARGET_RATE) if needs_resample else pcm
                        _process_chunk(pcm_16k)

        except Exception as e:
            if not _running:
                break
            error_name = type(e).__name__
            log.warning("Audio stream error (%s): %s — reconnecting in %ds",
                        error_name, e, RECONNECT_POLL_S)

            remaining = vad.flush()
            if remaining is not None and len(remaining) > TARGET_RATE * VAD_MIN_SPEECH_S:
                log.info("Flushing VAD buffer on disconnect")
                _process_utterance(remaining)

            for _ in range(RECONNECT_POLL_S * 10):
                if not _running:
                    break
                time.sleep(0.1)

    # Final flush on shutdown
    remaining = vad.flush()
    if remaining is not None and len(remaining) > TARGET_RATE * VAD_MIN_SPEECH_S:
        _process_utterance(remaining)

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
