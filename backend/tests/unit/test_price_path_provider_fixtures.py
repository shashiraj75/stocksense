"""
Trade Postmortem Sprint 3A, Pre-Stage-H Correction 2 — deterministic
provider-response fixtures proving the expanded source manifest
(provider/library version/interval/auto_adjust/back_adjust/actions/
repair/timezone_behavior/raw_ohlc_basis_claim/adjusted_close_available/
split_event_manifest/dividend_event_manifest/unresolved_basis_limitations)
is populated correctly for every acquisition scenario, entirely from
hand-built raw_bars dicts — no live network call anywhere in this file.
"""
import datetime as dt

import pytest

from services.market_hours import ET
from services.postmortem.price_path_acquisition import (
    ACQUISITION_ACTIONS,
    ACQUISITION_AUTO_ADJUST,
    ACQUISITION_BACK_ADJUST,
    ACQUISITION_REPAIR,
    COMPOSITE_ADJUSTED_UNKNOWN_BASIS,
    DIVIDEND_ADJUSTMENT_PRESENT,
    SPLIT_IN_WINDOW,
    UNADJUSTED_PROVIDER_OHLC,
    build_price_path_evidence,
    evaluate_basis_compatibility,
)
from services.postmortem.price_path_evidence import STATUS_AMBIGUOUS_RESOLUTION, STATUS_COMPLETE, UNKNOWN_ADJUSTMENT


