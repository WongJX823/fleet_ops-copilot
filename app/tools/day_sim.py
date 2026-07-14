"""Day-over-day mock data simulator, so the read-only demo isn't frozen.

Each simulated calendar day, trips/vehicles/drivers are rerolled from the
*previous* day's state rather than reset to the static baseline in
data/schedule.json / data/fleet.json — a vehicle that broke down yesterday
might be back in service today, a driver who was off duty might be on
standby, and trips get freshly rolled statuses. State persists in
runtime/daily_state.json so a restart on the same calendar day reuses it,
and a restart on a later day catches up one simulated day at a time (no
manual trigger needed — see README for how to force a re-seed on demand).

Delay/cancellation/maintenance reason text comes from gpt-4o-mini when
OPENAI_API_KEY is set (one batched call per simulated day), falling back to
a curated reason pool otherwise — keeps startup offline/test-safe, matching
the rest of the app's offline-first design.
"""
import json
import random
from datetime import date, timedelta

from ..config import DATA_DIR, OPENAI_API_KEY, OPENAI_CHAT_MODEL, RUNTIME_DIR

STATE_FILE = RUNTIME_DIR / "daily_state.json"

VEHICLE_LOCATIONS = ["Central Depot", "Harbour Terminal", "Airport West", "North Yard", "City Mall"]
WORKSHOP_LOCATIONS = ["Central Depot workshop", "North Yard workshop"]
DRIVER_STATUSES = ["on_duty", "standby", "off_duty"]

FALLBACK_DELAY_REASONS = [
    "road works on the route",
    "heavy traffic near the city centre",
    "signal fault caused a short hold",
    "waiting on a connecting passenger transfer",
    "minor mechanical check before departure",
    "heavy rain slowed the approach",
]
FALLBACK_CANCEL_REASONS = [
    "vehicle breakdown, replacement pending",
    "driver unavailable, no standby cover found",
    "route closed for scheduled roadworks",
    "low passenger demand, consolidated with next departure",
]
FALLBACK_MAINTENANCE_NOTES = [
    "engine fault, ETA repair 6h",
    "brake inspection required",
    "scheduled service overdue",
    "tyre replacement in progress",
    "electrical fault under diagnosis",
]


def ensure_current_day() -> dict:
    """Return today's simulated schedule/fleet state, catching up one
    simulated day at a time if the process hasn't run since an earlier day."""
    state = _load_state()
    if state is None:
        state = _seed_state()
    today = date.today().isoformat()
    while state["date"] < today:
        state = _simulate_next_day(state)
        _save_state(state)
    return state


def _load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _seed_state() -> dict:
    """First run: tag the static baseline as 'yesterday' so the very first
    request already gets one simulated (and therefore varied) day."""
    sched = json.loads((DATA_DIR / "schedule.json").read_text(encoding="utf-8"))
    fleet = json.loads((DATA_DIR / "fleet.json").read_text(encoding="utf-8"))
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return {
        "date": yesterday,
        "trips": sched["trips"],
        "vehicles": fleet["vehicles"],
        "drivers": fleet["drivers"],
    }


def _simulate_next_day(prev: dict) -> dict:
    next_date = (date.fromisoformat(prev["date"]) + timedelta(days=1)).isoformat()
    rng = random.Random(next_date)  # deterministic fallback content per calendar date

    vehicles = [_evolve_vehicle(dict(v), rng) for v in prev["vehicles"]]
    drivers = [_evolve_driver(dict(d), rng) for d in prev["drivers"]]
    trips = [_evolve_trip(dict(t), rng) for t in prev["trips"]]

    reasons = _generate_reasons(_collect_reason_needs(trips, vehicles), rng)
    _apply_reasons(trips, vehicles, reasons)

    available_vehicles = [v["vehicle_id"] for v in vehicles if v["status"] != "maintenance"]
    active_drivers = [d["driver_id"] for d in drivers if d["status"] in ("on_duty", "standby")]
    for t in trips:
        if available_vehicles:
            t["vehicle_id"] = rng.choice(available_vehicles)
        if active_drivers:
            t["driver_id"] = rng.choice(active_drivers)

    return {"date": next_date, "trips": trips, "vehicles": vehicles, "drivers": drivers}


