# Fleet Ops Copilot — TODO

Tracks work against the delivery roadmap in the project report (Section 12).
Update checkboxes as items land.

## Done

- [x] Concept design report + design diagrams (6 drawio pages, incl. RAG + Tool Pipeline)
- [x] Phase 2 scaffold: FastAPI backend, agent orchestrator, governed read tools
- [x] RAG prepare phase: SOP chunking, embeddings index, keyword fallback
- [x] Mock live services (schedule / fleet / roster) with observed-at timestamps
- [x] Role scoping (dispatcher / planner / driver / manager) enforced in tools + overview
- [x] Web chat UI with operations dashboard panel, quick questions, row-click prompts
- [x] Image & video attachments (video → sampled frames, OpenCV optional)
- [x] Append-only audit log (runtime/audit.log.jsonl)
- [x] Offline stub LLM + 6 passing end-to-end tests

## Next up (finish Phase 2 — read-only MVP)

- [x] Commit the scaffold and push to GitHub
- [x] Set `OPENAI_API_KEY` in `.env` and verify real grounded answers (text + image)
- [x] Install `opencv-python-headless` and verify video frame extraction end-to-end
- [ ] Real intent classification (LLM-based or embedding classifier) to replace keyword matching
- [x] Conversation memory: pass recent turns to the LLM so follow-up questions work
- [ ] Persist the SOP vector index to disk instead of re-embedding on every startup
- [ ] Real authentication (login/session) instead of the role dropdown
- [ ] Evaluation set: normal, ambiguous, stale-data, and failure cases (report Section 16)

## Phase 3 — guided resolution

- [ ] Confidence scoring on answers; escalate below threshold (FR-08)
- [ ] Stale-data simulation + source-precedence rules when sources conflict (FR-05)
- [ ] Incident diagnosis flows for the three pilot scenarios (breakdown, delay, driver unavailable)
- [ ] Escalation handoff: package conversation + evidence for a human operator

## Phase 4 — approved actions (write tools)

- [ ] Approval gate UI (proposed action → human confirms → execute → receipt)
- [ ] First action tools: create incident ticket, publish delay notice
- [ ] Idempotency keys + rollback notes on every action tool
- [ ] Action entries in the audit log with approver identity

## Phase 5 — scale & hardening

- [ ] Swap mock datastore for real system connectors (schedule, fleet, incident APIs)
- [ ] Prompt-injection defenses on retrieved documents (report Section 10)
- [ ] Observability: request metrics, tool latency, token cost per answer (Section 13 metrics)
- [ ] Rate limiting + API gateway auth in front of the service
- [ ] Multi-tenant / depot boundaries if needed

## Housekeeping

- [ ] Remove stray Office lock/backup files from `deliverables/` (now gitignored)
- [ ] Export drawio pages to PNG/PDF for people without draw.io
- [ ] Add CI (GitHub Actions: pytest on push)
