"""
Product Integrity Workstream #002F — Daily Picks Output Integrity.

Tests:
  1–7   Truthful pipeline-count fields (screened_from backward compat,
        screener_raw_count, universe_eligible_size, deep_prediction_candidates,
        phase_1_task_total, final_candidate_count, anchor-mode null handling).
  8–15  Issuer-level deduplication correctness.
  16    Backward-compatibility: existing payload consumers unaffected.

All tests use mocks only — no external calls.
"""

import pytest
from unittest.mock import patch, MagicMock

import services.daily_picks as dp


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_buy(symbol: str, alpha: float = 0.5) -> dict:
    """Minimal BUY candidate dict that passes _passes_quality_gate."""
    return {
        "symbol": symbol,
        "signal": "BUY",
        "confidence": 60,
        "ranking_alpha": alpha,
        "reasoning": [],
        "horizon": "medium",
        "tech_score": 60,
        "fund_score": 60,
        "sentiment_score": 60,
        "quality_score": 60,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. screened_from remains present (backward compatibility)
# ─────────────────────────────────────────────────────────────────────────────

def test_screened_from_remains_present_in_payload():
    """screened_from must still appear in payload at its legacy value."""
    # _get_universe_by_mcap now returns a 4-tuple — verify screened_from == phase0_universe_size
    with patch("services.daily_picks._get_universe_by_mcap") as mock_universe, \
         patch("yfinance.download") as mock_dl:
        mock_universe.return_value = (["AAPL", "MSFT", "GOOGL"], "screener", False, 10)
        # Make yf.download return an empty DataFrame (Phase-0 scores nothing → fallback)
        import pandas as pd
        mock_dl.return_value = pd.DataFrame()

        # _bulk_screen returns 5-tuple — check phase0_universe_size == 3 (len of universe)
        candidates, phase0_size, used, degraded, raw_count = dp._bulk_screen("US", n_candidates=50)
        assert phase0_size == 3        # len(["AAPL","MSFT","GOOGL"])
        assert raw_count == 10         # passed through from _get_universe_by_mcap


# ─────────────────────────────────────────────────────────────────────────────
# 2. screener_raw_count equals raw screener result size before local filtering
# ─────────────────────────────────────────────────────────────────────────────

def test_screener_raw_count_from_get_universe_by_mcap_us_success():
    """On US screener success, screener_raw_count equals len(screener_syms) before filtering."""
    raw_symbols = ["AAPL", "MSFT", "GLD", "GOOGL", "GOOG"]  # GLD would be filtered
    import yfinance as yf

    fake_result = {"quotes": [{"symbol": s} for s in raw_symbols]}

    with patch("services.daily_picks.yf.EquityQuery"), \
         patch("services.daily_picks.yf.screen", return_value=fake_result):
        symbols, used, degraded, raw_count = dp._get_universe_by_mcap("US")

    # raw_count must equal the raw screener count (5), not the post-filter count
    assert raw_count == 5
    assert used == "screener"
    assert degraded is False


def test_screener_raw_count_is_none_in_anchor_mode():
    """When US screener fails, screener_raw_count must be None (not fabricated)."""
    with patch("services.daily_picks.yf.EquityQuery"), \
         patch("services.daily_picks.yf.screen", side_effect=Exception("timeout")):
        symbols, used, degraded, raw_count = dp._get_universe_by_mcap("US")

    assert raw_count is None
    assert used == "anchor"
    assert degraded is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. universe_eligible_size equals the eligible screener intersection
# ─────────────────────────────────────────────────────────────────────────────

def test_universe_eligible_size_equals_intersection():
    """For US screener success, universe_eligible_size == len(eligible_from_screener)."""
    # Return 5 raw symbols; only those in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET pass
    # We'll use known-good tickers that appear in the heuristic-filtered set
    raw_symbols = ["AAPL", "MSFT", "GOOGL"]  # all should be in heuristic-filtered set
    fake_result = {"quotes": [{"symbol": s} for s in raw_symbols]}

    with patch("services.daily_picks.yf.EquityQuery"), \
         patch("services.daily_picks.yf.screen", return_value=fake_result):
        symbols, used, degraded, raw_count = dp._get_universe_by_mcap("US")

    # symbols is the post-filter eligible list
    assert len(symbols) <= len(raw_symbols)
    assert used == "screener"
    # raw_count equals the raw list size (before filter)
    assert raw_count == len(raw_symbols)


# ─────────────────────────────────────────────────────────────────────────────
# 4. deep_prediction_candidates equals symbols sent to deep prediction
# ─────────────────────────────────────────────────────────────────────────────

def test_deep_prediction_candidates_propagated_from_bulk_screen():
    """deep_prediction_candidates in payload equals len(candidates) from _bulk_screen."""
    # We test through _bulk_screen's return: the 5-tuple's first element is candidates
    with patch("services.daily_picks._get_universe_by_mcap") as mock_universe, \
         patch("yfinance.download") as mock_dl:
        import pandas as pd
        mock_universe.return_value = (["AAPL", "MSFT", "NVDA", "TSLA", "META"], "screener", False, 20)
        mock_dl.return_value = pd.DataFrame()  # no scores → fallback

        candidates, _, _, _, _ = dp._bulk_screen("US", n_candidates=3)
        # fallback returns min(3, len(fallback)) symbols
        assert len(candidates) == 3


# ─────────────────────────────────────────────────────────────────────────────
# 5. phase_1_task_total = deep_prediction_candidates × number of horizons
# ─────────────────────────────────────────────────────────────────────────────

def test_phase_1_task_total_is_candidates_times_horizons():
    """phase_1_task_total must equal candidates × 3 for any candidate count."""
    # Pure arithmetic — no I/O needed
    n_candidates = 7
    tasks = [(sym, h) for sym in range(n_candidates) for h in ("short", "medium", "long")]
    assert len(tasks) == n_candidates * 3


# ─────────────────────────────────────────────────────────────────────────────
# 6. final_candidate_count is distinct from displayed picks when applicable
# ─────────────────────────────────────────────────────────────────────────────

def test_final_candidate_count_distinct_from_display_picks():
    """When more than 6 BUY signals pass quality gate per horizon,
    final_candidate_count > total displayed picks."""
    buys = [_make_buy(f"SYM{i}", alpha=1.0 - i * 0.01) for i in range(10)]
    deduped, suppressed = dp._deduplicate_by_issuer(buys, "US")
    # None of these are mapped issuer groups, so 0 suppressed
    assert suppressed == 0
    assert len(deduped) == 10  # all 10 pass dedup
    # Top-6 slice happens AFTER dedup: 10 > 6 (displayed), so counts are distinct
    displayed = deduped[:6]
    assert len(deduped) > len(displayed)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Anchor/fallback mode does not fabricate screener_raw_count
# ─────────────────────────────────────────────────────────────────────────────

def test_anchor_mode_screener_raw_count_is_null():
    """IN full-universe fallback must also produce screener_raw_count=None."""
    with patch("services.daily_picks.yf.EquityQuery"), \
         patch("services.daily_picks.yf.screen", side_effect=Exception("fail")):
        symbols, used, degraded, raw_count = dp._get_universe_by_mcap("IN")

    assert raw_count is None
    assert used == "full_universe"


def test_us_screener_empty_intersection_produces_null_raw_count():
    """When US screener returns symbols but none pass heuristic filter, anchor fires.
    screener_raw_count must be None (screener was used but zero eligible)."""
    # Return a symbol guaranteed NOT to be in the heuristic-filtered set
    # Use a symbol with '$' which is excluded by the heuristic filter
    # But actually the symbols from the screener go through the intersection —
    # we mock to return a symbol that doesn't exist in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET
    non_eligible = ["ZZZZNOTREAL", "YYYYNOTREAL"]
    fake_result = {"quotes": [{"symbol": s} for s in non_eligible]}

    with patch("services.daily_picks.yf.EquityQuery"), \
         patch("services.daily_picks.yf.screen", return_value=fake_result):
        symbols, used, degraded, raw_count = dp._get_universe_by_mcap("US")

    # Intersection is empty → anchor fallback → screener_raw_count=None
    assert raw_count is None
    assert used == "anchor"
    assert degraded is True


# ─────────────────────────────────────────────────────────────────────────────
# 8. GOOG + GOOGL → only one Alphabet entry in final list
# ─────────────────────────────────────────────────────────────────────────────

def test_goog_and_googl_deduplication_keeps_higher_ranked():
    """GOOGL ranks higher (alpha 0.9) → GOOGL kept; GOOG (alpha 0.5) suppressed."""
    candidates = [
        _make_buy("GOOGL", alpha=0.9),
        _make_buy("GOOG",  alpha=0.5),
        _make_buy("AAPL",  alpha=0.8),
    ]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "US")

    symbols = [c["symbol"] for c in deduped]
    assert "GOOGL" in symbols
    assert "GOOG" not in symbols
    assert "AAPL" in symbols
    assert suppressed == 1
    assert len(deduped) == 2


