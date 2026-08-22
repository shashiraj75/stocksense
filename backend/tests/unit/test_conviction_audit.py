"""
Tests for the conviction audit: the analytical contract, the executable
return measure's market-calendar logic, population construction, denominator
reconciliation, and dependence-aware inference.

Every test here is offline and synthetic. Nothing in this module opens a
database connection, calls a live price provider, or writes anything
anywhere — the audit is read-only by construction and its tests must
demonstrate that without depending on production.
"""

import datetime as _dt

import pytest

from services.alpha_engine import audit_calendar, audit_contract, audit_stats
from scripts import conviction_gate_backtest as cgb


UTC = _dt.timezone.utc


# ── Market calendar: the executable entry point ────────────────────────────

def test_next_tradable_open_skips_weekend_for_both_markets():
    """A Friday-evening run must enter on Monday's open, not Saturday."""
    friday_evening = _dt.datetime(2026, 7, 17, 23, 0, tzinfo=UTC)
    for market in ("US", "IN"):
        session, open_utc = audit_calendar.next_tradable_open(market, friday_evening)
        assert session.weekday() < 5, f"{market} entered on a weekend"
        assert open_utc > friday_evening
        assert (session - friday_evening.date()).days <= 4


def test_next_tradable_open_skips_us_holiday():
    """US Independence Day 2026-07-03 (observed) is not a tradable session."""
    before = _dt.datetime(2026, 7, 2, 23, 0, tzinfo=UTC)
    session, _ = audit_calendar.next_tradable_open("US", before)
    assert audit_calendar.is_session("US", session)
    assert session != _dt.date(2026, 7, 4)


def test_next_tradable_open_is_strictly_after_generation():
    """A run generated after a session's open must NOT get that open — the
    price is already un-transactable by then."""
    session, open_utc = audit_calendar.next_tradable_open(
        "US", _dt.datetime(2026, 7, 20, 12, 0, tzinfo=UTC))
    # 12:00 UTC precedes the 13:30 UTC open, so the same day is correct.
    assert session == _dt.date(2026, 7, 20)
    later, later_open = audit_calendar.next_tradable_open(
        "US", open_utc + _dt.timedelta(minutes=1))
    assert later > session, "a mid-session run must roll to the next session"


def test_us_calendar_reflects_dst_transition():
    """NYSE opens at 13:30 UTC under EDT and 14:30 UTC under EST. The audit
    must get this from the calendar, never from a hardcoded offset."""
    _, summer = audit_calendar.next_tradable_open(
        "US", _dt.datetime(2026, 7, 20, 0, 0, tzinfo=UTC))
    _, winter = audit_calendar.next_tradable_open(
        "US", _dt.datetime(2026, 12, 8, 0, 0, tzinfo=UTC))
    assert summer.hour == 13 and summer.minute == 30
    assert winter.hour == 14 and winter.minute == 30


def test_india_calendar_has_no_dst_shift():
    """India observes no DST — NSE opens at 03:45 UTC (09:15 IST) year round."""
    for month in (7, 12):
        _, o = audit_calendar.next_tradable_open(
            "IN", _dt.datetime(2026, month, 7, 0, 0, tzinfo=UTC))
        assert (o.hour, o.minute) == (3, 45)


def test_session_offset_counts_sessions_not_calendar_days():
    """5 trading days after a Monday is the following Monday, not Saturday."""
    start = _dt.date(2026, 7, 20)  # a Monday
    assert audit_calendar.session_offset("US", start, 5) == _dt.date(2026, 7, 27)


def test_session_offset_projects_forward_and_caller_enforces_completeness():
    """`session_offset` answers "which session is N sessions out?" and will
    happily name a FUTURE session — exchange calendars are generated years
    ahead. Completeness is therefore enforced by the caller's `today` guard,
    which must exclude (never truncate) a window that has not elapsed."""
    exit_date = audit_calendar.session_offset("US", _dt.date(2026, 7, 20), 60)
    assert exit_date is not None and exit_date > _dt.date(2026, 7, 20)

    # The caller is what refuses an unelapsed window.
    _, reason, detail = cgb.resolve_executable_return(
        _obs_row("AAPL", _dt.datetime(2026, 7, 20, 6, 0, tzinfo=UTC)), "US", "long",
        _EMPTY_SNAPSHOT, today=_dt.date(2026, 8, 1))
    assert reason == "horizon_window_not_yet_complete"
    assert detail["exit_session_date"] > "2026-08-01"


