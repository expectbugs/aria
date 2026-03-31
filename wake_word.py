"""Wake word detection in transcript text.

Detects "ARIA" (or "hey ARIA") in transcribed ambient audio and extracts
the command text that follows. Uses text-level regex — no audio-level
keyword spotting.
"""

import re

# Patterns to detect the wake word in transcribed text.
# Order matters: longer/more specific patterns first.
_WAKE_PATTERNS = [
    # "hey aria, ..." or "hey aria ..."
    re.compile(r'\bhey\s+aria[,.:!?\s]+(.+)', re.IGNORECASE | re.DOTALL),
    # "aria, ..." or "aria ..." (wake word at start of utterance)
    re.compile(r'^aria[,.:!?\s]+(.+)', re.IGNORECASE | re.DOTALL),
    # "aria, ..." mid-sentence (requires comma/colon after)
    re.compile(r'\baria[,.:\s]+(.+)', re.IGNORECASE | re.DOTALL),
]

# False positives to reject — names/words that contain "aria"
_FALSE_POSITIVES = re.compile(
    r'\b(?:maria|malaria|aquaria|aviaria|bulgaria|aria[hn])\b',
    re.IGNORECASE,
)


def detect(text: str) -> tuple[bool, str]:
    """Check if text contains the ARIA wake word.

    Returns (detected, command_text).
    command_text is the text AFTER the wake word, stripped.
    Returns (False, "") if no wake word found.
    """
    text = text.strip()
    if not text:
        return False, ""

    # Quick check: does "aria" even appear?
    if "aria" not in text.lower():
        return False, ""

    # Reject false positives
    if _FALSE_POSITIVES.search(text):
        # Check if there's ALSO a standalone "aria" besides the false positive
        cleaned = _FALSE_POSITIVES.sub("", text)
        if "aria" not in cleaned.lower():
            return False, ""

    for pattern in _WAKE_PATTERNS:
        match = pattern.search(text)
        if match:
            command = match.group(1).strip()
            if command:
                return True, command

    return False, ""
