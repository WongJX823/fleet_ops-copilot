"""Mock 'live' systems backing the read tools.

Loads today's simulated schedule/fleet snapshot (see day_sim.py — each
calendar day's data evolves from the previous day rather than resetting to
the static baseline) and converts relative minute offsets to real datetimes,
so the demo always has upcoming departures. Every record carries observed_at
so freshness can be checked downstream (FR-02, FR-05). Swap this module for
real API clients in later phases.
"""
from datetime import datetime, timedelta, timezone

from . import day_sim


class DataStore:
    def __init__(self) -> None:
        self.loaded_at = datetime.now(timezone.utc)
        state = day_sim.ensure_current_day()
        self.sim_date = state["date"]

        self.trips = []
        for t in state["trips"]:
            t = dict(t)
            t["departure"] = (self.loaded_at + timedelta(minutes=t.pop("depart_offset_min"))).isoformat()
            self.trips.append(t)

        self.vehicles = list(state["vehicles"])
        self.drivers = []
        for d in state["drivers"]:
            d = dict(d)
            d["shift_end"] = (self.loaded_at + timedelta(minutes=d.pop("shift_end_offset_min"))).isoformat()
            self.drivers.append(d)


_store: DataStore | None = None


def get_store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store
