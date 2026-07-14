"""End-to-end pipeline smoke tests (run offline with the stub LLM)."""
import os

os.environ["OPENAI_API_KEY"] = ""  # force stub LLM / keyword search before app imports

from fastapi.testclient import TestClient

from app.main import app

# Demo credentials from data/users.json (see app/auth.py for the hashing scheme).
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


def test_health():
    with client() as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["llm"] == "stub"
        assert body["sop_search_mode"] == "keyword"
        assert body["intent_mode"] == "keyword"


def test_login_success_sets_session_cookie():
    with client() as c:
        r = c.post("/api/login", data={"username": "dispatcher", "password": "dispatcher123"})
        assert r.status_code == 200
        body = r.json()
        assert body == {"username": "dispatcher", "role": "dispatcher", "name": "Alex Tan"}
        assert "fleetops_session" in r.cookies

        me = c.get("/api/me")
        assert me.status_code == 200
        assert me.json()["role"] == "dispatcher"


def test_login_wrong_password_rejected():
    with client() as c:
        r = c.post("/api/login", data={"username": "dispatcher", "password": "wrong"})
        assert r.status_code == 401


def test_login_unknown_user_rejected():
    with client() as c:
        r = c.post("/api/login", data={"username": "nobody", "password": "whatever"})
        assert r.status_code == 401


def test_protected_endpoints_require_login():
    with client() as c:
        assert c.get("/api/me").status_code == 401
        assert c.get("/api/overview").status_code == 401
        assert c.post("/api/chat", data={"message": "hello"}).status_code == 401


def test_logout_clears_session():
    with client() as c:
        login(c)
        assert c.get("/api/me").status_code == 200
        r = c.post("/api/logout")
        assert r.status_code == 200
        assert c.get("/api/me").status_code == 401


def test_schedule_question_returns_grounded_evidence():
    with client() as c:
        login(c, "dispatcher")
        r = c.post("/api/chat", data={"message": "Why is route 18 delayed?"})
        assert r.status_code == 200
        body = r.json()
        assert "schedule_lookup" in body["intent"]
        sources = [e["source"] for e in body["evidence"]]
        assert "schedule_service" in sources
        sched = next(e for e in body["evidence"] if e["source"] == "schedule_service")
        assert all(t["route"] == "18" for t in sched["payload"]["trips"])
        assert sched["fresh"] is True


def test_sop_question_hits_vector_index():
    with client() as c:
        login(c, "driver")
        r = c.post(
            "/api/chat",
            data={"message": "What is the approved procedure for a vehicle breakdown?"},
        )
        body = r.json()
        sop = next(e for e in body["evidence"] if e["kind"] == "document")
        docs = {p["doc"] for p in sop["payload"]["passages"]}
        assert "vehicle_breakdown.md" in docs


def test_role_scoping_blocks_driver_fleet_access():
    with client() as c:
        login(c, "driver")
        r = c.post(
            "/api/chat",
            data={"message": "Which drivers are available for standby cover?"},
        )
        body = r.json()
        assert body["role"] == "driver"
        # fleet_status is intent-matched but not permitted for drivers
        assert "fleet_status" in body["intent"]
        assert all(e["source"] != "fleet_service" for e in body["evidence"])
        assert any("not permitted" in n for n in body["notes"])


def test_overview_scopes_drivers_by_role():
    with client() as c:
        login(c, "dispatcher")
        full = c.get("/api/overview").json()
        assert full["trips"] and full["vehicles"] and full["drivers"]

    with client() as c:
        login(c, "driver")
        scoped = c.get("/api/overview").json()
        assert scoped["drivers"] == []


def test_conversation_history_reaches_llm():
    history = (
        '[{"role":"user","content":"Why is route 18 delayed?"},'
        '{"role":"assistant","content":"Road works, +20 min."}]'
    )
    with client() as c:
        login(c, "dispatcher")
        r = c.post(
            "/api/chat",
            data={"message": "What about route 12?", "history": history},
        )
        assert "2 prior message(s)" in r.json()["answer"]  # stub echoes history length


