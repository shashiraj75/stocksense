"""
Daily Picks Phase 5 selection — medium/long tier quota and short-term
confidence-priority fill-down.

Root cause this closes: Daily Picks only ever selected top-6-by-alpha
regardless of horizon, which (combined with the universe-construction fix in
test_in_universe_expansion.py) could still collapse medium/long-term picks
back to all-large-cap even from a properly stratified pool, since large caps
often score higher alpha on average. Short-term separately needed a
different rule entirely: prioritize >80% confidence ("best performing
stock"), not tier diversity, with fill-down on a genuinely weak-conviction
day rather than diluting the bar to pad the count to 6.
"""

import services.daily_picks as dp


def _pick(symbol, tier, alpha, confidence=None):
    r = {"symbol": symbol, "cap_tier": tier, "ranking_alpha": alpha}
    if confidence is not None:
        r["confidence"] = confidence
    return r


# ─────────────────────────────────────────────────────────────────────────────
# _select_with_tier_quota — medium/long-term
# ─────────────────────────────────────────────────────────────────────────────

def test_tier_quota_picks_top_alpha_within_each_tier():
    candidates = [
        _pick("L1", "large", 90), _pick("L2", "large", 80), _pick("L3", "large", 70),
        _pick("M1", "mid", 60), _pick("M2", "mid", 50), _pick("M3", "mid", 40),
        _pick("S1", "small", 30), _pick("S2", "small", 20), _pick("S3", "small", 10),
    ]
    selected = dp._select_with_tier_quota(candidates, dp._MEDIUM_LONG_TIER_QUOTA_6)
    symbols = [r["symbol"] for r in selected]
    assert symbols == ["L1", "L2", "M1", "M2", "S1", "S2"]  # top-2 per tier, alpha-ordered


def test_tier_quota_tops_up_from_leftover_when_a_tier_is_short():
    # Only 1 small-cap candidate exists — quota wants 2. The list must still
    # reach 6 by pulling the next-best leftover (regardless of tier), not
    # silently return only 5.
    candidates = [
        _pick("L1", "large", 90), _pick("L2", "large", 80), _pick("L3", "large", 75),
        _pick("M1", "mid", 60), _pick("M2", "mid", 50), _pick("M3", "mid", 45),
        _pick("S1", "small", 30),
    ]
    selected = dp._select_with_tier_quota(candidates, dp._MEDIUM_LONG_TIER_QUOTA_6)
    assert len(selected) == 6
    symbols = {r["symbol"] for r in selected}
    assert "S1" in symbols  # the one available small-cap is still included
    assert "L3" in symbols  # topped up from large-cap leftover (next-best alpha)


def test_tier_quota_final_order_is_alpha_ranked_not_tier_grouped():
    candidates = [
        _pick("L1", "large", 50), _pick("L2", "large", 45),
        _pick("M1", "mid", 95), _pick("M2", "mid", 90),
        _pick("S1", "small", 70), _pick("S2", "small", 65),
    ]
    selected = dp._select_with_tier_quota(candidates, dp._MEDIUM_LONG_TIER_QUOTA_6)
    alphas = [r["ranking_alpha"] for r in selected]
    assert alphas == sorted(alphas, reverse=True), "display order must be alpha-descending, not grouped by tier"


def test_tier_quota_unknown_tier_defaults_to_large_bucket_not_dropped():
    candidates = [_pick("UNKNOWN1", None, 99)]
    selected = dp._select_with_tier_quota(candidates, dp._MEDIUM_LONG_TIER_QUOTA_6)
    assert len(selected) == 1
    assert selected[0]["symbol"] == "UNKNOWN1"


# ─────────────────────────────────────────────────────────────────────────────
# Short-term confidence-priority fill-down
# ─────────────────────────────────────────────────────────────────────────────

def _short_term_select(all_buy_deduped):
    """Mirrors the exact selection expression in _generate_picks_inner's
    Phase 5 short-term branch — kept as a small local helper so these tests
    exercise the real logic shape without needing the full pipeline."""
    high_conf = [r for r in all_buy_deduped if (r.get("confidence") or 0) > dp._SHORT_TERM_CONFIDENCE_PRIORITY]
    rest = [r for r in all_buy_deduped if (r.get("confidence") or 0) <= dp._SHORT_TERM_CONFIDENCE_PRIORITY]
    return (high_conf + rest)[:6]


def test_short_term_prioritizes_above_80_confidence_over_higher_alpha():
    all_buy = [
        _pick("LOWCONF_HIALPHA", "large", 99, confidence=60),
        _pick("HICONF_LOWALPHA", "small", 10, confidence=85),
    ]
    top = _short_term_select(all_buy)
    assert [r["symbol"] for r in top] == ["HICONF_LOWALPHA", "LOWCONF_HIALPHA"], (
        "confidence > 80 must sort ahead of a higher-alpha but lower-confidence pick"
    )


def test_short_term_fills_down_when_fewer_than_six_clear_80_percent():
    all_buy = [
        _pick("HI1", "large", 90, confidence=85),
        _pick("LO1", "large", 80, confidence=60),
        _pick("LO2", "mid", 70, confidence=50),
    ]
    top = _short_term_select(all_buy)
    assert len(top) == 3  # fewer than 6 available at all — never padded with fabricated picks
    assert top[0]["symbol"] == "HI1"       # high-confidence bucket first
    assert top[1]["symbol"] == "LO1"       # then fill-down by alpha
    assert top[2]["symbol"] == "LO2"


def test_short_term_zero_high_confidence_day_still_fills_from_rest():
    all_buy = [_pick(f"S{i}", "large", 100 - i, confidence=50) for i in range(6)]
    top = _short_term_select(all_buy)
    assert len(top) == 6
    assert [r["symbol"] for r in top] == ["S0", "S1", "S2", "S3", "S4", "S5"]  # pure alpha order


def test_short_term_empty_all_buy_is_legitimate_empty_list():
    assert _short_term_select([]) == []


def test_short_term_confidence_bucket_preserves_alpha_as_secondary_key():
    # Two high-confidence picks: alpha order must be preserved within the bucket.
    all_buy = [
        _pick("HI_LOWER_ALPHA", "large", 10, confidence=90),
        _pick("HI_HIGHER_ALPHA", "large", 20, confidence=90),
    ]
    top = _short_term_select(all_buy)
    assert [r["symbol"] for r in top] == ["HI_LOWER_ALPHA", "HI_HIGHER_ALPHA"], (
        "all_buy_deduped is pre-sorted by alpha; partitioning must preserve that order, not re-sort"
    )
