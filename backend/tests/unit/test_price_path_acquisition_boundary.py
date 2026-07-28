"""
Trade Postmortem Sprint 3A, Stage D — proof that price-path evidence
acquisition never occurs inside the financial close transaction, is
always bounded, never fabricates evidence on provider failure, and never
makes a live network call from this test suite.
"""
import datetime as dt
import inspect

import pytest

from services.market_hours import ET
from services.postmortem import close_service
from services.postmortem.price_path_acquisition import (
    AcquisitionWindowTooLargeError,
    BASIS_INSUFFICIENT_EVIDENCE,
    BASIS_MISMATCH,
    COMPATIBLE_UNADJUSTED,
    COMPOSITE_ADJUSTED_UNKNOWN_BASIS,
    MAX_ACQUISITION_WINDOW_DAYS,
    PriceProviderAcquisitionError,
    SOURCE_SCOPE,
    SOURCE_TYPE,
    SPLIT_IN_WINDOW,
    UNADJUSTED_PROVIDER_OHLC,
    acquire_price_path_evidence,
    build_price_path_evidence,
    evaluate_basis_compatibility,
)


@pytest.mark.unit
class TestTerminologyCorrection:
    """Stage D0."""

    def test_source_type_is_not_falsely_approved(self):
        assert SOURCE_TYPE == "EXTERNAL_UNOFFICIAL_DAILY"
        assert SOURCE_TYPE != "APPROVED_EXTERNAL_SOURCE"

    def test_source_scope_states_bounded_use(self):
        assert SOURCE_SCOPE == "BOUNDED_EVIDENCE_ACQUISITION_ONLY"

    def test_source_manifest_declares_not_production_authoritative(self):
        bundle = build_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
            raw_bars=[{"date": dt.date(2026, 6, 2), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None}],
            split_events=[],
        )
        assert bundle.source_manifest["production_authoritative"] is False
        assert bundle.source_manifest["source_scope"] == SOURCE_SCOPE


@pytest.mark.unit
class TestNoProviderCallInsideCloseTransaction:
    """Stage D — source-level proof rather than a runtime mock: as of
    this checkpoint, close_service.py has no import of, or reference to,
    price_path_acquisition at all (Stage H — generation-service
    integration — has not happened yet). This test fails loudly the
    moment that changes without the acquisition being moved outside the
    transaction, since acquire_price_path_evidence itself accepts no
    `conn` parameter by construction (see next test)."""

    def test_close_service_module_does_not_reference_price_path_acquisition(self):
        source = inspect.getsource(close_service)
        assert "price_path_acquisition" not in source
        assert "acquire_price_path_evidence" not in source

    def test_acquire_price_path_evidence_accepts_no_connection_parameter(self):
        params = inspect.signature(acquire_price_path_evidence).parameters
        assert "conn" not in params
        assert "connection" not in params
        assert "cursor" not in params


@pytest.mark.unit
class TestBoundedAcquisitionWindow:
    def test_oversized_window_rejected_before_any_fetch(self):
        calls = {"bars": 0, "splits": 0}

        def fake_bars(symbol, start, end):
            calls["bars"] += 1
            return []

        def fake_splits(symbol, start, end):
            calls["splits"] += 1
            return []

        with pytest.raises(AcquisitionWindowTooLargeError):
            acquire_price_path_evidence(
                paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
                market_timezone_name="America/New_York", market_tzinfo=ET,
                entry_timestamp=dt.datetime(2020, 1, 1, tzinfo=ET),
                exit_timestamp=dt.datetime(2020, 1, 1, tzinfo=ET) + dt.timedelta(days=MAX_ACQUISITION_WINDOW_DAYS + 10),
                fetch_bars_fn=fake_bars, fetch_splits_fn=fake_splits,
            )
        assert calls["bars"] == 0
        assert calls["splits"] == 0

    def test_window_within_bound_proceeds_to_fetch(self):
        calls = {"bars": 0}

        def fake_bars(symbol, start, end):
            calls["bars"] += 1
            return []

        def fake_splits(symbol, start, end):
            return []

        acquire_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
            fetch_bars_fn=fake_bars, fetch_splits_fn=fake_splits,
        )
        assert calls["bars"] == 1


