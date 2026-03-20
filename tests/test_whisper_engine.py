"""Tests for whisper_engine.py — Whisper STT engine components.

SAFETY: No actual Whisper model is loaded. GPU/model tests are mocked.
"""

import numpy as np
from unittest.mock import patch, MagicMock

import whisper_engine


class TestTranscriptResult:
    def test_defaults(self):
        r = whisper_engine.TranscriptResult(text="hello")
        assert r.text == "hello"
        assert r.segments == []
        assert r.language == ""
        assert r.language_probability == 0.0
        assert r.duration == 0.0
        assert r.processing_time == 0.0

    def test_all_fields(self):
        r = whisper_engine.TranscriptResult(
            text="test", segments=[{"start": 0.0, "end": 1.0, "text": "test"}],
            language="en", language_probability=0.99,
            duration=1.5, processing_time=0.3,
        )
        assert r.language == "en"
        assert r.duration == 1.5


class TestResample:
    def test_same_rate_noop(self):
        pcm = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        result = whisper_engine.resample(pcm, 16000, 16000)
        np.testing.assert_array_equal(result, pcm)

    def test_upsample(self):
        pcm = np.array([0.0, 1.0], dtype=np.float32)
        result = whisper_engine.resample(pcm, 8000, 16000)
        assert len(result) == 4  # 2 * (16000/8000) = 4
        assert result.dtype == np.float32
        # First and last should match original endpoints
        assert result[0] == 0.0
        assert abs(result[-1] - 1.0) < 0.01

    def test_downsample(self):
        pcm = np.linspace(0, 1, 100, dtype=np.float32)
        result = whisper_engine.resample(pcm, 44100, 16000)
        expected_len = int(100 * (16000 / 44100))
        assert len(result) == expected_len


class TestEnergyVAD:
    def _make_silence(self, duration_s=0.1, sample_rate=16000):
        """Generate silence (near-zero amplitude)."""
        n = int(sample_rate * duration_s)
        return np.zeros(n, dtype=np.float32)

    def _make_speech(self, duration_s=0.5, sample_rate=16000, amplitude=0.1):
        """Generate loud-enough-to-be-speech signal."""
        n = int(sample_rate * duration_s)
        t = np.linspace(0, duration_s, n, dtype=np.float32)
        return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    def test_initial_state_is_silence(self):
        vad = whisper_engine.EnergyVAD()
        assert vad.state == "silence"

    def test_silence_stays_silent(self):
        vad = whisper_engine.EnergyVAD()
        result = vad.process_chunk(self._make_silence())
        assert result is None
        assert vad.state == "silence"

    def test_speech_transitions_to_speech_state(self):
        vad = whisper_engine.EnergyVAD()
        result = vad.process_chunk(self._make_speech())
        assert result is None
        assert vad.state == "speech"

    def test_speech_then_silence_returns_utterance(self):
        vad = whisper_engine.EnergyVAD(sample_rate=16000)
        # Send speech for > MIN_SPEECH_DURATION
        speech = self._make_speech(duration_s=0.5)
        vad.process_chunk(speech)
        assert vad.state == "speech"

        # First silence chunk: speech → trailing_silence
        silence1 = self._make_silence(duration_s=0.4)
        result = vad.process_chunk(silence1)
        assert result is None
        assert vad.state == "trailing_silence"

        # Second silence chunk: threshold check fires, returns utterance
        silence2 = self._make_silence(duration_s=0.4)
        result = vad.process_chunk(silence2)
        assert result is not None
        assert isinstance(result, np.ndarray)
        assert len(result) > 0
        assert vad.state == "silence"

    def test_short_speech_is_ignored(self):
        vad = whisper_engine.EnergyVAD(sample_rate=16000)
        # Very short speech (< MIN_SPEECH_DURATION)
        short = self._make_speech(duration_s=0.1)
        vad.process_chunk(short)

        silence = self._make_silence(duration_s=0.6)
        result = vad.process_chunk(silence)
        assert result is None  # too short, filtered out

    def test_speech_with_brief_pause_continues(self):
        vad = whisper_engine.EnergyVAD(sample_rate=16000)
        # Speech
        vad.process_chunk(self._make_speech(duration_s=0.5))
        assert vad.state == "speech"

        # Brief silence (< SILENCE_DURATION)
        vad.process_chunk(self._make_silence(duration_s=0.2))
        assert vad.state == "trailing_silence"

        # More speech — should resume
        result = vad.process_chunk(self._make_speech(duration_s=0.3))
        assert result is None
        assert vad.state == "speech"

    def test_flush_returns_accumulated_speech(self):
        vad = whisper_engine.EnergyVAD(sample_rate=16000)
        vad.process_chunk(self._make_speech(duration_s=0.5))
        result = vad.flush()
        assert result is not None
        assert len(result) > 0

    def test_flush_with_no_data(self):
        vad = whisper_engine.EnergyVAD()
        assert vad.flush() is None

    def test_flush_too_short(self):
        vad = whisper_engine.EnergyVAD(sample_rate=16000)
        vad.process_chunk(self._make_speech(duration_s=0.1))
        assert vad.flush() is None  # too short

    def test_reset_after_utterance(self):
        vad = whisper_engine.EnergyVAD(sample_rate=16000)
        vad.process_chunk(self._make_speech(duration_s=0.5))
        # Two silence chunks needed: first transitions to trailing_silence,
        # second triggers threshold check and returns utterance
        vad.process_chunk(self._make_silence(duration_s=0.4))
        vad.process_chunk(self._make_silence(duration_s=0.4))

        # After returning an utterance, state should reset
        assert vad.state == "silence"
        assert vad._chunks == []


class TestWhisperEngine:
    def test_lazy_loading(self):
        engine = whisper_engine.WhisperEngine("test", "cpu", "int8")
        assert engine._model is None  # not loaded yet

    def test_transcribe_with_mock_model(self):
        mock_model = MagicMock()
        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.language_probability = 0.95
        mock_info.duration = 2.0

        mock_seg = MagicMock()
        mock_seg.start = 0.0
        mock_seg.end = 2.0
        mock_seg.text = " Hello world"

        mock_model.transcribe.return_value = (iter([mock_seg]), mock_info)

        engine = whisper_engine.WhisperEngine("test", "cpu", "int8")
        engine._model = mock_model  # Inject mock directly, skipping _ensure_model

        result = engine.transcribe("fake_audio.wav")
        mock_model.transcribe.assert_called_once()
        assert result.text == "Hello world"
        assert result.language == "en"
        assert len(result.segments) == 1

    def test_transcribe_numpy_resamples(self):
        engine = whisper_engine.WhisperEngine("test", "cpu", "int8")
        pcm = np.random.randn(44100).astype(np.float32)  # 1s at 44.1kHz

        with patch.object(engine, "transcribe") as mock_transcribe:
            mock_transcribe.return_value = whisper_engine.TranscriptResult(text="test")
            result = engine.transcribe_numpy(pcm, sample_rate=44100)

            # Should have been resampled to 16kHz
            called_audio = mock_transcribe.call_args[0][0]
            expected_len = int(44100 * (16000 / 44100))
            assert len(called_audio) == expected_len


class TestGetEngine:
    def test_singleton(self):
        whisper_engine._engine = None
        e1 = whisper_engine.get_engine()
        e2 = whisper_engine.get_engine()
        assert e1 is e2
        whisper_engine._engine = None
