"""Evaluation set: normal, ambiguous, stale-data, and failure cases.

Report Section 16 (Conclusion, "Immediate next actions") calls for "a
representative evaluation set containing normal, ambiguous, stale-data, and
failure cases" before the read-only MVP boundary is approved. This is that
set, grounded in the Functional Requirements it's meant to demonstrate:

- normal      -> FR-02/FR-03/FR-04: correct tool selection and grounded,
                 sourced answers on the happy path.
- ambiguous   -> FR-03/FR-08: intent is never empty, and role/tool
                 boundaries produce an explanatory note rather than a
                 silent gap, even when the question is underspecified or
                 straddles a permission edge.
- stale-data  -> FR-05: stale evidence is detected and flagged, and drives
                 escalation rather than being presented as current.
- failure     -> FR-08 + basic robustness: when nothing usable can be
                 retrieved, the system escalates explicitly instead of
                 fabricating an answer; oversized/unsupported input degrades
                 gracefully instead of crashing the request.

Runs entirely offline (stub LLM, keyword intent fallback) like the rest of
the suite, so it's deterministic and CI-safe. Distinct from test_pipeline.py
(general pipeline smoke tests) — this file is specifically the categorized
evaluation set the report asks for.
"""
import os

os.environ["OPENAI_API_KEY"] = ""  # force stub LLM / keyword search before app imports

from fastapi.testclient import TestClient

from app.config import MAX_UPLOAD_BYTES
from app.main import app

CREDENTIALS = {
    "dispatcher": "dispatcher123",
    "planner": "planner123",
    "driver": "driver123",
    "manager": "manager123",
}


def client() -> TestClient:
    return TestClient(app)


def login(c: TestClient, role: str = "dispatcher") -> None:
    r = c.post("/api/login", data={"username": role, "password": CREDENTIALS[role]})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- normal ---


def test_normal_schedule_question():
    """Clear single-intent question -> correct tool, grounded, no escalation."""
    with client() as c:
        login(c, "dispatcher")
        r = c.post("/api/chat", data={"message": "Why is route 18 delayed?"})
        body = r.json()
        # schedule tool routed; a diagnosis:delay block may accompany it now
        # that delay questions trigger the SOP-02 guided checklist (Phase 3)
        assert body["intent"][0] == "schedule_lookup"
        assert all(i == "schedule_lookup" or i.startswith("diagnosis:") for i in body["intent"])
        assert any(e["source"] == "schedule_service" and e["fresh"] for e in body["evidence"])
        assert body["escalated"] is False


def test_normal_sop_question():
    """Clear policy question -> cites the right SOP document, no escalation."""
    with client() as c:
        login(c, "driver")
        r = c.post(
            "/api/chat",
            data={"message": "What is the approved procedure for a vehicle breakdown?"},
        )
        body = r.json()
        sop = next(e for e in body["evidence"] if e["kind"] == "document")
        assert "vehicle_breakdown.md" in {p["doc"] for p in sop["payload"]["passages"]}
        assert body["escalated"] is False


def test_normal_fleet_question():
    """Clear fleet-availability question -> fleet_service evidence, no escalation."""
    with client() as c:
        login(c, "planner")
        r = c.post("/api/chat", data={"message": "Which vehicles are available right now?"})
        body = r.json()
        assert any(e["source"] == "fleet_service" for e in body["evidence"])
        assert body["escalated"] is False


# ------------------------------------------------------------- ambiguous ---


def test_ambiguous_vague_question_never_returns_empty_intent():
    """Underspecified question -> FR-03 guarantee: never an empty intent list."""
    with client() as c:
        login(c, "dispatcher")
        r = c.post("/api/chat", data={"message": "What's going on today?"})
        body = r.json()
        assert len(body["intent"]) > 0
        # No matching evidence is fine, but the gap must be explained, not silent.
        assert body["evidence"] or body["notes"]


def test_ambiguous_role_boundary_explains_rather_than_omits():
    """Question needs a tool this role can't use -> explanatory note, not a silent gap."""
    with client() as c:
        login(c, "driver")
        r = c.post("/api/chat", data={"message": "Which drivers are on standby right now?"})
        body = r.json()
        assert "fleet_status" in body["intent"]
        assert all(e["source"] != "fleet_service" for e in body["evidence"])
        assert any("not permitted" in n for n in body["notes"])


def test_ambiguous_overlapping_intents_use_both_tools():
    """Question straddling live-data and policy -> both tools engaged, not just one."""
    with client() as c:
        login(c, "planner")
        r = c.post(
            "/api/chat",
            data={
                "message": (
                    "What is the procedure for a vehicle breakdown, and are there "
                    "available replacement vehicles?"
                )
            },
        )
        body = r.json()
        sources = {e["source"] for e in body["evidence"]}
        assert "fleet_service" in sources
        assert any(s.startswith("sop_index") for s in sources)


# ------------------------------------------------------------ stale-data ---


