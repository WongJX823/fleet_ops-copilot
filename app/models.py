"""Pydantic schemas shared across the pipeline."""
from datetime import datetime

from pydantic import BaseModel


class Evidence(BaseModel):
    """One retrieved fact with provenance, per the report's grounding rule (FR-02)."""

    source: str          # e.g. "schedule_service", "sop:vehicle_breakdown.md"
    kind: str            # "live" | "document"
    observed_at: datetime
    fresh: bool
    summary: str         # short human-readable description shown in the UI
    payload: dict | str  # the actual records / chunk text given to the LLM


class ChatResponse(BaseModel):
    answer: str
    intent: list[str]
    role: str
    evidence: list[Evidence]
    model: str
    confidence: float = 1.0
    escalated: bool = False
    notes: list[str] = []