def test_unknown_market_raises_rather_than_guessing():
    with pytest.raises(audit_calendar.UnknownMarketError):
        audit_calendar.next_tradable_open("XX", _dt.datetime(2026, 7, 20, tzinfo=UTC))


# Helpers for the row/snapshot-based return measures. The measures now read a
# frozen price panel instead of calling a provider per observation, so a test
# that only exercises calendar/window logic supplies an EMPTY snapshot: the
# window guard must fire before any price is ever looked up.
from services.alpha_engine import audit_prices as _ap

_EMPTY_SNAPSHOT = _ap.PriceSnapshot().freeze()


def _obs_row(symbol, generated_at, reference_session_date=_dt.date(2026, 7, 20)):
    return {"symbol": symbol, "run_generated_at": generated_at,
            "reference_session_date": reference_session_date,
            "reference_price": 100.0}


# ── Return-measure labelling ───────────────────────────────────────────────

def test_research_measure_is_labelled_non_executable():
    text = audit_contract.RETURN_MEASURES[audit_contract.RESEARCH_PRIOR_CLOSE]
    assert "NON-EXECUTABLE" in text
    assert "Never investor P&L" in text


def test_both_measures_declare_they_are_gross_of_costs():
    for text in audit_contract.RETURN_MEASURES.values():
        assert "ross" in text, "each measure must state it is gross of costs"


def test_executable_measure_never_uses_a_pre_generation_price():
    """The executable entry session's OPEN must be strictly after the run
    timestamp — never a price that existed before the pick did."""
    run_at = _dt.datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
    _, reason, detail = cgb.resolve_executable_return(
        _obs_row("AAPL", run_at), "US", "short", _EMPTY_SNAPSHOT,
        today=_dt.date(2026, 7, 21))
    # Window is incomplete on that `today`, but the entry must still be sane.
    assert reason == "horizon_window_not_yet_complete"
    assert _dt.datetime.fromisoformat(detail["entry_session_open_utc"]) > run_at


def test_executable_measure_excludes_partial_window_rather_than_truncating():
    _, reason, _ = cgb.resolve_executable_return(
        _obs_row("AAPL", _dt.datetime(2026, 7, 20, 6, 0, tzinfo=UTC)), "US", "long",
        _EMPTY_SNAPSHOT, today=_dt.date(2026, 7, 25))
    assert reason == "horizon_window_not_yet_complete"


def test_executable_measure_excludes_missing_generation_timestamp():
    ret, reason, _ = cgb.resolve_executable_return(
        _obs_row("AAPL", None), "US", "short", _EMPTY_SNAPSHOT)
    assert ret is None and reason == "missing_run_generated_at"


# ── Populations ────────────────────────────────────────────────────────────

def _row(**kw):
    base = {"signal": "BUY", "signal_confidence": 90.0, "is_daily_pick": False}
    base.update(kw)
    return base


def test_high_conviction_is_measured_within_buy_only():
    """A high-confidence SELL must never land in BUY_HIGH_CONV — the gate
    question is P4-vs-P5 inside BUY, not BUY-vs-everything."""
    pops = cgb.assign_populations(_row(signal="SELL", signal_confidence=99.0))
    assert audit_contract.P_BUY_HIGH_CONV not in pops
    assert audit_contract.P_NON_BUY in pops


def test_buy_splits_cleanly_at_the_publication_threshold():
    assert audit_contract.P_BUY_HIGH_CONV in cgb.assign_populations(
        _row(signal_confidence=85.0))
    assert audit_contract.P_BUY_LOW_CONV in cgb.assign_populations(
        _row(signal_confidence=84.999))


