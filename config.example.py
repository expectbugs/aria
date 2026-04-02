"""ARIA configuration template.

Copy this to config.py and edit for your host. config.py is gitignored —
each machine keeps its own. The repo + config.py is the full deployment.

Required: HOST_NAME, TAILSCALE_IP, CLAUDE_CLI, AUTH_TOKEN, DATABASE_URL
Everything else has sensible defaults.
"""

from pathlib import Path

# --- Identity ---
HOST_NAME = "hostname"       # machine name (beardos, slappy, etc.)
IS_PRIMARY = True             # False for failover nodes

# --- Owner Profile (personal info — stays in gitignored config.py) ---
OWNER_NAME = "Your Name"
OWNER_TIMEZONE = "US Central"
OWNER_VEHICLE = "Vehicle make/model"
OWNER_WORK_SCHEDULE = "your work hours"
OWNER_WORK_STATUS = "employed/leave/etc."
OWNER_EMPLOYER = "Employer name"
OWNER_HEALTH_NOTES = "Any health context ARIA should be aware of"
OWNER_LIVING_SITUATION = "Where you currently live"
OWNER_BIRTH_DATE = "1984-01-01"  # YYYY-MM-DD, used for age-based health calculations
DIET_START_DATE = ""             # YYYY-MM-DD, set when starting a structured diet (leave empty if N/A)

# --- Paths ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# --- Network ---
TAILSCALE_IP = "100.x.x.x"   # this machine's Tailscale IP
PORT = 8450

# --- Claude Code CLI (Action ARIA + Amnesia pool) ---
CLAUDE_CLI = "/usr/bin/claude"  # or /home/user/.local/bin/claude
CLAUDE_TIMEOUT = 600            # seconds per CLI invocation (10 min — image gen can take a while)

# --- Anthropic API (ARIA Primary) ---
ANTHROPIC_API_KEY_FILE = DATA_DIR / "api_key.txt"  # or set ANTHROPIC_API_KEY directly
ANTHROPIC_API_KEY = ""                              # fallback if file doesn't exist
ARIA_MODEL = "claude-opus-4-6"                      # model for primary ARIA
ARIA_MAX_TOKENS = 80000                             # max response tokens (must exceed thinking budget)
ARIA_HISTORY_TURNS = 10                             # rolling conversation history window
ARIA_THINKING_BUDGET = 64000                        # extended thinking token budget (0 to disable)
ARIA_ALWAYS_THINK = False                           # True to force thinking on ALL queries (ignore bypass)

# --- Auth ---
AUTH_TOKEN = "your-token-here"  # Tasker sends this as Bearer token

# --- Database ---
DATABASE_URL = "postgresql://aria@/aria"  # local Unix socket, trust auth

# --- Session Pool (ARIA Primary — CLI-based) ---
SESSION_RECYCLE_AFTER = 150         # requests before recycling a session
SESSION_DEEP_EFFORT = "max"         # effort level for deep (complex) queries
SESSION_FAST_EFFORT = "auto"        # effort level for fast (simple) queries
SESSION_WATCHDOG_INTERVAL = 30      # seconds between health checks (0 to disable)
# Both sessions use Opus — never downgrade model, only effort level

# --- Amnesia Pool (stateless Claude Code workers) ---
AMNESIA_POOL_SIZE = 1               # number of warm instances (reduced from 3 — primary sessions absorb most work)
AMNESIA_TASK_TIMEOUT = 120          # seconds per agentic task
AMNESIA_SHELL_TIMEOUT = 60          # seconds per shell command

# --- Redis (task queue, swarm coordination) ---
REDIS_URL = "redis://127.0.0.1:6379/0"    # Redis database URL
REDIS_KEY_PREFIX = "aria:"                  # namespace for all ARIA keys

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

# --- Known Places (for location-based reminders) ---
# Values are matched as substrings against reverse-geocoded addresses (case-insensitive)
KNOWN_PLACES = {
    "home": "your street, your city",
    "work": "workplace street, city",
}

# --- Nudges ---
NUDGE_INTERVAL_MIN = 30           # minutes between nudge evaluations
QUIET_HOURS_START = 0              # midnight (0-23)
QUIET_HOURS_END = 7                # 7am (0-23)
MAX_NUDGES_PER_DAY = 15           # unified cap: max deliveries per 24h (nudges + findings)
MAX_NUDGES_PER_HOUR = 2           # global cap: max deliveries per 1h
STALE_REMINDER_DAYS = 3           # auto-expire reminders overdue by this many days

# --- Twilio (SMS/MMS/Voice) ---
TWILIO_ACCOUNT_SID = "your-twilio-account-sid"       # Basic Auth user for REST API
TWILIO_AUTH_TOKEN = "your-twilio-auth-token"          # Basic Auth password for REST API
TWILIO_API_SID = "your-twilio-api-sid"                # Revocable API key SID (alt auth user)
TWILIO_API_KEY = "your-twilio-api-key"                # Revocable API key secret (alt auth password)
TWILIO_MESSAGING_SID = "your-messaging-service-sid"   # Messaging Service for SMS/MMS
TWILIO_PHONE_NUMBER = "+1XXXXXXXXXX"                  # ARIA's phone number
TWILIO_WEBHOOK_URL = "https://host.tail.ts.net/webhook/sms"  # Public funnel URL for signature validation
OWNER_PHONE_NUMBER = "+1XXXXXXXXXX"                   # Your personal phone number

# --- SMS Redirect (temporary — remove when A2P 10DLC is approved) ---
SMS_REDIRECT_TO_IMAGE = True   # True = render SMS as image + push to phone (SMS is dead)
                                # False = normal Twilio SMS delivery

