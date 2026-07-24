"""
US Daily Picks generation-reliability incidents (2026-07-22 and
2026-07-23/24) — output-equivalence proof for the Phase 1 ordering.

2026-07-23/24 fix: Phase 1 is chunked CANDIDATE-major — candidates are
processed in chunks no larger than the SEC facts-cache capacity, and each
symbol's short/medium/long horizons run consecutively so its SEC
companyfacts payload is fetched once and hit warm for horizons 2/3 (the
horizon-major ordering the 2026-07-22 fix introduced defeated that cache
and tripled SEC download/parse churn — the actual dominant memory driver).
This can only be safe if these properties hold, proven directly below
rather than by running the full, deeply-integrated `_generate_picks_inner`
end-to-end (this codebase's own established convention for a function of
that shape — see test_daily_picks_raw_memory_release.py's docstring):

1. The SET of (symbol, horizon) calls to _predict_stock, and each call's
   individual arguments, is unchanged — only the order. _predict_stock
   has no cross-call state dependency other than prediction_engine.py's
   bounded, horizon-keyed, TTL'd _pred_cache/_regime_cache (unaffected by
   call order — see that module).

2. Downstream ranking/selection (_zscore_and_rank + the quality-gate/
   dedup/top-6 selection pipeline) is a pure function of the SET of items
   for one horizon — it does not depend on the order those items were
   appended to the list, so feeding it the same items in a different order
   cannot change which candidates are selected, their ranks, or their
   scores.

3. The full published payload is deterministic given deterministic inputs
   — two runs of the new implementation produce identical payloads except
   for genuinely nondeterministic operational metadata (see
   test_published_payload_is_deterministic below for the exact, documented
   exclusion list).

Together these prove the reordering cannot change any published score,
rank, selected symbol, confidence, target, or stop-loss.
"""
from unittest.mock import MagicMock, patch
import random

_FAKE_REGIME = {"regime_id": 0, "label": "neutral", "description": "", "weight_multipliers": {}}


