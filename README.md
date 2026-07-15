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
- Confidence scoring (coverage × freshness × grounding, discounted in keyword-fallback
  mode) drives escalation — an answer built on partial, stale, or empty evidence
  escalates to a human instead of being presented as confident (FR-08)
- SOP-guided incident diagnosis (breakdown / delay / driver unavailable): the SOP
  checklist is executed as code against live data, and low-confidence or conflicting
  answers are packaged into an escalation queue a human operator can pick up
- Approval-gated actions (FR-06): the agent proposes (create incident ticket, publish
  delay notice), a permitted human approves via an action card, the governed tool
  executes exactly once (idempotent) with a receipt + rollback note, and the audit
  log records the approver — the agent never executes anything autonomously

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

To see stale-evidence handling without waiting on wall-clock time, set
`SCHEDULE_LAG_MINUTES=15` or `FLEET_LAG_MINUTES=15` in `.env` and restart —
that source's evidence renders as stale and the answer escalates (FR-05).

## System connectors

Each source system sits behind a connector (see `app/connectors/`): the local
day-over-day simulation by default, or a real HTTP API when its URL is configured —
sources are independent, so schedule can go live while fleet stays mock. To try the
HTTP path against the bundled reference API:

```bash
# terminal 1: a stand-in "real" fleet backend
python -m uvicorn tools.demo_external_api:app --port 9001

# terminal 2: point the copilot at it
SCHEDULE_API_URL=http://127.0.0.1:9001 FLEET_API_URL=http://127.0.0.1:9001 INCIDENT_API_URL=http://127.0.0.1:9001 uvicorn app.main:app --reload
```

`/api/health` then reports each connector's mode and last error. Approved incident
tickets are POSTed to the external system (inspect with `GET :9001/incidents`).
If a source goes down, the copilot keeps serving its last snapshot — which fails
the freshness check once it ages past the limit, so answers escalate instead of
silently presenting dead data (Section 11: graceful degradation).

## Structure

| Path | Report section |
|---|---|
| `app/main.py` | Channels & experience (Section 7) |
| `app/auth.py` | Login/session auth — PBKDF2 password hashing, signed session cookies (FR-01) |
| `app/agent/` | Agent orchestration: intent → tools → validate → pack → LLM (Section 8) |
| `app/agent/confidence.py` | Confidence scoring + escalation threshold (FR-08) |
| `app/agent/precedence.py` | Cross-source conflict detection + source precedence (FR-05) |
| `app/agent/diagnosis.py` | SOP-guided incident diagnosis flows (Phase 3) |
| `app/agent/sanitize.py` | Prompt-injection defenses on retrieved content (Section 10) |
| `app/escalations.py` | Escalation handoff queue for human operators (FR-08) |
| `app/tools/` | Governed operational tools over mock live systems (Section 9) |
| `app/tools/actions.py` | Approval-gated write actions with idempotency + rollback notes (FR-06) |
| `app/connectors/` | Source-system connectors: mock simulation or real HTTP APIs per source (Section 9) |
| `tools/demo_external_api.py` | Reference external schedule/fleet/incident API for the HTTP connectors |
| `app/rag/` | Prepare phase: chunk → embed → vector index over SOPs |
| `app/audit.py` | Audit trail (FR-07) |
| `data/` | Mock schedule/fleet services, user directory, and SOP documents |
| `tests/test_evaluation.py` | Evaluation set: normal, ambiguous, stale-data, and failure cases (report Section 16) |
| `deliverables/` | Concept report (.docx) and design diagrams (.drawio) |
