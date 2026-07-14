"""Cross-source conflict detection + source precedence (FR-05).

Schedule and fleet data both describe the same trips, vehicles, and drivers,
but they come from separate governed tools that can fall out of sync -- e.g.
the schedule hasn't caught up with a vehicle going into maintenance mid-shift.
When they disagree about whether an actively-scheduled trip can actually run,
fleet_service -- closer to the vehicle/driver's real-world state -- takes
precedence, and the disagreement itself is surfaced rather than silently
resolved by whichever tool happened to answer first.
"""
from ..models import Evidence

ACTIVE_STATUSES = {"on_time", "delayed"}


def detect_conflicts(evidence: list[Evidence]) -> list[str]:
    schedule = next((e for e in evidence if e.source == "schedule_service"), None)
    fleet = next((e for e in evidence if e.source == "fleet_service"), None)
    if schedule is None or fleet is None:
        return []

    vehicle_status = {v["vehicle_id"]: v["status"] for v in fleet.payload.get("vehicles", [])}
    driver_status = {d["driver_id"]: d["status"] for d in fleet.payload.get("drivers", [])}

    notes: list[str] = []
    for t in schedule.payload.get("trips", []):
        if t["status"] not in ACTIVE_STATUSES:
            continue
        if vehicle_status.get(t["vehicle_id"]) == "maintenance":
            notes.append(
                f"Conflict: trip {t['trip_id']} (route {t['route']}) is marked "
                f"'{t['status']}' by schedule_service, but its assigned vehicle "
                f"{t['vehicle_id']} is in maintenance per fleet_service. "
                "fleet_service takes precedence for vehicle-availability facts -- "
                "treat this trip's departure as unconfirmed pending reassignment."
            )
        if driver_status.get(t.get("driver_id")) == "off_duty":
            notes.append(
                f"Conflict: trip {t['trip_id']} (route {t['route']}) is marked "
                f"'{t['status']}' by schedule_service, but its assigned driver "
                f"{t['driver_id']} is off_duty per fleet_service. "
                "fleet_service takes precedence for driver-availability facts -- "
                "treat this trip's departure as unconfirmed pending reassignment."
            )
    return notes
