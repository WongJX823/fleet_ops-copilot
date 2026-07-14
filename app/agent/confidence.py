"""Confidence scoring and threshold-based escalation (FR-08).

Combines three independent signals into a single 0.0-1.0 score:

- coverage:   of the tools we were actually permitted to use, how many
              returned evidence? Deliberately measured against *permitted*
              tools, not every keyword-matched intent -- a role-restricted
              tool being skipped is an expected, well-handled case (it gets
              its own explanatory note), not evidence of a weak answer. A
              keyword classifier can false-positive-match an intent the
              role can't use at all (e.g. "vehicle" in a pure SOP question
              also matches fleet_status) without that ever counting against
              confidence.
- freshness:  fraction of returned evidence that is still within the
              freshness window (FR-05).
- grounding:  fraction of returned evidence that actually contains data.
              schedule_lookup/fleet_status always return something (they
              fall back to the full set rather than an empty filter), so in
              practice this only bites sop_search returning zero passages
              -- a real "we found nothing relevant" signal.

Intent-classifier mode also applies a small multiplier: the offline keyword
fallback is a weaker signal than the LLM classifier, so it caps confidence
slightly below what an otherwise-perfect keyword-mode answer would get.
"""
from ..models import Evidence

INTENT_MODE_FACTOR = {"llm": 1.0, "keyword": 0.9}


def score(evidence: list[Evidence], intents: list[str], allowed: set[str], intent_mode: str) -> float:
    attempted = [t for t in intents if t in allowed]
    if not attempted:
        return 0.0

    coverage = len(evidence) / len(attempted)
    if evidence:
        freshness = sum(1 for e in evidence if e.fresh) / len(evidence)
        grounding = sum(1 for e in evidence if _has_content(e)) / len(evidence)
    else:
        freshness = 0.0
        grounding = 0.0

    mode_factor = INTENT_MODE_FACTOR.get(intent_mode, 0.9)
    confidence = coverage * freshness * grounding * mode_factor
    return round(min(1.0, max(0.0, confidence)), 2)


def _has_content(e: Evidence) -> bool:
    p = e.payload
    if isinstance(p, str):
        return bool(p.strip())
    if isinstance(p, dict):
        return any(bool(v) for v in p.values())
    return bool(p)
