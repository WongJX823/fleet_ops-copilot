"""Typed, allow-listed read tools (FR-03). The LLM never touches data sources
directly; the orchestrator calls these and each returns records + Evidence."""
from datetime import datetime, timezone

from ..config import FRESHNESS_LIMIT_MINUTES
from ..models import Evidence
from ..rag.index import SopIndex
from .datastore import get_store


def _evidence(source: str, kind: str, observed_at: datetime, summary: str, payload) -> Evidence:
    age_min = (datetime.now(timezone.utc) - observed_at).total_seconds() / 60
    return Evidence(
        source=source,
        kind=kind,
        observed_at=observed_at,
        fresh=age_min <= FRESHNESS_LIMIT_MINUTES,
        summary=summary,
        payload=payload,
    )


def schedule_lookup(query: str, role: str) -> Evidence:
    store = get_store()
    q = query.lower()
    trips = [t for t in store.trips if t["route"] in _route_mentions(q)] or store.trips
    return _evidence(
        source="schedule_service",
        kind="live",
        observed_at=store.schedule_observed_at,
        summary=f"{len(trips)} trip(s) from the schedule service",
        payload={"trips": trips},
    )


def fleet_status(query: str, role: str) -> Evidence:
    store = get_store()
    payload = {"vehicles": store.vehicles, "drivers": store.drivers}
    if role == "driver":
        # Least privilege: drivers see vehicle status but not other drivers' roster details.
        payload = {"vehicles": store.vehicles}
    return _evidence(
        source="fleet_service",
        kind="live",
        observed_at=store.fleet_observed_at,
        summary=f"{len(store.vehicles)} vehicles"
        + ("" if role == "driver" else f", {len(store.drivers)} drivers"),
        payload=payload,
    )


def make_sop_search(index: SopIndex):
    def sop_search(query: str, role: str) -> Evidence:
        results = index.search(query, k=4)
        passages = [
            {"doc": c.doc, "section": c.heading, "score": round(s, 3), "text": c.text}
            for c, s in results
        ]
        docs = sorted({c.doc for c, _ in results})
        return _evidence(
            source="sop_index:" + ",".join(docs) if docs else "sop_index",
            kind="document",
            observed_at=datetime.now(timezone.utc),
            summary=f"{len(passages)} SOP passage(s) via {index.mode} search",
            payload={"passages": passages},
        )

    return sop_search


def _route_mentions(q: str) -> set[str]:
    """Route numbers mentioned in the question, e.g. 'route 18' -> {'18'}."""
    import re

    return set(re.findall(r"\b(\d{1,3})\b", q))
