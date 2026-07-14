"""HTTP connectors for real source systems.

API contract expected from the remote side (see tools/demo_external_api.py
for a runnable reference implementation):

- GET  {SCHEDULE_API_URL}/trips     -> {"observed_at": iso8601, "trips": [...]}
- GET  {FLEET_API_URL}/fleet        -> {"observed_at": iso8601, "vehicles": [...], "drivers": [...]}
- POST {INCIDENT_API_URL}/incidents -> {"ticket_id": "...", "created_at": iso8601}

EXTERNAL_API_KEY, when set, is sent as a Bearer token. Failures (network,
non-2xx, malformed body) raise ConnectorError; freshness comes from the
response's own observed_at so a lagging upstream is visibly stale (FR-05).
"""
from datetime import datetime, timezone

import httpx

from ..config import CONNECTOR_TIMEOUT_SECONDS, CONNECTOR_TTL_SECONDS, EXTERNAL_API_KEY
from .base import ConnectorError


def _client(base_url: str, client: httpx.Client | None) -> httpx.Client:
    if client is not None:
        return client
    headers = {"Authorization": f"Bearer {EXTERNAL_API_KEY}"} if EXTERNAL_API_KEY else {}
    return httpx.Client(base_url=base_url, timeout=CONNECTOR_TIMEOUT_SECONDS, headers=headers)


def _get_json(client: httpx.Client, path: str, source: str) -> dict:
    try:
        resp = client.get(path)
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ConnectorError(f"{source}: {e}") from None
    if not isinstance(body, dict):
        raise ConnectorError(f"{source}: unexpected response shape")
    return body


def _observed_at(body: dict) -> datetime:
    raw = body.get("observed_at")
    if raw:
        try:
            ts = datetime.fromisoformat(raw)
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class HTTPScheduleConnector:
    mode = "http"
    ttl = CONNECTOR_TTL_SECONDS

    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._client = _client(base_url, client)

    def fetch(self) -> tuple[list[dict], datetime]:
        body = _get_json(self._client, "/trips", "schedule API")
        trips = body.get("trips")
        if not isinstance(trips, list):
            raise ConnectorError("schedule API: response missing 'trips' list")
        return trips, _observed_at(body)


class HTTPFleetConnector:
    mode = "http"
    ttl = CONNECTOR_TTL_SECONDS

    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._client = _client(base_url, client)

    def fetch(self) -> tuple[dict, datetime]:
        body = _get_json(self._client, "/fleet", "fleet API")
        vehicles, drivers = body.get("vehicles"), body.get("drivers")
        if not isinstance(vehicles, list) or not isinstance(drivers, list):
            raise ConnectorError("fleet API: response missing 'vehicles'/'drivers' lists")
        return {"vehicles": vehicles, "drivers": drivers}, _observed_at(body)


class HTTPIncidentConnector:
    mode = "http"

    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._client = _client(base_url, client)

    def create_ticket(self, ticket: dict) -> dict:
        try:
            resp = self._client.post("/incidents", json=ticket)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ConnectorError(f"incident API: {e}") from None
        if not isinstance(body, dict) or "ticket_id" not in body:
            raise ConnectorError("incident API: response missing 'ticket_id'")
        return {"ticket_id": body["ticket_id"], "created_at": body.get("created_at", datetime.now(timezone.utc).isoformat())}
