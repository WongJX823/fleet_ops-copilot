# fleet_ops-copilot

Fleet Ops Copilot — an AI agent for live transportation schedules, operational problem
solving, and guided action. Concept report and diagrams live in [`deliverables/`](deliverables/);
this repo also contains the **Phase 2 read-only MVP** described in the report.

## What the MVP does

- Web chat (text + image/video attachments) at `http://127.0.0.1:8000`
- **Two retrieval paths** (see the "RAG + Tool Pipeline" page in the design file):
  - *Live questions* (schedules, delays, vehicles, drivers) → governed read tools over
    mock JSON services in [`data/`](data/)
  - *Policy questions* → RAG: SOP markdown files are chunked and embedded at startup,
    then similarity-searched per question
- Role scoping (dispatcher / planner / driver / manager), evidence chips with source +
  timestamp + freshness, and an append-only audit log in `runtime/audit.log.jsonl`
- Read-only: the agent recommends actions and cites SOPs but cannot execute changes

## Quickstart

```bash
pip install -r requirements.txt
copy .env.example .env   # then set OPENAI_API_KEY
uvicorn app.main:app --reload
```

Without an API key the app still runs end-to-end using a stub LLM and keyword SOP
search — useful for tests and offline demos. Video attachments need
`opencv-python-headless` (optional) for frame extraction.

Run tests (offline, no key needed):

```bash
pytest
```

## Structure

| Path | Report section |
|---|---|
| `app/main.py` | Channels & experience (Section 7) |
| `app/agent/` | Agent orchestration: intent → tools → validate → pack → LLM (Section 8) |
| `app/tools/` | Governed operational tools over mock live systems (Section 9) |
| `app/rag/` | Prepare phase: chunk → embed → vector index over SOPs |
| `app/audit.py` | Audit trail (FR-07) |
| `data/` | Mock schedule/fleet services + SOP documents |
| `deliverables/` | Concept report (.docx) and design diagrams (.drawio) |