def test_stale_evidence_flagged_and_escalates(monkeypatch):
    """FR-05: evidence older than the freshness limit is flagged and escalates."""
    import app.tools.registry as registry

    monkeypatch.setattr(registry, "FRESHNESS_LIMIT_MINUTES", -1)  # even age~0 counts as stale
    with client() as c:
        login(c, "dispatcher")
        r = c.post("/api/chat", data={"message": "Why is route 18 delayed?"})
        body = r.json()
        sched = next(e for e in body["evidence"] if e["source"] == "schedule_service")
        assert sched["fresh"] is False
        assert body["escalated"] is True
        assert any("schedule_service" in n and "freshness" in n for n in body["notes"])


def test_freshness_window_separates_fresh_from_stale():
    """Evidence just inside the freshness window is fresh; just outside is
    stale. Calls the freshness helper directly with controlled timestamps
    instead of going through a live HTTP request, since the DataStore's
    loaded_at is a process-wide singleton set once at server start -- by
    the time later tests in the suite run, real wall-clock time has already
    passed, so asserting an exact age via HTTP timing is inherently racy."""
    from datetime import datetime, timedelta, timezone

    from app.config import FRESHNESS_LIMIT_MINUTES
    from app.tools.registry import _evidence

    now = datetime.now(timezone.utc)
    just_inside = _evidence(
        "test_source", "live", now - timedelta(minutes=FRESHNESS_LIMIT_MINUTES - 1), "s", {}
    )
    just_outside = _evidence(
        "test_source", "live", now - timedelta(minutes=FRESHNESS_LIMIT_MINUTES + 1), "s", {}
    )
    assert just_inside.fresh is True
    assert just_outside.fresh is False


def test_multiple_stale_sources_named_in_one_note(monkeypatch):
    """Several stale sources in one turn -> named together in a single note."""
    import app.tools.registry as registry

    monkeypatch.setattr(registry, "FRESHNESS_LIMIT_MINUTES", -1)
    with client() as c:
        login(c, "planner")
        r = c.post(
            "/api/chat",
            data={"message": "What is the current status of route 18 and available vehicles?"},
        )
        body = r.json()
        stale_note = next(n for n in body["notes"] if "freshness" in n)
        assert "schedule_service" in stale_note
        assert "fleet_service" in stale_note
        assert body["escalated"] is True


# ------------------------------------------------------------ conflicting ---


def test_conflicting_sources_flagged_and_escalates(monkeypatch):
    """FR-05: schedule and fleet disagree about whether a trip can run ->
    a source-precedence conflict note is surfaced and the answer escalates,
    regardless of what the confidence score alone would have decided."""
    import app.tools.registry as registry
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    class FakeStore:
        loaded_at = now
        schedule_observed_at = now
        fleet_observed_at = now
        trips = [
            {
                "trip_id": "T-1", "route": "12", "origin": "A", "destination": "B",
                "status": "on_time", "delay_min": 0, "vehicle_id": "V-1", "driver_id": "D-1",
            }
        ]
        vehicles = [
            {"vehicle_id": "V-1", "type": "bus_12m", "status": "maintenance", "location": "Depot"}
        ]
        drivers = [
            {"driver_id": "D-1", "name": "T. Test", "status": "on_duty", "shift_end": now.isoformat()}
        ]

    monkeypatch.setattr(registry, "get_store", lambda: FakeStore())
    with client() as c:
        login(c, "dispatcher")
        r = c.post(
            "/api/chat",
            data={"message": "What is the current status of route 12 and available vehicles?"},
        )
        body = r.json()
        assert any("Conflict" in n and "T-1" in n for n in body["notes"])
        assert body["escalated"] is True


# ---------------------------------------------------------------- failure ---


def test_failure_no_permitted_tool_escalates():
    """FR-08: when the only matched tool is off-limits, escalate explicitly."""
    with client() as c:
        login(c, "driver")
        r = c.post("/api/chat", data={"message": "Which drivers are on standby right now?"})
        body = r.json()
        assert body["evidence"] == []
        assert body["escalated"] is True
        assert any("No permitted tool matched" in n for n in body["notes"])


def test_failure_oversized_attachment_skipped_not_fatal():
    """Bad input degrades gracefully: oversized file skipped, chat still answers."""
    with client() as c:
        login(c, "dispatcher")
        oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
        r = c.post(
            "/api/chat",
            data={"message": "Why is route 18 delayed?"},
            files={"files": ("photo.jpg", oversized, "image/jpeg")},
        )
        assert r.status_code == 200
        body = r.json()
        assert any("exceeds the size limit" in n for n in body["notes"])
        assert body["answer"]  # still answered from the text alone


def test_failure_unauthenticated_request_rejected():
    """Security failure boundary: no session -> 401, not a degraded answer."""
    with client() as c:
        r = c.post("/api/chat", data={"message": "Why is route 18 delayed?"})
        assert r.status_code == 401
