"""Central configuration. Everything comes from environment variables (.env supported)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SOP_DIR = DATA_DIR / "sops"
STATIC_DIR = PROJECT_ROOT / "static"
RUNTIME_DIR = PROJECT_ROOT / "runtime"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

# Evidence older than this is flagged stale (FR-05). Mock data is loaded fresh,
# so in the demo this only trips if the process runs long.
FRESHNESS_LIMIT_MINUTES = 10

# Roles and the tools each may call (FR-01, FR-03). Read-only MVP: no action tools.
ROLE_TOOLS: dict[str, set[str]] = {
    "dispatcher": {"schedule_lookup", "fleet_status", "sop_search"},
    "planner": {"schedule_lookup", "fleet_status", "sop_search"},
    "driver": {"schedule_lookup", "sop_search"},
    "manager": {"schedule_lookup", "fleet_status", "sop_search"},
}
DEFAULT_ROLE = "dispatcher"

MAX_VIDEO_FRAMES = 3
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
