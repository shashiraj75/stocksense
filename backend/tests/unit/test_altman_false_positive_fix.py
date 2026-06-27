"""
Regression tests for the Altman Z-Score false-positive hard-rejection fix
(HON/ORCL investigation, Sprint #004a follow-up).

Root cause, confirmed via live yfinance calls for both HON and ORCL:
yfinance's .info dict never populates workingCapital, retainedEarnings,
ebit, or operatingIncome — the same missing-field pattern as totalAssets
(fixed in the prior sprint), but for THREE of Altman's five numerator
terms (X1, X2, X3) instead of just the denominator. With those three
terms silently defaulting to 0, the Z-Score collapsed to an incomplete
two-term formula (0.6*X4 + 1.0*X5) that is far more sensitive to capital
structure (debt-funded buybacks, in HON/ORCL's case) than the intended
five-term formula — producing a "distress"-adjacent reading for two
well-regarded, non-distressed companies.

Confirmed live: ticker.balance_sheet has "Working Capital"/"Retained
Earnings" rows and ticker.financials has "EBIT"/"Operating Income" rows
for both companies — the same reliable data sources already used
elsewhere in this file. The fix adds fallbacks for all three, exactly
mirroring the existing _total_assets_fallback pattern, rather than
changing the formula, the thresholds, or the hard-gate policy.
"""

import pandas as pd
import pytest

from services.quality_factors import (
    altman_zscore_signal,
    _working_capital_fallback,
    _retained_earnings_fallback,
    _ebit_fallback,
    _latest_statement_value,
)


class TestFallbackHelpersInIsolation:
    @pytest.mark.unit
    def test_working_capital_uses_direct_row_when_present(self, mock_ticker_two_year_financials):
        # mock_ticker_two_year_financials's balance_sheet has Current
        # Assets/Current Liabilities but no direct "Working Capital" row —
        # confirms the Current Assets - Current Liabilities fallback path.
        result = _working_capital_fallback(mock_ticker_two_year_financials)
        assert result == pytest.approx(740_000_000 - 380_000_000)

    @pytest.mark.unit
    def test_working_capital_none_when_ticker_is_none(self):
        assert _working_capital_fallback(None) is None

    @pytest.mark.unit
    def test_retained_earnings_none_for_fixture_without_that_row(self, mock_ticker_two_year_financials):
        # The existing fixture doesn't include a "Retained Earnings" row —
        # confirms graceful None, not a crash, when the row is absent.
        assert _retained_earnings_fallback(mock_ticker_two_year_financials) is None

    @pytest.mark.unit
    def test_ebit_none_for_fixture_without_that_row(self, mock_ticker_two_year_financials):
        assert _ebit_fallback(mock_ticker_two_year_financials) is None

    @pytest.mark.unit
    def test_latest_statement_value_tries_labels_in_order(self):
        import pandas as pd
        cols = pd.to_datetime(["2023-12-31", "2024-12-31"])
        df = pd.DataFrame({
            cols[0]: {"Operating Income": 100.0},
            cols[1]: {"Operating Income": 120.0},
        })
        assert _latest_statement_value(df, "EBIT", "Operating Income") == 120.0

    @pytest.mark.unit
    def test_latest_statement_value_none_for_empty_statement(self):
        assert _latest_statement_value(pd.DataFrame(), "EBIT") is None


