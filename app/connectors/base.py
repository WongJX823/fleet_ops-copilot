"""Connector contracts for the three source systems (report Section 9).

A connector hides WHERE operational data comes from; the rest of the app only
sees the DataStore facade. Two families exist:

- mock.py: the day-over-day local simulation (default, zero config)
- http.py: real REST APIs, activated by setting *_API_URL in the environment

Contract:
- ScheduleConnector.fetch()  -> (trips: list[dict], observed_at: datetime)
- FleetConnector.fetch()     -> ({"vehicles": [...], "drivers": [...]}, observed_at)
- IncidentConnector.create_ticket(ticket: dict) -> receipt dict (ticket_id, created_at)

Connectors raise ConnectorError on failure; the DataStore degrades gracefully
(keeps the last snapshot, which then fails the freshness check and escalates)
rather than crashing the request (report Section 11, availability).

`ttl` is how long a fetch result may be cached in seconds; None means the
snapshot never expires within the process (the mock simulation is stable for
the whole day, so refetching would only churn).
"""


class ConnectorError(RuntimeError):
    """A source system could not be reached or returned an invalid response."""
