"""Approved action tools (Phase 4, FR-06).

Lifecycle: the orchestrator PROPOSES actions derived from a diagnosis
checklist (deterministic, never from LLM output or client input); a human
APPROVES or REJECTS via the API; only then does the governed tool EXECUTE,
returning a receipt with a rollback note. Every transition is audited with
the approver's identity.

Safety properties:
- Proposals are persisted server-side; the client references them by id only,
  so parameters cannot be tampered with between proposal and approval.
- Approval is idempotent per proposal: re-approving returns the original
  receipt instead of executing twice (double-click / retry safe).
- Each action type has a role allow-list and an explicit rollback note.

First two actions (deliberately low-risk, per the report's Phase 4):
- create_incident_ticket -> runtime/incidents/INC-*.json
- publish_delay_notice   -> runtime/notices.log.jsonl
"""
import json
import re
import secrets
from datetime import datetime, timezone

from .. import connectors
from ..audit import record as audit
from ..config import RUNTIME_DIR
from ..connectors.base import ConnectorError
from ..connectors.mock import INCIDENT_DIR  # noqa: F401  (re-export; tests and callers use it)

ACTION_DIR = RUNTIME_DIR / "actions"
NOTICE_FILE = RUNTIME_DIR / "notices.log.jsonl"

_ID_RE = re.compile(r"^ACT-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")

ACTION_ROLES = {
    "create_incident_ticket": {"dispatcher", "manager"},
    "publish_delay_notice": {"dispatcher", "manager"},
}

ROLLBACK_NOTES = {
    "create_incident_ticket": "Roll back by closing the ticket with status 'created-in-error'; no downstream systems are notified automatically.",
    "publish_delay_notice": "Roll back by publishing a correction notice for the same trip; the original notice remains in the log for audit.",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


_INCIDENT_CONN = None


def _incident_conn():
    global _INCIDENT_CONN
    if _INCIDENT_CONN is None:
        _INCIDENT_CONN = connectors.incident_connector()
    return _INCIDENT_CONN


# ------------------------------------------------------------- proposals
def propose_from_diagnosis(payload: dict, user: dict | None, role: str) -> list[dict]:
    """Derive action proposals from a diagnosis checklist payload."""
    scenario = payload.get("scenario")
    proposals: list[dict] = []

    if scenario == "breakdown":
        affected = _first_match(payload, r"\bV-\d{3}\b") or "affected vehicle"
        proposals.append(_create(
            "create_incident_ticket",
            f"Open a breakdown incident for {affected}",
            {"kind": "breakdown", "subject": affected, "summary": payload.get("recommendation", ""), "sop": payload.get("sop", "")},
            payload.get("sop", ""), user, role,
        ))

    elif scenario == "driver_unavailable":
        subject = _first_match(payload, r"\bD-\d{2}\b") or "affected driver"
        proposals.append(_create(
            "create_incident_ticket",
            f"Open a driver-unavailable incident for {subject}",
            {"kind": "driver_unavailable", "subject": subject, "summary": payload.get("recommendation", ""), "sop": payload.get("sop", "")},
            payload.get("sop", ""), user, role,
        ))

    elif scenario == "delay":
        for step in payload.get("steps", []):
            if "thresholds" not in step["step"].lower():
                continue
            for part in step["result"].split(";"):
                m = re.match(r"\s*(T-\d{4}) \(\+(\d+)m\): (.+)", part)
                if not m:
                    continue
                trip_id, mins, action_text = m.group(1), int(m.group(2)), m.group(3)
                if 10 <= mins < 30:
                    proposals.append(_create(
                        "publish_delay_notice",
                        f"Publish a delay notice for {trip_id} (+{mins}m)",
                        {"trip_id": trip_id, "delay_min": mins, "required_action": action_text},
                        payload.get("sop", ""), user, role,
                    ))
                elif mins >= 30:
                    proposals.append(_create(
                        "create_incident_ticket",
                        f"Open a disruption incident for {trip_id} (+{mins}m)",
                        {"kind": "disruption", "subject": trip_id, "summary": action_text, "sop": payload.get("sop", "")},
                        payload.get("sop", ""), user, role,
                    ))
    return proposals[:3]


def _create(action_type: str, title: str, params: dict, sop_ref: str, user: dict | None, role: str) -> dict:
    ACTION_DIR.mkdir(parents=True, exist_ok=True)
    now = _now()
    prop_id = f"ACT-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    record = {
        "id": prop_id,
        "created_at": now.isoformat(),
        "status": "proposed",
        "type": action_type,
        "title": title,
        "params": params,
        "sop_ref": sop_ref,
        "allowed_roles": sorted(ACTION_ROLES[action_type]),
        "rollback_note": ROLLBACK_NOTES[action_type],
        "proposed_to": user,
        "proposed_role": role,
        "approved_by": None,
        "resolved_at": None,
        "reject_reason": None,
        "receipt": None,
    }
    _write(prop_id, record)
    return record


