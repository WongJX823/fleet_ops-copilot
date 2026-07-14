"""Mock connectors backed by the local day-over-day simulation (day_sim.py).

These preserve the original DataStore semantics: the snapshot is computed once
per process (ttl=None) so departure times stay anchored to the load moment,
and the SCHEDULE/FLEET_LAG_MINUTES demo knobs shift observed_at to simulate a
source falling behind (FR-05).
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

from ..config import FLEET_LAG_MINUTES, RUNTIME_DIR, SCHEDULE_LAG_MINUTES
from ..tools import day_sim

INCIDENT_DIR = RUNTIME_DIR / "incidents"


class MockScheduleConnector:
    mode = "mock"
    ttl = None  # stable for the process lifetime

    def __init__(self) -> None:
        self.sim_date: str | None = None

    def fetch(self) -> tuple[list[dict], datetime]:
        now = datetime.now(timezone.utc)
        state = day_sim.ensure_current_day()
        self.sim_date = state["date"]
        trips = []
        for t in state["trips"]:
            t = dict(t)
            t["departure"] = (now + timedelta(minutes=t.pop("depart_offset_min"))).isoformat()
            trips.append(t)
        return trips, now - timedelta(minutes=SCHEDULE_LAG_MINUTES)


class MockFleetConnector:
    mode = "mock"
    ttl = None

    def fetch(self) -> tuple[dict, datetime]:
        now = datetime.now(timezone.utc)
        state = day_sim.ensure_current_day()
        drivers = []
        for d in state["drivers"]:
            d = dict(d)
            d["shift_end"] = (now + timedelta(minutes=d.pop("shift_end_offset_min"))).isoformat()
            drivers.append(d)
        payload = {"vehicles": list(state["vehicles"]), "drivers": drivers}
        return payload, now - timedelta(minutes=FLEET_LAG_MINUTES)


class MockIncidentConnector:
    """Local incident 'system': one JSON file per ticket under runtime/."""

    mode = "mock"

    def create_ticket(self, ticket: dict) -> dict:
        INCIDENT_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        ticket_id = f"INC-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        record = {"ticket_id": ticket_id, "created_at": now.isoformat(), "status": "open", **ticket}
        (INCIDENT_DIR / f"{ticket_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ticket_id": ticket_id, "created_at": record["created_at"]}
