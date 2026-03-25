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


def _ensure_tts_splits(text: str, max_chars: int = 200) -> str:
    """Insert commas in long runs without Kokoro-friendly punctuation.

    Kokoro's phoneme batcher splits only on [.,!?;]. Text stretches without
    these marks can phonemize to >509 characters and get silently truncated
    by _create_audio(). This inserts a comma at the nearest word boundary
    when any run exceeds max_chars.

    max_chars=200 is safe even for number-heavy text (worst-case ~2.5x
    phoneme expansion: 200 chars → ~500 phonemes, under the 509 limit).
    """
    pattern = re.compile(r'[^.,!?;]{' + str(max_chars) + r',}')
    while True:
        m = pattern.search(text)
        if not m:
            break
        run = m.group()
        mid = len(run) // 2
        # Find nearest space to the midpoint for a natural word-boundary split
        best = None
        for offset in range(mid + 1):
            if mid + offset < len(run) and run[mid + offset] == ' ':
                best = mid + offset
                break
            if mid - offset >= 0 and run[mid - offset] == ' ':
                best = mid - offset
                break
        if best is None:
            break  # No space in the entire run — can't split
        insert_pos = m.start() + best
        text = text[:insert_pos] + ',' + text[insert_pos:]
    return text


def _prepare_for_speech(text: str) -> str:
    """Strip markdown formatting for natural-sounding TTS output.

    Claude sometimes uses markdown despite the system prompt requesting plain
    text. Passing it through to Kokoro wastes phonemes on literal asterisks
    and sounds unnatural. This strips it at the TTS boundary so all callers
    benefit regardless of what Claude produces.

    Also ensures the text has enough punctuation split points for Kokoro's
    phoneme batcher, which only splits on [.,!?;]. Without this, data-heavy
    responses (nutrition summaries, daily totals) can produce phoneme batches
    exceeding the 509-character limit, causing silent truncation.
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
    # Parentheses — Kokoro vocalizes these as audible artifacts (~250ms burst)
    text = re.sub(r'[()]', '', text)
    # Paragraph breaks → sentence pauses for natural prosody
    text = re.sub(r'\n{2,}', '. ', text)
    # Single newlines → commas for Kokoro split points (data listings).
    # Skip if line already ends with sentence punctuation or colon.
    text = re.sub(r'(?<![.,!?;:])\n', ', ', text)
    text = re.sub(r'\n', ' ', text)
    # Safety net: break up long runs without Kokoro-friendly punctuation
    text = _ensure_tts_splits(text)
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
