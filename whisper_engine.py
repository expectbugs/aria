"""Whisper STT engine — singleton, GPU-accelerated, thread-safe.

Uses faster-whisper (CTranslate2 runtime) for efficient inference.
Model loads lazily on first use and stays resident (like Kokoro TTS).
"""

import io
import logging
import threading
import time
from dataclasses import dataclass, field

import numpy as np

import config

log = logging.getLogger("aria.whisper")


@dataclass
class TranscriptResult:
    """Result of a Whisper transcription."""
    text: str
    segments: list[dict] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    processing_time: float = 0.0


def resample(pcm: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Resample audio via linear interpolation. Good enough for STT."""
    if from_rate == to_rate:
        return pcm
    ratio = to_rate / from_rate
    new_length = int(len(pcm) * ratio)
    return np.interp(
        np.linspace(0, len(pcm) - 1, new_length),
        np.arange(len(pcm)),
        pcm,
    ).astype(np.float32)


class EnergyVAD:
    """Simple RMS energy-based voice activity detector for streaming.

    Accumulates numpy chunks, returns a concatenated array when a
    speech→silence transition is detected. No external dependencies.
    """

    SPEECH_THRESHOLD = 0.015  # RMS above this = speech (~-36 dB)
    SILENCE_DURATION = 0.5    # seconds of silence to end an utterance
    MIN_SPEECH_DURATION = 0.3 # ignore clicks/pops shorter than this

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.state = "silence"  # silence | speech | trailing_silence
        self._chunks: list[np.ndarray] = []
        self._speech_samples = 0
        self._silence_samples = 0

    def process_chunk(self, pcm_float32: np.ndarray) -> np.ndarray | None:
        """Feed an audio chunk. Returns complete utterance array when speech ends.

        Returns None while accumulating, or a float32 numpy array containing
        the full utterance when a speech→silence transition completes.
        """
        rms = float(np.sqrt(np.mean(pcm_float32 ** 2)))
        is_speech = rms > self.SPEECH_THRESHOLD
        n_samples = len(pcm_float32)

        if self.state == "silence":
            if is_speech:
                self.state = "speech"
                self._chunks = [pcm_float32]
                self._speech_samples = n_samples
                self._silence_samples = 0

        elif self.state == "speech":
            self._chunks.append(pcm_float32)
            self._speech_samples += n_samples
            if not is_speech:
                self.state = "trailing_silence"
                self._silence_samples = n_samples

        elif self.state == "trailing_silence":
            self._chunks.append(pcm_float32)
            if is_speech:
                self.state = "speech"
                self._silence_samples = 0
            else:
                self._silence_samples += n_samples
                if self._silence_samples >= self.sample_rate * self.SILENCE_DURATION:
                    speech_duration = self._speech_samples / self.sample_rate
                    if speech_duration >= self.MIN_SPEECH_DURATION:
                        result = np.concatenate(self._chunks)
                        self._reset()
                        return result
                    self._reset()

        return None

    def flush(self) -> np.ndarray | None:
        """Return any accumulated speech on connection close."""
        if self._chunks and self._speech_samples > 0:
            speech_duration = self._speech_samples / self.sample_rate
            if speech_duration >= self.MIN_SPEECH_DURATION:
                result = np.concatenate(self._chunks)
                self._reset()
                return result
        self._reset()
        return None

    def _reset(self):
        self.state = "silence"
        self._chunks = []
        self._speech_samples = 0
        self._silence_samples = 0


class WhisperEngine:
    """Manages the faster-whisper model with lazy loading and thread-safe GPU access."""

    def __init__(self, model_name: str, device: str, compute_type: str):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        """Load model on first use. Called inside the lock."""
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info("Loading Whisper model %s on %s (%s)...",
                     self.model_name, self.device, self.compute_type)
            start = time.time()
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            log.info("Whisper model loaded in %.1fs", time.time() - start)

    def transcribe(self, audio, language: str | None = None) -> TranscriptResult:
        """Transcribe audio from a file path, BinaryIO, or numpy array.

        faster-whisper handles format decoding internally via ffmpeg.
        This is the main entry point for batch transcription.
        """
        with self._lock:
            self._ensure_model()
            start = time.time()

            segments_iter, info = self._model.transcribe(
                audio,
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )

            segments = []
            text_parts = []
            for seg in segments_iter:
                segments.append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                })
                text_parts.append(seg.text.strip())

            elapsed = time.time() - start
            return TranscriptResult(
                text=" ".join(text_parts),
                segments=segments,
                language=info.language,
                language_probability=round(info.language_probability, 3),
                duration=round(info.duration, 2),
                processing_time=round(elapsed, 3),
            )

    def transcribe_bytes(self, audio_bytes: bytes,
                         language: str | None = None) -> TranscriptResult:
        """Transcribe from raw audio bytes. Any format ffmpeg supports."""
        return self.transcribe(io.BytesIO(audio_bytes), language=language)

    def transcribe_numpy(self, pcm_float32: np.ndarray,
                         sample_rate: int = 16000,
                         language: str | None = None) -> TranscriptResult:
        """Transcribe from a float32 numpy array (for streaming use).

        Resamples to 16kHz if needed. Whisper expects 16kHz mono float32.
        """
        if sample_rate != 16000:
            pcm_float32 = resample(pcm_float32, sample_rate, 16000)
        return self.transcribe(pcm_float32, language=language)


# Module-level singleton
_engine: WhisperEngine | None = None


def get_engine() -> WhisperEngine:
    """Get or create the singleton WhisperEngine."""
    global _engine
    if _engine is None:
        _engine = WhisperEngine(
            model_name=getattr(config, 'WHISPER_MODEL', 'large-v3-turbo'),
            device=getattr(config, 'WHISPER_DEVICE', 'cuda'),
            compute_type=getattr(config, 'WHISPER_COMPUTE_TYPE', 'float16'),
        )
    return _engine
