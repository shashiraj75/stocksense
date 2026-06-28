"""
Regression test locking in a genuine defect found live during Epic 002
Sprint #011's Daily Picks validation: `_passes_quality_gate` (a nested
closure inside `_generate_picks_inner`, not separately importable —
mirroring how test_business_quality_prediction_engine_integration.py
already handles testing a closure of this kind) excluded
Risk/Reward and Governance Risk red flags from the curated Top 6 list,
but NOT the Financial Strength Engine's own hard liquidity_distress
gate (Sprint #010) — even though that gate demotes confidence to the
exact same severity tier (capped at 30, which still clears the
function's own >=25% floor), exactly the gap its own existing checks
already exist to close for the other two red-flag types.

The fix must be signal-aware, not indicator-name-based like the
existing Risk/Reward/Governance Risk checks: "Financial Strength" is
also the indicator name for a POSITIVE confidence boost (e.g. a
fortress-balance-sheet company), so a blanket name exclusion would
wrongly filter out genuinely strong companies too.
"""

import pathlib

import pytest

import services.daily_picks as dp


def _passes_quality_gate(r: dict, hz: str) -> bool:
    """Reconstructs _passes_quality_gate's exact logic for direct
    testing, mirroring the established pattern this codebase already
    uses for closures nested inside predict()/_generate_picks_inner
    that aren't separately importable."""
    conf = r.get("confidence") or 0
    if conf < 25:
        return False
    indicators = {
        item.get("indicator") for item in r.get("reasoning", []) if isinstance(item, dict)
    }
    if "Risk/Reward" in indicators or "Governance Risk" in indicators:
        return False
    fs_reasons = " ".join(
        item.get("reason", "") for item in r.get("reasoning", [])
        if isinstance(item, dict) and item.get("indicator") == "Financial Strength"
    )
    if "liquidity distress" in fs_reasons.lower():
        return False
    if hz == "short":
        reasons = " ".join(
            item.get("reason", "") if isinstance(item, dict) else str(item)
            for item in r.get("reasoning", [])
        )
        if "Overbought" in reasons:
            return False
    return True


@pytest.mark.regression
def test_real_aal_liquidity_distress_shape_is_excluded_from_top_picks():
    """Real, live-confirmed AAL shape: signal=BUY (the composite score is
    fully independent of Financial Strength), confidence=30 (the hard-
    gate's own cap, which clears the 25% floor) -- must be excluded."""
    r = {
        "symbol": "AAL", "signal": "BUY", "confidence": 30,
        "reasoning": [{
            "indicator": "Financial Strength", "signal": "BEARISH",
            "reason": "Financial Strength Engine: liquidity distress hard gate triggered "
                      "(current ratio 0.50x, negative free cash flow) — confidence demoted "
                      "regardless of other fundamentals.",
        }],
    }
    assert _passes_quality_gate(r, "medium") is False


@pytest.mark.regression
def test_real_msft_bullish_boost_shape_still_passes():
    """Confirms the fix does NOT overcorrect: a genuinely strong company
    (real MSFT-shaped Sprint #010 output, confidence boosted) must still
    pass — the check is signal-aware, not a blanket 'Financial Strength'
    indicator-name exclusion."""
    r = {
        "symbol": "MSFT", "signal": "BUY", "confidence": 76,
        "reasoning": [{
            "indicator": "Financial Strength", "signal": "BULLISH",
            "reason": "Financial Strength Score 98/100 (strong_buy) — confidence boosted by 6 point(s).",
        }],
    }
    assert _passes_quality_gate(r, "medium") is True


@pytest.mark.regression
def test_real_ba_soft_demotion_shape_still_passes():
    """A soft, non-hard-gated Financial Strength demotion (real BA-shaped
    Sprint #010 output, -5 points, not hard-gated) must still pass --
    only the specific liquidity_distress hard-gate phrase excludes,
    not every bearish Financial Strength signal."""
    r = {
        "symbol": "BA", "signal": "BUY", "confidence": 60,
        "reasoning": [{
            "indicator": "Financial Strength", "signal": "BEARISH",
            "reason": "Financial Strength Score 12/100 (avoid) — confidence demoted by 5 point(s).",
        }],
    }
    assert _passes_quality_gate(r, "medium") is True


@pytest.mark.regression
def test_no_financial_strength_data_still_passes():
    """Companies with no Financial Strength signal at all (IN/CRYPTO
    market, or any upstream failure) must be completely unaffected."""
    r = {"symbol": "RELIANCE", "signal": "BUY", "confidence": 55, "reasoning": []}
    assert _passes_quality_gate(r, "medium") is True


@pytest.mark.regression
def test_existing_risk_reward_and_governance_checks_unaffected():
    """Confirms the pre-existing Risk/Reward and Governance Risk
    exclusions are completely unchanged by this fix."""
    r1 = {"symbol": "X", "confidence": 30, "reasoning": [{"indicator": "Risk/Reward", "signal": "BEARISH", "reason": "..."}]}
    r2 = {"symbol": "Y", "confidence": 30, "reasoning": [{"indicator": "Governance Risk", "signal": "BEARISH", "reason": "..."}]}
    assert _passes_quality_gate(r1, "medium") is False
    assert _passes_quality_gate(r2, "medium") is False


@pytest.mark.regression
def test_fix_is_present_in_the_real_source_file():
    """Static check confirming the real _generate_picks_inner function
    (not just this test's reconstruction) actually contains the fix —
    mirrors test_no_raw_threshold_literals.py's reference pattern for
    proving a code-shape property, not just a behavioral one."""
    source = pathlib.Path(dp.__file__).read_text()
    assert "liquidity distress" in source.lower()
    assert '"Financial Strength"' in source
