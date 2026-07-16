"""
Unit tests for PredictionEngine._estimate_target's confidence-scaled BUY/
SELL floors (Product Integrity #018).

Root cause: the minimum-guaranteed target for a BUY/SELL signal (e.g.
"long-term BUY always shows at least +15% upside") was a flat constant,
completely decoupled from `confidence`. A 12%-confidence BUY and a
90%-confidence BUY got the identical floor — backwards, since a
weak-conviction call shouldn't advertise the same guaranteed upside as a
strong one. `conf_factor = max(0.2, confidence / 100)` now scales every
BUY/SELL floor (lowered from a 0.5 floor so low confidence visibly shrinks
it); HOLD floors are intentionally untouched — HOLD makes no directional
conviction claim for a floor to be proportional to.

Pure logic only — constructed df/info dicts, no network.
"""
import pandas as pd
import pytest

from services.prediction_engine import PredictionEngine


@pytest.fixture
def engine():
    return PredictionEngine()


def _df(closes):
    """Minimal OHLC frame — High/Low set to close ±1% so ATR is small and
    predictable, Close is the series used for medium-horizon monthly returns."""
    return pd.DataFrame({
        "Close": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
    })


PRICE = 100.0
FLAT_CLOSES = [PRICE] * 40  # near-zero monthly return / ATR — isolates the floor


# ── Long horizon — the exact case the user reported (12% confidence BUY) ────

@pytest.mark.unit
class TestLongHorizonFloorScaling:
    # The "organic" long_target computation (analyst-target*(1+growth) or
    # PE/EPS extrapolation or price*(1+growth)^3) always applies its own
    # max(eps_growth, 0.05) floor internally — compounded 3 years, that's
    # already ~15.8%, so with weak/no growth inputs long_target can still
    # land ABOVE the 15% BUY floor on its own, making the floor a no-op in
    # the test. Pushing the analyst target well below price with negative
    # earnings growth is what actually drives long_target low enough that
    # the confidence-scaled floor becomes the binding constraint — which is
    # the specific behavior these tests exist to verify.
    _LOW_ORGANIC_TARGET_INFO = {"targetMeanPrice": PRICE * 0.5, "earningsGrowth": -0.30}

    def test_low_confidence_buy_floor_is_much_smaller_than_high_confidence(self, engine):
        df = _df(FLAT_CLOSES)
        low = engine._estimate_target(PRICE, "BUY", 12, "long", df, self._LOW_ORGANIC_TARGET_INFO)
        high = engine._estimate_target(PRICE, "BUY", 100, "long", df, self._LOW_ORGANIC_TARGET_INFO)
        low_pct = (low - PRICE) / PRICE * 100
        high_pct = (high - PRICE) / PRICE * 100
        assert low_pct < high_pct
        # High confidence floor should still land at the original flat 15%
        # (conf_factor caps at 1.0, matching the pre-#018 constant exactly).
        assert high_pct == pytest.approx(15.0, abs=0.5)
        # Low confidence (12%) floors at conf_factor=0.2 (the new minimum),
        # not 12% directly — 15% * 0.2 = 3%.
        assert low_pct == pytest.approx(3.0, abs=0.5)

    def test_floor_never_goes_below_the_02_minimum_even_at_near_zero_confidence(self, engine):
        """A BUY must always show SOME positive edge, or the signal itself
        (calling it BUY at all) would be self-contradictory."""
        df = _df(FLAT_CLOSES)
        target = engine._estimate_target(PRICE, "BUY", 1, "long", df, self._LOW_ORGANIC_TARGET_INFO)
        pct = (target - PRICE) / PRICE * 100
        assert pct == pytest.approx(3.0, abs=0.5)  # 15% * 0.2 floor, not 15% * 0.01

    def test_sell_floor_also_scales_with_confidence(self, engine):
        df = _df(FLAT_CLOSES)
        # Mirror case for SELL: push the organic target well ABOVE price
        # (positive analyst target + strong growth) so it's not the SELL
        # floor's binding constraint either.
        info = {"targetMeanPrice": PRICE * 2.0, "earningsGrowth": 0.35}
        low = engine._estimate_target(PRICE, "SELL", 12, "long", df, info)
        high = engine._estimate_target(PRICE, "SELL", 100, "long", df, info)
        low_pct = (PRICE - low) / PRICE * 100
        high_pct = (PRICE - high) / PRICE * 100
        assert low_pct < high_pct
        assert high_pct == pytest.approx(20.0, abs=0.5)  # original flat 20% SELL floor
        assert low_pct == pytest.approx(4.0, abs=0.5)    # 20% * 0.2

    def test_hold_target_is_unaffected_by_confidence(self, engine):
        """HOLD makes no directional conviction claim — its ±10% band is
        intentionally not confidence-scaled, unlike BUY/SELL floors."""
        df = _df(FLAT_CLOSES)
        info = {}
        low = engine._estimate_target(PRICE, "HOLD", 12, "long", df, info)
        high = engine._estimate_target(PRICE, "HOLD", 100, "long", df, info)
        assert low == high