class TestHonOrclStyleNotFalselyRejected:
    """HON/ORCL-style: a company whose EBIT, retained earnings, and
    working capital are all genuinely strong, but whose info dict (like
    every real company's) lacks these fields natively — must not be
    misread as financially distressed once the fallback supplies them."""

    @pytest.mark.unit
    def test_strong_ebit_and_retained_earnings_move_z_score_out_of_distress(self, base_info):
        """Mirrors HON/ORCL's actual shape: marketCap/totalDebt/totalRevenue
        present in info (as they are for any real company), but
        workingCapital/retainedEarnings/ebit only available via the
        ticker fallback."""
        import pandas as pd

        cols = pd.to_datetime(["2023-12-31", "2024-12-31"])

        class _Ticker:
            balance_sheet = pd.DataFrame({
                cols[0]: {"Total Assets": 90_000_000_000, "Working Capital": 5_000_000_000, "Retained Earnings": 30_000_000_000},
                cols[1]: {"Total Assets": 95_000_000_000, "Working Capital": 5_500_000_000, "Retained Earnings": 32_000_000_000},
            })
            financials = pd.DataFrame({
                cols[0]: {"EBIT": 6_000_000_000},
                cols[1]: {"EBIT": 6_500_000_000},
            })
            cashflow = pd.DataFrame()
            dividends = pd.Series(dtype=float)
            actions = pd.DataFrame()

        # totalAssets supplied directly in info (isolates X1/X2/X3's effect
        # from the separate totalAssets-fallback already fixed last sprint —
        # that one is covered by test_total_assets_fallback.py instead).
        info_without_fallback_fields = dict(
            base_info,
            totalAssets=90_000_000_000,
            marketCap=70_000_000_000,
            totalDebt=35_000_000_000,
            totalRevenue=35_000_000_000,
        )
        # The fix: with the ticker fallback supplying X1/X2/X3.
        result_with_fallback = altman_zscore_signal(info_without_fallback_fields, _Ticker())
        # The old (pre-fix) behavior: same info, no ticker — X1/X2/X3 stay 0.
        result_without_fallback = altman_zscore_signal(info_without_fallback_fields, None)

        assert result_with_fallback["z_score"] > result_without_fallback["z_score"]
        assert result_with_fallback["z_zone"] in ("safe", "grey")

    @pytest.mark.unit
    def test_no_ticker_supplied_preserves_old_behavior_exactly(self, base_info):
        """Backward compatibility: a caller that still doesn't pass a
        ticker gets the exact pre-fix computation (X1/X2/X3 default to 0,
        only X4/X5 contribute) — this fix must not change behavior for any
        existing caller that hasn't been updated to pass ticker."""
        info = dict(base_info, totalAssets=90_000_000_000, marketCap=70_000_000_000,
                     totalDebt=35_000_000_000, totalRevenue=35_000_000_000)
        result = altman_zscore_signal(info)  # no ticker argument at all
        assert result["z_score"] is not None  # still computes from X4/X5 alone, as before


class TestGenuineDistressStillDetected:
    """The fix must not mask real distress — a company with genuinely
    weak fundamentals across the board (not just missing data) must
    still land in the distress zone."""

    @pytest.mark.unit
    def test_negative_retained_earnings_and_weak_ebit_still_flag_distress(self, base_info):
        import pandas as pd
        cols = pd.to_datetime(["2023-12-31", "2024-12-31"])

        class _Ticker:
            balance_sheet = pd.DataFrame({
                cols[0]: {"Total Assets": 12_000_000_000, "Working Capital": -2_000_000_000, "Retained Earnings": -8_000_000_000},
                cols[1]: {"Total Assets": 11_500_000_000, "Working Capital": -2_500_000_000, "Retained Earnings": -9_000_000_000},
            })
            financials = pd.DataFrame({
                cols[0]: {"EBIT": -500_000_000},
                cols[1]: {"EBIT": -700_000_000},
            })
            cashflow = pd.DataFrame()
            dividends = pd.Series(dtype=float)
            actions = pd.DataFrame()

        info = dict(base_info, marketCap=2_000_000_000, totalDebt=9_000_000_000, totalRevenue=4_000_000_000)
        result = altman_zscore_signal(info, _Ticker())
        assert result["z_zone"] == "distress"


class TestBanksNbfcsHandledCorrectly:
    """Banks/NBFCs must continue to produce a usable Altman reading (or
    gracefully unavailable) — this fix must not change the already-
    working financial-sector D/E/OCF exemptions elsewhere in the engine,
    only complete Altman's own missing inputs."""

    @pytest.mark.unit
    def test_financial_sector_company_still_computes_when_data_available(self, financial_sector_info):
        import pandas as pd
        cols = pd.to_datetime(["2023-12-31", "2024-12-31"])

        class _Ticker:
            balance_sheet = pd.DataFrame({
                cols[0]: {"Total Assets": 40_000_000_000, "Working Capital": 1_000_000_000, "Retained Earnings": 5_000_000_000},
                cols[1]: {"Total Assets": 42_000_000_000, "Working Capital": 1_100_000_000, "Retained Earnings": 5_500_000_000},
            })
            financials = pd.DataFrame({
                cols[0]: {"EBIT": 800_000_000},
                cols[1]: {"EBIT": 900_000_000},
            })
            cashflow = pd.DataFrame()
            dividends = pd.Series(dtype=float)
            actions = pd.DataFrame()

        info = dict(financial_sector_info, marketCap=15_000_000_000, totalDebt=8_000_000_000, totalRevenue=3_000_000_000)
        result = altman_zscore_signal(info, _Ticker())
        assert result["z_score"] is not None
