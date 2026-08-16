"""
V-VAL1 — per-stock BUY-metric cohort integrity.

Schema/insertion-invariant finding (documented, not assumed): every row
`run_validation()` ever writes to val_signals already has a fully computed,
non-null `correct` and `fwd_return_pct` — a signal-date window whose
evidence is unavailable is skipped via `continue` in _backtest_stock()
BEFORE being appended to the in-memory signals list, so it is never
persisted at all (see validation_engine.py's per-window loop). There is
currently no code path that inserts a val_signals row with a NULL
`correct` or `fwd_return_pct`.

However, the DDL does not enforce NOT NULL on either column, so this is an
application invariant, not a schema guarantee. get_per_stock_results()
must therefore compute its evaluated cohort defensively (explicit
`correct IS NOT NULL` / `fwd_return_pct IS NOT NULL` filters), not rely on
the invariant holding forever — and must expose the evaluated denominator
distinctly from the raw BUY count, so a future violation of the invariant
(a manual insert, a schema change, a future code path) would be visibly
honest in the API response rather than silently miscounted.

Root cause of the original defect: hit_rate_pct divided `correct`/`total`
across ALL predictions (BUY+SELL+HOLD) for the symbol, not just the BUY
subset — producing fractional hit rates (e.g. 40.2%) even for a stock
with a single BUY signal, where the true BUY hit rate can only be 0% or
100%.
"""

import sqlite3

import pytest

import services.validation_engine as ve


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "validation_results_test.db")
    monkeypatch.setattr(ve, "_DB_PATH", db_path)
    monkeypatch.setattr(ve, "_db_initialised", False)
    ve._init_db()
    return db_path


def _insert_run(db_path, horizon="medium", universe="nifty100"):
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
            "VALUES ('2026-08-01T00:00:00', ?, 1, 0, '{}', ?)",
            (horizon, universe),
        )
        return cur.lastrowid


def _insert_signal(db_path, run_id, symbol, horizon, predicted, correct, fwd_return_pct=1.0,
                    signal_date="2026-01-01"):
    """correct / fwd_return_pct may each be passed as None to simulate a
    hypothetical malformed/unresolved row — the application itself never
    produces one today (see module docstring), but the SQL must still
    handle it defensively rather than assume it can't happen."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO val_signals
               (run_id, symbol, horizon, signal_date, composite_score, tech_score,
                rs_score, obv_score, mfi_score, predicted, fwd_return_pct,
                nifty_fwd_ret_pct, alpha_pct, actual_direction, correct)
               VALUES (?, ?, ?, ?, 65, 60, 60, 60, 60, ?, ?, 0, 0, 'UP', ?)""",
            (run_id, symbol, horizon, signal_date, predicted, fwd_return_pct, correct),
        )


# ── 1-4: basic evaluated-cohort hit-rate correctness ─────────────────────────