# --- Fitbit (Web API — Personal app, register at dev.fitbit.com) ---
FITBIT_CLIENT_ID = "your-client-id"               # OAuth 2.0 Client ID
FITBIT_CLIENT_SECRET = "your-client-secret"         # Client Secret
FITBIT_REDIRECT_URI = "https://localhost:8000/callback"
FITBIT_TOKEN_FILE = DATA_DIR / "fitbit_tokens.json"  # auto-managed OAuth tokens
FITBIT_WEBHOOK_VERIFY = "aria-fitbit-verify"          # subscriber verification code
FITBIT_SCOPES = [                                     # data types to request access to
    "activity", "heartrate", "sleep", "oxygen_saturation",
    "respiratory_rate", "temperature", "weight", "profile",
]

# --- Google (OAuth2 — Calendar + Gmail, register at console.cloud.google.com) ---
GOOGLE_CLIENT_ID = "your-client-id.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "your-client-secret"
GOOGLE_REDIRECT_URI = "http://127.0.0.1:8080/callback"
GOOGLE_TOKEN_FILE = DATA_DIR / "google_tokens.json"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",    # read-write events
    "https://www.googleapis.com/auth/gmail.modify",          # read, send, trash, labels
]

# --- Gmail Intelligence ---
GMAIL_POLL_INTERVAL_MIN = 3            # minutes between Gmail syncs
GMAIL_RULES_FILE = DATA_DIR / "gmail_rules.yaml"
GMAIL_ATTACHMENTS_DIR = DATA_DIR / "email_attachments"

# --- Whisper STT (faster-whisper + CTranslate2) ---
WHISPER_MODEL = "large-v3-turbo"     # "large-v3" for max accuracy, "large-v3-turbo" for speed
WHISPER_DEVICE = "cuda"               # "cuda" for GPU, "cpu" for machines without GPU
WHISPER_COMPUTE_TYPE = "float16"      # "float16" for GPU, "int8" for CPU
ENABLE_WHISPER = False                # True only on GPU-equipped hosts

# --- Training Data Collection ---
COLLECT_TOOL_TRACES = True          # log query.py invocations to tool_traces table
COLLECT_ENTITY_MENTIONS = True      # extract entities from responses

# --- Haiku (system-internal composition — nudges, task completion summaries) ---
HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_MAX_TOKENS = 2048

# --- Tier 3 Email Classification (smarter model for uncertain emails) ---
TIER3_EMAIL_MODEL = "claude-sonnet-4-6"

# --- Junk Email Auto-Archive (remove Tier 1 junk from Gmail INBOX) ---
JUNK_AUTO_ARCHIVE = True

# --- Response Verification (Phase 3) ---
VERIFICATION_ENABLED = True             # master switch for claim verification
VERIFICATION_MAX_RETRIES = 2            # max retries on action claim violations

# --- Context Window Management (Phase 3) ---
SESSION_MAX_CONTEXT_BYTES = 500000      # ~125K tokens, recycle threshold (~62% of 200K)

# --- Delivery Intelligence (Phase 2) ---
DELIVERY_ENGINE_ENABLED = True          # master switch for delivery engine
DELIVERY_LOCATION_STALE_MINUTES = 30    # treat location as "unknown" after this
DELIVERY_LOG_ENABLED = True             # log all delivery decisions
DEFERRED_DELIVERY_EXPIRES_HOURS = 12    # expire undelivered deferred items

# --- Domain Monitors (Phase 1) ---
MONITORS_ENABLED = True                   # master switch for all monitors
MONITOR_FINDING_TTL_HOURS = 24            # auto-expire undelivered findings after this
MONITOR_DELIVERY_MIN_INTERVAL_MIN = 30    # min minutes between unified deliveries (nudges + findings)

# --- Ambient Audio Pipeline (Phase 6 — DJI Mic 3) ---
AMBIENT_ENABLED = False                               # master switch for ambient pipeline
AMBIENT_AUDIO_DIR = DATA_DIR / "ambient"              # storage for raw audio chunks
AMBIENT_AUDIO_RETENTION_HOURS = 72                    # audio deleted after this; transcripts permanent
AMBIENT_WHISPER_MODEL = "base"                        # first-pass model for slappy CPU (beardos uses main WHISPER_MODEL)
AMBIENT_VAD_SILENCE_S = 2.0                           # seconds of silence to end an utterance
AMBIENT_VAD_MIN_SPEECH_S = 1.0                        # min speech duration to keep (filters noise)
AMBIENT_EXTRACTION_INTERVAL_MIN = 5                   # minutes between Haiku extraction passes
AMBIENT_QUALITY_INTERVAL_MIN = 10                     # minutes between WhisperX quality passes
BEARDOS_URL = "http://100.107.139.121:8450"           # target URL for slappy relay (set per machine)
AMBIENT_CAPTURE_ENABLED = False                       # True on slappy when DJI Mic 3 is being used
AMBIENT_CAPTURE_DEVICE = None                         # PipeWire/PulseAudio source name (None = auto-detect)

# --- Qdrant (vector search — Phase 6+) ---
QDRANT_URL = "http://localhost:6333"
QDRANT_COLLECTION = "aria_memory"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"                  # sentence-transformers model name

# --- Neo4j (knowledge graph — Phase 6+) ---
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = ""                                    # set in config.py (gitignored)

# --- Hardware capabilities ---
# Set based on what this machine can do. Daemon checks these at runtime.
ENABLE_GPU = False              # True if NVIDIA GPU available (for Whisper, LoRA, etc.)
ENABLE_IMAGE_GEN = False        # True if image generation models are installed
