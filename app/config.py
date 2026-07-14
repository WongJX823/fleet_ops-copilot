"""Central configuration. Everything comes from environment variables (.env supported)."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SOP_DIR = DATA_DIR / "sops"
USERS_FILE = DATA_DIR / "users.json"
STATIC_DIR = PROJECT_ROOT / "static"
RUNTIME_DIR = PROJECT_ROOT / "runtime"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

# Session cookie signing key. If unset, a random key is generated per process
# start, which is fine for a local demo but means logins don't survive a
# restart — set SESSION_SECRET in .env for persistent sessions.
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
SESSION_TTL_HOURS = 12

# Evidence older than this is flagged stale (FR-05). Mock data is loaded fresh,
# so in the demo this only trips if the process runs long.
FRESHNESS_LIMIT_MINUTES = 10

# Optional demo/debug knob: simulate a source falling behind without waiting on
# wall-clock time. Minutes subtracted from that source's observed_at (FR-05).
# Both default to 0 (no lag, current behavior) -- set e.g. SCHEDULE_LAG_MINUTES=15
# in .env to see stale evidence and an escalation note.
SCHEDULE_LAG_MINUTES = int(os.getenv("SCHEDULE_LAG_MINUTES", "0"))
FLEET_LAG_MINUTES = int(os.getenv("FLEET_LAG_MINUTES", "0"))

# Answers scoring below this (0.0-1.0, see app/agent/confidence.py) escalate
# to a human rather than being presented as a confident answer (FR-08).
CONFIDENCE_ESCALATION_THRESHOLD = float(os.getenv("CONFIDENCE_ESCALATION_THRESHOLD", "0.5"))

# Roles and the READ tools each may call (FR-01, FR-03). Write actions are separate:
# they go through server-side proposals + the approval gate (app/tools/actions.py).
ROLE_TOOLS: dict[str, set[str]] = {
    "dispatcher": {"schedule_lookup", "fleet_status", "sop_search"},
    "planner": {"schedule_lookup", "fleet_status", "sop_search"},
    "driver": {"schedule_lookup", "sop_search"},
    "manager": {"schedule_lookup", "fleet_status", "sop_search"},
}
DEFAULT_ROLE = "dispatcher"

MAX_VIDEO_FRAMES = 3
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
