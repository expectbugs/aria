"""ARIA TTS (text-to-speech) via Kokoro."""

import asyncio
import re

import config


# Cache the Kokoro TTS model so it's not reloaded on every request
_kokoro = None


def is_loaded() -> bool:
    """Check if the Kokoro TTS model is loaded."""
    return _kokoro is not None


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        # Fix kokoro-onnx v0.5.0 off-by-one: voice embedding has 510 rows
        # (indices 0-509) but MAX_PHONEME_LENGTH=510 allows voice[510].
        # Reducing to 509 keeps all indexing within bounds.
        import kokoro_onnx
        kokoro_onnx.MAX_PHONEME_LENGTH = 509
        _kokoro = kokoro_onnx.Kokoro(
            str(config.KOKORO_MODEL), str(config.KOKORO_VOICES)
        )
    return _kokoro


def _prepare_for_speech(text: str) -> str:
    """Strip markdown formatting for natural-sounding TTS output.

    Claude sometimes uses markdown despite the system prompt requesting plain
    text. Passing it through to Kokoro wastes phonemes on literal asterisks
    and sounds unnatural. This strips it at the TTS boundary so all callers
    benefit regardless of what Claude produces.
    """
    # Bold **text** → text (must precede italic)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Italic *text* → text
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Code blocks ```...``` → removed
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    # Inline code `text` → text
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Headings ## text → text
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Bullet/list markers
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Links [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Paragraph breaks → sentence pauses for natural prosody
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', ' ', text)
    # Normalize whitespace
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def _tts_sync(text: str) -> bytes:
    """Generate TTS audio synchronously. Called from thread pool."""
    import io
    import soundfile as sf

    kokoro = _get_kokoro()
    text = _prepare_for_speech(text)
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