def get(prop_id: str) -> dict | None:
    if not _ID_RE.match(prop_id):
        return None
    path = ACTION_DIR / f"{prop_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def summary(record: dict, viewer_role: str) -> dict:
    """Slim view for chat responses / cards."""
    return {
        "id": record["id"],
        "status": record["status"],
        "type": record["type"],
        "title": record["title"],
        "params": record["params"],
        "sop_ref": record["sop_ref"],
        "allowed_roles": record["allowed_roles"],
        "can_approve": viewer_role in record["allowed_roles"],
        "receipt": record["receipt"],
        "rollback_note": record["rollback_note"],
    }


# ------------------------------------------------------------- lifecycle
def approve(prop_id: str, approver: dict) -> tuple[dict | None, str]:
    """Returns (record, outcome). Outcomes: executed | already_executed |
    forbidden | rejected_previously | not_found."""
    record = get(prop_id)
    if record is None:
        return None, "not_found"
    if approver["role"] not in record["allowed_roles"]:
        audit({"event": "action_denied", "action_id": prop_id, "type": record["type"],
               "by": approver, "reason": "role not permitted"})
        return record, "forbidden"
    if record["status"] == "executed":
        # Idempotency: same proposal id -> same receipt, no second execution.
        audit({"event": "action_duplicate_approval", "action_id": prop_id,
               "type": record["type"], "by": approver, "receipt": record["receipt"]})
        return record, "already_executed"
    if record["status"] == "rejected":
        return record, "rejected_previously"

    executor = {"create_incident_ticket": _exec_incident, "publish_delay_notice": _exec_notice}[record["type"]]
    try:
        receipt = executor(record)
    except ConnectorError as e:
        # The external system failed: the proposal stays open so it can be
        # retried, and nothing is marked executed (no phantom receipts).
        audit({"event": "action_failed", "action_id": prop_id, "type": record["type"],
               "by": approver, "error": str(e)})
        return record, "failed"
    receipt["rollback_note"] = record["rollback_note"]
    record.update(
        status="executed",
        approved_by=approver,
        resolved_at=_now().isoformat(),
        receipt=receipt,
    )
    _write(prop_id, record)
    audit({"event": "action_approved", "action_id": prop_id, "type": record["type"],
           "params": record["params"], "approved_by": approver, "receipt": receipt})
    return record, "executed"


def reject(prop_id: str, user: dict, reason: str = "") -> tuple[dict | None, str]:
    record = get(prop_id)
    if record is None:
        return None, "not_found"
    if user["role"] not in record["allowed_roles"]:
        return record, "forbidden"
    if record["status"] == "executed":
        return record, "already_executed"
    record.update(status="rejected", approved_by=user, resolved_at=_now().isoformat(),
                  reject_reason=reason or None)
    _write(prop_id, record)
    audit({"event": "action_rejected", "action_id": prop_id, "type": record["type"],
           "by": user, "reason": reason})
    return record, "rejected"


# ------------------------------------------------------------- executors
def _exec_incident(record: dict) -> dict:
    ticket = {"source_action": record["id"], **record["params"]}
    return _incident_conn().create_ticket(ticket)


def _exec_notice(record: dict) -> dict:
    RUNTIME_DIR.mkdir(exist_ok=True)
    now = _now()
    notice_id = f"NTC-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    notice = {
        "notice_id": notice_id,
        "published_at": now.isoformat(),
        "source_action": record["id"],
        **record["params"],
    }
    with NOTICE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(notice, ensure_ascii=False) + "\n")
    return {"notice_id": notice_id, "published_at": notice["published_at"]}


def _first_match(payload: dict, pattern: str) -> str | None:
    m = re.search(pattern, json.dumps(payload))
    return m.group(0) if m else None


def _write(prop_id: str, record: dict) -> None:
    (ACTION_DIR / f"{prop_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
