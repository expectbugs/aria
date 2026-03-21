"""ARIA TTS (text-to-speech) via Kokoro."""

import asyncio

import config


# Cache the Kokoro TTS model so it's not reloaded on every request
_kokoro = None


def is_loaded() -> bool:
    """Check if the Kokoro TTS model is loaded."""
    return _kokoro is not None


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
    return _kokoro


def _tts_sync(text: str) -> bytes:
    """Generate TTS audio synchronously. Called from thread pool."""
    import io
    import soundfile as sf

    kokoro = _get_kokoro()
    samples, sample_rate = kokoro.create(
        text, voice=config.KOKORO_VOICE, speed=1.0, lang="en-us"
    )
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    buf.seek(0)
    return buf.read()


async def _generate_tts(text: str) -> bytes:
    """Generate TTS audio without blocking the event loop."""
    return await asyncio.to_thread(_tts_sync, text)
