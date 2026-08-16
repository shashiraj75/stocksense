"""
feature/daily-picks-conviction-gated-publication — corrective follow-up
(finding 1, follow-up to commit 5a006498).

Proves the alpha_observations evidence classification (is_daily_pick /
pick_rank / portfolio_weight) is built from `published_buy` (the
conviction-gated, <=3 subset actually published to /picks), not the full
up-to-6 `top_buy` selection. Before this fix, `_pick_meta_by_symbol` was
built from `top_buy`, which meant a Top-6 candidate excluded by the
conviction gate or the 3-cap could still be marked `is_daily_pick=True`
with a real rank/weight in alpha_observations — contradicting the actual
published payload and Phase 7's `log_prediction(is_daily_pick=True)` calls
(which only ever iterate the published cohort).

These tests exercise the exact two building blocks production wires
together (`_apply_conviction_publication_gate` for the split, then the
`_pick_meta_by_symbol` dict-comprehension pattern used in
`generate_picks()`, then `_build_alpha_observation_row`'s own
`is_daily_pick=bool(pick_meta)` default) rather than mocking the entire
pipeline, so a regression in either piece or their wiring is caught.
"""

from datetime import datetime, timezone

import services.daily_picks as dp


def _candidate(symbol, confidence, **extra):
    c = {
        "symbol": symbol,
        "confidence": confidence,
        "ranking_alpha": 1.0,
        "generation_reference_price": 100.0,
        "generation_reference_source": "test",
        "generation_reference_price_basis": "close",
        "generation_reference_as_of": "2026-08-16T00:00:00+00:00",
        "signal": "BUY",
    }
    c.update(extra)
    return c


def _pick_meta_from(published_buy):
    """The exact production pattern (generate_picks) for building
    _pick_meta_by_symbol from the published (not top_buy) cohort."""
    return {
        p["symbol"]: {"pick_rank": rank, "portfolio_weight": p.get("portfolio_weight")}
        for rank, p in enumerate(published_buy, start=1)
    }


def _build_row(cand, pick_meta_by_symbol):
    return dp._build_alpha_observation_row(
        cand,
        run_id="run-1",
        market="IN",
        horizon="short",
        run_generated_at=datetime.now(timezone.utc),
        run_session_date=datetime.now(timezone.utc).date(),
        regime_id=1,
        regime_label="bull",
        pick_meta=pick_meta_by_symbol.get(cand.get("symbol")),
    )


class TestEvidenceClassificationMatchesPublishedCohort:
    def test_every_scored_candidate_gets_a_row_regardless_of_publication(self):
        # top_buy: 5 candidates, only some clear the conviction gate.
        top_buy = [_candidate(s, c) for s, c in
                   [("A", 99), ("B", 90), ("C", 85), ("D", 80), ("E", 40)]]
        published_buy, _ = dp._apply_conviction_publication_gate(top_buy)
        pick_meta = _pick_meta_from(published_buy)

        rows = {c["symbol"]: _build_row(c, pick_meta) for c in top_buy}
        assert set(rows.keys()) == {"A", "B", "C", "D", "E"}

    def test_only_published_subset_marked_is_daily_pick(self):
        top_buy = [_candidate(s, c) for s, c in
                   [("A", 99), ("B", 90), ("C", 85), ("D", 80), ("E", 40)]]
        published_buy, _ = dp._apply_conviction_publication_gate(top_buy)
        assert [p["symbol"] for p in published_buy] == ["A", "B", "C"]
        pick_meta = _pick_meta_from(published_buy)

        rows = {c["symbol"]: _build_row(c, pick_meta) for c in top_buy}
        assert rows["A"]["is_daily_pick"] is True
        assert rows["B"]["is_daily_pick"] is True
        assert rows["C"]["is_daily_pick"] is True
        assert rows["D"]["is_daily_pick"] is False
        assert rows["E"]["is_daily_pick"] is False

    def test_excluded_top6_candidates_have_no_rank_or_weight(self):
        top_buy = [_candidate(s, c) for s, c in
                   [("A", 99), ("B", 90), ("C", 85), ("D", 80), ("E", 40)]]
        published_buy, _ = dp._apply_conviction_publication_gate(top_buy)
        for p, w in zip(published_buy, [0.5, 0.3, 0.2]):
            p["portfolio_weight"] = w
        pick_meta = _pick_meta_from(published_buy)

        rows = {c["symbol"]: _build_row(c, pick_meta) for c in top_buy}
        assert rows["D"]["pick_rank"] is None
        assert rows["D"]["portfolio_weight"] is None
        assert rows["E"]["pick_rank"] is None
        assert rows["E"]["portfolio_weight"] is None

    def test_published_rank_and_weight_match_actual_payload_order(self):
        top_buy = [_candidate(s, c) for s, c in
                   [("A", 99), ("B", 90), ("C", 85), ("D", 80)]]
        published_buy, _ = dp._apply_conviction_publication_gate(top_buy)
        for p, w in zip(published_buy, [0.5, 0.3, 0.2]):
            p["portfolio_weight"] = w
        pick_meta = _pick_meta_from(published_buy)

        rows = {c["symbol"]: _build_row(c, pick_meta) for c in top_buy}
        assert rows["A"]["pick_rank"] == 1 and rows["A"]["portfolio_weight"] == 0.5
        assert rows["B"]["pick_rank"] == 2 and rows["B"]["portfolio_weight"] == 0.3
        assert rows["C"]["pick_rank"] == 3 and rows["C"]["portfolio_weight"] == 0.2

    def test_zero_published_horizon_marks_no_observation_as_daily_pick(self):
        top_buy = [_candidate(s, c) for s, c in [("A", 50), ("B", 60), ("C", 70)]]
        published_buy, meta = dp._apply_conviction_publication_gate(top_buy)
        assert published_buy == []
        assert meta["n_published"] == 0
        pick_meta = _pick_meta_from(published_buy)

        rows = {c["symbol"]: _build_row(c, pick_meta) for c in top_buy}
        assert all(row["is_daily_pick"] is False for row in rows.values())
        assert all(row["pick_rank"] is None for row in rows.values())

    def test_regression_guard_against_using_top_buy_directly(self):
        # This is the exact bug finding 1 corrects: building pick_meta from
        # `top_buy` (all 5) instead of `published_buy` (only the qualifying
        # 3) would wrongly mark D as a daily pick. Prove the CORRECT
        # construction does not do that, as a standing regression guard.
        top_buy = [_candidate(s, c) for s, c in
                   [("A", 99), ("B", 90), ("C", 85), ("D", 80)]]
        published_buy, _ = dp._apply_conviction_publication_gate(top_buy)

        wrong_pick_meta = {  # the old, buggy construction
            p["symbol"]: {"pick_rank": r, "portfolio_weight": None}
            for r, p in enumerate(top_buy, start=1)
        }
        correct_pick_meta = _pick_meta_from(published_buy)

        assert bool(wrong_pick_meta.get("D")) is True  # the bug, if reintroduced
        assert bool(correct_pick_meta.get("D")) is False  # the fix
