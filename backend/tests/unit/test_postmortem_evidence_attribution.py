"""
Unit tests for services/postmortem/evidence_attribution.py — the Trade
Postmortem Engine's Sprint 1 evidence attribution engine. No DB, no HTTP.

Covers Sprint 1's Stage 14 test matrix items 1-21 (24/25 are covered by
the regression suite; see test_paper_trading_postmortem_daily_endpoint.py
and test_postmortem_deterministic.py respectively).
"""
import datetime as dt

import pytest

from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
from services.postmortem.entry_snapshot import EntrySnapshot, RecommendationContext, build_entry_snapshot
from services.postmortem.evidence import EvidenceClass, ConfidenceBand, INSUFFICIENT_EVIDENCE_SENTENCE, RULE_REGISTRY
from services.postmortem.evidence_attribution import (
    ATTRIBUTION_RULES_VERSION,
    ContributorCategory,
    SignalStatus,
    SupportLevel,
    ThesisVerdict,
    build_evidence_attribution,
)

UTC = dt.timezone.utc


def _closed_trade(
    *, trade_id=1, exit_price=120.0, entry_price=100.0, quantity=10,
    stop_loss=90.0, target_price=130.0, exit_reason="MANUAL",
):
    return ClosedTradeRecord(
        trade_id=trade_id, status="CLOSED", symbol="AAPL", market="US", quantity=quantity,
        entry_price=entry_price, exit_price=exit_price, stop_loss=stop_loss, target_price=target_price,
        opened_at=dt.datetime(2026, 6, 1, 9, 30, tzinfo=UTC), closed_at=dt.datetime(2026, 6, 2, 9, 30, tzinfo=UTC),
        trade_management_mode="manual", exit_reason=exit_reason,
    )


def _snapshot(*, trade_id=1, technical_signal=None, recommendation_signal=None,
              fundamental_score=None, sentiment_score=None, execution_range_position_inputs=(None, None, None)) -> EntrySnapshot:
    exec_price, entry_low, entry_high = execution_range_position_inputs
    ctx = RecommendationContext(
        evidence_source="DAILY_PICK", simulated_execution_price=exec_price if exec_price is not None else 100.0,
        technical_signal=technical_signal, recommendation_signal=recommendation_signal,
        fundamental_score=fundamental_score, sentiment_score=sentiment_score,
        recommendation_entry_low=entry_low, recommendation_entry_high=entry_high,
    )
    return build_entry_snapshot(paper_trade_id=trade_id, user_id="u", symbol="AAPL", market="US", ctx=ctx)


# ---------------------------------------------------------------------
# 1-2: no outcome-circular signal reasoning
# ---------------------------------------------------------------------

