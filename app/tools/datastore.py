"""Mock 'live' systems backing the read tools.

Loads data/*.json once and converts relative minute offsets to real datetimes,
so the demo always has upcoming departures. Every record carries observed_at
so freshness can be checked downstream (FR-02, FR-05). Swap this module for
real API clients in later phases.
"""
import json
from datetime import datetime, timedelta, timezone

from ..config import DATA_DIR


class DataStore:
    def __init__(self) -> None:
        self.loaded_at = datetime.now(timezone.utc)
        sched = json.loads((DATA_DIR / "schedule.json").read_text(encoding="utf-8"))
        fleet = json.loads((DATA_DIR / "fleet.json").read_text(encoding="utf-8"))

        self.trips = []
        for t in sched["trips"]:
            t = dict(t)
            t["departure"] = (self.loaded_at + timedelta(minutes=t.pop("depart_offset_min"))).isoformat()
            self.trips.append(t)

        self.vehicles = list(fleet["vehicles"])
        self.drivers = []
        for d in fleet["drivers"]:
            d = dict(d)
            d["shift_end"] = (self.loaded_at + timedelta(minutes=d.pop("shift_end_offset_min"))).isoformat()
            self.drivers.append(d)


_store: DataStore | None = None


def get_store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store
