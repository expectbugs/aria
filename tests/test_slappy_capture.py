"""Tests for slappy_capture.py — ambient audio capture daemon.

Tests the relay, queue, VAD integration, and device discovery logic.
All external I/O (sounddevice, httpx, filesystem) is mocked.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import numpy as np
import pytest

import slappy_capture
from whisper_engine import EnergyVAD, resample


# ---------------------------------------------------------------------------
# VAD with ambient thresholds
# ---------------------------------------------------------------------------

class TestAmbientVAD:
    """Test EnergyVAD with ambient-tuned thresholds."""

    def test_longer_silence_threshold(self):
        """Ambient VAD uses 2.0s silence (vs 0.5s for directed speech)."""
        vad = EnergyVAD(sample_rate=16000)
        vad.SILENCE_DURATION = 2.0
        vad.MIN_SPEECH_DURATION = 1.0

        # Feed 1.5 seconds of speech
        speech = np.random.randn(24000).astype(np.float32) * 0.1
        for i in range(0, len(speech), 1600):
            result = vad.process_chunk(speech[i:i + 1600])
            assert result is None  # still accumulating

        # Feed 1.5 seconds of silence — should NOT trigger (< 2.0s threshold)
        silence = np.zeros(24000, dtype=np.float32)
        for i in range(0, len(silence), 1600):
            result = vad.process_chunk(silence[i:i + 1600])
        assert result is None  # 1.5s silence < 2.0s threshold

        # Feed another 1 second of silence — NOW it should trigger
        triggered = None
        for i in range(0, 16000, 1600):
            r = vad.process_chunk(silence[i:i + 1600])
            if r is not None:
                triggered = r
        assert triggered is not None

    def test_min_speech_filters_noise(self):
        """Speech shorter than 1.0s should be filtered in ambient mode."""
        vad = EnergyVAD(sample_rate=16000)
        vad.SILENCE_DURATION = 2.0
        vad.MIN_SPEECH_DURATION = 1.0

        # 0.5s of speech (below threshold)
        short_speech = np.random.randn(8000).astype(np.float32) * 0.1
        for i in range(0, len(short_speech), 1600):
            vad.process_chunk(short_speech[i:i + 1600])

        # 3 seconds of silence
        silence = np.zeros(48000, dtype=np.float32)
        result = None
        for i in range(0, len(silence), 1600):
            r = vad.process_chunk(silence[i:i + 1600])
            if r is not None:
                result = r
        assert result is None  # too short, filtered

    def test_flush_returns_accumulated(self):
        vad = EnergyVAD(sample_rate=16000)
        vad.MIN_SPEECH_DURATION = 0.5

        # Feed 1 second of speech
        speech = np.random.randn(16000).astype(np.float32) * 0.1
        for i in range(0, len(speech), 1600):
            vad.process_chunk(speech[i:i + 1600])

        result = vad.flush()
        assert result is not None
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Resample
# ---------------------------------------------------------------------------

class TestResample:
    def test_48k_to_16k(self):
        pcm = np.random.randn(4800).astype(np.float32)
        out = resample(pcm, 48000, 16000)
        assert len(out) == 1600  # 4800 * (16000/48000)

    def test_same_rate_noop(self):
        pcm = np.random.randn(1600).astype(np.float32)
        out = resample(pcm, 16000, 16000)
        assert np.array_equal(out, pcm)


# ---------------------------------------------------------------------------
# Offline queue
# ---------------------------------------------------------------------------

class TestOfflineQueue:
    def test_queue_and_read(self, tmp_path):
        with patch.object(slappy_capture, "QUEUE_DIR", tmp_path):
            payload = {
                "text": "Test transcript",
                "source": "slappy",
                "started_at": "2026-03-30T14:00:00",
            }
            slappy_capture._queue_transcript(payload)

            files = list(tmp_path.glob("*.json"))
            assert len(files) == 1
            data = json.loads(files[0].read_text())
            assert data["text"] == "Test transcript"

    def test_queue_max_files_drops_oldest(self, tmp_path):
        with patch.object(slappy_capture, "QUEUE_DIR", tmp_path), \
             patch.object(slappy_capture, "QUEUE_MAX_FILES", 3):
            for i in range(4):
                slappy_capture._queue_transcript({"text": f"msg {i}", "source": "slappy",
                                                   "started_at": "2026-03-30T14:00:00"})
                import time
                time.sleep(0.01)  # ensure unique timestamps

            files = list(tmp_path.glob("*.json"))
            assert len(files) == 3

    def test_drain_sends_and_deletes(self, tmp_path):
        with patch.object(slappy_capture, "QUEUE_DIR", tmp_path), \
             patch.object(slappy_capture, "_relay_to_beardos", return_value=True) as mock_relay:
            # Queue two items
            slappy_capture._queue_transcript({"text": "first", "source": "slappy",
                                               "started_at": "2026-03-30T14:00:00"})
            slappy_capture._queue_transcript({"text": "second", "source": "slappy",
                                               "started_at": "2026-03-30T14:01:00"})

            sent = slappy_capture._drain_queue()
            assert sent == 2
            assert mock_relay.call_count == 2
            assert len(list(tmp_path.glob("*.json"))) == 0

    def test_drain_stops_on_failure(self, tmp_path):
        with patch.object(slappy_capture, "QUEUE_DIR", tmp_path), \
             patch.object(slappy_capture, "_relay_to_beardos", return_value=False):
            slappy_capture._queue_transcript({"text": "queued", "source": "slappy",
                                               "started_at": "2026-03-30T14:00:00"})
            sent = slappy_capture._drain_queue()
            assert sent == 0
            assert len(list(tmp_path.glob("*.json"))) == 1  # still queued

    def test_drain_empty_queue(self, tmp_path):
        with patch.object(slappy_capture, "QUEUE_DIR", tmp_path):
            assert slappy_capture._drain_queue() == 0

    def test_drain_nonexistent_dir(self, tmp_path):
        nonexistent = tmp_path / "nonexistent"
        with patch.object(slappy_capture, "QUEUE_DIR", nonexistent):
            assert slappy_capture._drain_queue() == 0


# ---------------------------------------------------------------------------
# HTTP relay
# ---------------------------------------------------------------------------

class TestRelay:
    @patch("httpx.post")
    def test_successful_relay(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 42, "has_wake_word": False}
        mock_post.return_value = mock_resp

        result = slappy_capture._relay_to_beardos({
            "text": "Hello", "source": "slappy",
            "started_at": "2026-03-30T14:00:00",
        })
        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "/ambient/transcript" in call_kwargs[0][0] or \
               "/ambient/transcript" in str(call_kwargs)

    @patch("httpx.post")
    def test_relay_connect_error(self, mock_post):
        import httpx
        mock_post.side_effect = httpx.ConnectError("Connection refused")
        result = slappy_capture._relay_to_beardos({"text": "Test", "source": "slappy",
                                                    "started_at": "2026-03-30T14:00:00"})
        assert result is False

    @patch("httpx.post")
    def test_relay_timeout(self, mock_post):
        import httpx
        mock_post.side_effect = httpx.TimeoutException("Timed out")
        result = slappy_capture._relay_to_beardos({"text": "Test", "source": "slappy",
                                                    "started_at": "2026-03-30T14:00:00"})
        assert result is False

    @patch("httpx.post")
    def test_relay_http_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "Service unavailable"
        mock_post.return_value = mock_resp

        result = slappy_capture._relay_to_beardos({"text": "Test", "source": "slappy",
                                                    "started_at": "2026-03-30T14:00:00"})
        assert result is False

    @patch("httpx.post")
    def test_relay_wake_word_logged(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 1, "has_wake_word": True}
        mock_post.return_value = mock_resp

        result = slappy_capture._relay_to_beardos({
            "text": "ARIA set a timer", "source": "slappy",
            "started_at": "2026-03-30T14:00:00",
        })
        assert result is True


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------

class TestDeviceDiscovery:
    def test_configured_device_returned(self):
        with patch.object(slappy_capture, "CAPTURE_DEVICE", "my_device"):
            result = slappy_capture._find_capture_device()
            assert result == "my_device"

    @patch("sounddevice.query_devices")
    def test_auto_detect_dji(self, mock_query):
        with patch.object(slappy_capture, "CAPTURE_DEVICE", None):
            mock_query.return_value = [
                {"name": "Built-in Microphone", "max_input_channels": 2},
                {"name": "DJI Mic 3 Receiver", "max_input_channels": 1},
            ]
            result = slappy_capture._find_capture_device()
            assert result == 1  # index of DJI device

    @patch("sounddevice.query_devices")
    @patch("sounddevice.query_devices")
    def test_fallback_to_default(self, mock_default, mock_all):
        with patch.object(slappy_capture, "CAPTURE_DEVICE", None):
            # First call: query_devices() for all devices (no DJI found)
            mock_all.return_value = [
                {"name": "Built-in Microphone", "max_input_channels": 2},
            ]
            # query_devices(kind="input") for default
            mock_default.return_value = {"name": "Built-in Microphone"}

            # Reimport to use mock - just test the logic path
            import sounddevice as sd
            with patch.object(sd, "query_devices", side_effect=[
                [{"name": "Built-in Microphone", "max_input_channels": 2}],
                {"name": "Built-in Microphone"},
            ]):
                result = slappy_capture._find_capture_device()
                assert result is None  # None = use default


# ---------------------------------------------------------------------------
# Audio save
# ---------------------------------------------------------------------------

class TestAudioSave:
    def test_saves_wav(self, tmp_path):
        with patch.object(slappy_capture, "AUDIO_DIR", tmp_path):
            pcm = np.random.randn(16000).astype(np.float32) * 0.1
            dt = datetime(2026, 3, 30, 14, 23, 1)
            path = slappy_capture._save_audio_locally(pcm, dt, 1.0)
            assert path is not None
            assert os.path.exists(path)
            assert "142301" in path

    def test_returns_none_on_error(self, tmp_path):
        with patch.object(slappy_capture, "AUDIO_DIR", tmp_path), \
             patch("soundfile.write", side_effect=Exception("Write failed")):
            pcm = np.random.randn(16000).astype(np.float32)
            path = slappy_capture._save_audio_locally(pcm, datetime.now(), 1.0)
            assert path is None