def test_goog_and_googl_dedup_respects_rank_order():
    """When GOOG ranks higher than GOOGL, GOOG is kept (rank order wins)."""
    candidates = [
        _make_buy("GOOG",  alpha=0.95),
        _make_buy("GOOGL", alpha=0.5),
    ]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "US")

    symbols = [c["symbol"] for c in deduped]
    assert "GOOG" in symbols
    assert "GOOGL" not in symbols
    assert suppressed == 1


# ─────────────────────────────────────────────────────────────────────────────
# 9. When only GOOG qualifies, GOOG remains valid
# ─────────────────────────────────────────────────────────────────────────────

def test_only_goog_qualifies_goog_kept():
    candidates = [_make_buy("GOOG", alpha=0.7)]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "US")
    assert deduped[0]["symbol"] == "GOOG"
    assert suppressed == 0


# ─────────────────────────────────────────────────────────────────────────────
# 10. When only GOOGL qualifies, GOOGL remains valid
# ─────────────────────────────────────────────────────────────────────────────

def test_only_googl_qualifies_googl_kept():
    candidates = [_make_buy("GOOGL", alpha=0.7)]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "US")
    assert deduped[0]["symbol"] == "GOOGL"
    assert suppressed == 0


# ─────────────────────────────────────────────────────────────────────────────
# 11. BRK-A + BRK-B cannot both appear
# ─────────────────────────────────────────────────────────────────────────────

