"""Connector factories: mock by default, HTTP when a *_API_URL is configured."""
from ..config import FLEET_API_URL, INCIDENT_API_URL, SCHEDULE_API_URL
from .base import ConnectorError  # re-export for callers
from .http import HTTPFleetConnector, HTTPIncidentConnector, HTTPScheduleConnector
from .mock import MockFleetConnector, MockIncidentConnector, MockScheduleConnector

__all__ = [
    "ConnectorError",
    "schedule_connector",
    "fleet_connector",
    "incident_connector",
]


def schedule_connector():
    return HTTPScheduleConnector(SCHEDULE_API_URL) if SCHEDULE_API_URL else MockScheduleConnector()


def fleet_connector():
    return HTTPFleetConnector(FLEET_API_URL) if FLEET_API_URL else MockFleetConnector()


def incident_connector():
    return HTTPIncidentConnector(INCIDENT_API_URL) if INCIDENT_API_URL else MockIncidentConnector()
