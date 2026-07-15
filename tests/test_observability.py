"""Observability, rate limiting, and gateway auth tests (Phase 5)."""
import time

from fastapi.testclient import TestClient

import app.config as app_config
import app.main as app_main
from app.main import app
from app.ratelimit import RateLimiter

CREDENTIALS = {"dispatcher": "dispatcher123", "manager": "manager123"}


def client() -> TestClient:
    return TestClient(app)


def login(c: TestClient, role: str) -> None:
    r = c.post("/api/login", data={"username": role, "password": CREDENTIALS[role]})
    assert r.status_code == 200, r.text


# ----------------------------------------------------------------- limiter
def test_rate_limiter_sliding_window():
    rl = RateLimiter(max_events=3, window_s=0.2)
    assert all(rl.allow("k") for _ in range(3))
    assert rl.allow("k") is False
    assert rl.allow("other-key") is True  # keys are independent
    time.sleep(0.25)
    assert rl.allow("k") is True  # window expired


def test_login_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(app_main, "LOGIN_LIMITER", RateLimiter(2, 60))
    with client() as c:
        login(c, "dispatcher")
        login(c, "dispatcher")
        r = c.post("/api/login", data={"username": "dispatcher", "password": "dispatcher123"})
        assert r.status_code == 429


def test_chat_rate_limit_is_per_user(monkeypatch):
    monkeypatch.setattr(app_main, "CHAT_LIMITER", RateLimiter(1, 60))
    with client() as c:
        login(c, "dispatcher")
        assert c.post("/api/chat", data={"message": "next trips on route 12"}).status_code == 200
        r = c.post("/api/chat", data={"message": "next trips on route 12"})
        assert r.status_code == 429

    with client() as c:  # a different user has their own budget
        login(c, "manager")
        assert c.post("/api/chat", data={"message": "next trips on route 12"}).status_code == 200


# ----------------------------------------------------------------- metrics
def test_metrics_capture_requests_tools_and_llm():
    with client() as c:
        login(c, "dispatcher")
        assert c.post("/api/chat", data={"message": "Why is route 18 delayed?"}).status_code == 200

        login(c, "manager")
        snap = c.get("/api/metrics").json()

    chat = snap["requests"].get("POST /api/chat")
    assert chat and chat["count"] >= 1 and chat["avg_ms"] >= 0
    assert snap["tools"].get("schedule_lookup", {}).get("count", 0) >= 1
    assert snap["llm"]["calls"] >= 1
    assert snap["llm"]["cost_usd"] == 0.0  # stub model is free
    assert "uptime_s" in snap


def test_metrics_are_manager_only():
    with client() as c:
        login(c, "dispatcher")
        assert c.get("/api/metrics").status_code == 403


# ------------------------------------------------------------ gateway auth
def test_gateway_key_enforced_when_configured(monkeypatch):
    monkeypatch.setattr(app_config, "GATEWAY_API_KEY", "sekret")
    with client() as c:
        assert c.get("/api/health").status_code == 401
        assert c.get("/api/health", headers={"X-Gateway-Key": "wrong"}).status_code == 401
        assert c.get("/api/health", headers={"X-Gateway-Key": "sekret"}).status_code == 200
        # non-API paths (the UI itself) stay reachable
        assert c.get("/").status_code == 200


def test_gateway_key_disabled_by_default():
    with client() as c:
        assert c.get("/api/health").status_code == 200