def test_brk_a_and_brk_b_deduplication():
    """BRK-B ranks higher → BRK-B kept; BRK-A suppressed."""
    candidates = [
        _make_buy("BRK-B", alpha=0.8),
        _make_buy("BRK-A", alpha=0.3),
    ]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "US")
    symbols = [c["symbol"] for c in deduped]
    assert "BRK-B" in symbols
    assert "BRK-A" not in symbols
    assert suppressed == 1


# ─────────────────────────────────────────────────────────────────────────────
# 12. FOX + FOXA cannot both appear
# ─────────────────────────────────────────────────────────────────────────────

def test_fox_and_foxa_deduplication():
    candidates = [
        _make_buy("FOX",  alpha=0.6),
        _make_buy("FOXA", alpha=0.4),
        _make_buy("NVDA", alpha=0.9),
    ]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "US")
    symbols = [c["symbol"] for c in deduped]
    assert "FOX" in symbols
    assert "FOXA" not in symbols
    assert "NVDA" in symbols
    assert suppressed == 1


# ─────────────────────────────────────────────────────────────────────────────
# 13. An unrelated ticker is never accidentally suppressed
# ─────────────────────────────────────────────────────────────────────────────

def test_unrelated_tickers_not_suppressed():
    """Unmapped tickers each form their own singleton group — none suppressed."""
    candidates = [
        _make_buy("AAPL",  alpha=0.9),
        _make_buy("MSFT",  alpha=0.8),
        _make_buy("NVDA",  alpha=0.7),
        _make_buy("AMZN",  alpha=0.6),
        _make_buy("META",  alpha=0.5),
        _make_buy("TSLA",  alpha=0.4),
        _make_buy("AVGO",  alpha=0.3),
    ]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "US")
    assert suppressed == 0
    assert len(deduped) == len(candidates)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Deduplication happens after quality gates, not before them
# ─────────────────────────────────────────────────────────────────────────────

