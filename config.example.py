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
CLAUDE_TIMEOUT = 600            # seconds per CLI invocation (10 min — image gen can take a while)

# --- Auth ---
AUTH_TOKEN = "your-token-here"  # Tasker sends this as Bearer token

# --- Logging ---
REQUEST_LOG = LOGS_DIR / "requests.jsonl"

# --- Calendar & Reminders ---
CALENDAR_DB = DATA_DIR / "calendar.json"
REMINDERS_DB = DATA_DIR / "reminders.json"

# --- Specialist Logs ---
VEHICLE_DB = DATA_DIR / "vehicle.json"
HEALTH_DB = DATA_DIR / "health.json"
LEGAL_DB = DATA_DIR / "legal.json"

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

# --- Phone (Tasker HTTP Server for image push) ---
PHONE_IP = "100.x.x.x"         # phone's Tailscale IP
PHONE_PORT = 8451               # Tasker HTTP Server port
PHONE_IMAGE_DIR = "ARIA"        # directory on phone for pushed images (relative to internal storage)

# --- Known Places (for location-based reminders) ---
# Values are matched as substrings against reverse-geocoded addresses (case-insensitive)
KNOWN_PLACES = {
    "home": "your street, your city",
    "work": "workplace street, city",
}

# --- Timers & Nudges ---
TIMER_DB = DATA_DIR / "timers.json"
NUDGE_INTERVAL_MIN = 30           # minutes between nudge evaluations
QUIET_HOURS_START = 0              # midnight (0-23)
QUIET_HOURS_END = 7                # 7am (0-23)
TICK_STATE_FILE = DATA_DIR / "tick_state.json"
NUDGE_COOLDOWNS_FILE = DATA_DIR / "nudge_cooldowns.json"

# --- Twilio (SMS/MMS/Voice) ---
TWILIO_ACCOUNT_SID = "your-twilio-account-sid"       # Basic Auth user for REST API
TWILIO_AUTH_TOKEN = "your-twilio-auth-token"          # Basic Auth password for REST API
TWILIO_API_SID = "your-twilio-api-sid"                # Revocable API key SID (alt auth user)
TWILIO_API_KEY = "your-twilio-api-key"                # Revocable API key secret (alt auth password)
TWILIO_MESSAGING_SID = "your-messaging-service-sid"   # Messaging Service for SMS/MMS
TWILIO_PHONE_NUMBER = "+1XXXXXXXXXX"                  # ARIA's phone number
TWILIO_WEBHOOK_URL = "https://host.tail.ts.net/webhook/sms"  # Public funnel URL for signature validation
OWNER_PHONE_NUMBER = "+1XXXXXXXXXX"                   # Your personal phone number

# --- Hardware capabilities ---
# Set based on what this machine can do. Daemon checks these at runtime.
ENABLE_GPU = False              # True if NVIDIA GPU available (for Whisper, LoRA, etc.)
ENABLE_IMAGE_GEN = False        # True if image generation models are installed