def test_single_correct_buy_signal_is_100_percent(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "FOO", "medium", "BUY", correct=1)
    _insert_signal(isolated_db, run_id, "FOO", "medium", "SELL", correct=0)
    _insert_signal(isolated_db, run_id, "FOO", "medium", "HOLD", correct=0)
    _insert_signal(isolated_db, run_id, "FOO", "medium", "HOLD", correct=0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    foo = next(r for r in rows if r["symbol"] == "FOO")

    assert foo["buy_signal_count"] == 1
    assert foo["evaluated_buy_count"] == 1
    assert foo["hit_rate_pct"] == 100.0


def test_single_wrong_buy_signal_is_0_percent(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "BAR", "medium", "BUY", correct=0)
    _insert_signal(isolated_db, run_id, "BAR", "medium", "SELL", correct=1)
    _insert_signal(isolated_db, run_id, "BAR", "medium", "SELL", correct=1)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    bar = next(r for r in rows if r["symbol"] == "BAR")

    assert bar["buy_signal_count"] == 1
    assert bar["evaluated_buy_count"] == 1
    assert bar["hit_rate_pct"] == 0.0


def test_sell_and_hold_rows_cannot_affect_buy_hit_rate(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "QUX", "medium", "BUY", correct=1)
    _insert_signal(isolated_db, run_id, "QUX", "medium", "BUY", correct=1)
    _insert_signal(isolated_db, run_id, "QUX", "medium", "SELL", correct=0)
    _insert_signal(isolated_db, run_id, "QUX", "medium", "SELL", correct=0)
    _insert_signal(isolated_db, run_id, "QUX", "medium", "HOLD", correct=0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    qux = next(r for r in rows if r["symbol"] == "QUX")

    # 2/2 BUY correct = 100%, regardless of the 3 wrong non-BUY rows.
    assert qux["evaluated_buy_count"] == 2
    assert qux["hit_rate_pct"] == 100.0


def test_multi_buy_hit_rate_matches_evaluated_ratio_exactly(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "MULTI", "medium", "BUY", correct=1)
    _insert_signal(isolated_db, run_id, "MULTI", "medium", "BUY", correct=1)
    _insert_signal(isolated_db, run_id, "MULTI", "medium", "BUY", correct=0)
    _insert_signal(isolated_db, run_id, "MULTI", "medium", "SELL", correct=0)
    _insert_signal(isolated_db, run_id, "MULTI", "medium", "SELL", correct=0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    multi = next(r for r in rows if r["symbol"] == "MULTI")

    assert multi["evaluated_buy_count"] == 3
    assert multi["hit_rate_pct"] == pytest.approx(66.7, abs=0.1)


# ── 5-7: defensive null-correctness handling (hypothetical malformed rows) ──

def test_buy_row_with_null_correctness_is_excluded_from_evaluated_denominator(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "PEND", "medium", "BUY", correct=1)
    _insert_signal(isolated_db, run_id, "PEND", "medium", "BUY", correct=None)  # hypothetical unresolved row

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    pend = next(r for r in rows if r["symbol"] == "PEND")

    # buy_signal_count counts ALL BUY rows; evaluated_buy_count excludes
    # the null-correctness one — they must differ, and the null row must
    # NOT be silently counted as a loss (which would make hit_rate 50%).
    assert pend["buy_signal_count"] == 2
    assert pend["evaluated_buy_count"] == 1
    assert pend["hit_rate_pct"] == 100.0


def test_total_buy_count_can_differ_from_evaluated_buy_count(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "DIFF", "medium", "BUY", correct=None)
    _insert_signal(isolated_db, run_id, "DIFF", "medium", "BUY", correct=None)
    _insert_signal(isolated_db, run_id, "DIFF", "medium", "BUY", correct=1)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    diff = next(r for r in rows if r["symbol"] == "DIFF")

    assert diff["buy_signal_count"] == 3
    assert diff["evaluated_buy_count"] == 1
    assert diff["hit_rate_pct"] == 100.0


def test_no_evaluated_buy_outcomes_is_null_not_fabricated(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "ALLNULL", "medium", "BUY", correct=None)
    _insert_signal(isolated_db, run_id, "ALLNULL", "medium", "BUY", correct=None)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    allnull = next(r for r in rows if r["symbol"] == "ALLNULL")

    assert allnull["buy_signal_count"] == 2
    assert allnull["evaluated_buy_count"] == 0
    assert allnull["hit_rate_pct"] is None


def test_hit_rate_pct_is_none_with_no_buy_signals(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "BAZ", "medium", "SELL", correct=1)
    _insert_signal(isolated_db, run_id, "BAZ", "medium", "HOLD", correct=0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    baz = next(r for r in rows if r["symbol"] == "BAZ")

    assert baz["buy_signal_count"] == 0
    assert baz["evaluated_buy_count"] == 0
    assert baz["hit_rate_pct"] is None


# ── 8-10: average-return cohort — separate, explicit, defensive ─────────────

def test_null_return_excluded_from_average_return_denominator(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "RET", "medium", "BUY", correct=1, fwd_return_pct=10.0)
    _insert_signal(isolated_db, run_id, "RET", "medium", "BUY", correct=1, fwd_return_pct=None)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    ret = next(r for r in rows if r["symbol"] == "RET")

    assert ret["buy_return_count"] == 1
    assert ret["buy_avg_return_pct"] == 10.0


def test_average_return_uses_documented_cohort_not_all_predictions(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "COHORT", "medium", "BUY", correct=1, fwd_return_pct=4.0)
    _insert_signal(isolated_db, run_id, "COHORT", "medium", "BUY", correct=1, fwd_return_pct=6.0)
    _insert_signal(isolated_db, run_id, "COHORT", "medium", "SELL", correct=1, fwd_return_pct=-50.0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    c = next(r for r in rows if r["symbol"] == "COHORT")

    # buy_avg_return_pct must be the BUY-only average (5.0), unaffected by
    # the SELL row's -50% return.
    assert c["buy_return_count"] == 2
    assert c["buy_avg_return_pct"] == 5.0


def test_zero_valid_returns_is_none_not_fabricated(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "NORET", "medium", "BUY", correct=1, fwd_return_pct=None)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    noret = next(r for r in rows if r["symbol"] == "NORET")

    assert noret["buy_return_count"] == 0
    assert noret["buy_avg_return_pct"] is None


# ── 11, 14: SQLite execution + deterministic ordering ────────────────────────

def test_deterministic_ordering_by_avg_buy_return_desc(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "LOW", "medium", "BUY", correct=1, fwd_return_pct=1.0)
    _insert_signal(isolated_db, run_id, "HIGH", "medium", "BUY", correct=1, fwd_return_pct=9.0)
    _insert_signal(isolated_db, run_id, "MID", "medium", "BUY", correct=1, fwd_return_pct=5.0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    symbols_with_buys = [r["symbol"] for r in rows if r["buy_signal_count"] > 0]
    assert symbols_with_buys == ["HIGH", "MID", "LOW"]

    # Repeated execution must be byte-identical (no nondeterministic tie-break).
    rows2 = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    assert rows == rows2


# ── 13: API-facing field presence (serialization contract) ──────────────────

def test_result_dict_exposes_all_required_evaluated_cohort_fields(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "FIELDS", "medium", "BUY", correct=1, fwd_return_pct=2.0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "FIELDS")

    for key in ("symbol", "total_signals", "buy_signal_count", "evaluated_buy_count",
                "buy_hits", "correct", "hit_rate_pct", "buy_return_count", "buy_avg_return_pct",
                "avg_fwd_return_pct"):
        assert key in r, f"missing field: {key}"


def test_legacy_correct_field_preserved_as_compatible_alias_of_buy_hits(isolated_db):
    """V-VAL1 correction (section 5): get_per_stock_results is a public API
    surface (/api/validation/results/stocks, /results/stock/{symbol}) — the
    pre-V-VAL1 'correct' key must remain present with the same value as the
    new 'buy_hits' field, not silently disappear, even though this repo's
    own frontend no longer reads it."""
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "LEGACY", "medium", "BUY", correct=1)
    _insert_signal(isolated_db, run_id, "LEGACY", "medium", "BUY", correct=0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "LEGACY")

    assert r["correct"] == r["buy_hits"] == 1


def test_deterministic_ordering_has_explicit_symbol_tie_breaker(isolated_db):
    """Two stocks with an identical avg BUY return must sort in a stable,
    deterministic order (alphabetical by symbol), not database-insertion
    order, which SQL does not guarantee absent an explicit tie-breaker."""
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "ZEBRA", "medium", "BUY", correct=1, fwd_return_pct=5.0)
    _insert_signal(isolated_db, run_id, "ALPHA", "medium", "BUY", correct=1, fwd_return_pct=5.0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    symbols = [r["symbol"] for r in rows if r["symbol"] in ("ZEBRA", "ALPHA")]
    assert symbols == ["ALPHA", "ZEBRA"]


# ═════════════════════════════════════════════════════════════════════════
# V-VAL1 correction — AGGREGATE evaluated-BUY-cohort fields in
# _compute_metrics(). The prior implementation exposed the aggregate
# buy_hit_rate_pct/buy_signals fields but nothing distinguishing an
# "evaluated" cohort from the raw predicted-BUY count, so the frontend's
# Wilson interval had no honest n/hits to compute from. These tests build
# signal dicts directly (the same shape _backtest_stock() appends to
# all_signals) and call _compute_metrics() itself — no DB involved,
# _compute_metrics operates purely on the in-memory signal list.
# ═════════════════════════════════════════════════════════════════════════

from services.validation_engine import _compute_metrics


def _signal(predicted, correct, fwd_return_pct=1.0, signal_date="2026-01-01", **overrides):
    base = {
        "symbol": "X", "horizon": "medium", "signal_date": signal_date,
        "composite_score": 65, "tech_score": 60, "rs_score": 60, "obv_score": 60, "mfi_score": 60,
        "predicted": predicted, "fwd_return_pct": fwd_return_pct, "nifty_fwd_ret_pct": 0.0,
        "alpha_pct": fwd_return_pct, "actual_direction": "UP", "correct": correct,
    }
    base.update(overrides)
    return base


def test_aggregate_evaluated_buy_count_equals_buy_count_when_all_resolved():
    signals = [
        _signal("BUY", correct=1), _signal("BUY", correct=0), _signal("BUY", correct=1),
        _signal("SELL", correct=1), _signal("HOLD", correct=0),
    ]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["buy_signal_count"] == 3
    assert m["evaluated_buy_count"] == 3
    assert m["buy_hits"] == 2
    assert m["buy_hit_rate_pct"] == pytest.approx(66.7, abs=0.1)


def test_aggregate_total_buy_count_can_differ_from_evaluated_buy_count():
    signals = [
        _signal("BUY", correct=1),
        _signal("BUY", correct=None),  # hypothetical unresolved row
        _signal("BUY", correct=None),
    ]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["buy_signal_count"] == 3
    assert m["evaluated_buy_count"] == 1
    assert m["buy_hits"] == 1
    # 1/1 evaluated, not 1/3 — the two unresolved rows must not drag the
    # rate down to 33.3% or be silently counted as losses.
    assert m["buy_hit_rate_pct"] == 100.0


def test_aggregate_no_evaluated_buys_is_null_not_fabricated():
    signals = [
        _signal("BUY", correct=None), _signal("BUY", correct=None),
        _signal("SELL", correct=1),
    ]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["buy_signal_count"] == 2
    assert m["evaluated_buy_count"] == 0
    assert m["buy_hits"] == 0
    assert m["buy_hit_rate_pct"] is None


def test_aggregate_buy_return_count_excludes_null_returns():
    signals = [
        _signal("BUY", correct=1, fwd_return_pct=10.0),
        _signal("BUY", correct=1, fwd_return_pct=None),
    ]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["buy_return_count"] == 1


def test_aggregate_retained_unevaluated_signal_counts_only_toward_total():
    """V-PS2A — the exact shape _backtest_stock() now persists for a valid
    entry whose exit price never became observable: predicted=BUY,
    correct=None, fwd_return_pct=None, alpha_pct=None together. It must
    count toward buy_signal_count (the prediction existed) and toward
    NOTHING else — not evaluated_buy_count, not buy_hits, not
    buy_return_count — via the real _compute_metrics() boundary, not a
    re-derivation of the formula."""
    signals = [
        _signal("BUY", correct=1, fwd_return_pct=5.0),
        _signal("BUY", correct=None, fwd_return_pct=None, alpha_pct=None, actual_direction=None),
    ]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["buy_signal_count"] == 2
    assert m["evaluated_buy_count"] == 1
    assert m["buy_hits"] == 1
    assert m["buy_return_count"] == 1
    assert m["buy_hit_rate_pct"] == 100.0


def test_aggregate_hit_rate_is_never_reconstructed_from_a_rounded_percentage():
    # 2/3 = 66.666...% which rounds to 66.7% — buy_hits must be the exact
    # integer 2, not round(66.7/100 * 3) which could silently diverge for
    # other fractions.
    signals = [_signal("BUY", correct=1), _signal("BUY", correct=1), _signal("BUY", correct=0)]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["buy_hits"] == 2
    assert isinstance(m["buy_hits"], int)


def test_predicted_class_counts_are_not_labeled_as_actual_class_distribution():
    # buy_signals/sell_signals/hold_signals are PREDICTED-class counts
    # (what the model called), not ground-truth actual labels — this test
    # exists to fail loudly if a future change ever renames these fields
    # to imply otherwise (e.g. "actual_buy_count") without a genuine
    # actual-label source backing it.
    signals = [_signal("BUY", correct=1), _signal("SELL", correct=1), _signal("HOLD", correct=1)]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert "actual_buy_count" not in m
    assert "actual_sell_count" not in m
    assert "actual_hold_count" not in m
    assert "majority_class_accuracy_pct" not in m


# ═════════════════════════════════════════════════════════════════════════
# V-VAL1 p-value correction phase — audit of every _hit_rate()/_avg_ret()
# consumer beyond buy_hit_rate_pct itself, per the correction's required
# sections 5-6. sell_hit_rate_pct/overall_accuracy_pct/score_buckets/
# confidence_buckets/sell_confidence_buckets all route through the shared
# _hit_rate() helper (now excludes None); avg_return_on_buy_pct/
# avg_return_on_sell_pct/score_buckets'/confidence_buckets' avg_return_pct
# route through _avg_ret() (now excludes None); profitable_buy_pct/
# sharpe_on_buys/buy_outperformance_pct consume buy_rets, which is already
# the null-filtered buy_return_signals list (fixed in the prior V-VAL1
# session alongside _hit_rate/_avg_ret — this section proves it end to
# end via _compute_metrics rather than re-deriving it).
# ═════════════════════════════════════════════════════════════════════════

def test_overall_accuracy_excludes_null_correctness_across_all_predicted_classes():
    signals = [
        _signal("BUY", correct=1), _signal("BUY", correct=None),
        _signal("SELL", correct=0), _signal("SELL", correct=None),
        _signal("HOLD", correct=1), _signal("HOLD", correct=None),
    ]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    # 3 evaluated (1 correct BUY, 1 wrong SELL, 1 correct HOLD) of 6 total
    # rows -> 2/3 = 66.7%, not 2/6 = 33.3% (which would treat the 3 null
    # rows as failures) and not a crash.
    assert m["overall_accuracy_pct"] == pytest.approx(66.7, abs=0.1)


def test_overall_accuracy_is_none_when_no_signal_is_evaluated():
    signals = [_signal("BUY", correct=None), _signal("SELL", correct=None)]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["overall_accuracy_pct"] is None


def test_sell_hit_rate_excludes_null_correctness():
    signals = [_signal("SELL", correct=1), _signal("SELL", correct=0), _signal("SELL", correct=None)]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["sell_hit_rate_pct"] == 50.0


def test_sell_hit_rate_is_none_with_no_sell_signals():
    signals = [_signal("BUY", correct=1)]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    assert m["sell_hit_rate_pct"] is None


def test_score_bucket_hit_rate_and_return_exclude_null_rows():
    signals = [
        _signal("BUY", correct=1, fwd_return_pct=5.0, composite_score=62),
        _signal("BUY", correct=None, fwd_return_pct=None, composite_score=63),
        _signal("BUY", correct=0, fwd_return_pct=-2.0, composite_score=64),
    ]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    bucket = next(b for b in m["score_buckets"] if b["score_range"] == "60–65")
    assert bucket["count"] == 3  # raw bucket membership, unaffected
    assert bucket["hit_rate_pct"] == 50.0  # 1/2 evaluated, not 1/3
    assert bucket["avg_return_pct"] == 1.5  # mean(5.0, -2.0), null excluded


def test_profitable_buy_pct_and_sharpe_use_valid_return_cohort_only():
    signals = [
        _signal("BUY", correct=1, fwd_return_pct=5.0),
        _signal("BUY", correct=1, fwd_return_pct=-1.0),
        _signal("BUY", correct=1, fwd_return_pct=None),
    ]
    m = _compute_metrics(signals, benchmark_return_pct=0.5, horizon="medium")
    # 1 of 2 VALID-RETURN buys is profitable -> 50%, not 1/3 = 33.3% and
    # not a crash from averaging a None into the Sharpe calculation.
    assert m["profitable_buy_pct"] == 50.0
    assert m["sharpe_on_buys"] is not None


def test_buy_outperformance_uses_valid_return_cohort_and_stays_none_if_empty():
    signals = [_signal("BUY", correct=1, fwd_return_pct=None)]
    m = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium")
    assert m["avg_return_on_buy_pct"] is None
    assert m["buy_outperformance_pct"] is None


# ═════════════════════════════════════════════════════════════════════════
# V-PS2 — historical per-stock aggregation must exclude non-finite
# fwd_return_pct from every denominator (evaluated_buy_count, buy_hits,
# buy_return_count, avg_ret, buy_avg_ret), and must never let AVG() over a
# single NaN/Infinity row poison that symbol's aggregate — root-caused
# against actual production runs 227/228 (see V-PS1). A row whose
# fwd_return_pct is NaN/Infinity has a `correct` value that was derived
# from a NaN alpha (alpha = NaN - benchmark => NaN; `NaN > 0` is False in
# Python) — a FABRICATED miss, not a genuine evaluated outcome — so such a
# row must be excluded from evaluated_buy_count/buy_hits too, not just from
# the return average.
# ═════════════════════════════════════════════════════════════════════════

import math


def test_nan_fwd_return_excluded_from_avg_return_not_poisoning_the_whole_symbol(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "POISON", "medium", "BUY", correct=1, fwd_return_pct=10.0)
    _insert_signal(isolated_db, run_id, "POISON", "medium", "BUY", correct=0, fwd_return_pct=float("nan"))

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "POISON")

    assert r["avg_fwd_return_pct"] is not None and math.isfinite(r["avg_fwd_return_pct"])
    assert r["buy_avg_return_pct"] is not None and math.isfinite(r["buy_avg_return_pct"])
    # The NaN row must not count as a valid return: only the one finite
    # 10.0 return contributes, so the average must be exactly 10.0, not
    # NaN and not a fabricated blend.
    assert r["avg_fwd_return_pct"] == 10.0
    assert r["buy_avg_return_pct"] == 10.0
    assert r["buy_return_count"] == 1


def test_nan_fwd_return_row_excluded_from_evaluated_buy_count_and_hits(isolated_db):
    """The core fabricated-miss defect: a BUY row whose fwd_return_pct is
    NaN was persisted with correct=0 (a false miss derived from a NaN
    alpha) — this row must NOT be counted as a genuine evaluated outcome
    at all, in either direction."""
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "GHOST", "medium", "BUY", correct=1, fwd_return_pct=5.0)
    _insert_signal(isolated_db, run_id, "GHOST", "medium", "BUY", correct=0, fwd_return_pct=float("nan"))

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "GHOST")

    assert r["buy_signal_count"] == 2  # the raw BUY count is unaffected
    assert r["evaluated_buy_count"] == 1  # the NaN row is excluded, not counted as a miss
    assert r["buy_hits"] == 1
    assert r["hit_rate_pct"] == 100.0  # not 50.0, which would count the fabricated miss


def test_infinity_fwd_return_also_excluded(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "INFSYM", "medium", "BUY", correct=1, fwd_return_pct=float("inf"))
    _insert_signal(isolated_db, run_id, "INFSYM", "medium", "BUY", correct=1, fwd_return_pct=-float("inf"))
    _insert_signal(isolated_db, run_id, "INFSYM", "medium", "BUY", correct=1, fwd_return_pct=3.0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "INFSYM")

    assert r["buy_return_count"] == 1
    assert r["buy_avg_return_pct"] == 3.0
    assert r["evaluated_buy_count"] == 1
    assert r["hit_rate_pct"] == 100.0


def test_hold_row_with_nan_return_does_not_affect_buy_metrics(isolated_db):
    """A non-BUY row's non-finite return must not leak into BUY-only
    denominators — it only affects the ALL-signal avg_fwd_return_pct,
    which must also exclude it (never poison symbol-wide avg either)."""
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "MIXED", "medium", "BUY", correct=1, fwd_return_pct=8.0)
    _insert_signal(isolated_db, run_id, "MIXED", "medium", "HOLD", correct=0, fwd_return_pct=float("nan"))

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "MIXED")

    assert r["buy_return_count"] == 1
    assert r["buy_avg_return_pct"] == 8.0
    assert r["avg_fwd_return_pct"] == 8.0  # HOLD's NaN excluded from the all-signal average too


