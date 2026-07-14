"""Reference 'real' source systems for the HTTP connectors.

A tiny standalone API implementing the contract in app/connectors/http.py,
so the connector swap can be exercised without an actual fleet backend:

    python -m uvicorn tools.demo_external_api:app --port 9001

Then point the copilot at it and restart:

    SCHEDULE_API_URL=http://127.0.0.1:9001
    FLEET_API_URL=http://127.0.0.1:9001
    INCIDENT_API_URL=http://127.0.0.1:9001

Serves the same baseline data as the mock simulation (data/*.json) so answers
stay comparable, and stores POSTed incidents in memory (GET /incidents to
inspect them).
"""
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

app = FastAPI(title="Demo External Fleet Systems")
_incidents: list[dict] = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


@app.get("/trips")
async def trips() -> dict:
    now = _now()
    sched = json.loads((DATA_DIR / "schedule.json").read_text(encoding="utf-8"))
    out = []
    for t in sched["trips"]:
        t = dict(t)
        t["departure"] = (now + timedelta(minutes=t.pop("depart_offset_min"))).isoformat()
        out.append(t)
    return {"observed_at": now.isoformat(), "trips": out, "source": "demo_external_api"}


@app.get("/fleet")
async def fleet() -> dict:
    now = _now()
    data = json.loads((DATA_DIR / "fleet.json").read_text(encoding="utf-8"))
    drivers = []
    for d in data["drivers"]:
        d = dict(d)
        d["shift_end"] = (now + timedelta(minutes=d.pop("shift_end_offset_min"))).isoformat()
        drivers.append(d)
    return {
        "observed_at": now.isoformat(),
        "vehicles": data["vehicles"],
        "drivers": drivers,
        "source": "demo_external_api",
    }


@app.post("/incidents")
async def create_incident(ticket: dict) -> dict:
    now = _now()
    record = {
        "ticket_id": f"EXT-INC-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}",
        "created_at": now.isoformat(),
        "status": "open",
        **ticket,
    }
    _incidents.append(record)
    return {"ticket_id": record["ticket_id"], "created_at": record["created_at"]}


@app.get("/incidents")
async def list_incidents() -> list[dict]:
    return _incidents
