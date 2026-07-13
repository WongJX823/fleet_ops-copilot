"""Append-only JSONL audit trail (FR-07). One line per conversation turn."""
import json
from datetime import datetime, timezone

from .config import RUNTIME_DIR

AUDIT_FILE = RUNTIME_DIR / "audit.log.jsonl"


def record(event: dict) -> None:
    RUNTIME_DIR.mkdir(exist_ok=True)
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
