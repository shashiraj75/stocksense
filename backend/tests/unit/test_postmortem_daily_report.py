"""
Unit tests for services/postmortem/daily_report.py — the Trade Postmortem
Engine's Sprint 1 day-level aggregation. No DB, no HTTP; pure aggregation
over already-computed per-trade results.
"""
import datetime as dt

import pytest

from services.postmortem.daily_report import DailyTradePostmortem, build_daily_report
from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
from services.postmortem.evidence_attribution import ContributorAssessment, EvidenceClass, SupportLevel, build_evidence_attribution

UTC = dt.timezone.utc


def _closed_trade(*, trade_id, exit_price, entry_price=100.0, quantity=10, stop_loss=90.0, target_price=130.0, exit_reason="MANUAL"):
    return ClosedTradeRecord(
        trade_id=trade_id, status="CLOSED", symbol="AAPL", market="US", quantity=quantity,
        entry_price=entry_price, exit_price=exit_price, stop_loss=stop_loss, target_price=target_price,
        opened_at=dt.datetime(2026, 6, 1, 9, 30, tzinfo=UTC), closed_at=dt.datetime(2026, 6, 2, 9, 30, tzinfo=UTC),
        trade_management_mode="manual", exit_reason=exit_reason,
    )


def _daily_entry(*, trade_id, exit_price, entry_price=100.0, quantity=10, stop_loss=90.0, target_price=130.0, exit_reason="MANUAL") -> DailyTradePostmortem:
    record = _closed_trade(trade_id=trade_id, exit_price=exit_price, entry_price=entry_price, quantity=quantity,
                            stop_loss=stop_loss, target_price=target_price, exit_reason=exit_reason)
    pm = compute_postmortem(record)
    attribution = build_evidence_attribution(pm, None)
    return DailyTradePostmortem(trade_id=trade_id, symbol=record.symbol, market=record.market, postmortem=pm, attribution=attribution)


class TestEmptyDay:
    def test_empty_list_produces_zeroed_summary(self):
        report = build_daily_report("2026-06-02", "US", [])
        assert report.summary.trade_count == 0
        assert report.summary.total_realized_pnl_abs is None
        assert report.summary.pnl_excluded_trade_count == 0
        assert report.summary.recurring_supported_contributors == {}
        assert report.trades == []


class TestOutcomeTallying:
    def test_mixed_outcomes_tallied_correctly(self):
        entries = [
            _daily_entry(trade_id=1, exit_price=120.0),  # WIN
            _daily_entry(trade_id=2, exit_price=80.0),   # LOSS
            _daily_entry(trade_id=3, exit_price=100.0),  # BREAKEVEN
            _daily_entry(trade_id=4, exit_price=120.0, entry_price=None),  # INDETERMINATE
        ]
        report = build_daily_report("2026-06-02", "US", entries)
        s = report.summary
        assert s.trade_count == 4
        assert s.win_count == 1
        assert s.loss_count == 1
        assert s.breakeven_count == 1
        assert s.indeterminate_count == 1


class TestPartialPnlSum:
    def test_sums_only_valid_pnl_and_flags_excluded_count(self):
        entries = [
            _daily_entry(trade_id=1, exit_price=120.0),  # valid P&L: +200
            _daily_entry(trade_id=2, exit_price=110.0, entry_price=None),  # indeterminate, no P&L
        ]
        report = build_daily_report("2026-06-02", "US", entries)
        s = report.summary
        assert s.total_realized_pnl_abs == pytest.approx(200.0)
        assert s.pnl_excluded_trade_count == 1


class TestContributorAggregationSafety:
    """Sprint 1, Stage 13: no 'most common root cause' — only STRONGLY_
    SUPPORTED/SUPPORTED contributor occurrences count as 'recurring
    supported'; conflicting and not-assessable are tracked separately."""

    def test_no_supported_contributors_when_none_match(self):
        entries = [_daily_entry(trade_id=1, exit_price=120.0), _daily_entry(trade_id=2, exit_price=80.0)]
        report = build_daily_report("2026-06-02", "US", entries)
        assert report.summary.recurring_supported_contributors == {}
        assert report.summary.trades_with_no_supported_contributor == 2

    def test_position_management_counted_as_recurring_supported(self):
        entries = [
            _daily_entry(trade_id=1, exit_price=95.0, stop_loss=10.0, target_price=101.0),  # POSITION_MANAGEMENT: SUPPORTED
            _daily_entry(trade_id=2, exit_price=95.0, stop_loss=10.0, target_price=101.0),
        ]
        report = build_daily_report("2026-06-02", "US", entries)
        assert report.summary.recurring_supported_contributors.get("POSITION_MANAGEMENT") == 2
        assert report.summary.trades_with_no_supported_contributor == 0

    def test_daily_summary_has_no_root_cause_breakdown_field(self):
        report = build_daily_report("2026-06-02", "US", [])
        assert not hasattr(report.summary, "root_cause_breakdown")

    def test_conflicting_contributor_counted_separately_from_supported(self):
        """No current rule produces a contributor-level CONFLICTED support
        level (only claim-level CONFLICTING_EVIDENCE) — this test proves
        the AGGREGATION code path is correct in isolation, using a
        synthetic ContributorAssessment, independent of whether any Sprint
        1 rule currently reaches that state."""
        pm = compute_postmortem(_closed_trade(trade_id=1, exit_price=120.0))
        attribution = build_evidence_attribution(pm, None)
        synthetic_conflicted = ContributorAssessment(
            category="MARKET_CONDITIONS", support_level=SupportLevel.CONFLICTED.value,
            evidence_class=EvidenceClass.CONFLICTING_EVIDENCE.value, confidence_band="LOW",
            supporting_evidence_ids=["EV-1-a"], opposing_evidence_ids=["EV-1-b"], claim_id="CLM-1-TEST",
        )
        patched_contributors = [
            c if c.category != "MARKET_CONDITIONS" else synthetic_conflicted
            for c in attribution.contributor_assessments
        ]
        patched_attribution = attribution.__class__(
            **{**attribution.__dict__, "contributor_assessments": patched_contributors}
        )
        entry = DailyTradePostmortem(trade_id=1, symbol="AAPL", market="US", postmortem=pm, attribution=patched_attribution)
        report = build_daily_report("2026-06-02", "US", [entry])
        assert report.summary.recurring_conflicting_contributors.get("MARKET_CONDITIONS") == 1
        assert "MARKET_CONDITIONS" not in report.summary.recurring_supported_contributors