def test_published_and_unpublished_buy_are_disjoint():
    pub = cgb.assign_populations(_row(is_daily_pick=True))
    unpub = cgb.assign_populations(_row(is_daily_pick=False))
    assert audit_contract.P_PUBLISHED in pub
    assert audit_contract.P_UNPUBLISHED_BUY not in pub
    assert audit_contract.P_UNPUBLISHED_BUY in unpub


def test_non_finite_confidence_is_not_treated_as_high_conviction():
    pops = cgb.assign_populations(_row(signal_confidence=float("nan")))
    assert audit_contract.P_BUY_HIGH_CONV not in pops
    assert audit_contract.P_BUY_LOW_CONV in pops


# ── Within-run ranking percentiles ─────────────────────────────────────────

def test_rank_percentiles_are_computed_within_a_run_not_across_runs():
    """Raw ranking_alpha is not comparable across runs; run B's low absolute
    scores must still span the full percentile range inside run B."""
    rows = [{"run_id": "A", "signal": "BUY", "ranking_alpha": a} for a in (10, 20, 30)]
    rows += [{"run_id": "B", "signal": "BUY", "ranking_alpha": a} for a in (1, 2, 3)]
    cgb.within_run_rank_percentile(rows)
    assert [r["rank_percentile"] for r in rows[:3]] == [0.0, 0.5, 1.0]
    assert [r["rank_percentile"] for r in rows[3:]] == [0.0, 0.5, 1.0]


def test_single_buy_run_gets_no_percentile():
    """A one-element percentile carries no information and must be None, not
    silently coded as a top-ranked 1.0."""
    rows = [{"run_id": "A", "signal": "BUY", "ranking_alpha": 5.0}]
    cgb.within_run_rank_percentile(rows)
    assert rows[0]["rank_percentile"] is None


# ── Denominator reconciliation ─────────────────────────────────────────────

def test_reconciliation_passes_when_counts_balance():
    audit_contract.assert_reconciles("cell", 100, 60, 40)


def test_reconciliation_raises_on_any_gap():
    """The exact failure mode that produced the superseded report's
    unexplained 331-row gap must now be loud, not silent."""
    with pytest.raises(audit_contract.ReconciliationError) as exc:
        audit_contract.assert_reconciles("US/short", 1358, 1020, 331)
    assert "refuses to report an unreconciled denominator" in str(exc.value)


def test_non_finite_returns_are_excluded_not_counted_as_losses():
    for bad in (float("nan"), float("inf"), float("-inf"), None, "x"):
        assert not cgb._finite(bad)
    assert cgb._finite(0.0) and cgb._finite(-1.5)


# ── Dependence-aware inference ─────────────────────────────────────────────

def _synthetic(n_dates, per_date, rate_a, rate_b):
    rows = []
    for d in range(n_dates):
        for i in range(per_date):
            rows.append({"group": "A", "is_win": i < rate_a * per_date,
                         "cluster_date": f"2026-07-{d + 1:02d}", "symbol": f"S{i}"})
            rows.append({"group": "B", "is_win": i < rate_b * per_date,
                         "cluster_date": f"2026-07-{d + 1:02d}", "symbol": f"S{i}"})
    return rows


def test_date_block_bootstrap_is_deterministic_given_a_seed():
    rows = _synthetic(25, 10, 0.63, 0.60)
    a = audit_stats.date_block_bootstrap("t", rows, draws=500, seed=7)
    b = audit_stats.date_block_bootstrap("t", rows, draws=500, seed=7)
    assert a.block_ci_pp == b.block_ci_pp
    assert a.block_p_value == b.block_p_value


def test_inference_fails_closed_below_the_cluster_floor():
    """13 distinct US session dates cannot support a significance claim,
    however many rows sit inside them."""
    rows = _synthetic(13, 60, 0.63, 0.60)
    res = audit_stats.date_block_bootstrap("t", rows, draws=400, seed=1)
    assert res.n_clusters == 13
    assert res.clusters_adequate is False
    assert res.inference_permitted is False
    assert any("NO significance claim" in n for n in res.notes)


def test_adequate_clusters_permit_inference():
    rows = _synthetic(30, 20, 0.70, 0.50)
    res = audit_stats.date_block_bootstrap("t", rows, draws=400, seed=1)
    assert res.clusters_adequate and res.inference_permitted


