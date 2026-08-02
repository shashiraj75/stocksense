"""
Adversarial anti-fabrication tests — Trade Postmortem Engine, Sprint 1.

Runs `build_evidence_attribution` across a matrix of synthetic inputs
(every Outcome x with/without an entry snapshot x every exit mechanism)
and searches every produced claim's text for phrases that would assert
unsupported causality, invented market/sector/news facts, or outcome-
circular signal reasoning. A single occurrence anywhere fails the test —
this is deliberately a blunt, wide net rather than a narrow one.
"""
import datetime as dt
import itertools

import pytest

from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
from services.postmortem.entry_snapshot import RecommendationContext, build_entry_snapshot
from services.postmortem.evidence_attribution import build_evidence_attribution

UTC = dt.timezone.utc

_FORBIDDEN_PHRASES = [
    "caused the price to",
    "the signal worked because the trade won",
    "the signal failed because the trade lost",
    "the market caused",
    "proves",
    "guaranteed",
    "definitely caused",
    "we know that",
    "news broke",
    "earnings report",
    "fed announcement",
    "sector rotation",
    "increased volatility caused",
    "low liquidity caused",
]

_EXIT_REASONS = ["MANUAL", "STOP_LOSS", "TARGET_HIT", "SOMETHING_UNRECOGNIZED", None]
_EXIT_PRICES = [80.0, 100.0, 120.0]
_SIGNAL_COMBOS = [
    {},
    {"technical_signal": "BUY"},
    {"technical_signal": "SELL", "recommendation_signal": "BUY"},
    {"technical_signal": "BUY", "fundamental_score": 90.0, "sentiment_score": 10.0},
]


def _closed_trade(*, exit_reason, exit_price, trade_id):
    return ClosedTradeRecord(
        trade_id=trade_id, status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=100.0, exit_price=exit_price, stop_loss=90.0, target_price=130.0,
        opened_at=dt.datetime(2026, 6, 1, tzinfo=UTC), closed_at=dt.datetime(2026, 6, 2, tzinfo=UTC),
        trade_management_mode="manual", exit_reason=exit_reason,
    )


def _all_results():
    trade_id = 0
    for exit_reason, exit_price, signals in itertools.product(_EXIT_REASONS, _EXIT_PRICES, _SIGNAL_COMBOS):
        trade_id += 1
        record = _closed_trade(exit_reason=exit_reason, exit_price=exit_price, trade_id=trade_id)
        pm = compute_postmortem(record)
        snapshot = None
        if signals:
            ctx = RecommendationContext(evidence_source="DAILY_PICK", simulated_execution_price=100.0, **signals)
            snapshot = build_entry_snapshot(paper_trade_id=trade_id, user_id="u", symbol="AAPL", market="US", ctx=ctx)
        yield build_evidence_attribution(pm, snapshot)


@pytest.mark.regression
def test_no_forbidden_unsupported_phrases_anywhere_in_claim_text():
    violations = []
    for result in _all_results():
        for claim in result.claims:
            lowered = claim.claim_text.lower()
            for phrase in _FORBIDDEN_PHRASES:
                if phrase in lowered:
                    violations.append((claim.claim_id, phrase, claim.claim_text))
    assert violations == [], f"forbidden phrase(s) found: {violations}"


@pytest.mark.regression
def test_no_fabricated_news_or_sector_facts_in_any_claim():
    """A stronger, more specific check than the generic phrase list:
    claim_text must never assert a concrete news headline, sector name, or
    numeric price target that isn't traceable to an evidence_id — checked
    indirectly here by asserting every NEWS_OR_EVENT/SECTOR_CONDITIONS
    contributor claim is always INSUFFICIENT_EVIDENCE this sprint (the
    only way it could avoid fabricating a source this codebase doesn't
    have)."""
    from services.postmortem.evidence import EvidenceClass
    from services.postmortem.evidence_attribution import ContributorCategory

    for result in _all_results():
        for contributor in result.contributor_assessments:
            if contributor.category in (ContributorCategory.NEWS_OR_EVENT.value, ContributorCategory.SECTOR_CONDITIONS.value):
                assert contributor.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value


@pytest.mark.regression
def test_no_signal_status_asserts_performance_without_later_evidence():
    from services.postmortem.evidence_attribution import SignalStatus

    forbidden_statuses = {
        SignalStatus.WORKED_AS_EXPECTED.value, SignalStatus.PARTIALLY_WORKED.value,
        SignalStatus.WEAKENED.value, SignalStatus.REVERSED.value, SignalStatus.INVALIDATED.value,
    }
    for result in _all_results():
        for sig in result.signal_scorecard:
            assert sig.status not in forbidden_statuses, (
                f"signal {sig.signal_id} was scored {sig.status} without any later comparable evidence"
            )
