"""Fleet Ops Copilot - read-only MVP API.

Run:  uvicorn app.main:app --reload
Then open http://127.0.0.1:8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .agent.llm import extract_video_frames
from .agent.orchestrator import Orchestrator
from .config import MAX_UPLOAD_BYTES, STATIC_DIR
from .models import ChatResponse
from .rag.ingest import build_index

orchestrator: Orchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
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
        "llm": orchestrator.llm.model if orchestrator else None,
    }


@app.post("/api/chat")
async def chat(
    message: str = Form(...),
    role: str = Form("dispatcher"),
    files: list[UploadFile] = File(default=[]),
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

    return orchestrator.handle(message, role, images, notes)