def _bars(*rows):
    """rows: (date, o, h, l, c, adj_close_or_None, dividend)"""
    return [
        {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": 1000.0, "adj_close": ac, "dividend": div}
        for (d, o, h, l, c, ac, div) in rows
    ]


def _build(raw_bars, split_events=(), dividend_events=(), entry_date=dt.date(2026, 6, 2), exit_date=dt.date(2026, 6, 2)):
    return build_price_path_evidence(
        paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
        market_timezone_name="America/New_York", market_tzinfo=ET,
        entry_timestamp=dt.datetime(entry_date.year, entry_date.month, entry_date.day, tzinfo=ET),
        exit_timestamp=dt.datetime(exit_date.year, exit_date.month, exit_date.day, tzinfo=ET),
        raw_bars=raw_bars, split_events=list(split_events), dividend_events=list(dividend_events),
    )


@pytest.mark.unit
class TestPinnedAcquisitionArgumentsPersisted:
    def test_manifest_records_exact_pinned_arguments(self):
        bundle = _build(_bars((dt.date(2026, 6, 2), 100, 105, 99, 102, None, 0.0)))
        m = bundle.source_manifest
        assert m["auto_adjust"] == ACQUISITION_AUTO_ADJUST == False
        assert m["back_adjust"] == ACQUISITION_BACK_ADJUST == False
        assert m["actions"] == ACQUISITION_ACTIONS == True
        assert m["repair"] == ACQUISITION_REPAIR == False
        assert m["interval"] == "1d"
        assert m["provider"] == "yfinance"
        assert "provider_library_version" in m
        assert m["raw_ohlc_basis_claim"] == "PROVIDER_RETURNED_UNADJUSTED_OHLC"


@pytest.mark.unit
class TestUnadjustedOHLCNoActions:
    def test_no_split_no_dividend_is_complete_and_unadjusted(self):
        bundle = _build(_bars((dt.date(2026, 6, 2), 100, 105, 99, 102, None, 0.0)))
        assert bundle.data_completeness == STATUS_COMPLETE
        assert bundle.source_manifest["adjusted_close_available"] is False
        assert bundle.source_manifest["split_event_manifest"] == []
        assert bundle.source_manifest["dividend_event_manifest"] == []


@pytest.mark.unit
class TestSplitEvent:
    def test_split_forces_unknown_adjustment_and_ambiguous(self):
        bundle = _build(
            _bars((dt.date(2026, 6, 2), 100, 105, 99, 102, None, 0.0)),
            split_events=[dt.date(2026, 6, 3)],
        )
        assert bundle.price_adjustment_basis == UNKNOWN_ADJUSTMENT
        assert bundle.data_completeness == STATUS_AMBIGUOUS_RESOLUTION
        assert bundle.source_manifest["split_event_manifest"] == ["2026-06-03"]

    def test_split_classification_is_split_in_window(self):
        result = evaluate_basis_compatibility(
            acquisition_mode="auto_adjust_false", split_events=[dt.date(2026, 6, 3)], bars_observed=3,
        )
        assert result == SPLIT_IN_WINDOW


@pytest.mark.unit
class TestDividendEvent:
    def test_dividend_alone_does_not_block_computation(self):
        bundle = _build(
            _bars((dt.date(2026, 6, 2), 100, 105, 99, 102, 101.5, 0.5)),
            dividend_events=[dt.date(2026, 6, 2)],
        )
        assert bundle.data_completeness == STATUS_COMPLETE
        assert bundle.price_adjustment_basis != UNKNOWN_ADJUSTMENT
        assert bundle.source_manifest["dividend_event_manifest"] == ["2026-06-02"]
        assert any("dividend was paid" in lim for lim in bundle.limitations)


@pytest.mark.unit
class TestBothSplitAndDividend:
    def test_split_still_dominates_when_both_present(self):
        bundle = _build(
            _bars((dt.date(2026, 6, 2), 100, 105, 99, 102, 101.5, 0.5)),
            split_events=[dt.date(2026, 6, 3)], dividend_events=[dt.date(2026, 6, 2)],
        )
        assert bundle.price_adjustment_basis == UNKNOWN_ADJUSTMENT
        assert bundle.data_completeness == STATUS_AMBIGUOUS_RESOLUTION
        # Both manifests are still recorded even though split forces ambiguity.
        assert bundle.source_manifest["split_event_manifest"] == ["2026-06-03"]
        assert bundle.source_manifest["dividend_event_manifest"] == ["2026-06-02"]


@pytest.mark.unit
class TestAdjustedCloseDiffersFromClose:
    def test_adjusted_close_availability_flag_set_when_present(self):
        bundle = _build(_bars((dt.date(2026, 6, 2), 100, 105, 99, 102, 98.7, 0.0)))
        assert bundle.source_manifest["adjusted_close_available"] is True

    def test_adjusted_close_availability_flag_false_when_absent(self):
        bundle = _build(_bars((dt.date(2026, 6, 2), 100, 105, 99, 102, None, 0.0)))
        assert bundle.source_manifest["adjusted_close_available"] is False


@pytest.mark.unit
class TestMissingActionsData:
    def test_missing_dividend_key_defaults_gracefully(self):
        raw = [{"date": dt.date(2026, 6, 2), "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1000.0}]
        bundle = _build(raw)
        assert bundle.data_completeness == STATUS_COMPLETE
        assert bundle.source_manifest["adjusted_close_available"] is False


@pytest.mark.unit
class TestMalformedActionData:
    def test_nan_adj_close_treated_as_unavailable(self):
        bundle = _build(_bars((dt.date(2026, 6, 2), 100, 105, 99, 102, float("nan"), 0.0)))
        assert bundle.source_manifest["adjusted_close_available"] is False


@pytest.mark.unit
class TestProviderVersionRecorded:
    def test_library_version_string_is_recorded_and_non_empty(self):
        bundle = _build(_bars((dt.date(2026, 6, 2), 100, 105, 99, 102, None, 0.0)))
        assert isinstance(bundle.source_manifest["provider_library_version"], str)
        assert bundle.source_manifest["provider_library_version"] != ""


@pytest.mark.unit
class TestUnknownAdjustmentBasisClassification:
    def test_unrecognized_mode_never_silently_compatible(self):
        result = evaluate_basis_compatibility(acquisition_mode="mystery_mode", split_events=[], bars_observed=5)
        assert result == COMPOSITE_ADJUSTED_UNKNOWN_BASIS
        assert result not in ("COMPATIBLE_UNADJUSTED", UNADJUSTED_PROVIDER_OHLC)


@pytest.mark.unit
class TestReplayWithoutSecondProviderCall:
    def test_unresolved_basis_limitations_persisted_for_replay(self):
        """The manifest's own limitations list is enough to replay the
        basis decision without calling yfinance again — no separate
        network-dependent step is needed to reproduce it."""
        bundle = _build(
            _bars((dt.date(2026, 6, 2), 100, 105, 99, 102, 101.5, 0.5)),
            dividend_events=[dt.date(2026, 6, 2)],
        )
        assert bundle.source_manifest["unresolved_basis_limitations"] == list(bundle.limitations)
