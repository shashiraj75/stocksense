"""
Daily Picks universe construction — large/mid/small-cap stratification.

Root cause this replaces (previously locked in by this file against
yf.screen() pagination): a flat market-cap-descending cutoff at 250 (IN) or
a $2,000M hard floor (US) was, by construction, Large+Mid cap only — real
small-cap stocks never reached the pipeline regardless of screener health.
_get_universe_by_mcap now sources from stock_fundamentals_cache (the
nightly-refreshed, screener.in/yfinance-derived table Multibagger already
maintains) instead of a live yf.screen() call, tiers by SEBI rank (IN) or
value convention (US), and stratifies a ~400-symbol pool across tiers.

These tests mock services.fundamentals_cache.get_ranked_universe entirely —
no Yahoo, no network, no live DB.
"""

from unittest.mock import patch

import pytest

import services.daily_picks as dp


def _ranked(prefix: str, n: int, start_cap: float, step: float = -1.0):
    """(symbol, market_cap) pairs, descending, prefix-numbered."""
    return [(f"{prefix}{i:04d}", start_cap + step * i) for i in range(n)]


# ─────────────────────────────────────────────────────────────────────────────
# IN — rank-based tiering (SEBI convention: Large 1-100, Mid 101-250, Small 251+)
# ─────────────────────────────────────────────────────────────────────────────

def test_in_healthy_cache_stratifies_into_all_three_tiers():
    ranked = _ranked("NSE", 500, 100_000)  # 500 symbols, descending market cap
    with patch("services.fundamentals_cache.get_ranked_universe", return_value=ranked):
        symbols, used, degraded, raw, meta = dp._get_universe_by_mcap("IN")

    assert used == "fundamentals_cache"
    assert degraded is False
    assert raw == 500
    tier_counts = meta["tier_counts"]
    # 2026-09: IN's mid/small quotas raised 120 -> 135 each (the +30
    # candidate-pool increase); "large" stays rank-capped at exactly 100
    # regardless of its (unchanged, still-moot) 160 quota value.
    assert tier_counts["large"] == 100     # rank 1-100, quota irrelevant here
    assert tier_counts["mid"] == 135       # capped at the new quota, 150 were available
    assert tier_counts["small"] == 135     # capped at the new quota, 250 were available
    assert len(symbols) == 370             # 100 + 135 + 135 — no cross-tier backfill


def test_in_tier_boundaries_are_exact():
    ranked = _ranked("NSE", 300, 100_000)
    with patch("services.fundamentals_cache.get_ranked_universe", return_value=ranked):
        _, _, _, _, meta = dp._get_universe_by_mcap("IN")
    tiers = meta["tier_map"]
    assert tiers["NSE0000"] == "large"   # rank 1
    assert tiers["NSE0099"] == "large"   # rank 100
    assert tiers["NSE0100"] == "mid"     # rank 101
    assert tiers["NSE0249"] == "mid"     # rank 250
    assert tiers["NSE0250"] == "small"   # rank 251


def test_in_small_cap_junk_floor_still_applies():
    # Below-floor caps at the tail (rank 251+, market cap < 100 Cr) must be
    # excluded from the small tier entirely, not swept in just because they
    # rank past 250.
    ranked = _ranked("NSE", 260, 100_000) + [("JUNK1", 5.0), ("JUNK2", 1.0)]
    with patch("services.fundamentals_cache.get_ranked_universe", return_value=ranked):
        symbols, *_rest, meta = dp._get_universe_by_mcap("IN")
    assert "JUNK1" not in symbols
    assert "JUNK2" not in symbols
    assert meta["tier_map"].get("JUNK1") is None


def test_in_thin_cache_falls_back_to_nifty_100():
    with patch("services.fundamentals_cache.get_ranked_universe", return_value=_ranked("TINY", 10, 500)):
        symbols, used, degraded, raw, meta = dp._get_universe_by_mcap("IN")
    assert used == "static_fallback"
    assert degraded is True
    assert symbols == list(dp._NIFTY_100)
    assert raw == 10
    assert meta["reason"] == "cache_insufficient_symbols"


def test_in_cache_query_exception_falls_back_truthfully():
    with patch("services.fundamentals_cache.get_ranked_universe", side_effect=RuntimeError("db down")):
        symbols, used, degraded, raw, meta = dp._get_universe_by_mcap("IN")
    assert used == "static_fallback"
    assert degraded is True
    assert symbols == list(dp._NIFTY_100)
    assert raw is None
    assert meta["reason"] == "cache_query_failed"
    assert meta["error_category"] == "cache_error"


def test_in_fallback_never_claims_cache_coverage():
    with patch("services.fundamentals_cache.get_ranked_universe", return_value=[]):
        symbols, used, degraded, raw, meta = dp._get_universe_by_mcap("IN")
    assert used != "fundamentals_cache"
    assert degraded is True


# ─────────────────────────────────────────────────────────────────────────────
# US — value-based tiering (Large >$10B, Mid $2B-$10B, Small <$2B)
# ─────────────────────────────────────────────────────────────────────────────

