"""Unit tests for the confidence-scoring mechanism (FR-08) behind escalation.

Uses controlled Evidence fixtures rather than live keyword/SOP search
results, so these are precise and independent of how the SOP index scores
any particular question -- test_evaluation.py covers the end-to-end
escalation behavior through the real pipeline; this file covers the scoring
formula itself.
"""
from datetime import datetime, timezone

from app.agent import confidence
from app.models import Evidence

NOW = datetime.now(timezone.utc)


def _evidence(fresh: bool = True, payload: dict | None = None) -> Evidence:
    return Evidence(
        source="test_source",
        kind="live",
        observed_at=NOW,
        fresh=fresh,
        summary="test",
        payload=payload if payload is not None else {"items": ["x"]},
    )


def test_full_coverage_fresh_grounded_llm_mode_scores_near_one():
    allowed = {"schedule_lookup", "fleet_status"}
    intents = ["schedule_lookup", "fleet_status"]
    evidence = [_evidence(), _evidence()]
    assert confidence.score(evidence, intents, allowed, "llm") == 1.0


def test_keyword_mode_is_capped_slightly_below_full_confidence():
    allowed = {"schedule_lookup"}
    intents = ["schedule_lookup"]
    evidence = [_evidence()]
    score = confidence.score(evidence, intents, allowed, "keyword")
    assert 0.0 < score < 1.0


def test_partial_coverage_reduces_score():
    """Two permitted tools attempted, only one returned evidence."""
    allowed = {"schedule_lookup", "fleet_status"}
    intents = ["schedule_lookup", "fleet_status"]
    evidence = [_evidence()]  # only one of the two attempted tools came back
    score = confidence.score(evidence, intents, allowed, "llm")
    assert score == 0.5


def test_empty_grounding_reduces_score_even_when_fresh():
    """Tool ran and returned an Evidence object, but with no actual content
    (e.g. sop_search finding zero matching passages)."""
    allowed = {"sop_search"}
    intents = ["sop_search"]
    evidence = [_evidence(payload={"passages": []})]
    score = confidence.score(evidence, intents, allowed, "llm")
    assert score == 0.0


def test_stale_evidence_reduces_score():
    allowed = {"schedule_lookup"}
    intents = ["schedule_lookup"]
    evidence = [_evidence(fresh=False)]
    score = confidence.score(evidence, intents, allowed, "llm")
    assert score == 0.0


def test_role_restricted_intent_not_permitted_at_all_does_not_count_against_score():
    """A keyword-matched intent the role isn't permitted to use at all must
    not drag confidence down -- that's an expected, well-explained scoping
    gap (its own note), not evidence of a weak answer to what the role
    *could* be answered from."""
    allowed = {"sop_search"}  # e.g. a driver
    intents = ["fleet_status", "sop_search"]  # fleet_status matched by keyword overlap only
    evidence = [_evidence()]  # sop_search succeeded fully
    score = confidence.score(evidence, intents, allowed, "keyword")
    assert score == 0.9  # full marks on the one tool we could actually use, minus keyword-mode cap


def test_no_permitted_tools_attempted_scores_zero():
    allowed = {"sop_search"}
    intents = ["fleet_status"]  # the only matched intent isn't permitted at all
    score = confidence.score([], intents, allowed, "llm")
    assert score == 0.0


def test_score_is_clamped_to_zero_one_range():
    allowed = {"schedule_lookup"}
    intents = ["schedule_lookup"]
    assert 0.0 <= confidence.score([_evidence()], intents, allowed, "llm") <= 1.0
    assert 0.0 <= confidence.score([], intents, allowed, "llm") <= 1.0
