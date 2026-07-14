"""Connector layer tests: HTTP contract, graceful degradation, failure gating."""
import os

os.environ["OPENAI_API_KEY"] = ""  # keep the app offline before any import

from datetime import datetime, timezone

import httpx
import pytest

from app.connectors.base import ConnectorError
from app.connectors.http import HTTPFleetConnector, HTTPIncidentConnector, HTTPScheduleConnector
from app.tools.datastore import DataStore


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="http://external.test",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test-key"},
    )


def test_http_schedule_connector_parses_contract():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={
            "observed_at": "2026-07-15T08:00:00+00:00",
            "trips": [{"trip_id": "T-9001", "route": "9", "status": "on_time"}],
        })

    conn = HTTPScheduleConnector("http://external.test", client=_client(handler))
    trips, observed = conn.fetch()
    assert seen["path"] == "/trips"
    assert seen["auth"] == "Bearer test-key"
    assert trips[0]["trip_id"] == "T-9001"
    assert observed == datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)


def test_http_fleet_connector_parses_contract():
    def handler(request):
        return httpx.Response(200, json={
            "observed_at": "2026-07-15T08:00:00Z",
            "vehicles": [{"vehicle_id": "V-901", "status": "available"}],
            "drivers": [{"driver_id": "D-90", "status": "standby"}],
        })

    conn = HTTPFleetConnector("http://external.test", client=_client(handler))
    payload, observed = conn.fetch()
    assert payload["vehicles"][0]["vehicle_id"] == "V-901"
    assert payload["drivers"][0]["driver_id"] == "D-90"
    assert observed.tzinfo is not None


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"unexpected": True}),
])
def test_http_schedule_connector_failures_raise(response):
    conn = HTTPScheduleConnector("http://external.test", client=_client(lambda r: response))
    with pytest.raises(ConnectorError):
        conn.fetch()


def test_http_incident_connector_posts_ticket():
    posted = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        posted["path"] = request.url.path
        posted["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"ticket_id": "EXT-1", "created_at": "2026-07-15T08:00:00Z"})

    conn = HTTPIncidentConnector("http://external.test", client=_client(handler))
    receipt = conn.create_ticket({"kind": "breakdown", "subject": "V-105"})
    assert posted["path"] == "/incidents"
    assert posted["body"]["subject"] == "V-105"
    assert receipt["ticket_id"] == "EXT-1"


# ------------------------------------------------------- DataStore degradation
class FlakyConnector:
    """Succeeds once, then fails — for testing graceful degradation."""

    mode = "http"
    ttl = 0  # every access refetches

    def __init__(self, fail_from_start=False):
        self.calls = 0
        self.fail_from_start = fail_from_start

    def fetch(self):
        self.calls += 1
        if self.fail_from_start or self.calls > 1:
            raise ConnectorError("source down")
        return [{"trip_id": "T-0001", "route": "1", "status": "on_time"}], datetime.now(timezone.utc)


def test_datastore_keeps_last_snapshot_when_source_goes_down():
    conn = FlakyConnector()
    store = DataStore(schedule=conn, fleet=FlakyFleet())
    first = store.trips
    assert first and first[0]["trip_id"] == "T-0001"
    good_observed = store.schedule_observed_at

    # source now fails: data survives, observed_at stops advancing
    again = store.trips
    assert again == first
    assert store.schedule_observed_at == good_observed
    assert store.connector_status()["schedule"]["error"] == "source down"


class FlakyFleet(FlakyConnector):
    def fetch(self):
        self.calls += 1
        if self.fail_from_start or self.calls > 1:
            raise ConnectorError("source down")
        return {"vehicles": [], "drivers": []}, datetime.now(timezone.utc)


def test_datastore_serves_empty_when_source_never_worked():
    store = DataStore(schedule=FlakyConnector(fail_from_start=True), fleet=FlakyFleet(fail_from_start=True))
    assert store.trips == []
    assert store.vehicles == [] and store.drivers == []
    # epoch observed_at -> evidence would be flagged stale downstream
    assert store.schedule_observed_at.year == 1970
    assert store.connector_status()["schedule"]["error"] == "source down"


# ------------------------------------------------- action gate on failure
def test_failed_incident_execution_keeps_proposal_open(monkeypatch):
    from app.tools import actions

    class DownIncidentConnector:
        mode = "http"

        def create_ticket(self, ticket):
            raise ConnectorError("incident API unreachable")

    monkeypatch.setattr(actions, "_INCIDENT_CONN", DownIncidentConnector())
    prop = actions._create(
        "create_incident_ticket", "test proposal", {"kind": "breakdown", "subject": "V-105"},
        "SOP-01", {"username": "t", "name": "t"}, "dispatcher",
    )
    record, outcome = actions.approve(prop["id"], {"username": "m", "name": "M", "role": "manager"})
    assert outcome == "failed"
    assert record["status"] == "proposed"  # still open for retry
    assert record["receipt"] is None

    # connector restored -> same proposal can now be approved
    monkeypatch.setattr(actions, "_INCIDENT_CONN", None)
    record, outcome = actions.approve(prop["id"], {"username": "m", "name": "M", "role": "manager"})
    assert outcome == "executed"
    assert record["receipt"]["ticket_id"].startswith("INC-")