# ── Medium horizon ────────────────────────────────────────────────────────

@pytest.mark.unit
class TestMediumHorizonFloorScaling:
    def test_analyst_target_path_buy_floor_scales_with_confidence(self, engine):
        df = _df(FLAT_CLOSES)
        info = {"targetMeanPrice": PRICE}  # analyst target == price, so blend alone won't clear the floor
        low = engine._estimate_target(PRICE, "BUY", 12, "medium", df, info)
        high = engine._estimate_target(PRICE, "BUY", 100, "medium", df, info)
        low_pct = (low - PRICE) / PRICE * 100
        high_pct = (high - PRICE) / PRICE * 100
        assert low_pct < high_pct
        assert high_pct == pytest.approx(5.0, abs=0.5)   # original flat 5% floor
        assert low_pct == pytest.approx(1.0, abs=0.5)    # 5% * 0.2

    def test_projection_path_sell_floor_scales_with_confidence(self, engine):
        df = _df(FLAT_CLOSES)
        info = {}  # no analyst target — falls to the monthly-return projection path
        low = engine._estimate_target(PRICE, "SELL", 12, "medium", df, info)
        high = engine._estimate_target(PRICE, "SELL", 100, "medium", df, info)
        low_pct = (PRICE - low) / PRICE * 100
        high_pct = (PRICE - high) / PRICE * 100
        assert low_pct < high_pct
        assert high_pct == pytest.approx(8.0, abs=0.5)   # original flat 8% floor
        assert low_pct == pytest.approx(1.6, abs=0.5)    # 8% * 0.2

    def test_hold_target_is_unaffected_by_confidence(self, engine):
        df = _df(FLAT_CLOSES)
        info = {"targetMeanPrice": PRICE}
        low = engine._estimate_target(PRICE, "HOLD", 12, "medium", df, info)
        high = engine._estimate_target(PRICE, "HOLD", 100, "medium", df, info)
        assert low == high


# ── Short horizon (already had conf_factor — floor just lowered) ────────────

@pytest.mark.unit
class TestShortHorizonFloorLowered:
    def test_low_confidence_move_is_smaller_than_before_the_018_change(self, engine):
        """Pre-#018, conf_factor floored at 0.5 — a 12%-confidence BUY got
        at least half the full-confidence move. Post-#018, it gets at most
        0.2x, a materially smaller (more honest) minimum move."""
        df = _df(FLAT_CLOSES)
        info = {}
        low = engine._estimate_target(PRICE, "BUY", 12, "short", df, info)
        high = engine._estimate_target(PRICE, "BUY", 100, "short", df, info)
        low_move = low - PRICE
        high_move = high - PRICE
        assert low_move > 0  # still a positive move — never literally flat
        assert low_move < high_move * 0.3  # well under the old 0.5 floor ratio