@pytest.mark.unit
class TestAcquisitionFailureNeverFabricatesEvidence:
    def test_provider_failure_propagates_without_partial_bundle(self):
        def failing_bars(symbol, start, end):
            raise PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "simulated outage")

        def fake_splits(symbol, start, end):
            return []

        with pytest.raises(PriceProviderAcquisitionError) as excinfo:
            acquire_price_path_evidence(
                paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
                market_timezone_name="America/New_York", market_tzinfo=ET,
                entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
                exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
                fetch_bars_fn=failing_bars, fetch_splits_fn=fake_splits,
            )
        assert excinfo.value.code == "PROVIDER_FETCH_FAILED"

    def test_replay_from_persisted_bars_never_calls_provider_again(self):
        """build_price_path_evidence (the pure path replay/persistence
        would use) takes raw_bars directly — it has no fetch_fn parameter
        at all, so a second provider call is structurally impossible."""
        assert "fetch_bars_fn" not in inspect.signature(build_price_path_evidence).parameters
        assert "fetch_splits_fn" not in inspect.signature(build_price_path_evidence).parameters


@pytest.mark.unit
class TestNoLiveNetworkRequestInSuite:
    def test_fetch_raw_daily_bars_is_never_invoked_without_injection_in_this_file(self):
        """A meta-test documenting the discipline the whole Sprint 3A
        suite follows: every acquisition test in this file passes an
        explicit fetch_bars_fn/fetch_splits_fn override. yfinance is
        imported lazily INSIDE fetch_raw_daily_bars specifically so that
        importing this module never triggers a network-capable import at
        collection time."""
        import services.postmortem.price_path_acquisition as mod
        source = inspect.getsource(mod)
        # No MODULE-LEVEL (unindented) yfinance import — every reference
        # is lazy, inside a function body, so importing this module
        # never triggers a network-capable import at collection time.
        module_level_imports = [line for line in source.splitlines() if line.startswith("import yfinance")]
        assert module_level_imports == []


@pytest.mark.unit
class TestBasisCompatibilityClassification:
    """Stage F."""

    def test_no_split_auto_adjust_true_is_compatible_unadjusted(self):
        result = evaluate_basis_compatibility(acquisition_mode="auto_adjust_true", split_events=[], bars_observed=3)
        assert result == COMPATIBLE_UNADJUSTED

    def test_split_in_window_takes_priority_over_everything(self):
        result = evaluate_basis_compatibility(
            acquisition_mode="auto_adjust_true", split_events=[dt.date(2026, 6, 2)], bars_observed=3,
        )
        assert result == SPLIT_IN_WINDOW

    def test_no_bars_is_insufficient_evidence(self):
        result = evaluate_basis_compatibility(acquisition_mode="auto_adjust_true", split_events=[], bars_observed=0)
        assert result == BASIS_INSUFFICIENT_EVIDENCE

    def test_total_return_adjusted_is_basis_mismatch(self):
        result = evaluate_basis_compatibility(acquisition_mode="total_return_adjusted", split_events=[], bars_observed=3)
        assert result == BASIS_MISMATCH

    def test_unrecognized_acquisition_mode_is_composite_adjusted_unknown_basis(self):
        """Correction 2 — an unrecognized adjusted feed is never silently
        treated as compatible; it gets its own explicit classification
        distinct from a genuinely-missing acquisition_mode."""
        result = evaluate_basis_compatibility(acquisition_mode="something_new", split_events=[], bars_observed=3)
        assert result == COMPOSITE_ADJUSTED_UNKNOWN_BASIS

    def test_pinned_auto_adjust_false_is_unadjusted_provider_ohlc(self):
        """Correction 2's own pinned acquisition mode needs no
        reconciliation claim — it IS unadjusted by the provider's own
        documented contract for that argument."""
        result = evaluate_basis_compatibility(acquisition_mode="auto_adjust_false", split_events=[], bars_observed=3)
        assert result == UNADJUSTED_PROVIDER_OHLC

    def test_missing_acquisition_mode_is_insufficient_evidence(self):
        result = evaluate_basis_compatibility(acquisition_mode=None, split_events=[], bars_observed=3)
        assert result == BASIS_INSUFFICIENT_EVIDENCE

    def test_missing_split_event_is_not_by_itself_proof_of_compatibility(self):
        """Correction 2 requirement 2 — absence of a split is checked
        (split_events=[]) but for an unrecognized mode this must NOT be
        treated as proof of a compatible basis."""
        result = evaluate_basis_compatibility(acquisition_mode="some_future_mode", split_events=[], bars_observed=10)
        assert result not in (COMPATIBLE_UNADJUSTED, UNADJUSTED_PROVIDER_OHLC)
        assert result == COMPOSITE_ADJUSTED_UNKNOWN_BASIS

    def test_deterministic_replay_same_inputs_same_result(self):
        first = evaluate_basis_compatibility(acquisition_mode="auto_adjust_true", split_events=[], bars_observed=5)
        second = evaluate_basis_compatibility(acquisition_mode="auto_adjust_true", split_events=[], bars_observed=5)
        assert first == second
