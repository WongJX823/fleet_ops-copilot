"""Guided incident diagnosis for the three pilot scenarios (Phase 3).

When a question describes an operational problem — vehicle breakdown, delay
handling, or an unavailable driver — the matching SOP's checklist is executed
against live data and the results are attached as a structured Evidence block.
The LLM then explains a recommendation it did NOT have to invent: every step
below is computed deterministically from the datastore, so the answer stays
grounded even for multi-step reasoning.

Diagnosis needs roster data, so it runs only for roles permitted to use
fleet_status (drivers get their driver-facing SOP steps via sop_search).
"""
import re
from datetime import datetime, timedelta, timezone

from ..models import Evidence
from ..tools.datastore import get_store

# Rough single-trip duty envelope used to test whether a standby driver's
# remaining shift can absorb the trip (mock data has no per-trip durations).
TRIP_DURATION_MIN = 90

_VEH_RE = re.compile(r"\bV-\d{3}\b", re.I)
_DRV_RE = re.compile(r"\bD-\d{2}\b", re.I)
_TRIP_RE = re.compile(r"\bT-\d{4}\b", re.I)
_ROUTE_RE = re.compile(r"\broute\s+(\d{1,3})\b", re.I)

_BREAKDOWN_WORDS = ("breakdown", "broke down", "broken down", "unserviceable", "won't start", "wont start", "check engine", "engine fault", "mechanical fault", "vehicle fault")
_DRIVER_WORDS = ("no-show", "no show", "driver unavailable", "driver is sick", "driver is ill", "driver called in", "driver cannot", "driver can't", "hours limit", "driver missing")
_DELAY_WORDS = ("delay", "delayed", "running late", "running behind", "congestion", "held up")


def detect_scenario(question: str) -> str | None:
    q = question.lower()
    if any(w in q for w in _BREAKDOWN_WORDS):
        return "breakdown"
    if any(w in q for w in _DRIVER_WORDS) or ("driver" in q and any(w in q for w in ("sick", "ill", "unavailable", "cover", "replace"))):
        return "driver_unavailable"
    if any(w in q for w in _DELAY_WORDS):
        return "delay"
    return None


def run(scenario: str, question: str) -> Evidence:
    store = get_store()
    flow = {"breakdown": _breakdown, "delay": _delay, "driver_unavailable": _driver_unavailable}[scenario]
    payload = flow(question, store)
    return Evidence(
        source=f"diagnosis:{scenario}",
        kind="diagnosis",
        observed_at=datetime.now(timezone.utc),
        fresh=True,
        summary=f"SOP-guided {scenario.replace('_', ' ')} checklist ({len(payload['steps'])} steps)",
        payload=payload,
    )


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _step(steps: list, name: str, ok: bool | None, result: str) -> None:
    steps.append({"step": name, "ok": ok, "result": result})


# ---------------------------------------------------------------- breakdown
def _breakdown(question: str, store) -> dict:
    steps: list[dict] = []

    mentioned = {v.upper() for v in _VEH_RE.findall(question)}
    affected = [v for v in store.vehicles if v["vehicle_id"] in mentioned]
    if not affected:
        affected = [v for v in store.vehicles if v["status"] == "maintenance"]
    affected_ids = {v["vehicle_id"] for v in affected}
    affected_trips = [
        t for t in store.trips
        if t.get("vehicle_id") in affected_ids and t["status"] != "cancelled"
    ]
    _step(
        steps, "Identify affected vehicle and trips", bool(affected),
        f"Affected: {', '.join(sorted(affected_ids)) or 'none identified'}; "
        f"upcoming trips at risk: {', '.join(t['trip_id'] for t in affected_trips) or 'none'}",
    )

    types_needed = {v["type"] for v in affected}
    replacements = [
        v for v in store.vehicles
        if v["status"] == "available" and (not types_needed or v["type"] in types_needed)
    ] or [v for v in store.vehicles if v["status"] == "available"]
    _step(
        steps, "Find available replacement vehicle (SOP-01 dispatch step 2)", bool(replacements),
        "; ".join(f"{v['vehicle_id']} ({v['type']}) at {v['location']}" for v in replacements) or "No available vehicle",
    )

    standby = _standby_drivers(store, affected_trips)
    _step(
        steps, "Check standby driver coverage (SOP-01 dispatch step 3)", bool(standby),
        "; ".join(f"{d['driver_id']} {d['name']} (shift ends {_dt(d['shift_end']).strftime('%H:%M')})" for d in standby) or "No standby driver with sufficient remaining shift",
    )

    if replacements:
        recommendation = (
            f"Assign {replacements[0]['vehicle_id']} from {replacements[0]['location']} to the affected trip(s); "
            "keep the rostered driver if they can continue, otherwise use a standby driver. "
            "Arrange recovery of the failed vehicle to the nearest workshop."
        )
        approval = "Same-depot replacement: dispatcher may approve. Cross-depot or trip cancellation: operations manager approval (SOP-01)."
    else:
        recommendation = (
            "No replacement available: if none can be sourced within 30 minutes, cancel the trip, "
            "notify affected connections, and open an incident (SOP-01 dispatch step 4)."
        )
        approval = "Trip cancellation requires operations manager approval (SOP-01)."

    return {
        "scenario": "breakdown",
        "sop": "SOP-01: Vehicle Breakdown in Service (vehicle_breakdown.md)",
        "steps": steps,
        "recommendation": recommendation,
        "approval_rule": approval,
    }