def test_us_tier_boundaries_are_value_based_not_rank_based():
    ranked = [
        ("BIGCO", 50_000.0),   # $50B -> large
        ("MIDCO", 5_000.0),    # $5B -> mid
        ("SMALLCO", 500.0),    # $500M -> small
        ("MICROCO", 10.0),     # $10M -> below junk floor, excluded
    ]
    tiers = dp._assign_cap_tiers("US", ranked)
    assert tiers["BIGCO"] == "large"
    assert tiers["MIDCO"] == "mid"
    assert tiers["SMALLCO"] == "small"
    assert "MICROCO" not in tiers


def test_us_thin_cache_falls_back_to_megacap_anchor():
    with patch("services.fundamentals_cache.get_ranked_universe", return_value=[("X", 100.0)]):
        symbols, used, degraded, raw, meta = dp._get_universe_by_mcap("US")
    assert used == "anchor"
    assert degraded is True
    assert symbols == list(dp._US_MEGACAP_100)
    assert meta["reason"] == "cache_insufficient_symbols"


# ─────────────────────────────────────────────────────────────────────────────
# Invariants — junk floors, batch/candidate sizing, tier quota constant
# ─────────────────────────────────────────────────────────────────────────────

def test_junk_floors():
    assert dp._MIN_MCAP_CR == 100            # crores INR, IN small-cap floor
    assert dp._MIN_MCAP_USD_M_FLOOR == 100   # $M, US small-cap floor


def test_universe_and_candidate_sizing():
    assert dp._SCREEN_BATCH_SIZE == 300
    assert dp._TARGET_UNIVERSE_SIZE == 400
    # N_CANDIDATES is sized to match the stratified pool so Phase 0's
    # momentum-rank-then-truncate step doesn't narrow the tier distribution
    # built upstream back down.
    assert dp._N_CANDIDATES == dp._TARGET_UNIVERSE_SIZE == 400


def test_tier_quota_sums_to_target_universe_size():
    assert sum(dp._TIER_QUOTA.values()) == dp._TARGET_UNIVERSE_SIZE
    assert dp._TIER_QUOTA == {"large": 160, "mid": 120, "small": 120}


def test_in_market_uses_the_staged_370_candidate_pool_us_unaffected():
    """2026-09: IN-only +30 candidate-pool increase (a deliberately smaller
    first step than an initially considered +50/390 — see
    _TARGET_UNIVERSE_SIZE_IN's own comment for why) — NOT 400 -> 430, but
    340 -> 370. IN's "large" tier is hard-capped at SEBI rank 1-100 in
    _assign_cap_tiers regardless of quota value, so IN's real achievable
    universe under the ORIGINAL quota (160/120/120) was always
    min(100, 160) + 120 + 120 = 340, never the nominal 400. The +30 is
    delivered entirely via mid/small (120 -> 135 each); "large"'s quota is
    left at 160 (unchanged, moot either way — never reachable past 100)."""
    assert dp._target_universe_size_for_market("IN") == 370
    assert dp._n_candidates_for_market("IN") == 370
    assert dp._tier_quota_for_market("IN") == {"large": 160, "mid": 135, "small": 135}
    # The quota dict's own sum (430) intentionally does NOT equal the
    # target (370) — "large" contributes only 100 in practice regardless
    # of its 160 quota value; this is the realistic achievable ceiling,
    # not sum(quota.values()).
    assert 100 + dp._tier_quota_for_market("IN")["mid"] + dp._tier_quota_for_market("IN")["small"] \
        == dp._target_universe_size_for_market("IN")

    # US must resolve to the exact original, unmodified values.
    assert dp._target_universe_size_for_market("US") == dp._TARGET_UNIVERSE_SIZE == 400
    assert dp._n_candidates_for_market("US") == dp._N_CANDIDATES == 400
    assert dp._tier_quota_for_market("US") == dp._TIER_QUOTA == {"large": 160, "mid": 120, "small": 120}


def test_in_mid_and_small_quotas_increased_identically_by_15():
    """The +30 increase is split evenly across mid/small (the only two
    tiers IN's quota can actually move) — not an arbitrary or lopsided
    split, and "large" is deliberately untouched since raising it would be
    a no-op given the rank-based cap."""
    base = dp._TIER_QUOTA
    staged = dp._tier_quota_for_market("IN")
    assert staged["large"] == base["large"]
    assert staged["mid"] == base["mid"] + 15
    assert staged["small"] == base["small"] + 15
    assert (staged["mid"] - base["mid"]) + (staged["small"] - base["small"]) == 30


def test_us_tier_ratio_is_still_exactly_40_30_30():
    """US's own methodology (40/30/30 large/mid/small) is completely
    unaffected by the IN-only change — verified against the unmodified
    base _TIER_QUOTA/_TARGET_UNIVERSE_SIZE, not the market-aware helpers,
    so this can never accidentally pass due to IN-side changes."""
    assert dp._TIER_QUOTA["large"] / dp._TARGET_UNIVERSE_SIZE == pytest.approx(0.40)
    assert dp._TIER_QUOTA["mid"] / dp._TARGET_UNIVERSE_SIZE == pytest.approx(0.30)
    assert dp._TIER_QUOTA["small"] / dp._TARGET_UNIVERSE_SIZE == pytest.approx(0.30)


def test_medium_long_tier_quota_sums_to_six():
    assert sum(dp._MEDIUM_LONG_TIER_QUOTA_6.values()) == 6


def test_short_term_confidence_priority_constant():
    assert dp._SHORT_TERM_CONFIDENCE_PRIORITY == 80