def test_symbol_with_only_nonfinite_outcomes_returns_unavailable_not_zero(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "ALLBAD", "medium", "BUY", correct=0, fwd_return_pct=float("nan"))
    _insert_signal(isolated_db, run_id, "ALLBAD", "medium", "BUY", correct=1, fwd_return_pct=float("inf"))

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "ALLBAD")

    assert r["buy_signal_count"] == 2
    assert r["evaluated_buy_count"] == 0
    assert r["buy_hits"] is None  # SUM() over zero evaluated rows is SQL NULL, never a fabricated 0
    assert r["hit_rate_pct"] is None  # never a fabricated 0%
    assert r["buy_return_count"] == 0
    assert r["buy_avg_return_pct"] is None


def test_symbol_with_valid_and_invalid_rows_only_counts_valid_ones(isolated_db):
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "PARTIAL", "medium", "BUY", correct=1, fwd_return_pct=4.0)
    _insert_signal(isolated_db, run_id, "PARTIAL", "medium", "BUY", correct=0, fwd_return_pct=6.0)
    _insert_signal(isolated_db, run_id, "PARTIAL", "medium", "BUY", correct=0, fwd_return_pct=float("nan"))

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "PARTIAL")

    assert r["buy_signal_count"] == 3
    assert r["evaluated_buy_count"] == 2
    assert r["buy_hits"] == 1
    assert r["hit_rate_pct"] == 50.0
    assert r["buy_return_count"] == 2
    assert r["buy_avg_return_pct"] == 5.0


