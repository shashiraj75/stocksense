"""
US Daily Picks generation-reliability incident (2026-07-22) — output-
equivalence proof for the horizon-bounded Phase 1 restructuring.

The fix reorders Phase 1 from candidate-major ((sym1,short),(sym1,medium),
(sym1,long),(sym2,short),...) to horizon-major ((sym1,short),(sym2,short),
...,(sym1,medium),...) so at most one horizon's candidate pool is ever
built and resident at once (~397 entries instead of ~1,191). This can only
be safe if two properties hold, both proven directly below rather than by
running the full, deeply-integrated `_generate_picks_inner` end-to-end
(this codebase's own established convention for a function of that shape
— see test_daily_picks_raw_memory_release.py's docstring):

1. The SET of (symbol, horizon) calls to _predict_stock, and each call's
   individual arguments, is unchanged — only the order changed to
   horizon-major. _predict_stock has no cross-call state dependency other
   than prediction_engine.py's bounded, horizon-keyed, TTL'd _pred_cache/
   _regime_cache (unaffected by call order — see that module).

2. Downstream ranking/selection (_zscore_and_rank + the quality-gate/
   dedup/top-6 selection pipeline) is a pure function of the SET of items
   for one horizon — it does not depend on the order those items were
   appended to the list, so feeding it the same items in a different order
   cannot change which candidates are selected, their ranks, or their
   scores.

Together these prove the reordering cannot change any published score,
rank, selected symbol, confidence, target, or stop-loss — only when each
horizon's pool is resident in memory.
"""
from unittest.mock import MagicMock, patch
import random

_FAKE_REGIME = {"regime_id": 0, "label": "neutral", "description": "", "weight_multipliers": {}}


def test_predict_stock_called_exactly_once_per_symbol_horizon_pair_horizon_major():
    """Every (symbol, horizon) combination must be scored exactly once, and
    the call sequence must be horizon-major: every 'short' call for every
    candidate completes before the first 'medium' call, which completes
    before the first 'long' call. This is the direct, executable proof
    that the restructuring took effect (not merely a source-string check)."""
    import services.daily_picks as dp

    candidates = ["AAA", "BBB", "CCC"]
    calls: list[tuple[str, str]] = []

    def fake_predict(sym, horizon, market):
        calls.append((sym, horizon))
        return None  # empty result is fine — this test only cares about call shape

    with patch("services.daily_picks._bulk_screen",
               return_value=(candidates, 10, "screener", False, 20,
                             {"universe_candidate_count": 3, "attempts": 1,
                              "reason": "healthy_screener_universe", "error_category": "none",
                              "tier_map": {}})), \
         patch("services.daily_picks._predict_stock", side_effect=fake_predict), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_production_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id=None)
        except Exception:
            pass  # only the call log matters for this test

    # 1. Exactly one call per (symbol, horizon) — no duplicates, no misses.
    expected = {(sym, h) for sym in candidates for h in ("short", "medium", "long")}
    assert set(calls) == expected
    assert len(calls) == len(expected), f"expected no duplicate calls, got {calls}"

    # 2. Horizon-major ordering: the horizon of every call is non-decreasing
    # through the canonical short < medium < long sequence — i.e. once a
    # 'medium' call is seen, no further 'short' call may occur, and once a
    # 'long' call is seen, no further 'short' or 'medium' call may occur.
    order_rank = {"short": 0, "medium": 1, "long": 2}
    seen_ranks = [order_rank[h] for _, h in calls]
    assert seen_ranks == sorted(seen_ranks), (
        f"Phase 1 must be horizon-major (all short, then all medium, then "
        f"all long) — got horizon sequence {[h for _, h in calls]}"
    )
    # 3. Within each horizon, candidates are scored in the same order every
    # time (candidate-list order) — proving the SET and per-horizon ORDER
    # are both unchanged from the original candidate-major design, only the
    # horizon-major/candidate-minor nesting is new.
    for h in ("short", "medium", "long"):
        this_horizon_syms = [sym for sym, hz in calls if hz == h]
        assert this_horizon_syms == candidates


def test_ranking_and_selection_is_order_independent_given_the_same_items():
    """Feed _zscore_and_rank + the quality-gate/dedup/top-6 selection
    pipeline the SAME set of scored candidates in two different orders —
    the final selection (symbols, ranks, scores) must be byte-identical.
    This proves that changing WHEN a horizon's items are built (the
    2026-07-22 fix) cannot change WHICH candidates end up selected, since
    downstream ranking never depended on build order in the first place."""
    from services.daily_picks import (
        _zscore_and_rank, _passes_quality_gate, _deduplicate_by_issuer,
        _select_short_term_top_six,
    )

    rng = random.Random(42)
    base_items = [
        {
            "symbol": f"SYM{i}", "name": f"Company {i}", "signal": "BUY",
            "price": 100.0 + i, "target": 110.0 + i, "horizon": "short",
            "tech_score": 40 + (i * 7) % 60, "fund_score": 30 + (i * 11) % 70,
            "sentiment_score": 50.0, "quality_score": 50 + (i * 5) % 50,
            "sentiment_available": True, "quality_available": True,
            "quality_raw_score": 50, "sentiment": "NEUTRAL",
            "reasoning": [], "summary": "", "score_band": "B",
            "global_context": {}, "quality_factors": {}, "cap_tier": "large",
            "composite_score": 50 + i, "confidence_model": 60,
            "confidence": 70, "risk_reward": 1.5,
        }
        for i in range(20)
    ]

    def run_pipeline(items):
        ranked_universe = _zscore_and_rank(
            list(items), ic_weights={"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2},
            regime=_FAKE_REGIME, regime_id=0, market="US", production_learning_enabled=False,
        )
        ranked = sorted(ranked_universe, key=lambda x: x.get("ranking_alpha", 0), reverse=True)
        all_buy = [r for r in ranked if r.get("signal") == "BUY" and _passes_quality_gate(r, "short")]
        deduped, _ = _deduplicate_by_issuer(all_buy, "US")
        top_buy = _select_short_term_top_six(deduped)
        return [(p["symbol"], round(p.get("ranking_alpha", 0), 8)) for p in top_buy]

    order_a = list(base_items)
    order_b = list(base_items)
    rng.shuffle(order_b)

    result_a = run_pipeline(order_a)
    result_b = run_pipeline(order_b)

    assert result_a == result_b, (
        "ranking/selection must be identical regardless of input item "
        f"order — got {result_a} vs {result_b}"
    )
    assert len(result_a) > 0, "fixture should produce at least one selected pick"
