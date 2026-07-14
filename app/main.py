"""Fleet Ops Copilot - read-only MVP API.

Run:  uvicorn app.main:app --reload
Then open http://127.0.0.1:8000
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from . import escalations
from .tools import actions
from .agent.llm import extract_video_frames
from .agent.orchestrator import Orchestrator
from .auth import COOKIE_NAME, SESSION_TTL_HOURS, User, create_session_token, get_current_user, verify_password
from .config import MAX_UPLOAD_BYTES, STATIC_DIR
from .models import ChatResponse
from .rag.ingest import build_index
from .tools.datastore import get_store

orchestrator: Orchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    store = get_store()  # prepare phase: catch up the daily fleet/schedule simulation
    app.state.sim_date = store.sim_date
    index = build_index()  # prepare phase: chunk + embed SOPs
    orchestrator = Orchestrator(index)
    app.state.sop_mode = index.mode
    yield


app = FastAPI(title="Fleet Ops Copilot", lifespan=lifespan)


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "sop_search_mode": app.state.sop_mode,
        "intent_mode": orchestrator.classifier.mode if orchestrator else None,
        "llm": orchestrator.llm.model if orchestrator else None,
        "data_date": app.state.sim_date,
        "connectors": {
            **get_store().connector_status(),
            "incident": {"mode": actions._incident_conn().mode, "error": None},
        },
    }


@app.post("/api/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)) -> dict:
    user = verify_password(username, password)
    if user is None:
        raise HTTPException(401, "Invalid username or password")
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user),
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_HOURS * 3600,
    )
    return {"username": user.username, "role": user.role, "name": user.name}


@app.post("/api/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"status": "ok"}


@app.get("/api/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {"username": user.username, "role": user.role, "name": user.name}


@app.get("/api/overview")
async def overview(user: User = Depends(get_current_user)) -> dict:
    """Snapshot for the dashboard panel. Same least-privilege rule as the
    fleet tool: drivers don't see other drivers' roster details."""
    store = get_store()
    return {
        "observed_at": store.loaded_at.isoformat(),
        "trips": store.trips,
        "vehicles": store.vehicles,
        "drivers": [] if user.role == "driver" else store.drivers,
    }


ESCALATION_VIEW_ROLES = {"dispatcher", "planner", "manager"}


@app.get("/api/escalations")
async def list_escalations(user: User = Depends(get_current_user)) -> list[dict]:
    """Operator queue (summaries). Drivers have no operator duties."""
    if user.role not in ESCALATION_VIEW_ROLES:
        raise HTTPException(403, "Role may not view escalations")
    return escalations.list_all()


@app.get("/api/escalations/{esc_id}")
async def get_escalation(esc_id: str, user: User = Depends(get_current_user)) -> dict:
    """Full handoff package: history, evidence, notes, draft answer."""
    if user.role not in ESCALATION_VIEW_ROLES:
        raise HTTPException(403, "Role may not view escalations")
    record = escalations.get(esc_id)
    if record is None:
        raise HTTPException(404, "Escalation not found")
    return record


@app.post("/api/escalations/{esc_id}/resolve")
async def resolve_escalation(
    esc_id: str,
    note: str = Form(""),
    user: User = Depends(get_current_user),
) -> dict:
    if user.role != "manager":
        raise HTTPException(403, "Only managers can resolve escalations")
    record = escalations.resolve(esc_id, resolver=user.username, note=note)
    if record is None:
        raise HTTPException(404, "Escalation not found")
    return record


@app.post("/api/actions/{prop_id}/approve")
async def approve_action(prop_id: str, user: User = Depends(get_current_user)) -> dict:
    """Approval gate (FR-06): executes the server-stored proposal exactly once;
    re-approval returns the original receipt (idempotent)."""
    approver = {"username": user.username, "name": user.name, "role": user.role}
    record, outcome = actions.approve(prop_id, approver)
    if outcome == "not_found":
        raise HTTPException(404, "Action proposal not found")
    if outcome == "forbidden":
        raise HTTPException(403, f"Role '{user.role}' may not approve this action")
    if outcome == "rejected_previously":
        raise HTTPException(409, "This proposal was already rejected")
    if outcome == "failed":
        raise HTTPException(502, "The target system rejected or did not answer; the proposal remains open for retry")
    return {"outcome": outcome, "action": actions.summary(record, user.role)}


@app.post("/api/actions/{prop_id}/reject")
async def reject_action(
    prop_id: str,
    reason: str = Form(""),
    user: User = Depends(get_current_user),
) -> dict:
    who = {"username": user.username, "name": user.name, "role": user.role}
    record, outcome = actions.reject(prop_id, who, reason)
    if outcome == "not_found":
        raise HTTPException(404, "Action proposal not found")
    if outcome == "forbidden":
        raise HTTPException(403, f"Role '{user.role}' may not reject this action")
    if outcome == "already_executed":
        raise HTTPException(409, "This proposal was already executed")
    return {"outcome": outcome, "action": actions.summary(record, user.role)}


MAX_HISTORY_TURNS = 8


def _parse_history(raw: str) -> list[dict]:
    """Validate client-sent history: only user/assistant roles, text only,
    capped length so the prompt cannot be flooded."""
    import json

    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    history = []
    for it in items if isinstance(items, list) else []:
        if (
            isinstance(it, dict)
            and it.get("role") in ("user", "assistant")
            and isinstance(it.get("content"), str)
        ):
            history.append({"role": it["role"], "content": it["content"][:4000]})
    return history[-MAX_HISTORY_TURNS:]


@app.post("/api/chat")
async def chat(
    message: str = Form(...),
    history: str = Form("[]"),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
) -> ChatResponse:
    if not message.strip():
        raise HTTPException(400, "Empty message")

    images: list[tuple[str, bytes]] = []
    notes: list[str] = []
    for f in files:
        raw = await f.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            notes.append(f"Attachment '{f.filename}' exceeds the size limit and was skipped.")
            continue
        ctype = f.content_type or ""
        if ctype.startswith("image/"):
            images.append((ctype, raw))
        elif ctype.startswith("video/"):
            suffix = "." + (f.filename or "clip.mp4").rsplit(".", 1)[-1]
            frames, note = extract_video_frames(raw, suffix)
            images.extend(frames)
            if note:
                notes.append(note)
        else:
            notes.append(f"Attachment '{f.filename}' has unsupported type '{ctype}' and was skipped.")

    return orchestrator.handle(
        message,
        user.role,
        images,
        notes,
        _parse_history(history),
        user={"username": user.username, "name": user.name},
    )