def test_a_fail_closed_result_can_never_be_classified_proven():
    rows = _synthetic(13, 60, 0.90, 0.10)
    res = audit_stats.date_block_bootstrap("t", rows, draws=400, seed=1)
    assert cgb.classify_claim(res) != audit_contract.PROVEN


def test_method_disagreement_caps_the_claim_below_proven():
    rows = _synthetic(30, 20, 0.70, 0.50)
    res = audit_stats.date_block_bootstrap("t", rows, draws=400, seed=1)
    res.naive_p_value = 0.30          # naive says null
    res.block_p_value = 0.001         # blocked says signal
    res = audit_stats.reconcile_methods(res)
    assert res.methods_agree is False
    # Disagreement caps below PROVEN. With a significant two-way permutation
    # p-value and a sign-stable jackknife the cap lands on PRELIMINARY.
    res.identifiability = audit_stats.IDENTIFIABLE
    res.permutation_p_two_way = 0.001
    res.jackknife = {"sign_stable": True}
    assert cgb.classify_claim(res) == audit_contract.PRELIMINARY


def test_symbol_jackknife_reports_sign_stability():
    rows = _synthetic(20, 8, 0.75, 0.25)
    jk = audit_stats.symbol_cluster_jackknife(rows)
    assert jk["sign_stable"] is True
    assert jk["n_symbols"] == 8


def test_holm_correction_is_applied_across_the_family():
    p = {"a": 0.01, "b": 0.04, "c": 0.20}
    out = audit_stats.holm_correction(p)
    assert out["a"]["adjusted_p"] == pytest.approx(0.03)
    assert out["c"]["reject"] is False
    assert all(v["family_size"] == 3 for v in out.values())


def test_holm_skips_comparisons_with_no_permitted_inference():
    """A fail-closed comparison must not consume family budget."""
    out = audit_stats.holm_correction({"a": 0.01, "b": None})
    assert out["b"]["adjusted_p"] is None
    assert out["a"]["family_size"] == 1


def test_minimum_detectable_effect_is_reported_for_null_results():
    mde = audit_stats.minimum_detectable_effect(500, 500, 0.60)
    assert mde is not None and 0 < mde < 100


# ── Contract invariants ────────────────────────────────────────────────────

def test_superseded_claims_are_recorded_with_corrected_levels():
    c = audit_contract.SUPERSEDED_CLAIMS
    assert c["US edge confirmed"][0] == audit_contract.PRELIMINARY
    assert c["13,988 sample size"][0] == audit_contract.FALSE
    assert c["edge is broad across sectors"][0] == audit_contract.NOT_REPRODUCIBLE
    assert c["alpha_observations is completely empty"][0] == audit_contract.FALSE
    assert c["look-ahead bias in the backtest"][0] == audit_contract.FALSE


def test_claim_rejects_an_undefined_level():
    with pytest.raises(ValueError):
        audit_contract.Claim(key="k", level="DEFINITELY_TRUE", statement="s")


def test_india_and_us_are_never_pooled_by_the_contract():
    """Both markets must have their own calendar — the mechanism that makes
    a cross-market baseline impossible by construction."""
    assert audit_calendar._MARKET_CALENDAR["US"] != audit_calendar._MARKET_CALENDAR["IN"]


def test_audit_bundle_refuses_to_write_inside_the_repository():
    """No generated data file may ever land in the working tree."""
    import pathlib
    repo_root = pathlib.Path(cgb.__file__).resolve().parents[2]
    with pytest.raises(ValueError) as exc:
        cgb.write_audit_bundle(repo_root / "audit_out", [], manifest={},
                               integrity={}, reconstruction={}, holm={})
    assert "inside the repository" in str(exc.value)


def test_calculation_version_and_default_seed_are_pinned():
    assert audit_contract.CALCULATION_VERSION.startswith("conviction-audit-")
    assert isinstance(audit_stats.DEFAULT_SEED, int)


def test_permanently_not_reproducible_gaps_are_recorded_honestly():
    joined = " ".join(audit_contract.PERMANENTLY_NOT_REPRODUCIBLE)
    assert "331-row" in joined and "PANW" in joined