def _evolve_vehicle(v: dict, rng: random.Random) -> dict:
    v.pop("note", None)
    if v["status"] == "maintenance":
        if rng.random() < 0.55:  # repaired
            v["status"] = rng.choice(["available", "in_service"])
            v["location"] = rng.choice(VEHICLE_LOCATIONS)
        # else: stays in maintenance; note re-applied by _apply_reasons
    elif rng.random() < 0.12:  # breaks down
        v["status"] = "maintenance"
        v["location"] = rng.choice(WORKSHOP_LOCATIONS)
    elif rng.random() < 0.25:  # shuffle around while active
        v["status"] = rng.choice(["in_service", "available"])
        v["location"] = rng.choice(VEHICLE_LOCATIONS)
    return v


def _evolve_driver(d: dict, rng: random.Random) -> dict:
    status = rng.choices(DRIVER_STATUSES, weights=[0.6, 0.2, 0.2])[0]
    d["status"] = status
    d["shift_end_offset_min"] = {
        "on_duty": rng.randint(120, 480),
        "standby": rng.randint(60, 360),
        "off_duty": 0,
    }[status]
    return d


def _evolve_trip(t: dict, rng: random.Random) -> dict:
    t.pop("delay_reason", None)
    t.pop("cancel_reason", None)
    status = rng.choices(["on_time", "delayed", "cancelled"], weights=[0.65, 0.20, 0.15])[0]
    t["status"] = status
    t["delay_min"] = rng.choice([10, 15, 20, 25, 30, 45]) if status == "delayed" else 0
    t["depart_offset_min"] = rng.randint(10, 150)
    return t


def _collect_reason_needs(trips: list[dict], vehicles: list[dict]) -> list[dict]:
    # Keyed by the bare trip_id/vehicle_id (not a composite string) because
    # LLMs reliably echo an ID they were just shown but tend to "simplify"
    # composite keys like "trip:T-1201:delay" back down to "T-1201" — using
    # the bare ID as the key works *with* that tendency instead of against
    # it. No collision risk: trip IDs start with "T-", vehicle IDs with
    # "V-", and a trip is never simultaneously delayed and cancelled.
    needs = []
    for t in trips:
        if t["status"] == "delayed":
            needs.append(
                {
                    "key": t["trip_id"],
                    "kind": "delay",
                    "context": f"route {t['route']} trip from {t['origin']} to {t['destination']}",
                }
            )
        elif t["status"] == "cancelled":
            needs.append(
                {
                    "key": t["trip_id"],
                    "kind": "cancel",
                    "context": f"route {t['route']} trip from {t['origin']} to {t['destination']}",
                }
            )
    for v in vehicles:
        if v["status"] == "maintenance":
            needs.append(
                {
                    "key": v["vehicle_id"],
                    "kind": "maintenance",
                    "context": f"{v['type']} at {v['location']}",
                }
            )
    return needs


def _generate_reasons(needs: list[dict], rng: random.Random) -> dict[str, str]:
    if not needs:
        return {}
    if OPENAI_API_KEY:
        try:
            return _llm_generate_reasons(needs)
        except Exception:
            pass  # degrade to the fallback pool rather than failing startup
    pools = {
        "delay": FALLBACK_DELAY_REASONS,
        "cancel": FALLBACK_CANCEL_REASONS,
        "maintenance": FALLBACK_MAINTENANCE_NOTES,
    }
    return {n["key"]: rng.choice(pools[n["kind"]]) for n in needs}


def _llm_generate_reasons(needs: list[dict]) -> dict[str, str]:
    from openai import OpenAI

    client = OpenAI()
    listing = "\n".join(f"- {n['key']} ({n['kind']}): {n['context']}" for n in needs)
    resp = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You write short, realistic operational reason text for a bus/coach "
                    "transportation operations dashboard. For each item below, write a "
                    "plausible one-line reason (under 12 words, no trailing period) for "
                    "why the trip is delayed/cancelled or the vehicle is in maintenance. "
                    'Reply with JSON only: {"<id>": "<reason>", ...} — one entry per item, '
                    "using the trip/vehicle ID exactly as given (e.g. T-1201 or V-105) as "
                    "the JSON key, covering every ID listed."
                ),
            },
            {"role": "user", "content": listing},
        ],
        temperature=0.9,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return {n["key"]: data[n["key"]] for n in needs if n["key"] in data}


def _apply_reasons(trips: list[dict], vehicles: list[dict], reasons: dict[str, str]) -> None:
    for t in trips:
        if t["status"] == "delayed":
            t["delay_reason"] = reasons.get(t["trip_id"], "operational delay")
        elif t["status"] == "cancelled":
            t["cancel_reason"] = reasons.get(t["trip_id"], "operational cancellation")
    for v in vehicles:
        if v["status"] == "maintenance":
            v["note"] = reasons.get(v["vehicle_id"], "under maintenance")
