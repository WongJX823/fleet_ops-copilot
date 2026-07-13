"""End-to-end pipeline smoke tests (run offline with the stub LLM)."""
import os

os.environ["OPENAI_API_KEY"] = ""  # force stub LLM / keyword search before app imports

from fastapi.testclient import TestClient

from app.main import app


def client() -> TestClient:
    return TestClient(app)


def test_health():
    with client() as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["llm"] == "stub"
        assert body["sop_search_mode"] == "keyword"


def test_schedule_question_returns_grounded_evidence():
    with client() as c:
        r = c.post("/api/chat", data={"message": "Why is route 18 delayed?", "role": "dispatcher"})
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
        r = c.post(
            "/api/chat",
            data={"message": "What is the approved procedure for a vehicle breakdown?", "role": "driver"},
        )
        body = r.json()
        sop = next(e for e in body["evidence"] if e["kind"] == "document")
        docs = {p["doc"] for p in sop["payload"]["passages"]}
        assert "vehicle_breakdown.md" in docs


def test_role_scoping_blocks_driver_fleet_access():
    with client() as c:
        r = c.post(
            "/api/chat",
            data={"message": "Which drivers are available for standby cover?", "role": "driver"},
        )
        body = r.json()
        # fleet_status is intent-matched but not permitted for drivers
        assert "fleet_status" in body["intent"]
        assert all(e["source"] != "fleet_service" for e in body["evidence"])
        assert any("not permitted" in n for n in body["notes"])


def test_overview_scopes_drivers_by_role():
    with client() as c:
        full = c.get("/api/overview", params={"role": "dispatcher"}).json()
        assert full["trips"] and full["vehicles"] and full["drivers"]
        scoped = c.get("/api/overview", params={"role": "driver"}).json()
        assert scoped["drivers"] == []


def test_unknown_role_falls_back_to_default():
    with client() as c:
        r = c.post("/api/chat", data={"message": "next trips on route 12", "role": "hacker"})
        assert r.json()["role"] == "dispatcher"


def test_conversation_history_reaches_llm():
    history = (
        '[{"role":"user","content":"Why is route 18 delayed?"},'
        '{"role":"assistant","content":"Road works, +20 min."}]'
    )
    with client() as c:
        r = c.post(
            "/api/chat",
            data={"message": "What about route 12?", "role": "dispatcher", "history": history},
        )
        assert "2 prior message(s)" in r.json()["answer"]  # stub echoes history length


def test_malformed_history_is_ignored():
    with client() as c:
        r = c.post(
            "/api/chat",
            data={"message": "next trips", "role": "dispatcher", "history": '{"role":"system"}'},
        )
        assert r.status_code == 200
        assert "0 prior message(s)" in r.json()["answer"]


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
        r = c.post(
            "/api/chat",
            data={"message": "What does this clip show about the vehicle?", "role": "dispatcher"},
            files={"files": ("clip.mp4", f, "video/mp4")},
        )
    body = r.json()
    assert any("Video sampled into" in n for n in body["notes"]), body["notes"]
    assert "3 attached image(s)/frame(s)" in body["answer"]
