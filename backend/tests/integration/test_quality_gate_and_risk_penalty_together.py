"""
Integration test: exercises _quality_gate and _compute_risk_penalty together,
mirroring how PredictionEngine actually uses them in sequence (gate first —
reject outright if it fails; risk penalty only matters for stocks that
passed the gate). Still no network/Postgres I/O — "integration" here means
multiple in-process modules/functions, not external services.
"""

import pandas as pd
import pytest

from services.prediction_engine import PredictionEngine, _compute_risk_penalty


@pytest.fixture
def engine():
    return PredictionEngine()


@pytest.fixture
def empty_df():
    return pd.DataFrame()


@pytest.mark.integration
def test_stock_that_fails_gate_should_not_be_scored(engine, base_info, empty_df):
    """A stock with severely negative ROE fails the gate; in the real
    pipeline (daily_picks.py / prediction_engine.predict) this short-circuits
    before _compute_risk_penalty is ever called. This test documents that
    contract: gate failure is authoritative regardless of what the risk
    penalty would have computed."""
    base_info["returnOnEquity"] = -0.30
    gate_passed, gate_reasons = engine._quality_gate(base_info, empty_df, horizon="medium")
    assert gate_passed is False

    # Even though the stock failed the gate, risk-penalty math still runs
    # cleanly on the same input if called directly — it has no knowledge of
    # gate state. This is exactly the "three independently-built mechanisms"
    # SEAR-001 flagged in Section 4: nothing here actually prevents risk
    # penalty from being computed on a gate-rejected stock if a future
    # change calls it out of order.
    penalty, _ = _compute_risk_penalty(base_info, empty_df)
    assert isinstance(penalty, int)


@pytest.mark.integration
def test_stock_that_passes_gate_can_still_accumulate_risk_penalty(engine, base_info, empty_df):
    """A stock can cleanly pass the hard gate (no broken financials) while
    still carrying real risk that the softer risk-penalty layer should
    capture — e.g. moderate leverage that's well under the 500% hard-reject
    line but above the 300% severe-penalty line."""
    base_info["debtToEquity"] = 350.0
    gate_passed, _ = engine._quality_gate(base_info, empty_df, horizon="medium")
    assert gate_passed is True

    penalty, reasons = _compute_risk_penalty(base_info, empty_df)
    assert penalty > 0
    assert any("debt" in r.lower() for r in reasons)