def _run_inner_with_stubbed_pipeline(candidates, fake_predict, market="US"):
    """Shared harness: run _generate_picks_inner with the deterministic
    stub set this file's tests all use. Returns whatever the run returns
    (payload, persisted_at) or raises are swallowed by callers that only
    care about the call log."""
    import services.daily_picks as dp
    with patch("services.daily_picks._bulk_screen",
               return_value=(candidates, 10, "screener", False, 20,
                             {"universe_candidate_count": len(candidates), "attempts": 1,
                              "reason": "healthy_screener_universe", "error_category": "none",
                              "tier_map": {}})), \
         patch("services.daily_picks._predict_stock", side_effect=fake_predict), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._fetch_returns_matrix", return_value=None), \
         patch("services.daily_picks.yf", MagicMock()), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_production_ic_weights",
               return_value={"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        return dp._generate_picks_inner(market, job_id=None)


def test_predict_stock_called_exactly_once_per_pair_chunked_candidate_major():
    """Every (symbol, horizon) combination must be scored exactly once, and
    the call sequence must be chunked candidate-major: each symbol's three
    horizons appear consecutively, symbols in candidate-list order. Uses
    27 candidates (> the 25-entry SEC facts-cache chunk size) so the
    sequence provably spans a chunk boundary without changing shape. This
    is the direct, executable proof that the restructuring took effect
    (not merely a source-string check)."""
    candidates = [f"SYM{i:02d}" for i in range(27)]
    calls: list[tuple[str, str]] = []

    def fake_predict(sym, horizon, market):
        calls.append((sym, horizon))
        return None  # empty result is fine — this test only cares about call shape

    try:
        _run_inner_with_stubbed_pipeline(candidates, fake_predict)
    except Exception:
        pass  # only the call log matters for this test

    # 1. Exactly one call per (symbol, horizon) — no duplicates, no misses.
    expected = {(sym, h) for sym in candidates for h in ("short", "medium", "long")}
    assert set(calls) == expected
    assert len(calls) == len(expected), f"expected no duplicate calls, got {len(calls)}"

    # 2. Candidate-major ordering: the full call sequence is exactly each
    # candidate's three horizons consecutively, in canonical short/medium/
    # long order, candidates in candidate-list order — the precise property
    # that guarantees a symbol's SEC facts fetch is immediately reused by
    # its other two horizons.
    expected_sequence = [
        (sym, h) for sym in candidates for h in ("short", "medium", "long")
    ]
    assert calls == expected_sequence, (
        "Phase 1 must be chunked candidate-major (each symbol's three "
        "horizons consecutive, symbols in candidate order)"
    )


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


def _deterministic_rich_predict(sym, horizon, market):
    """A deterministic, non-trivial _predict_stock stub: every field a pure
    function of (sym, horizon) so two runs — or two orderings — must
    produce identical downstream payloads."""
    i = int(sym.replace("SYM", ""))
    hseed = {"short": 1, "medium": 2, "long": 3}[horizon]
    return {
        "symbol": sym, "name": f"Company {i}", "signal": "BUY",
        "price": 100.0 + i, "target": 115.0 + i + hseed, "stop_loss": 90.0 + i,
        "entry_low": 99.0 + i, "entry_high": 101.0 + i,
        "generated_at": "2026-07-24T00:00:00+00:00",
        "generation_reference_price": 100.0 + i,
        "generation_reference_source": "test", "generation_reference_price_basis": "close",
        "generation_reference_as_of": "2026-07-23T00:00:00+00:00",
        "generation_reference_is_stale": False,
        "generation_reference_expected_session": "2026-07-23",
        "generation_reference_bhavcopy_failure_reason": None,
        "risk_reward": 2.0, "confidence": 60 + (i * 3 + hseed) % 30,
        "tech_score": 40 + (i * 7 + hseed) % 55, "fund_score": 35 + (i * 11 + hseed) % 60,
        "sentiment_score": 45.0 + (i + hseed) % 10, "quality_score": 50 + (i * 5) % 45,
        "sentiment_available": True, "quality_available": True,
        "quality_raw_score": 50 + (i * 5) % 45, "sentiment": "NEUTRAL",
        "reasoning": [f"reason-{i}-{horizon}"], "summary": f"summary-{i}-{horizon}",
        "score_band": "B", "global_context": {"k": i}, "quality_factors": {"score": 50 + (i * 5) % 45},
        "horizon": horizon, "composite_score": 50.0 + i, "confidence_model": 55.0,
    }


def test_published_payload_is_deterministic_across_runs():
    """Output-equivalence regression: two runs of the (new, chunked
    candidate-major) implementation over identical deterministic fixtures
    must produce identical published payloads — identical selected symbols,
    horizon membership, scores, rank ordering, structure, and values.

    Exclusions (each genuinely nondeterministic operational metadata, and
    nothing else):
      - payload["generated_at"]: wall-clock timestamp taken at payload
        construction.
    source_job_id is NOT excluded — both runs pass job_id=None and must
    both publish None. Combined with the call-set equality test above and
    the order-independence test, this closes the equivalence chain: same
    calls -> same items (pure stub) -> order-independent ranking -> same
    payload."""
    candidates = [f"SYM{i:02d}" for i in range(30)]

    payload_a, _ = _run_inner_with_stubbed_pipeline(candidates, _deterministic_rich_predict)
    payload_b, _ = _run_inner_with_stubbed_pipeline(candidates, _deterministic_rich_predict)

    for p in (payload_a, payload_b):
        assert p.get("generated_at"), "runs must produce a real payload"
        p.pop("generated_at")

    assert payload_a == payload_b, "published payload must be fully deterministic"
    # And the payload is genuinely non-trivial: at least one horizon
    # published picks with ranked, scored entries.
    total_picks = sum(len(v) for v in payload_a["picks"].values())
    assert total_picks > 0, "fixture should publish at least one pick"
    assert payload_a["phase_1_task_total"] == len(candidates) * 3
    assert payload_a["source_job_id"] is None