def test_malformed_history_is_ignored():
    with client() as c:
        login(c, "dispatcher")
        r = c.post(
            "/api/chat",
            data={"message": "next trips", "history": '{"role":"system"}'},
        )
        assert r.status_code == 200
        assert "0 prior message(s)" in r.json()["answer"]


def test_keyword_intent_fallback():
    from app.agent.intent import keyword_classify

    assert keyword_classify("Why is route 18 delayed?") == ["schedule_lookup"]
    assert keyword_classify("Which vehicles are available?") == ["fleet_status"]
    assert keyword_classify("What is the approved breakdown procedure?") == [
        "fleet_status",
        "sop_search",
    ]
    # nothing matches -> defaults to sop_search so the agent always has evidence
    assert keyword_classify("hello there") == ["sop_search"]


def test_video_upload_extracts_frames(tmp_path):
    import pytest

    cv2 = pytest.importorskip("cv2")
    import numpy as np

    path = tmp_path / "clip.mp4"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (64, 64))
    for i in range(10):
        w.write(np.full((64, 64, 3), i * 20, np.uint8))
    w.release()

    with client() as c, path.open("rb") as f:
        login(c, "dispatcher")
        r = c.post(
            "/api/chat",
            data={"message": "What does this clip show about the vehicle?"},
            files={"files": ("clip.mp4", f, "video/mp4")},
        )
    body = r.json()
    assert any("Video sampled into" in n for n in body["notes"]), body["notes"]
    assert "3 attached image(s)/frame(s)" in body["answer"]


def test_escalated_turn_creates_handoff_package():
    with client() as c:
        login(c, "driver")
        r = c.post("/api/chat", data={"message": "Which drivers are available for standby cover?"})
        body = r.json()
        assert body["escalated"] is True
        assert body["escalation_id"], "escalated turn should create a handoff"
        assert any("logged for operator review" in n for n in body["notes"])

        login(c, "manager")
        queue = c.get("/api/escalations").json()
        match = next(e for e in queue if e["id"] == body["escalation_id"])
        assert match["status"] == "open"
        assert match["confidence"] == body["confidence"]

        full = c.get(f"/api/escalations/{body['escalation_id']}").json()
        # the operator package carries the complete turn
        for key in ("question", "history", "evidence", "notes", "draft_answer", "user"):
            assert key in full, f"package missing {key}"
        assert full["user"]["username"] == "driver"


def test_confident_turn_creates_no_handoff():
    with client() as c:
        login(c, "dispatcher")
        r = c.post("/api/chat", data={"message": "Why is route 18 delayed?"})
        body = r.json()
        assert body["escalated"] is False
        assert body["escalation_id"] is None


def test_driver_cannot_view_escalations():
    with client() as c:
        login(c, "driver")
        assert c.get("/api/escalations").status_code == 403


def test_only_manager_resolves_escalations():
    with client() as c:
        login(c, "driver")
        esc_id = c.post("/api/chat", data={"message": "Which drivers are on standby?"}).json()["escalation_id"]
        assert esc_id

        login(c, "dispatcher")
        assert c.post(f"/api/escalations/{esc_id}/resolve", data={"note": "x"}).status_code == 403

        login(c, "manager")
        done = c.post(f"/api/escalations/{esc_id}/resolve", data={"note": "Reassigned manually"})
        assert done.status_code == 200
        body = done.json()
        assert body["status"] == "resolved"
        assert body["resolved_by"] == "manager"
        assert body["resolution_note"] == "Reassigned manually"


def test_escalation_ids_are_validated():
    with client() as c:
        login(c, "manager")
        assert c.get("/api/escalations/../../etc/passwd").status_code in (404, 422)
        assert c.get("/api/escalations/ESC-99999999-000000-ffff").status_code == 404
