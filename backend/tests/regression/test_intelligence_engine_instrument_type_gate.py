"""
Epic 007 Phase 3A — Intelligence Engine V1, Instrument Type Gate unit tests.

Pure-function tests only; no network, no database, no Daily Picks pipeline
involved. Confirms the gate reproduces the same category boundaries as the
existing US Daily Picks heuristic (_build_us_daily_picks_heuristic_filtered
in daily_picks.py) it generalizes from, plus a few IN-specific and
edge-case checks that heuristic never had to handle.
"""
import pytest

from services.intelligence_engine.instrument_type_gate import (
    classify_instrument,
    INSTRUMENT_TYPE_EQUITY,
    INSTRUMENT_TYPE_PREFERRED,
    INSTRUMENT_TYPE_ETF,
    INSTRUMENT_TYPE_ETN,
    INSTRUMENT_TYPE_LEVERAGED,
    INSTRUMENT_TYPE_INDEX_FUND,
    INSTRUMENT_TYPE_SPAC,
    INSTRUMENT_TYPE_UNIT,
    INSTRUMENT_TYPE_CLOSED_END,
)


@pytest.mark.regression
class TestInstrumentTypeGate:
    def test_plain_equity_passes(self):
        result = classify_instrument("AAPL", "Apple Inc.")
        assert result.passed is True
        assert result.instrument_type == INSTRUMENT_TYPE_EQUITY

    def test_preferred_share_excluded(self):
        result = classify_instrument("BAC$K", "Bank of America Corp Preferred K")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_PREFERRED

    def test_etf_excluded(self):
        result = classify_instrument("SPY", "SPDR S&P 500 ETF Trust")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_ETF

    def test_etf_whole_word_boundary_avoids_false_positive(self):
        # "Netflix" contains no whole-word "ETF"; must not misclassify.
        result = classify_instrument("NFLX", "Netflix, Inc.")
        assert result.passed is True
        assert result.instrument_type == INSTRUMENT_TYPE_EQUITY

    def test_etn_excluded(self):
        result = classify_instrument("VXX", "iPath Series B S&P 500 VIX ETN")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_ETN

    def test_leveraged_2x_excluded(self):
        result = classify_instrument("TQQQX", "Direxion Daily Technology Bull 2x Shares")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_LEVERAGED

    def test_leveraged_v2x_ticker_not_falsely_excluded(self):
        # "V2X, Inc. Common Stock" — the space-prefixed " 2x"/" 3x" match must
        # not fire on a name that merely contains "2X" without a real lever
        # product's " 2x"/" 3x" phrasing (documented false-positive guard).
        result = classify_instrument("VVX", "V2X, Inc. Common Stock")
        assert result.passed is True

    def test_ultrapro_excluded(self):
        result = classify_instrument("TQQQ", "ProShares UltraPro QQQ")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_LEVERAGED

    def test_daily_bull_bear_excluded(self):
        result = classify_instrument("XYZ", "Some Daily Bull 3x Notes")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_LEVERAGED

    def test_index_fund_excluded(self):
        result = classify_instrument("IWM", "iShares Russell 2000 Index Fund")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_INDEX_FUND

    def test_spac_excluded(self):
        result = classify_instrument("SPAQ", "Example Acquisition Corp")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_SPAC

    def test_spac_special_purpose_wording_excluded(self):
        result = classify_instrument("SPAQ2", "Example Special Purpose Acquisition Company")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_SPAC

    def test_unit_trust_excluded(self):
        result = classify_instrument("ABCU", "Example Corp - Units")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_UNIT

    def test_closed_end_fund_excluded(self):
        result = classify_instrument("ABCD", "Example Closed-End Fund")
        assert result.passed is False
        assert result.instrument_type == INSTRUMENT_TYPE_CLOSED_END

    def test_empty_name_fails_open_to_equity(self):
        # No name to classify against — fail-open (don't fabricate an
        # exclusion reason from missing data).
        result = classify_instrument("XYZ", "")
        assert result.passed is True
        assert result.instrument_type == INSTRUMENT_TYPE_EQUITY

    def test_none_name_fails_open_to_equity(self):
        result = classify_instrument("XYZ", None)
        assert result.passed is True

    def test_in_symbol_plain_equity_passes(self):
        # IN never had an equivalent filter before this gate — confirm it
        # works identically for IN-style (symbol, name) pairs.
        result = classify_instrument("RELIANCE", "Reliance Industries Limited")
        assert result.passed is True
        assert result.instrument_type == INSTRUMENT_TYPE_EQUITY

    def test_result_always_has_symbol(self):
        result = classify_instrument("AAPL", "Apple Inc.")
        assert result.symbol == "AAPL"
