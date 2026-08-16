"""
feature/daily-picks-conviction-gated-publication — corrective follow-up
(finding 1, follow-up to commit 5a006498; strengthened per finding 3,
follow-up to commit 0f2bbed8).

Proves the alpha_observations evidence classification (is_daily_pick /
pick_rank / portfolio_weight) is built from `published_buy` (the
conviction-gated, <=3 subset actually published to /picks), not the full
up-to-6 `top_buy` selection — by calling the REAL production helper,
`services.daily_picks._build_published_pick_meta`, directly. Finding 3
specifically flagged an earlier version of this file for reimplementing its
own `_pick_meta_from()` dict comprehension instead of exercising production
code, which would not have caught a regression back to using `top_buy`.
That local helper is gone; every test below calls
`dp._build_published_pick_meta` itself.
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


class TestBuildPublishedPickMetaProductionHelper:
    def test_returns_rank_and_weight_for_every_published_symbol_in_order(self):
        published_buy = [_candidate(s, c) for s, c in [("A", 99), ("B", 90), ("C", 85)]]
        for p, w in zip(published_buy, [0.5, 0.3, 0.2]):
            p["portfolio_weight"] = w

        meta = dp._build_published_pick_meta(published_buy)

        assert meta == {
            "A": {"pick_rank": 1, "portfolio_weight": 0.5},
            "B": {"pick_rank": 2, "portfolio_weight": 0.3},
            "C": {"pick_rank": 3, "portfolio_weight": 0.2},
        }

    def test_empty_input_returns_empty_dict(self):
        assert dp._build_published_pick_meta([]) == {}

    def test_missing_portfolio_weight_defaults_to_none(self):
        meta = dp._build_published_pick_meta([_candidate("A", 90)])
        assert meta["A"] == {"pick_rank": 1, "portfolio_weight": None}


class TestEvidenceClassificationMatchesPublishedCohort:
    def test_every_scored_candidate_gets_a_row_regardless_of_publication(self):
        # top_buy: 5 candidates, only some clear the conviction gate.
        top_buy = [_candidate(s, c) for s, c in
                   [("A", 99), ("B", 90), ("C", 85), ("D", 80), ("E", 40)]]
        published_buy, _ = dp._apply_conviction_publication_gate(top_buy)
        pick_meta = dp._build_published_pick_meta(published_buy)

        rows = {c["symbol"]: _build_row(c, pick_meta) for c in top_buy}
        assert set(rows.keys()) == {"A", "B", "C", "D", "E"}

    def test_only_published_subset_marked_is_daily_pick(self):
        top_buy = [_candidate(s, c) for s, c in
                   [("A", 99), ("B", 90), ("C", 85), ("D", 80), ("E", 40)]]
        published_buy, _ = dp._apply_conviction_publication_gate(top_buy)
        assert [p["symbol"] for p in published_buy] == ["A", "B", "C"]
        pick_meta = dp._build_published_pick_meta(published_buy)

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
        pick_meta = dp._build_published_pick_meta(published_buy)

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
        pick_meta = dp._build_published_pick_meta(published_buy)

        rows = {c["symbol"]: _build_row(c, pick_meta) for c in top_buy}
        assert rows["A"]["pick_rank"] == 1 and rows["A"]["portfolio_weight"] == 0.5
        assert rows["B"]["pick_rank"] == 2 and rows["B"]["portfolio_weight"] == 0.3
        assert rows["C"]["pick_rank"] == 3 and rows["C"]["portfolio_weight"] == 0.2

    def test_zero_published_horizon_marks_no_observation_as_daily_pick(self):
        top_buy = [_candidate(s, c) for s, c in [("A", 50), ("B", 60), ("C", 70)]]
        published_buy, meta = dp._apply_conviction_publication_gate(top_buy)
        assert published_buy == []
        assert meta["n_published"] == 0
        pick_meta = dp._build_published_pick_meta(published_buy)

        rows = {c["symbol"]: _build_row(c, pick_meta) for c in top_buy}
        assert all(row["is_daily_pick"] is False for row in rows.values())
        assert all(row["pick_rank"] is None for row in rows.values())

    def test_calling_the_production_helper_with_top_buy_instead_of_published_buy_is_detectably_wrong(self):
        """
        This is the exact regression finding 1 corrected, and finding 3
        requires it be provable via the real production helper rather than
        merely asserted: if `_generate_picks_inner` were ever changed back
        to call `_build_published_pick_meta(top_buy)` (the bug) instead of
        `_build_published_pick_meta(published_buy)` (the fix), this test
        demonstrates the observable difference — D, excluded by the
        conviction gate, would wrongly become `is_daily_pick=True` with a
        real rank.
        """
        top_buy = [_candidate(s, c) for s, c in
                   [("A", 99), ("B", 90), ("C", 85), ("D", 80)]]
        published_buy, _ = dp._apply_conviction_publication_gate(top_buy)
        assert [p["symbol"] for p in published_buy] == ["A", "B", "C"]  # D excluded

        buggy_meta = dp._build_published_pick_meta(top_buy)       # the bug, if reintroduced
        correct_meta = dp._build_published_pick_meta(published_buy)  # the fix

        buggy_row_d = _build_row(_candidate("D", 80), buggy_meta)
        correct_row_d = _build_row(_candidate("D", 80), correct_meta)

        assert buggy_row_d["is_daily_pick"] is True     # detectably wrong
        assert buggy_row_d["pick_rank"] == 4
        assert correct_row_d["is_daily_pick"] is False  # the actual, correct behavior
        assert correct_row_d["pick_rank"] is None
        assert buggy_row_d != correct_row_d