def test_control_symbol_with_no_invalid_rows_completely_unaffected(isolated_db):
    """A symbol with zero non-finite rows must produce byte-identical
    results to the pre-fix formula — this correction changes nothing
    about valid-row arithmetic."""
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "CLEAN", "medium", "BUY", correct=1, fwd_return_pct=2.0)
    _insert_signal(isolated_db, run_id, "CLEAN", "medium", "BUY", correct=1, fwd_return_pct=4.0)

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    r = next(x for x in rows if x["symbol"] == "CLEAN")

    assert r["buy_signal_count"] == 2
    assert r["evaluated_buy_count"] == 2
    assert r["buy_hits"] == 2
    assert r["hit_rate_pct"] == 100.0
    assert r["buy_return_count"] == 2
    assert r["buy_avg_return_pct"] == 3.0


def test_sqlite_actually_stores_and_compares_nan_as_expected(isolated_db):
    """Explicit proof (not assumption) that SQLite's REAL storage of a
    Python float('nan') round-trips as a value whose self-equality is
    never truthy — SQLite returns NULL (not 0) for `NaN = NaN`, a
    genuinely different representation than PostgreSQL's `false`, but
    functionally equivalent inside `CASE WHEN ... THEN x END`: SQLite
    treats a NULL condition as not-taken, identical in effect to `false`.
    This is exactly what the finite-value predicate this fix relies on
    (`x = x`) depends on across both dialects — proven here, not assumed."""
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "SQLITECHK", "medium", "BUY", correct=1, fwd_return_pct=float("nan"))

    with sqlite3.connect(isolated_db) as conn:
        row = conn.execute(
            "SELECT fwd_return_pct = fwd_return_pct FROM val_signals WHERE symbol='SQLITECHK'"
        ).fetchone()
    assert row[0] in (0, None)  # never truthy (1) for a NaN — SQLite's own falsy representation is NULL


def test_deterministic_ordering_and_symbol_tiebreak_unaffected_by_nan_symbol(isolated_db):
    """A poisoned symbol must not disturb the deterministic ordering of
    the other, unaffected symbols."""
    run_id = _insert_run(isolated_db)
    _insert_signal(isolated_db, run_id, "ZEBRA", "medium", "BUY", correct=1, fwd_return_pct=5.0)
    _insert_signal(isolated_db, run_id, "ALPHA", "medium", "BUY", correct=1, fwd_return_pct=5.0)
    _insert_signal(isolated_db, run_id, "POISONED", "medium", "BUY", correct=0, fwd_return_pct=float("nan"))

    rows = ve.get_per_stock_results(run_id=run_id, horizon="medium", universe="nifty100")
    symbols = [r["symbol"] for r in rows if r["symbol"] in ("ZEBRA", "ALPHA")]
    assert symbols == ["ALPHA", "ZEBRA"]
