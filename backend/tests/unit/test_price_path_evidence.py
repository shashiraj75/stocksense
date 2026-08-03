"""
Trade Postmortem Sprint 3A — unit tests for services.postmortem.
price_path_evidence: the typed contract's own construction-time
validation (NaN/Infinity rejection, timezone-naive rejection, OHLC
internal-consistency, duplicate/out-of-order bar rejection).
"""
import datetime as dt

import pytest

from services.postmortem.price_path_evidence import (
    PricePathBar,
    PricePathEvidenceBundle,
    PricePathEvidenceError,
    STATUS_COMPLETE,
    UNADJUSTED,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _bar(**overrides):
    kwargs = dict(
        timestamp=dt.datetime(2026, 6, 2, tzinfo=IST), interval="1d",
        open=100.0, high=105.0, low=99.0, close=102.0, volume=1000.0,
        session_date=dt.date(2026, 6, 2), source_id="yfinance_daily",
        adjustment_basis=UNADJUSTED, verification_level="DIRECTLY_OBSERVED",
    )
    kwargs.update(overrides)
    return PricePathBar(**kwargs)


@pytest.mark.unit
class TestPricePathBarValidation:
    def test_valid_bar_constructs(self):
        bar = _bar()
        assert bar.high == 105.0

    def test_timezone_naive_timestamp_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bar(timestamp=dt.datetime(2026, 6, 2))

    @pytest.mark.parametrize("field_name,bad_value", [
        ("open", float("nan")), ("high", float("inf")), ("low", float("-inf")),
        ("close", float("nan")), ("open", 0), ("high", -5.0),
    ])
    def test_non_finite_or_non_positive_ohlc_rejected(self, field_name, bad_value):
        with pytest.raises(PricePathEvidenceError):
            _bar(**{field_name: bad_value})

    def test_infinite_volume_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bar(volume=float("inf"))

    def test_none_volume_permitted(self):
        bar = _bar(volume=None)
        assert bar.volume is None

    def test_internally_inconsistent_ohlc_rejected(self):
        """high must be >= open/close/low, low must be <= open/close/high."""
        with pytest.raises(PricePathEvidenceError):
            _bar(open=110.0, high=105.0, low=99.0, close=102.0)  # open exceeds high

    def test_unknown_adjustment_basis_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bar(adjustment_basis="NOT_A_REAL_BASIS")


def _bundle(bars=(), **overrides):
    kwargs = dict(
        evidence_bundle_version="1.0.0", paper_trade_id=1, user_id="user-aaa",
        symbol="TCS", market="IN", source_id="yfinance_daily", source_type="EXTERNAL_UNOFFICIAL_DAILY",
        source_version="1.0.0", provider_symbol="TCS.NS", price_adjustment_basis=UNADJUSTED,
        bar_interval="1d", market_timezone="Asia/Kolkata",
        entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=IST),
        exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=IST),
        entry_bar_policy="ENTRY_BAR_PARTIAL_UNKNOWN", exit_bar_policy="EXIT_BAR_PARTIAL_UNKNOWN",
        requested_window_start=dt.date(2026, 6, 1), requested_window_end=dt.date(2026, 6, 5),
        observed_window_start=dt.date(2026, 6, 1), observed_window_end=dt.date(2026, 6, 5),
        bars_expected=5, bars_observed=len(bars), missing_bar_count=None,
        data_completeness=STATUS_COMPLETE, freshness_basis="acquired_at_generation_time",
        acquisition_timestamp=dt.datetime(2026, 6, 5, 16, tzinfo=IST), source_manifest={},
        bars=tuple(bars),
    )
    kwargs.update(overrides)
    return PricePathEvidenceBundle(**kwargs)


@pytest.mark.unit
class TestPricePathEvidenceBundleValidation:
    def test_valid_bundle_constructs(self):
        bundle = _bundle(bars=[_bar(session_date=dt.date(2026, 6, 2))])
        assert bundle.bars_observed == 1

    def test_naive_entry_timestamp_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bundle(entry_timestamp=dt.datetime(2026, 6, 1))

    def test_exit_before_entry_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bundle(
                entry_timestamp=dt.datetime(2026, 6, 5, tzinfo=IST),
                exit_timestamp=dt.datetime(2026, 6, 1, tzinfo=IST),
            )

    def test_bars_observed_mismatch_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bundle(bars=[_bar()], bars_observed=99)

    def test_duplicate_session_date_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bundle(bars=[
                _bar(session_date=dt.date(2026, 6, 2)),
                _bar(session_date=dt.date(2026, 6, 2)),
            ], bars_observed=2)

    def test_out_of_order_bars_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bundle(bars=[
                _bar(session_date=dt.date(2026, 6, 3)),
                _bar(session_date=dt.date(2026, 6, 2)),
            ], bars_observed=2)

    def test_unknown_status_rejected(self):
        with pytest.raises(PricePathEvidenceError):
            _bundle(data_completeness="NOT_A_REAL_STATUS")


@pytest.mark.unit
class TestLegacyInvalidSourceDataContract:
    """Stage J-F2 — STATUS_INVALID_SOURCE_DATA has zero live producers
    (confirmed by direct inspection of price_path_acquisition.py across
    two separate audit phases) but remains declared and
    _VALID_STATUSES-accepted for legacy-read compatibility only. These
    tests prove both halves of that contract: the value is still
    constructible (so an old persisted row replays without error) and no
    current production code path ever produces it fresh."""

    def test_legacy_invalid_source_data_status_has_no_producer(self):
        import inspect

        from services.postmortem import price_path_acquisition

        source = inspect.getsource(price_path_acquisition)
        assert "STATUS_INVALID_SOURCE_DATA" not in source, (
            "price_path_acquisition.py must never assign STATUS_INVALID_SOURCE_DATA — "
            "it is legacy-read-only, see price_path_evidence.py's status declaration comment"
        )

    def test_legacy_invalid_source_data_bundle_replays_without_error(self):
        from services.postmortem.price_path_evidence import STATUS_INVALID_SOURCE_DATA

        bundle = _bundle(bars=(), data_completeness=STATUS_INVALID_SOURCE_DATA)
        assert bundle.data_completeness == STATUS_INVALID_SOURCE_DATA
        assert bundle.bars_observed == 0