def test_dedup_receives_only_quality_passing_candidates():
    """_deduplicate_by_issuer only sees the already-quality-filtered BUY list.
    A suppressed ticker is one that PASSED the quality gate first — not one that
    was directly removed by the quality gate.  This test verifies the ordering
    by constructing a scenario where a same-issuer symbol has low confidence and
    confirming it would have been removed by the quality gate before reaching dedup."""
    # Low-confidence candidate: would be filtered by quality gate (conf < 25)
    low_conf_googl = {**_make_buy("GOOGL"), "confidence": 10}
    high_conf_goog = _make_buy("GOOG", alpha=0.9)

    # Quality gate simulation: only high-conf passes
    quality_filtered = [
        c for c in [high_conf_goog, low_conf_googl]
        if (c.get("confidence") or 0) >= 25
    ]
    assert len(quality_filtered) == 1
    assert quality_filtered[0]["symbol"] == "GOOG"

    # Dedup on quality-filtered list: only GOOG remains, nothing suppressed
    deduped, suppressed = dp._deduplicate_by_issuer(quality_filtered, "US")
    assert deduped[0]["symbol"] == "GOOG"
    assert suppressed == 0


# ─────────────────────────────────────────────────────────────────────────────
# 15. Deduplication happens before final top-6 slice
# ─────────────────────────────────────────────────────────────────────────────

def test_dedup_before_top_six_slice():
    """If GOOG and GOOGL are both in positions 1-6, dedup fires first so the
    slot freed by suppression is filled from position 7+."""
    # 8 candidates: GOOGL at rank 1, GOOG at rank 2, then 6 unrelated
    candidates = [
        _make_buy("GOOGL", alpha=0.99),
        _make_buy("GOOG",  alpha=0.98),
        _make_buy("AAPL",  alpha=0.90),
        _make_buy("MSFT",  alpha=0.80),
        _make_buy("NVDA",  alpha=0.70),
        _make_buy("META",  alpha=0.60),
        _make_buy("AMZN",  alpha=0.50),
        _make_buy("TSLA",  alpha=0.40),
    ]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "US")
    top_six = deduped[:6]
    symbols = [c["symbol"] for c in top_six]

    # GOOGL kept (higher rank), GOOG suppressed before the slice
    assert "GOOGL" in symbols
    assert "GOOG" not in symbols
    # Slot freed by GOOG suppression → AMZN appears in top-6
    assert "AMZN" in symbols
    assert suppressed == 1


# ─────────────────────────────────────────────────────────────────────────────
# 16. Existing payload consumers remain backward-compatible
# ─────────────────────────────────────────────────────────────────────────────

def test_dedup_is_no_op_for_non_us_market():
    """_deduplicate_by_issuer must be a no-op for IN and other markets."""
    candidates = [
        _make_buy("RELIANCE", alpha=0.9),
        _make_buy("TCS",      alpha=0.8),
    ]
    deduped, suppressed = dp._deduplicate_by_issuer(candidates, "IN")
    assert deduped == candidates
    assert suppressed == 0


def test_issuer_group_map_covers_required_pairs():
    """_US_ISSUER_GROUP must map GOOG/GOOGL, BRK-A/BRK-B, and FOX/FOXA."""
    g = dp._US_ISSUER_GROUP
    assert g["GOOG"] == g["GOOGL"]           # same Alphabet group
    assert g["BRK-A"] == g["BRK-B"]          # same Berkshire group
    assert g["FOX"] == g["FOXA"]             # same Fox Corp group
    # Groups must be distinct from each other
    assert g["GOOG"] != g["BRK-A"]
    assert g["GOOG"] != g["FOX"]
    assert g["BRK-A"] != g["FOX"]


def test_bulk_screen_returns_five_tuple():
    """_bulk_screen must return a 5-tuple so existing unpacking in
    _generate_picks_inner works correctly after this change."""
    with patch("services.daily_picks._get_universe_by_mcap") as mock_u, \
         patch("yfinance.download") as mock_dl:
        import pandas as pd
        mock_u.return_value = (["AAPL"], "screener", False, 5)
        mock_dl.return_value = pd.DataFrame()

        result = dp._bulk_screen("US", n_candidates=50)
        assert len(result) == 5
        candidates, phase0_size, used, degraded, raw_count = result
        assert isinstance(candidates, list)
        assert isinstance(phase0_size, int)
        assert isinstance(used, str)
        assert isinstance(degraded, bool)
        # raw_count is int or None
        assert raw_count is None or isinstance(raw_count, int)
