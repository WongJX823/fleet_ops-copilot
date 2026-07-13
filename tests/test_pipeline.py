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


def test_unknown_role_falls_back_to_default():
    with client() as c:
        r = c.post("/api/chat", data={"message": "next trips on route 12", "role": "hacker"})
        assert r.json()["role"] == "dispatcher"