class TestNoOutcomeCircularSignals:
    def test_winning_trade_does_not_automatically_validate_bullish_signal(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        snap = _snapshot(technical_signal="BUY")
        result = build_evidence_attribution(pm, snap)
        tech = next(s for s in result.signal_scorecard if s.signal_id == "technical_signal")
        assert tech.status == SignalStatus.NOT_TESTABLE.value
        assert tech.status != SignalStatus.WORKED_AS_EXPECTED.value

    def test_losing_trade_does_not_automatically_invalidate_bullish_signal(self):
        pm = compute_postmortem(_closed_trade(exit_price=80.0))
        snap = _snapshot(technical_signal="BUY")
        result = build_evidence_attribution(pm, snap)
        tech = next(s for s in result.signal_scorecard if s.signal_id == "technical_signal")
        assert tech.status == SignalStatus.NOT_TESTABLE.value
        assert tech.status != SignalStatus.INVALIDATED.value

    def test_no_signal_scorecard_status_is_ever_worked_or_invalidated_this_sprint(self):
        """This codebase has no later-signal-recapture or price-path
        evidence yet — every signal in every outcome must resolve to
        NOT_TESTABLE or INSUFFICIENT_EVIDENCE, never a performance verdict."""
        for exit_price in (80.0, 100.0, 120.0):
            pm = compute_postmortem(_closed_trade(exit_price=exit_price))
            snap = _snapshot(technical_signal="BUY", recommendation_signal="SELL", fundamental_score=80.0, sentiment_score=20.0)
            result = build_evidence_attribution(pm, snap)
            for sig in result.signal_scorecard:
                assert sig.status in (SignalStatus.NOT_TESTABLE.value, SignalStatus.INSUFFICIENT_EVIDENCE.value)

    def test_entry_only_field_cannot_be_classified_worked_or_failed(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        snap = _snapshot(fundamental_score=90.0)
        result = build_evidence_attribution(pm, snap)
        fund = next(s for s in result.signal_scorecard if s.signal_id == "fundamental_score")
        assert fund.status == SignalStatus.NOT_TESTABLE.value
        assert fund.comparison_evidence_ids == []


# ---------------------------------------------------------------------
# 3-4: thesis verdict safety
# ---------------------------------------------------------------------

class TestThesisVerdictSafety:
    def test_winning_trade_with_no_thesis_is_unsupported(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        result = build_evidence_attribution(pm, None)
        assert result.thesis_verdict == ThesisVerdict.UNSUPPORTED.value

    def test_losing_trade_with_captured_signal_is_not_assessable(self):
        pm = compute_postmortem(_closed_trade(exit_price=80.0))
        snap = _snapshot(technical_signal="BUY")
        result = build_evidence_attribution(pm, snap)
        assert result.thesis_verdict == ThesisVerdict.NOT_ASSESSABLE.value

    def test_thesis_verdict_never_derived_from_outcome_directly(self):
        """Same snapshot, opposite outcomes -> identical thesis verdict,
        proving outcome plays no role in this sprint's thesis logic."""
        snap = _snapshot(technical_signal="BUY")
        pm_win = compute_postmortem(_closed_trade(exit_price=120.0))
        pm_loss = compute_postmortem(_closed_trade(exit_price=80.0))
        result_win = build_evidence_attribution(pm_win, snap)
        result_loss = build_evidence_attribution(pm_loss, snap)
        assert result_win.thesis_verdict == result_loss.thesis_verdict == ThesisVerdict.NOT_ASSESSABLE.value


# ---------------------------------------------------------------------
# 6-9: multi-contributor model, no forced single root cause
# ---------------------------------------------------------------------

class TestMultiContributorModel:
    def test_no_root_cause_field_exists_anymore(self):
        pm = compute_postmortem(_closed_trade())
        result = build_evidence_attribution(pm, None)
        assert not hasattr(result, "root_cause")

    def test_contributor_assessments_cover_every_category_independently(self):
        pm = compute_postmortem(_closed_trade())
        result = build_evidence_attribution(pm, None)
        categories = {c.category for c in result.contributor_assessments}
        assert categories == {c.value for c in ContributorCategory}

    def test_no_contributor_supported_when_no_matching_evidence(self):
        pm = compute_postmortem(_closed_trade(exit_reason="TARGET_HIT", exit_price=130.0))
        result = build_evidence_attribution(pm, None)
        supported = [c for c in result.contributor_assessments if c.support_level in
                     (SupportLevel.STRONGLY_SUPPORTED.value, SupportLevel.SUPPORTED.value)]
        assert supported == []

    def test_position_management_supported_from_stored_prices_only(self):
        """Manual loss where exit finished closer to target than stop —
        pure stored-price computation, never touches outcome-vs-signal
        agreement."""
        pm = compute_postmortem(_closed_trade(exit_reason="MANUAL", exit_price=95.0, stop_loss=10.0, target_price=101.0))
        assert pm.outcome.value == "LOSS"
        result = build_evidence_attribution(pm, None)
        pm_contributor = next(c for c in result.contributor_assessments if c.category == ContributorCategory.POSITION_MANAGEMENT.value)
        assert pm_contributor.support_level == SupportLevel.SUPPORTED.value

    def test_primary_contributor_can_be_null(self):
        pm = compute_postmortem(_closed_trade())
        result = build_evidence_attribution(pm, None)
        assert result.primary_contributor is None
        assert result.primary_contributor_claim_id is not None

    def test_null_primary_contributor_claim_uses_exact_fallback_sentence(self):
        pm = compute_postmortem(_closed_trade())
        result = build_evidence_attribution(pm, None)
        claim = next(c for c in result.claims if c.claim_id == result.primary_contributor_claim_id)
        assert claim.claim_text == INSUFFICIENT_EVIDENCE_SENTENCE

    def test_primary_contributor_never_selected_from_only_supported_no_strongly_supported(self):
        """SUPPORTED alone (not STRONGLY_SUPPORTED) must never populate
        primary_contributor — Stage 9's bar is deliberately high."""
        pm = compute_postmortem(_closed_trade(exit_reason="MANUAL", exit_price=95.0, stop_loss=10.0, target_price=101.0))
        result = build_evidence_attribution(pm, None)
        pm_contributor = next(c for c in result.contributor_assessments if c.category == ContributorCategory.POSITION_MANAGEMENT.value)
        assert pm_contributor.support_level == SupportLevel.SUPPORTED.value
        assert result.primary_contributor is None


# ---------------------------------------------------------------------
# 10-11: contradiction handling
# ---------------------------------------------------------------------

class TestContradictionHandling:
    def test_disagreeing_entry_signals_retained_as_conflicting_evidence(self):
        pm = compute_postmortem(_closed_trade())
        snap = _snapshot(technical_signal="BUY", sentiment_score=20.0)
        result = build_evidence_attribution(pm, snap)
        claim = next(c for c in result.claims if c.factor == "entry_signal_agreement")
        assert claim.evidence_class == EvidenceClass.CONFLICTING_EVIDENCE.value

    def test_conflicting_claim_has_both_supporting_and_opposing_ids(self):
        pm = compute_postmortem(_closed_trade())
        snap = _snapshot(technical_signal="BUY", sentiment_score=20.0)
        result = build_evidence_attribution(pm, snap)
        claim = next(c for c in result.claims if c.factor == "entry_signal_agreement")
        assert claim.supporting_evidence_ids
        assert claim.opposing_evidence_ids

    def test_agreeing_entry_signals_not_flagged_conflicting(self):
        pm = compute_postmortem(_closed_trade())
        snap = _snapshot(technical_signal="BUY", sentiment_score=80.0)
        result = build_evidence_attribution(pm, snap)
        claim = next(c for c in result.claims if c.factor == "entry_signal_agreement")
        assert claim.evidence_class != EvidenceClass.CONFLICTING_EVIDENCE.value

    def test_momentum_vs_range_contradiction_detected(self):
        pm = compute_postmortem(_closed_trade())
        snap = _snapshot(technical_signal="BUY", execution_range_position_inputs=(150.0, 100.0, 140.0))
        result = build_evidence_attribution(pm, snap)
        claim = next(c for c in result.claims if c.factor == "momentum_vs_entry_range")
        assert claim.evidence_class == EvidenceClass.CONFLICTING_EVIDENCE.value

    def test_price_path_dependent_contradictions_resolve_insufficient_evidence(self):
        """4 named contradiction patterns needing evidence this sprint
        doesn't acquire (price-path/benchmark) — honestly deferred."""
        pm = compute_postmortem(_closed_trade())
        result = build_evidence_attribution(pm, None)
        factors = {
            "favourable_entry_signal_vs_adverse_price_behavior",
            "favourable_stock_movement_vs_adverse_benchmark_movement",
            "positive_pnl_vs_unsupported_thesis",
            "negative_pnl_vs_briefly_followed_thesis",
        }
        found = {c.factor for c in result.claims if c.factor in factors}
        assert found == factors
        for c in result.claims:
            if c.factor in factors:
                assert c.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value

    def test_disagreement_requires_at_least_two_assessable_signals(self):
        pm = compute_postmortem(_closed_trade())
        snap = _snapshot(technical_signal="BUY")  # only one assessable signal
        result = build_evidence_attribution(pm, snap)
        claim = next(c for c in result.claims if c.factor == "entry_signal_agreement")
        assert claim.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value


# ---------------------------------------------------------------------
# 12-15: claim provenance completeness
# ---------------------------------------------------------------------

class TestClaimProvenance:
    def _all_claims(self):
        pm = compute_postmortem(_closed_trade(exit_reason="MANUAL", exit_price=95.0, stop_loss=10.0, target_price=101.0))
        snap = _snapshot(technical_signal="BUY", sentiment_score=20.0)
        return build_evidence_attribution(pm, snap).claims

    def test_every_supported_claim_has_evidence_ids(self):
        for c in self._all_claims():
            if c.evidence_class != EvidenceClass.INSUFFICIENT_EVIDENCE.value:
                assert c.supporting_evidence_ids, f"claim {c.claim_id} has no supporting evidence"

    def test_every_claim_has_rule_id_and_version(self):
        for c in self._all_claims():
            assert c.rule_id
            assert c.rule_version

    def test_every_rule_id_is_registered(self):
        for c in self._all_claims():
            base_rule_id = c.rule_id
            assert base_rule_id in RULE_REGISTRY, f"claim {c.claim_id} references unregistered rule {base_rule_id}"

    def test_unsupported_claims_use_exact_fallback_sentence(self):
        for c in self._all_claims():
            if c.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value:
                assert c.claim_text == INSUFFICIENT_EVIDENCE_SENTENCE

    def test_insufficient_evidence_claims_have_not_assessable_confidence(self):
        for c in self._all_claims():
            if c.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value:
                assert c.confidence_band == ConfidenceBand.NOT_ASSESSABLE.value


# ---------------------------------------------------------------------
# 16-19: no fabrication
# ---------------------------------------------------------------------

class TestNoFabrication:
    def test_market_conditions_never_fabricated(self):
        pm = compute_postmortem(_closed_trade())
        result = build_evidence_attribution(pm, None)
        mc = next(c for c in result.contributor_assessments if c.category == ContributorCategory.MARKET_CONDITIONS.value)
        assert mc.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value

    def test_sector_conditions_never_fabricated(self):
        pm = compute_postmortem(_closed_trade())
        result = build_evidence_attribution(pm, None)
        sc = next(c for c in result.contributor_assessments if c.category == ContributorCategory.SECTOR_CONDITIONS.value)
        assert sc.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value

    def test_news_context_never_fabricated(self):
        pm = compute_postmortem(_closed_trade())
        result = build_evidence_attribution(pm, None)
        news = next(c for c in result.contributor_assessments if c.category == ContributorCategory.NEWS_OR_EVENT.value)
        assert news.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value

    def test_no_fabricated_indicator_evolution_in_signal_claim_text(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        snap = _snapshot(technical_signal="BUY")
        result = build_evidence_attribution(pm, snap)
        tech_claim = next(c for c in result.claims if c.factor == "technical_signal")
        assert "rose" not in tech_claim.claim_text.lower()
        assert "fell" not in tech_claim.claim_text.lower()
        assert "later" not in tech_claim.claim_text.lower() or "cannot be assessed" in tech_claim.claim_text.lower()


# ---------------------------------------------------------------------
# 20-21: derivation and determinism
# ---------------------------------------------------------------------

class TestDerivationAndDeterminism:
    def test_narrative_fields_reference_real_claims(self):
        pm = compute_postmortem(_closed_trade())
        snap = _snapshot(technical_signal="BUY")
        result = build_evidence_attribution(pm, snap)
        claim_ids = {c.claim_id for c in result.claims}
        assert result.thesis_verdict_claim_id in claim_ids
        assert result.primary_contributor_claim_id in claim_ids
        for sig in result.signal_scorecard:
            assert sig.explanation_claim_id in claim_ids
        for contributor in result.contributor_assessments:
            assert contributor.claim_id in claim_ids

    def test_deterministic_input_yields_identical_output(self):
        pm = compute_postmortem(_closed_trade(exit_reason="MANUAL", exit_price=95.0, stop_loss=10.0, target_price=101.0))
        snap = _snapshot(technical_signal="BUY", sentiment_score=20.0)
        result_a = build_evidence_attribution(pm, snap)
        result_b = build_evidence_attribution(pm, snap)
        assert result_a == result_b
        assert [c.claim_id for c in result_a.claims] == [c.claim_id for c in result_b.claims]
        assert [e.evidence_id for e in result_a.evidence_items] == [e.evidence_id for e in result_b.evidence_items]


def test_attribution_rules_version_format():
    assert ATTRIBUTION_RULES_VERSION.count(".") == 2
