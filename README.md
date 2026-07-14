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
- Login/session auth (dispatcher / planner / driver / manager) — role is read from the
  signed session cookie server-side, not a client-editable dropdown — plus evidence
  chips with source + timestamp + freshness and an append-only audit log in
  `runtime/audit.log.jsonl`
- Read-only: the agent recommends actions and cites SOPs but cannot execute changes

## Quickstart

```bash
pip install -r requirements.txt
copy .env.example .env   # then set OPENAI_API_KEY (optional) and SESSION_SECRET
uvicorn app.main:app --reload
```

Sign in at `http://127.0.0.1:8000` with one of the demo accounts (mock user directory
in `data/users.json`, PBKDF2-hashed, no plaintext at rest):

| Username | Password | Role |
|---|---|---|
| `dispatcher` | `dispatcher123` | Dispatcher |
| `planner` | `planner123` | Planner |
| `driver` | `driver123` | Driver |
| `manager` | `manager123` | Operations Manager |

Without an API key the app still runs end-to-end using a stub LLM and keyword SOP
search — useful for tests and offline demos. Video attachments need
`opencv-python-headless` (optional) for frame extraction.

No real incident photos on hand to test the image/video attachment path? Generate
synthetic ones:

```bash
pip install opencv-python-headless
python tools/generate_demo_attachments.py
```

Writes `demo_assets/fault_image.jpg` (a dashboard warning-light illustration) and
`demo_assets/fault_video.mp4` (a short "engine smoke + hazard lights" clip) — attach
either in the chat UI to exercise the real vision path.

Run tests (offline, no key needed):

```bash
pytest
```

## Structure

| Path | Report section |
|---|---|
| `app/main.py` | Channels & experience (Section 7) |
| `app/auth.py` | Login/session auth — PBKDF2 password hashing, signed session cookies (FR-01) |
| `app/agent/` | Agent orchestration: intent → tools → validate → pack → LLM (Section 8) |
| `app/tools/` | Governed operational tools over mock live systems (Section 9) |
| `app/rag/` | Prepare phase: chunk → embed → vector index over SOPs |
| `app/audit.py` | Audit trail (FR-07) |
| `data/` | Mock schedule/fleet services, user directory, and SOP documents |
| `deliverables/` | Concept report (.docx) and design diagrams (.drawio) |
