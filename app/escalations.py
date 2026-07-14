"""Escalation handoff queue (Phase 3, FR-08).

When the agent cannot answer confidently -- low confidence score, conflicting
sources, or no permitted evidence -- the full turn is packaged as a JSON
record a human operator can pick up: question, conversation history, every
piece of evidence with timestamps, the confidence score, the notes explaining
what failed, and the agent's draft answer.

File-backed (one JSON per escalation in runtime/escalations/) to match the
audit log's approach; swap for a real queue/DB in Phase 5.
"""
import json
import re
import secrets
from datetime import datetime, timezone

from .config import RUNTIME_DIR

ESCALATION_DIR = RUNTIME_DIR / "escalations"
_ID_RE = re.compile(r"^ESC-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")

# Fields returned by list_all(); the full package (evidence, history, answer)
# stays behind get() so the queue view stays light.
_SUMMARY_KEYS = (
    "id", "created_at", "status", "resolved_at", "resolved_by", "resolution_note",
    "question", "role", "user", "confidence",
)


def create(package: dict) -> str:
    ESCALATION_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    esc_id = f"ESC-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    record = {
        "id": esc_id,
        "created_at": now.isoformat(),
        "status": "open",
        "resolved_at": None,
        "resolved_by": None,
        "resolution_note": None,
        **package,
    }
    _write(esc_id, record)
    return esc_id


def list_all() -> list[dict]:
    if not ESCALATION_DIR.exists():
        return []
    records = []
    for path in ESCALATION_DIR.glob("ESC-*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        records.append({k: rec.get(k) for k in _SUMMARY_KEYS})
    records.sort(key=lambda r: r["created_at"] or "", reverse=True)
    return records


def get(esc_id: str) -> dict | None:
    if not _ID_RE.match(esc_id):
        return None
    path = ESCALATION_DIR / f"{esc_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(esc_id: str, resolver: str, note: str = "") -> dict | None:
    record = get(esc_id)
    if record is None:
        return None
    record.update(
        status="resolved",
        resolved_at=datetime.now(timezone.utc).isoformat(),
        resolved_by=resolver,
        resolution_note=note or None,
    )
    _write(esc_id, record)
    return record


def _write(esc_id: str, record: dict) -> None:
    path = ESCALATION_DIR / f"{esc_id}.json"
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
