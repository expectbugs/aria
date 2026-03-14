"""ARIA configuration template.

Copy this to config.py and edit for your host. config.py is gitignored —
each machine keeps its own. The repo + config.py is the full deployment.

Required: HOST_NAME, TAILSCALE_IP, CLAUDE_CLI, AUTH_TOKEN
Everything else has sensible defaults.
"""

from pathlib import Path

# --- Identity ---
HOST_NAME = "hostname"       # machine name (beardos, slappy, etc.)
IS_PRIMARY = True             # False for failover nodes

# --- Paths ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# --- Network ---
TAILSCALE_IP = "100.x.x.x"   # this machine's Tailscale IP
PORT = 8450

# --- Claude ---
CLAUDE_CLI = "/usr/bin/claude"  # or /home/user/.local/bin/claude
CLAUDE_TIMEOUT = 120            # seconds per CLI invocation

# --- Auth ---
AUTH_TOKEN = "your-token-here"  # Tasker sends this as Bearer token

# --- Logging ---
REQUEST_LOG = LOGS_DIR / "requests.jsonl"

# --- Calendar & Reminders ---
CALENDAR_DB = DATA_DIR / "calendar.json"
REMINDERS_DB = DATA_DIR / "reminders.json"

# --- Weather (NWS API, free, no key needed) ---
WEATHER_LAT = 42.58
WEATHER_LON = -88.43
WEATHER_USER_AGENT = "ARIA/1.0 (personal assistant)"

# --- TTS (Kokoro) ---
KOKORO_MODEL = BASE_DIR / "tts_models" / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = BASE_DIR / "tts_models" / "kokoro" / "voices-v1.0.bin"
KOKORO_VOICE = "af_heart"

# --- News RSS feeds ---
NEWS_FEEDS = {
    "tech": "https://feeds.arstechnica.com/arstechnica/index",
    "wisconsin": "https://www.jsonline.com/rss/",
    "manufacturing": "https://www.industryweek.com/rss.xml",
}

# --- Hardware capabilities ---
# Set based on what this machine can do. Daemon checks these at runtime.
ENABLE_GPU = False              # True if NVIDIA GPU available (for Whisper, LoRA, etc.)
ENABLE_IMAGE_GEN = False        # True if image generation models are installed
