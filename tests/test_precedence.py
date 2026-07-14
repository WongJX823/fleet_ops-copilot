"""Unit tests for cross-source conflict detection + precedence (FR-05).

Uses controlled Evidence fixtures, same philosophy as test_confidence.py:
independent of live schedule/fleet data so these are precise and independent
of day_sim's daily simulation.
"""
from datetime import datetime, timezone

from app.agent import precedence
from app.models import Evidence

NOW = datetime.now(timezone.utc)


def _schedule(trips: list[dict]) -> Evidence:
    return Evidence(
        source="schedule_service", kind="live", observed_at=NOW, fresh=True,
        summary="s", payload={"trips": trips},
    )


def _fleet(vehicles: list[dict] | None = None, drivers: list[dict] | None = None) -> Evidence:
    return Evidence(
        source="fleet_service", kind="live", observed_at=NOW, fresh=True,
        summary="f", payload={"vehicles": vehicles or [], "drivers": drivers or []},
    )


def test_no_conflict_when_sources_agree():
    schedule = _schedule([{"trip_id": "T-1", "route": "12", "status": "on_time", "vehicle_id": "V-1", "driver_id": "D-1"}])
    fleet = _fleet(
        vehicles=[{"vehicle_id": "V-1", "status": "in_service"}],
        drivers=[{"driver_id": "D-1", "status": "on_duty"}],
    )
    assert precedence.detect_conflicts([schedule, fleet]) == []


def test_conflict_when_active_trip_uses_maintenance_vehicle():
    schedule = _schedule([{"trip_id": "T-1", "route": "12", "status": "delayed", "vehicle_id": "V-1", "driver_id": "D-1"}])
    fleet = _fleet(
        vehicles=[{"vehicle_id": "V-1", "status": "maintenance"}],
        drivers=[{"driver_id": "D-1", "status": "on_duty"}],
    )
    notes = precedence.detect_conflicts([schedule, fleet])
    assert len(notes) == 1
    assert "T-1" in notes[0] and "V-1" in notes[0] and "fleet_service takes precedence" in notes[0]


def test_conflict_when_active_trip_uses_off_duty_driver():
    schedule = _schedule([{"trip_id": "T-1", "route": "12", "status": "on_time", "vehicle_id": "V-1", "driver_id": "D-1"}])
    fleet = _fleet(
        vehicles=[{"vehicle_id": "V-1", "status": "in_service"}],
        drivers=[{"driver_id": "D-1", "status": "off_duty"}],
    )
    notes = precedence.detect_conflicts([schedule, fleet])
    assert len(notes) == 1
    assert "D-1" in notes[0]


def test_both_vehicle_and_driver_conflicts_reported_separately():
    schedule = _schedule([{"trip_id": "T-1", "route": "12", "status": "on_time", "vehicle_id": "V-1", "driver_id": "D-1"}])
    fleet = _fleet(
        vehicles=[{"vehicle_id": "V-1", "status": "maintenance"}],
        drivers=[{"driver_id": "D-1", "status": "off_duty"}],
    )
    notes = precedence.detect_conflicts([schedule, fleet])
    assert len(notes) == 2


def test_cancelled_trip_on_maintenance_vehicle_is_not_a_conflict():
    """A cancelled trip already accounts for the vehicle being unavailable."""
    schedule = _schedule([{"trip_id": "T-1", "route": "12", "status": "cancelled", "vehicle_id": "V-1", "driver_id": "D-1"}])
    fleet = _fleet(vehicles=[{"vehicle_id": "V-1", "status": "maintenance"}])
    assert precedence.detect_conflicts([schedule, fleet]) == []


def test_missing_fleet_evidence_returns_no_conflicts():
    """No fleet_service evidence -> nothing to cross-check against (e.g. driver role)."""
    schedule = _schedule([{"trip_id": "T-1", "route": "12", "status": "on_time", "vehicle_id": "V-1", "driver_id": "D-1"}])
    assert precedence.detect_conflicts([schedule]) == []


def test_missing_schedule_evidence_returns_no_conflicts():
    fleet = _fleet(vehicles=[{"vehicle_id": "V-1", "status": "maintenance"}])
    assert precedence.detect_conflicts([fleet]) == []
