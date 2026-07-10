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
    assert tier_counts["large"] == 100     # rank 1-100, exactly the quota
    assert tier_counts["mid"] == 120       # capped at quota, 150 were available
    assert tier_counts["small"] == 120     # capped at quota, 250 were available
    assert len(symbols) == 340             # 100 + 120 + 120 — no cross-tier backfill


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


def test_medium_long_tier_quota_sums_to_six():
    assert sum(dp._MEDIUM_LONG_TIER_QUOTA_6.values()) == 6


def test_short_term_confidence_priority_constant():
    assert dp._SHORT_TERM_CONFIDENCE_PRIORITY == 80