# -------------------------------------------------------------------- delay
def _delay(question: str, store) -> dict:
    steps: list[dict] = []
    routes = {m for m in _ROUTE_RE.findall(question)}
    delayed = [
        t for t in store.trips
        if t["status"] == "delayed" and (not routes or t["route"] in routes)
    ]
    _step(
        steps, "Identify delayed trips" + (f" on route(s) {', '.join(sorted(routes))}" if routes else ""), bool(delayed),
        "; ".join(f"{t['trip_id']} route {t['route']} +{t.get('delay_min', 0)}m" for t in delayed) or "No delayed trips in the current schedule window",
    )

    classified = []
    for t in delayed:
        mins = t.get("delay_min", 0)
        if mins < 10:
            action = "monitor only; no announcement required"
        elif mins < 30:
            action = "publish a delay notice at affected stops and update the trip status"
        else:
            action = "treat as a disruption: open an incident and consider a relief vehicle"
        classified.append(f"{t['trip_id']} (+{mins}m): {action}")
    _step(
        steps, "Apply SOP-02 thresholds (<10 monitor / 10-29 notice / >=30 disruption)",
        None if not delayed else True,
        "; ".join(classified) or "n/a",
    )

    severe = [t for t in delayed if t.get("delay_min", 0) >= 30]
    if severe:
        relief = [v for v in store.vehicles if v["status"] == "available"]
        _step(
            steps, "Relief vehicle availability for >=30m disruptions", bool(relief),
            "; ".join(f"{v['vehicle_id']} at {v['location']}" for v in relief[:4]) or "No relief vehicle available",
        )

    _step(
        steps, "Connection impact (SOP-02 dispatch step 3)", None,
        "Connection data is not integrated in the MVP; confirm guaranteed connections manually before announcing.",
    )

    recommendation = (
        "; ".join(classified)
        if classified
        else "No action needed now; continue monitoring and re-confirm any estimate older than 10 minutes before announcing (SOP-02)."
    )
    return {
        "scenario": "delay",
        "sop": "SOP-02: Delay Management (delay_management.md)",
        "steps": steps,
        "recommendation": recommendation,
        "approval_rule": "Delay notices: dispatcher may publish. Announcements must include route, direction, expected delay, and timestamp (SOP-02).",
    }


# --------------------------------------------------------- driver unavailable
def _driver_unavailable(question: str, store) -> dict:
    steps: list[dict] = []
    mentioned = {d.upper() for d in _DRV_RE.findall(question)}
    trip_mentions = {t.upper() for t in _TRIP_RE.findall(question)}

    affected_trips = [
        t for t in store.trips
        if t["status"] != "cancelled"
        and (t.get("driver_id") in mentioned or t["trip_id"] in trip_mentions)
    ]
    _step(
        steps, "Identify affected driver / trips", bool(mentioned or trip_mentions),
        f"Driver(s): {', '.join(sorted(mentioned)) or 'not specified'}; "
        f"affected trips: {', '.join(t['trip_id'] for t in affected_trips) or 'none found in current window'}",
    )

    standby = _standby_drivers(store, affected_trips)
    _step(
        steps, "Find standby driver whose shift covers the trip (SOP-03 step 2)", bool(standby),
        "; ".join(f"{d['driver_id']} {d['name']} (shift ends {_dt(d['shift_end']).strftime('%H:%M')})" for d in standby) or "No standby driver with sufficient remaining shift",
    )

    _step(
        steps, "Hours-of-service guardrail (SOP-03)", None,
        f"Assignment must not exceed the daily duty limit; roster data here does not include duty hours, "
        f"so confirm before assigning (assumed trip duration {TRIP_DURATION_MIN}m).",
    )

    if standby:
        recommendation = (
            f"Reassign to standby driver {standby[0]['driver_id']} ({standby[0]['name']}) and confirm acknowledgement. "
            "Dispatcher may approve a standby reassignment (SOP-03)."
        )
        approval = "Standby reassignment: dispatcher approval sufficient (SOP-03)."
    else:
        recommendation = (
            "No standby cover: check whether a later trip's driver can swap without breaking hours-of-service; "
            "as a last resort cancel the lowest-priority trip and notify passengers per SOP-02."
        )
        approval = "Trip cancellation or duty-limit exception: operations manager approval, documented in the incident record (SOP-03)."

    return {
        "scenario": "driver_unavailable",
        "sop": "SOP-03: Driver Unavailable (driver_unavailable.md)",
        "steps": steps,
        "recommendation": recommendation,
        "approval_rule": approval,
    }


def _standby_drivers(store, affected_trips: list[dict]) -> list[dict]:
    """Standby drivers whose remaining shift covers the affected departure + duration."""
    latest_needed = None
    departures = [_dt(t["departure"]) for t in affected_trips if t.get("departure")]
    if departures:
        latest_needed = max(departures) + timedelta(minutes=TRIP_DURATION_MIN)
    result = []
    for d in store.drivers:
        if d["status"] != "standby":
            continue
        if latest_needed and _dt(d["shift_end"]) < latest_needed:
            continue
        result.append(d)
    return result
