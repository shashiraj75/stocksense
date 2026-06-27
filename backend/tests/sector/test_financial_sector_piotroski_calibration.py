"""
Sector tests, named per the specific companies that drove the Piotroski
sector-discount calibration fix (Business Quality Engine Production
Readiness Validation, Phase 6 Finding B): HDFCBANK, ICICIBANK, YESBANK,
BAJFINANCE. Each fixture below models the real, distinguishing
characteristic that made that specific company's case important —
not a generic "a bank" fixture, but the specific Piotroski/leverage
profile each one actually exhibited in the live validation run.

These are unit tests against synthetic, named fixtures (no network
calls) — the live re-validation against the real tickers is reported
separately in the calibration sprint report, not duplicated here.
"""

import pandas as pd
import pytest

from services.business_quality_engine import compute_business_quality


def _bank_info(base_info, **overrides):
    info = dict(base_info)
    info.update({
        "sector": "Financial Services",
        "industry": "Banks",
    })
    info.update(overrides)
    return info


def _nbfc_info(base_info, **overrides):
    info = dict(base_info)
    info.update({
        "sector": "Financial Services",
        "industry": "NBFC",
    })
    info.update(overrides)
    return info


class TestFinancialSectorPiotroskiCalibration:
    @pytest.mark.unit
    def test_hdfcbank_style_strong_bank_not_penalized_for_normal_leverage(
        self, base_info, mock_ticker_two_year_financials, monkeypatch
    ):
        """HDFCBANK-style case: a genuinely well-run, conservatively-managed
        bank. Piotroski's leverage-decreasing/asset-turnover checks are
        largely irrelevant to a bank's real health — the discount must not
        turn a healthy bank into a falsely low score just because those
        checks don't apply the way they would to a manufacturer."""
        import services.business_quality_engine as bqe
        monkeypatch.setattr(bqe, "quality_metrics_score", lambda ticker, df, info: {
            "score": 55, "reasons": ["Adequate Piotroski F-Score"], "piotroski": 5,
        })
        info = _bank_info(base_info, returnOnEquity=0.17, returnOnCapitalEmployed=0.16)
        df = pd.DataFrame({"Close": [100.0] * 30})
        result = compute_business_quality("HDFCBANK", mock_ticker_two_year_financials, df, info, market="IN")
        assert result["grade"] != "rejected"
        assert result["metadata"]["sector_bucket"] == "FINANCIAL"

    @pytest.mark.unit
    def test_icicibank_style_safe_altman_zone_rewarded(
        self, base_info, mock_ticker_two_year_financials, monkeypatch
    ):
        """ICICIBANK-style case: of the three real banks in the live
        validation, ICICIBANK showed the strongest (safest) Altman Z-Score
        once Fix 1 made it computable. Confirms a genuinely strong balance
        sheet still gets credit even for a FINANCIAL-sector company —
        the D/E exemption shouldn't be mistaken for "balance sheet
        strength can never improve this category for a bank."""
        import services.business_quality_engine as bqe
        monkeypatch.setattr(bqe, "altman_zscore_signal", lambda info, ticker=None: {
            "score": 62, "reasons": ["Altman Z-Score 4.74 — Safe Zone"], "z_score": 4.74, "z_zone": "safe",
        })
        info = _bank_info(base_info)
        df = pd.DataFrame({"Close": [100.0] * 30})
        result = compute_business_quality("ICICIBANK", mock_ticker_two_year_financials, df, info, market="IN")
        assert result["metadata"]["category_contributions"]["balance_sheet_strength"] > 0

    @pytest.mark.unit
    def test_yesbank_style_weak_bank_with_misleadingly_strong_piotroski_is_not_overrated(
        self, base_info, mock_ticker_two_year_financials, monkeypatch
    ):
        """YESBANK-style case: the exact scenario that exposed Defect 2.
        YESBANK's Piotroski F-Score was 7/9 ("financially healthy") despite
        the bank's well-documented 2020 near-collapse — because several
        Piotroski sub-checks reward patterns (e.g. declining leverage, no
        dilution) that can be true of a bank in retrenchment for reasons
        unrelated to genuine health. The discount must prevent this
        misleadingly strong Piotroski reading from single-handedly
        producing a "healthy bank" verdict when other evidence (Altman
        distress zone) says otherwise."""
        import services.business_quality_engine as bqe
        monkeypatch.setattr(bqe, "quality_metrics_score", lambda ticker, df, info: {
            "score": 78, "reasons": ["Strong Piotroski F-Score 7/9"], "piotroski": 7,
        })
        monkeypatch.setattr(bqe, "altman_zscore_signal", lambda info, ticker=None: {
            "score": 30, "reasons": ["Altman Z-Score 0.17 — Distress Zone"], "z_score": 0.17, "z_zone": "distress",
        })
        monkeypatch.setattr(bqe, "sloan_accruals_signal", lambda info, ticker=None: {
            "score": 50, "reasons": [], "accruals_ratio": 0.03,
        })
        info = _bank_info(base_info, returnOnEquity=0.02, returnOnCapitalEmployed=0.01)
        df = pd.DataFrame({"Close": [100.0] * 30})
        result = compute_business_quality("YESBANK", mock_ticker_two_year_financials, df, info, market="IN")

        # The Piotroski bonus IS discounted (not zeroed) — it still
        # contributes positively, but Altman's distress reading must drag
        # the overall picture down rather than being overridden entirely
        # by a misleadingly strong Piotroski score.
        cc = result["metadata"]["category_contributions"]
        from services.business_quality_engine import _map_subscore
        undiscounted_piotroski = _map_subscore(78, cap=12)
        assert cc["profitability_capital_efficiency"] < undiscounted_piotroski
        assert cc["balance_sheet_strength"] <= 0  # distress zone must still register as a negative

    @pytest.mark.unit
    def test_bajfinance_style_leveraged_nbfc_not_unfairly_penalized(
        self, base_info, mock_ticker_two_year_financials, monkeypatch
    ):
        """BAJFINANCE-style case: a widely-regarded financial compounder
        whose Piotroski F-Score (3/9) looks weak ONLY because its
        leverage-funded growth model fails sub-checks designed for
        manufacturers (declining-leverage, asset-turnover-improving).
        The discount must reduce — not eliminate, and not invert — the
        resulting penalty relative to what a non-financial company with
        the identical Piotroski score would receive."""
        import services.business_quality_engine as bqe
        monkeypatch.setattr(bqe, "quality_metrics_score", lambda ticker, df, info: {
            "score": 25, "reasons": ["Weak Piotroski F-Score 3/9"], "piotroski": 3,
        })

        financial_info = _nbfc_info(base_info, returnOnEquity=0.21, returnOnCapitalEmployed=0.19)
        non_financial_info = dict(base_info, sector="Technology", industry="Software",
                                   returnOnEquity=0.21, returnOnCapitalEmployed=0.19)
        df = pd.DataFrame({"Close": [100.0] * 30})

        financial_result = compute_business_quality("BAJFINANCE", mock_ticker_two_year_financials, df, financial_info, market="IN")
        non_financial_result = compute_business_quality("TEST", mock_ticker_two_year_financials, df, non_financial_info, market="US")

        financial_penalty = financial_result["metadata"]["category_contributions"]["profitability_capital_efficiency"]
        non_financial_penalty = non_financial_result["metadata"]["category_contributions"]["profitability_capital_efficiency"]

        # Same weak Piotroski score (25), same strong ROE/ROCE — the only
        # difference is sector. The financial-sector company's penalty
        # must be smaller in magnitude (less negative, or higher), not
        # equal to or worse than the non-financial company's.
        assert financial_penalty > non_financial_penalty
