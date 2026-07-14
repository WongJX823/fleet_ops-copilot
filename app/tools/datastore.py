"""DataStore: facade over the source-system connectors.

Callers (tools, diagnosis, overview) keep the same attribute API as before —
trips / vehicles / drivers / schedule_observed_at / fleet_observed_at /
loaded_at / sim_date — but each source now comes from a connector selected by
config: the local day-simulation by default, or a real HTTP API when its
*_API_URL is set (app/connectors/).

Snapshots are cached per connector TTL (mock: process lifetime; HTTP: ~30s).
On connector failure the store degrades gracefully instead of raising: it
keeps serving the last snapshot (whose observed_at then fails the freshness
check, so answers escalate per FR-05/FR-08) or an empty one if the source
never succeeded, and records the error for the health endpoint.
"""
from datetime import datetime, timezone

from .. import connectors
from ..connectors.base import ConnectorError

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class DataStore:
    def __init__(self, schedule=None, fleet=None) -> None:
        self.loaded_at = datetime.now(timezone.utc)
        self._schedule = schedule if schedule is not None else connectors.schedule_connector()
        self._fleet = fleet if fleet is not None else connectors.fleet_connector()
        self._snap: dict[str, dict] = {}

    # ------------------------------------------------------------- snapshots
    def _snapshot(self, name: str, conn, empty) -> dict:
        cached = self._snap.get(name)
        now = datetime.now(timezone.utc)
        ttl = getattr(conn, "ttl", None)
        if cached and cached["error"] is None and (
            ttl is None or (now - cached["fetched_at"]).total_seconds() < ttl
        ):
            return cached
        try:
            data, observed = conn.fetch()
            cached = {"data": data, "observed_at": observed, "fetched_at": now, "error": None}
        except ConnectorError as e:
            # Graceful degradation: keep the last good snapshot (now visibly
            # stale) rather than failing the request outright.
            previous = cached or {"data": empty, "observed_at": _EPOCH}
            cached = {
                "data": previous["data"],
                "observed_at": previous["observed_at"],
                "fetched_at": now,
                "error": str(e),
            }
        self._snap[name] = cached
        return cached

    def _schedule_snap(self) -> dict:
        return self._snapshot("schedule", self._schedule, empty=[])

    def _fleet_snap(self) -> dict:
        return self._snapshot("fleet", self._fleet, empty={"vehicles": [], "drivers": []})

    # ------------------------------------------------------------ facade API
    @property
    def trips(self) -> list[dict]:
        return self._schedule_snap()["data"]

    @property
    def schedule_observed_at(self) -> datetime:
        return self._schedule_snap()["observed_at"]

    @property
    def vehicles(self) -> list[dict]:
        return self._fleet_snap()["data"]["vehicles"]

    @property
    def drivers(self) -> list[dict]:
        return self._fleet_snap()["data"]["drivers"]

    @property
    def fleet_observed_at(self) -> datetime:
        return self._fleet_snap()["observed_at"]

    @property
    def sim_date(self) -> str:
        self._schedule_snap()  # ensure the mock connector has loaded its day
        return getattr(self._schedule, "sim_date", None) or "live"

    def connector_status(self) -> dict:
        """For /api/health: mode and last error per source."""
        status = {}
        for name, conn, snap in (
            ("schedule", self._schedule, self._snap.get("schedule")),
            ("fleet", self._fleet, self._snap.get("fleet")),
        ):
            status[name] = {"mode": conn.mode, "error": snap["error"] if snap else None}
        return status


_store: DataStore | None = None


def get_store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store
