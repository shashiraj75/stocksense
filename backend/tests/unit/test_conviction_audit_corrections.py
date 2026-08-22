"""
Regression tests for the CORRECTED conviction audit (DP-037, calculation
version 2.0.0).

Every test here exists because an independent review found the first
implementation asserting something its method could not support. Each one
pins a specific correction so it cannot silently regress:

  * a sign-unstable jackknife can never reach PROVEN,
  * inadequate clusters / empty groups / an immature era yield
    NOT_IDENTIFIABLE, never an automatic PRELIMINARY,
  * published-vs-unpublished uses only run-matched rows inside ONE policy era,
  * unmatched runs reconcile through explicit, recorded exclusions,
  * tied ranking scores receive tie-aware (average) ranks,
  * provider requests scale with unique symbols and batch size, not with the
    number of observations,
  * provider failures trip the missingness guard,
  * the manifest carries every reproducibility field,
  * the CLI genuinely populates the data-integrity results,
  * every reported aggregate reconstructs from row_decisions.jsonl,
  * a closure bundle is refused unless its directory is EMPTY (one bundle ==
    one invocation), and both markets run in ONE invocation,
  * both concrete exchange-holiday/DST cases resolve correctly,
  * UI copy cannot claim completed full-population evidence while the live
    audit is still pending.

No test here queries production, calls a live provider, or hardcodes a live
row count.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
for p in (str(BACKEND), str(BACKEND / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import conviction_gate_backtest as cgb  # noqa: E402
from services.alpha_engine import (  # noqa: E402
    audit_calendar, audit_contract, audit_prices, audit_stats,
)

UTC = _dt.timezone.utc


# ══════════════════════════════════════════════════════════════════════════
# 1. Claim classification: the jackknife veto and NOT_IDENTIFIABLE
# ══════════════════════════════════════════════════════════════════════════

def _result(**kw):
    """An otherwise PROVEN-eligible result, so each test isolates one veto."""
    res = audit_stats.EffectResult(
        label="t", n_a=200, n_b=200, rate_a=0.6, rate_b=0.5, difference_pp=10.0)
    res.identifiability = audit_stats.IDENTIFIABLE
    res.inference_permitted = True
    res.clusters_adequate = True
    res.block_ci_pp = (2.0, 18.0)
    res.permutation_p_dual_one_way_max = 0.001
    res.methods_agree = True
    res.jackknife = {"sign_stable": True}
    # Default the METHOD CEILING open, so each veto test isolates ONE veto.
    # A dedicated test below pins the ceiling itself.
    res.joint_two_way_inference_available = True
    for k, v in kw.items():
        setattr(res, k, v)
    return res


def test_a_fully_satisfied_result_is_proven():
    """Guard test: without it, a veto test could pass for the wrong reason."""
    assert cgb.classify_claim(_result()) == audit_contract.PROVEN


def test_the_method_ceiling_caps_an_otherwise_proven_result_at_preliminary():
    """
    A1. Every p-value this audit produces is the MAXIMUM of two SEPARATE
    one-way stratified permutation tests — a dual sensitivity check, NOT joint
    two-way clustered inference. While that is so, PROVEN must be unreachable
    even when every other precondition is satisfied.
    """
    res = _result(joint_two_way_inference_available=False)
    assert cgb.classify_claim(res) == audit_contract.PRELIMINARY
    assert audit_stats.MAX_CLAIM_LEVEL_WITHOUT_JOINT_INFERENCE == \
        audit_contract.PRELIMINARY


def test_the_real_pipeline_never_reports_joint_two_way_inference():
    """
    The ceiling is only meaningful if the PRODUCTION path actually sets the
    flag False. This asserts against `analyse_comparison`, not a hand-built
    result, so the ceiling cannot be bypassed by the code that really runs.
    """
    res = audit_stats.analyse_comparison("t", _flat(), draws=40,
                                         permutation_draws=40)
    assert res.joint_two_way_inference_available is False
    assert res.max_claim_level == audit_contract.PRELIMINARY
    assert cgb.classify_claim(res) != audit_contract.PROVEN


def test_sign_unstable_jackknife_can_never_be_proven():
    """
    THE headline correction. A difference that flips sign when a single ticker
    is deleted is not robust, however small its p-value — and the jackknife is
    an INPUT to classification, not a footnote appended afterwards.
    """
    res = _result(jackknife={"sign_stable": False})
    assert cgb.classify_claim(res) != audit_contract.PROVEN
    assert cgb.classify_claim(res) == audit_contract.PRELIMINARY


def test_missing_jackknife_also_blocks_proven():
    """Absent evidence of stability is not evidence of stability."""
    assert cgb.classify_claim(_result(jackknife=None)) != audit_contract.PROVEN
    assert cgb.classify_claim(_result(jackknife={})) != audit_contract.PROVEN


def test_not_identifiable_is_never_downgraded_into_preliminary():
    res = _result(identifiability=audit_stats.NOT_IDENTIFIABLE)
    assert cgb.classify_claim(res) == audit_contract.NOT_IDENTIFIABLE


def test_inadequate_clusters_are_not_identifiable_not_auto_preliminary():
    """
    Too few clusters means nothing was tested. Reporting that as PRELIMINARY
    would assert suggestive evidence; reporting it as NOT_PROVEN would assert
    a real test came back null. Both are false.
    """
    status, reasons = audit_stats.assess_identifiability(
        n_a=500, n_b=500, n_clusters_date=3, n_clusters_symbol=400)
    assert status == audit_stats.NOT_IDENTIFIABLE
    assert any("session-date clusters" in r for r in reasons)


def test_too_few_symbol_clusters_is_also_not_identifiable():
    status, reasons = audit_stats.assess_identifiability(
        n_a=500, n_b=500, n_clusters_date=40, n_clusters_symbol=4)
    assert status == audit_stats.NOT_IDENTIFIABLE
    assert any("symbol clusters" in r for r in reasons)


def test_empty_comparison_group_is_not_identifiable():
    status, reasons = audit_stats.assess_identifiability(
        n_a=0, n_b=500, n_clusters_date=40, n_clusters_symbol=400)
    assert status == audit_stats.NOT_IDENTIFIABLE
    assert any("empty" in r for r in reasons)


def test_immature_policy_era_is_not_identifiable():
    status, reasons = audit_stats.assess_identifiability(
        n_a=100, n_b=100, n_clusters_date=40, n_clusters_symbol=400,
        n_runs=2, min_runs=8)
    assert status == audit_stats.NOT_IDENTIFIABLE
    assert any("immature" in r for r in reasons)


def test_not_identifiable_is_a_declared_contract_level():
    assert audit_contract.NOT_IDENTIFIABLE in audit_contract.CLAIM_LEVELS
    # It must NOT be usable as evidence of anything.
    assert audit_contract.NOT_IDENTIFIABLE not in audit_contract.EVIDENTIAL_LEVELS


def test_estimable_but_uninferable_with_unstable_sign_is_not_preliminary():
    """
    "Inference unavailable" only becomes PRELIMINARY when the evidence is
    genuinely directionally suggestive — which requires a stable sign.
    """
    res = _result(inference_permitted=False, block_ci_pp=None,
                  jackknife={"sign_stable": False})
    assert cgb.classify_claim(res) == audit_contract.NOT_IDENTIFIABLE


# ══════════════════════════════════════════════════════════════════════════
# 2. Dependence-aware inference and honest power reporting
# ══════════════════════════════════════════════════════════════════════════

def _flat(n_dates=30, n_syms=40, seed=3, effect=0.0):
    """
    Realistic shape: a symbol's GROUP VARIES across dates, exactly as a real
    ticker is BUY on some runs and HOLD on others. This matters — if group
    were a fixed property of the symbol it would be perfectly confounded with
    the symbol cluster and no within-symbol contrast would exist (pinned
    separately by `test_symbol_dimension_declines_when_group_is_fixed_per_symbol`).
    """
    import random
    rng = random.Random(seed)
    rows = []
    for d in range(n_dates):
        for s in range(n_syms):
            grp = "A" if (s + d) % 2 == 0 else "B"
            p = 0.5 + (effect if grp == "A" else 0.0)
            rows.append({"group": grp, "is_win": rng.random() < p,
                         "cluster_date": f"2026-07-{d + 1:02d}",
                         "symbol": f"S{s:03d}"})
    return rows


def test_symbol_dimension_declines_when_group_is_fixed_per_symbol():
    """
    If a symbol is ALWAYS in the same group, the group label is perfectly
    confounded with the symbol cluster: there is no within-symbol contrast, so
    the symbol-stratified test must decline rather than invent a p-value — and
    the dual one-way combination must then decline too.
    """
    rows = []
    for d in range(30):
        for s in range(40):
            rows.append({"group": "A" if s % 2 == 0 else "B", "is_win": s % 3 == 0,
                         "cluster_date": f"2026-07-{d + 1:02d}",
                         "symbol": f"S{s:03d}"})
    out = audit_stats.dual_one_way_stratified_permutation_sensitivity(
        rows, draws=30, seed=1)
    assert out["by_symbol"]["p_value"] is None
    assert out["p_dual_one_way_max"] is None, (
        "a dimension that cannot be tested must not be quietly dropped")


def test_dual_one_way_permutation_reports_both_dimensions_and_takes_the_max():
    out = audit_stats.dual_one_way_stratified_permutation_sensitivity(
        _flat(), draws=60, seed=1)
    assert out["by_date"]["p_value"] is not None
    assert out["by_symbol"]["p_value"] is not None
    assert out["p_dual_one_way_max"] == max(out["by_date"]["p_value"],
                                            out["by_symbol"]["p_value"])
    assert "MAXIMUM" in out["method"]


def test_the_permutation_declares_itself_not_joint_two_way_inference():
    """
    A1. The method is named and self-describes as a DUAL ONE-WAY SENSITIVITY
    check. It must never present itself as joint two-way clustered inference,
    and it must carry its own claim ceiling.
    """
    out = audit_stats.dual_one_way_stratified_permutation_sensitivity(
        _flat(), draws=40, seed=1)
    assert out["joint_two_way_inference"] is False
    assert out["max_claim_level"] == audit_contract.PRELIMINARY
    assert "NOT a joint two-way" in out["method"]
    # The misleading key and name are GONE, not merely deprecated.
    assert "p_two_way" not in out
    assert not hasattr(audit_stats, "two_way_cluster_permutation")


def test_the_trend_test_carries_the_same_ceiling():
    out = audit_stats.dual_one_way_trend_sensitivity(
        [{"rank_percentile": (i % 10) / 10.0, "is_win": i % 3 == 0,
          "cluster_date": f"d{i % 25}", "symbol": f"S{i % 30}"}
         for i in range(600)], draws=30, seed=1)
    assert out["joint_two_way_inference"] is False
    assert out["max_claim_level"] == audit_contract.PRELIMINARY
    assert "p_two_way" not in out
    assert not hasattr(audit_stats, "two_way_trend_test")


def test_permutation_p_value_is_never_zero():
    """A randomization p-value uses the +1/+1 correction, so 0 is impossible."""
    out = audit_stats.stratified_permutation_test(
        _flat(effect=0.45), stratum_key="cluster_date", draws=40, seed=1)
    assert out["p_value"] > 0.0


def test_permutation_is_deterministic_given_a_seed():
    a = audit_stats.dual_one_way_stratified_permutation_sensitivity(
        _flat(), draws=40, seed=11)
    b = audit_stats.dual_one_way_stratified_permutation_sensitivity(
        _flat(), draws=40, seed=11)
    assert a["p_dual_one_way_max"] == b["p_dual_one_way_max"]


def test_permutation_declines_when_no_stratum_holds_both_groups():
    """Perfect confounding of group with stratum leaves no contrast at all."""
    rows = [{"group": "A" if d < 2 else "B", "is_win": True,
             "cluster_date": f"d{d}", "symbol": "X"} for d in range(4)]
    out = audit_stats.stratified_permutation_test(
        rows, stratum_key="cluster_date", draws=20, seed=1)
    assert out["p_value"] is None
    assert "confounded" in out["note"]


def test_design_effect_exceeds_one_when_outcomes_cluster():
    """A clustered outcome must inflate variance, never deflate it."""
    rows = []
    for d in range(20):
        win = d % 2 == 0                      # whole date moves together
        for s in range(20):
            rows.append({"group": "A", "is_win": win,
                         "cluster_date": f"d{d}", "symbol": f"S{s}"})
    icc = audit_stats.intracluster_correlation(rows, cluster_key="cluster_date")
    assert icc["icc"] > 0.5
    assert icc["design_effect"] > 1.0


def test_icc_is_clamped_at_zero_so_power_is_never_overstated():
    import random
    rng = random.Random(5)
    rows = [{"group": "A", "is_win": rng.random() < 0.5,
             "cluster_date": f"d{i % 25}", "symbol": f"S{i}"} for i in range(500)]
    icc = audit_stats.intracluster_correlation(rows, cluster_key="cluster_date")
    assert icc["icc"] >= 0.0
    assert icc["design_effect"] >= 1.0


def test_cluster_adjusted_mde_is_explicitly_unavailable_never_approximated():
    """
    A2. Taking the larger of two ONE-WAY design effects is not a valid
    multiway adjustment and is not provably conservative for cross-classified
    date x symbol dependence. No validated multiway power approximation is
    implemented, so the cluster-adjusted MDE must be reported as UNAVAILABLE
    — never as a number, and never by substituting the independence figure.
    """
    out = audit_stats.mde_report(500, 500, 0.55,
                                 design_effect_date=4.0,
                                 design_effect_symbol=9.0)
    assert out["mde_cluster_adjusted_pp"] is None
    assert out["mde_cluster_adjusted_status"] == \
        audit_stats.MDE_CLUSTER_ADJUSTED_UNAVAILABLE
    # The independence MDE survives, but ONLY as a labelled optimistic bound.
    assert out["mde_independence_pp"] is not None
    assert "OPTIMISTIC" in out["mde_independence_label"]
    # Design effects are retained as DESCRIPTIVE diagnostics, separately, and
    # are never combined into one "two-way" number.
    assert out["design_effect_date"] == 4.0
    assert out["design_effect_symbol"] == 9.0
    assert "DESCRIPTIVE ONLY" in out["design_effect_label"]
    assert "UNAVAILABLE" in out["note"]
    # The old approximating entry point is gone, not merely unused.
    assert not hasattr(audit_stats, "cluster_adjusted_mde")


def test_the_headline_mde_field_reads_unavailable_rather_than_optimistic():
    """
    A legacy consumer reading `minimum_detectable_effect_pp` must get
    "unavailable", never the optimistic independence number wearing a
    cluster-aware label.
    """
    res = audit_stats.analyse_comparison("t", _flat(), seed=2,
                                         permutation_draws=40, draws=200)
    assert res.mde_cluster_adjusted_pp is None
    assert res.minimum_detectable_effect_pp is None
    assert res.mde_cluster_adjusted_status == \
        audit_stats.MDE_CLUSTER_ADJUSTED_UNAVAILABLE
    assert res.mde_independence_pp is not None
    # Both one-way design effects survive separately; no combined field exists.
    assert not hasattr(res, "design_effect")
    assert res.design_effect_date is not None


def test_date_block_bootstrap_supplies_an_interval_not_a_p_value():
    res = audit_stats.date_block_bootstrap("t", _flat(), draws=200, seed=1)
    assert res.block_ci_pp is not None
    assert res.block_p_value is None, (
        "the bootstrap must not masquerade as a null-based p-value")


def test_analyse_comparison_computes_the_jackknife_before_classification():
    """The jackknife must be present on the result the classifier receives."""
    res = audit_stats.analyse_comparison("t", _flat(), seed=2,
                                         permutation_draws=40, draws=200)
    assert res.jackknife is not None
    assert "sign_stable" in res.jackknife


# ══════════════════════════════════════════════════════════════════════════
# 3. Policy eras
# ══════════════════════════════════════════════════════════════════════════

def test_gate_and_cap_eras_start_on_different_verified_dates():
    """
    The conviction gate and the publication cap did NOT land together, and the
    gate landed on different dates in the two markets. Collapsing them into a
    single boundary is the error this pins.
    """
    us = audit_contract.POLICY_ERA_BOUNDARIES["US"]
    india = audit_contract.POLICY_ERA_BOUNDARIES["IN"]
    assert us[audit_contract.ERA_GATE_ONLY] != us[audit_contract.ERA_GATE_PLUS_CAP]
    assert us[audit_contract.ERA_GATE_ONLY] != india[audit_contract.ERA_GATE_ONLY]


def test_policy_era_is_total_and_per_market():
    assert audit_contract.policy_era("US", _dt.date(2026, 8, 9)) == audit_contract.ERA_LEGACY
    assert audit_contract.policy_era("US", _dt.date(2026, 8, 10)) == audit_contract.ERA_GATE_ONLY
    assert audit_contract.policy_era("US", _dt.date(2026, 8, 17)) == audit_contract.ERA_GATE_PLUS_CAP
    # India's gate era starts LATER than the US one — same date, different era.
    assert audit_contract.policy_era("IN", _dt.date(2026, 8, 10)) == audit_contract.ERA_LEGACY
    assert audit_contract.policy_era("IN", _dt.date(2026, 8, 13)) == audit_contract.ERA_GATE_ONLY


def test_unknown_market_has_no_silent_default_era():
    with pytest.raises(ValueError):
        audit_contract.policy_era("XX", _dt.date(2026, 8, 20))


# ══════════════════════════════════════════════════════════════════════════
# 4. Published vs unpublished: run matching and era purity
# ══════════════════════════════════════════════════════════════════════════

def _pu_rows():
    """
    Three runs: one fully matched, one with published-only, one with
    unpublished-only. Two policy eras are represented.
    """
    rows = []

    def add(run, day, symbol, published, ret):
        r = {"run_id": run, "market": "US", "horizon": "short",
             "symbol": symbol, "signal": "BUY", "signal_confidence": 90.0,
             "is_daily_pick": published, "ranking_alpha": 1.0,
             "run_session_date": day, "reference_session_date": day,
             "reference_price": 10.0}
        r["run_session_date_iso"] = day.isoformat()
        r["reference_session_date_iso"] = day.isoformat()
        r["canonical_key"] = cgb.canonical_key("US", "short", run, symbol)
        r["policy_era"] = audit_contract.policy_era("US", day)
        r["populations"] = cgb.assign_populations(r)
        r[audit_contract.EXECUTABLE_NEXT_OPEN] = ret
        rows.append(r)

    legacy = _dt.date(2026, 8, 3)
    add("r1", legacy, "AAA", True, 1.0)
    add("r1", legacy, "BBB", False, -1.0)
    add("r2", legacy, "CCC", True, 1.0)          # published only
    add("r3", legacy, "DDD", False, -1.0)        # unpublished only
    newer = _dt.date(2026, 8, 18)                 # gate_plus_cap era
    add("r4", newer, "EEE", True, 1.0)
    add("r4", newer, "FFF", False, -1.0)
    return rows


def test_published_unpublished_uses_only_run_matched_rows():
    m = cgb.match_published_unpublished(_pu_rows(), audit_contract.EXECUTABLE_NEXT_OPEN)
    matched = {r["run_id"] for r in m["matched_runs"]}
    assert matched == {"r1", "r4"}
    assert "run_id" in m["matching_rule"]


def test_unmatched_runs_are_excluded_explicitly_with_recorded_reasons():
    m = cgb.match_published_unpublished(_pu_rows(), audit_contract.EXECUTABLE_NEXT_OPEN)
    excluded = {r["run_id"]: r["exclusion_reason"] for r in m["excluded_runs"]}
    assert excluded["r2"] == "no_resolved_unpublished_buy_row_in_this_run"
    assert excluded["r3"] == "no_resolved_published_row_in_this_run"


def test_unmatched_runs_reconcile_against_the_population():
    """Every run lands in exactly one of matched / excluded — no run vanishes."""
    m = cgb.match_published_unpublished(_pu_rows(), audit_contract.EXECUTABLE_NEXT_OPEN)
    assert m["n_runs_matched"] + m["n_runs_excluded"] == m["n_runs_total"]
    audit_contract.assert_reconciles(
        "pub_unpub_runs", m["n_runs_total"], m["n_runs_matched"], m["n_runs_excluded"])


def test_eras_are_never_pooled_and_there_is_no_pooled_headline():
    out = cgb.published_vs_unpublished(
        _pu_rows(), audit_contract.EXECUTABLE_NEXT_OPEN, seed=1,
        market="US", horizon="short", permutation_draws=20)
    assert out["headline"] is None
    assert "NO pooled headline" in out["headline_note"]
    assert set(out["by_policy_era"]) == set(audit_contract.POLICY_ERAS)


def test_each_era_only_ever_sees_its_own_runs():
    m = cgb.match_published_unpublished(_pu_rows(), audit_contract.EXECUTABLE_NEXT_OPEN)
    assert m["by_era"][audit_contract.ERA_LEGACY]["n_matched_runs"] == 1
    assert m["by_era"][audit_contract.ERA_GATE_PLUS_CAP]["n_matched_runs"] == 1
    legacy_keys = m["by_era"][audit_contract.ERA_LEGACY]["matched_keys"]
    assert all("/r1/" in k for k in legacy_keys)


def test_an_underpowered_era_is_not_identifiable_not_not_proven():
    out = cgb.published_vs_unpublished(
        _pu_rows(), audit_contract.EXECUTABLE_NEXT_OPEN, seed=1,
        market="US", horizon="short", permutation_draws=20)
    for era, res in out["by_policy_era"].items():
        assert res["claim_level"] == audit_contract.NOT_IDENTIFIABLE, era


# ══════════════════════════════════════════════════════════════════════════
# 5. Ranking: tie-awareness and a proven direction
# ══════════════════════════════════════════════════════════════════════════

def _buy(run, symbol, alpha):
    return {"run_id": run, "symbol": symbol, "signal": "BUY",
            "ranking_alpha": alpha}


def test_tied_ranking_scores_receive_identical_tie_aware_percentiles():
    """
    The previous implementation used positional index after a plain sort, so
    tied rows got DIFFERENT percentiles decided by list order.
    """
    rows = [_buy("r", "A", 1.0), _buy("r", "B", 1.0),
            _buy("r", "C", 1.0), _buy("r", "D", 5.0)]
    cgb.within_run_rank_percentile(rows)
    tied = [r["rank_percentile"] for r in rows if r["symbol"] in ("A", "B", "C")]
    assert len(set(tied)) == 1, "tied ranking_alpha must yield one percentile"
    assert all(r["rank_tie_handling"] == "average_ranks" for r in rows)


def test_tie_group_percentile_is_the_average_of_the_positions_it_occupies():
    rows = [_buy("r", "A", 1.0), _buy("r", "B", 1.0), _buy("r", "C", 5.0)]
    cgb.within_run_rank_percentile(rows)
    by = {r["symbol"]: r["rank_percentile"] for r in rows}
    assert by["A"] == by["B"] == pytest.approx(0.25)   # mean(0,1)/(3-1)
    assert by["C"] == pytest.approx(1.0)


def test_tie_handling_is_order_independent():
    a = [_buy("r", "A", 1.0), _buy("r", "B", 1.0), _buy("r", "C", 5.0)]
    b = [_buy("r", "C", 5.0), _buy("r", "B", 1.0), _buy("r", "A", 1.0)]
    cgb.within_run_rank_percentile(a)
    cgb.within_run_rank_percentile(b)
    assert ({r["symbol"]: r["rank_percentile"] for r in a}
            == {r["symbol"]: r["rank_percentile"] for r in b})


def test_higher_ranking_alpha_means_a_better_percentile():
    """
    Direction is not assumed. Production sorts by ranking_alpha DESCENDING
    (daily_picks.py), so higher alpha must map to a higher percentile.
    """
    rows = [_buy("r", "LOW", -1.0), _buy("r", "HIGH", 2.0)]
    cgb.within_run_rank_percentile(rows)
    by = {r["symbol"]: r["rank_percentile"] for r in rows}
    assert by["HIGH"] > by["LOW"]
    assert cgb.RANKING_ALPHA_HIGHER_IS_BETTER is True


def test_rank_quantile_is_recorded_alongside_the_percentile():
    rows = [_buy("r", f"S{i}", float(i)) for i in range(8)]
    cgb.within_run_rank_percentile(rows, n_quantiles=4)
    for r in rows:
        assert r["rank_quantile"] in (1, 2, 3, 4)
        assert r["rank_tied_with"] == 0


def test_single_buy_run_gets_no_percentile_rather_than_a_fabricated_one():
    rows = [_buy("solo", "A", 1.0)]
    cgb.within_run_rank_percentile(rows)
    assert rows[0]["rank_percentile"] is None


def test_ranking_lift_never_rests_on_monotonicity_alone():
    """Quartile monotonicity is descriptive; only the trend test can claim."""
    import random
    rng = random.Random(7)
    rows = []
    for d in range(30):
        for s in range(12):
            r = {"run_id": f"r{d}", "symbol": f"S{s}", "signal": "BUY",
                 "ranking_alpha": rng.random(),
                 "run_session_date_iso": f"2026-07-{d + 1:02d}",
                 "populations": [audit_contract.P_ALL_ELIGIBLE, audit_contract.P_BUY],
                 audit_contract.EXECUTABLE_NEXT_OPEN: rng.uniform(-1, 1)}
            rows.append(r)
    cgb.within_run_rank_percentile(rows)
    out = cgb.ranking_lift(rows, audit_contract.EXECUTABLE_NEXT_OPEN,
                           seed=1, permutation_draws=40)
    assert "DESCRIPTIVE ONLY" in out["monotone_note"]
    assert out["trend_test"]["p_dual_one_way_max"] is not None
    assert out["trend_test"]["joint_two_way_inference"] is False
    assert out["max_claim_level"] == audit_contract.PRELIMINARY
    assert out["claim_level"] in (audit_contract.NOT_PROVEN,
                                  audit_contract.PRELIMINARY,
                                  audit_contract.NOT_IDENTIFIABLE)
    assert out["claim_level"] != audit_contract.PROVEN


# ══════════════════════════════════════════════════════════════════════════
# 5a. Extract completeness and reconciliation (B)
# ══════════════════════════════════════════════════════════════════════════

def _packed(rows):
    return ";".join(
        f'{r["symbol"]}~{r["sig"]}~{r["conf"]}~{r["ra"]}~{r["px"]}~'
        f'{r["dp"]}~{r["pr"]}~{r["off"]}' for r in rows)


def _run(run_id, market, syms, sess="2026-07-17"):
    import hashlib
    rows = [{"symbol": s, "sig": "B", "conf": "90", "ra": "0.5", "px": "100",
             "dp": "0", "pr": "", "off": "-1"} for s in sorted(syms)]
    packed = _packed(rows)
    return {"run_id": run_id, "market": market, "horizon": "short",
            "run_generated_at": f"{sess}T00:00:00+00:00",
            "run_session_date": sess, "n": len(rows),
            "md5": hashlib.md5(packed.encode()).hexdigest(), "packed": packed}


def _reconcile(runs, *, expect_runs, expect_rows, expect_by_market):
    """
    The reconciliation the real full-population extract had to satisfy: every
    eligible row appears EXACTLY ONCE, run and row totals match an
    independently-taken database count, and India/US reconcile separately.
    """
    seen_runs, seen_keys, problems = set(), set(), []
    by_market: dict[str, int] = {}
    for r in runs:
        if r["run_id"] in seen_runs:
            problems.append(f"duplicate run {r['run_id']}")
        seen_runs.add(r["run_id"])
        toks = [t for t in r["packed"].split(";") if t]
        if len(toks) != r["n"]:
            problems.append(f"run {r['run_id']} truncated: {len(toks)} != {r['n']}")
        for t in toks:
            key = (r["market"], r["horizon"], r["run_id"], t.split("~")[0])
            if key in seen_keys:
                problems.append(f"duplicate row {key}")
            seen_keys.add(key)
        by_market[r["market"]] = by_market.get(r["market"], 0) + r["n"]
    if len(seen_runs) != expect_runs:
        problems.append(f"runs {len(seen_runs)} != {expect_runs}")
    if len(seen_keys) != expect_rows:
        problems.append(f"rows {len(seen_keys)} != {expect_rows}")
    if by_market != expect_by_market:
        problems.append(f"per-market {by_market} != {expect_by_market}")
    return problems


def test_a_complete_extract_reconciles_on_every_axis():
    runs = [_run("a", "IN", [f"I{i}" for i in range(5)]),
            _run("b", "IN", [f"J{i}" for i in range(3)]),
            _run("c", "US", [f"U{i}" for i in range(4)])]
    assert _reconcile(runs, expect_runs=3, expect_rows=12,
                      expect_by_market={"IN": 8, "US": 4}) == []


def test_a_missing_run_fails_reconciliation():
    """A short extract must never be accepted as the full population."""
    runs = [_run("a", "IN", [f"I{i}" for i in range(5)])]
    problems = _reconcile(runs, expect_runs=3, expect_rows=12,
                          expect_by_market={"IN": 8, "US": 4})
    assert any("runs 1 != 3" in p for p in problems)
    assert any("per-market" in p for p in problems)


def test_a_duplicated_run_fails_reconciliation():
    r = _run("a", "IN", [f"I{i}" for i in range(5)])
    problems = _reconcile([r, dict(r)], expect_runs=2, expect_rows=10,
                          expect_by_market={"IN": 10})
    assert any("duplicate run" in p for p in problems)
    assert any("duplicate row" in p for p in problems)


def test_a_silently_truncated_run_fails_reconciliation():
    """The declared n and the decoded row count must agree exactly."""
    r = _run("a", "IN", [f"I{i}" for i in range(5)])
    r["packed"] = ";".join(r["packed"].split(";")[:3])
    problems = _reconcile([r], expect_runs=1, expect_rows=3,
                          expect_by_market={"IN": 5})
    assert any("truncated" in p for p in problems)


def test_the_loader_refuses_an_extract_whose_checksum_does_not_match(tmp_path):
    """Transport corruption must abort the audit, not be quietly audited."""
    r = _run("a", "IN", [f"I{i}" for i in range(5)])
    r["md5"] = "0" * 32
    path = tmp_path / "bad_extract.json"
    path.write_text(json.dumps({"meta": {}, "runs": [r]}))
    with pytest.raises(cgb.PopulationSourceError, match="checksum mismatch"):
        cgb.load_extract(path)


def test_the_loader_refuses_an_extract_whose_row_count_disagrees(tmp_path):
    """A run declaring more rows than it carries is a truncated population."""
    r = _run("a", "IN", [f"I{i}" for i in range(5)])
    r["n"] = 9
    path = tmp_path / "short_extract.json"
    path.write_text(json.dumps({"meta": {}, "runs": [r]}))
    payload = cgb.load_extract(path)      # the md5 still matches the payload
    with pytest.raises(cgb.PopulationSourceError, match="truncated population"):
        cgb.parse_extract(payload, "IN", "short")


# ══════════════════════════════════════════════════════════════════════════
# 5b. Documentation consistency (A5) — the register must read as ONE current
#     truth, not an old overclaim followed by a later correction elsewhere.
# ══════════════════════════════════════════════════════════════════════════

REGISTER = (BACKEND.parent / "Documentation" / "Engineering-Handbook" /
            "Daily-Picks" / "DAILY-PICKS-IMPLEMENTATION-REGISTER.md")

# Each entry: (forbidden substring, why it is wrong now).
# Matching is case-insensitive and whitespace-normalised so a reflow cannot
# smuggle a stale phrase back in.
STALE_REGISTER_PHRASES = [
    ("six claim levels",
     "there are SEVEN claim levels since NOT_IDENTIFIABLE was added"),
    ("as the primary inference method",
     "the date-blocked bootstrap supplies an INTERVAL ONLY and sets no p-value"),
    ("real, full-population, definitive negative result",
     "DP-036's denominator was a fetched, horizon-pooled count and its "
     "framing has been withdrawn"),
    ("genuine two-way cluster permutation",
     "the method is a DUAL ONE-WAY sensitivity check, not joint two-way "
     "inference"),
    ("two-way clustered p-value",
     "no joint two-way clustered p-value is computed anywhere"),
    ("conservative but not exact",
     "max-of-two-one-way is not provably conservative under two-way "
     "dependence, so it may not be described that way"),
    ("genuinely cluster-adjusted",
     "the cluster-adjusted MDE is UNAVAILABLE; nothing may be described as "
     "genuinely cluster-adjusted"),
    ("fully two-way cluster-adjusted",
     "the max-of-one-way design effect was never a two-way adjustment"),
    ("larger of the date/symbol design effects taken",
     "taking the larger one-way design effect was withdrawn as an invalid "
     "two-way adjustment"),
    ("what this entry closes",
     "the audit is not closed by that entry; the heading overclaimed"),
]


def _normalised_register() -> str:
    import re
    return re.sub(r"\s+", " ", REGISTER.read_text(encoding="utf-8")).lower()


@pytest.mark.parametrize("phrase,why", STALE_REGISTER_PHRASES,
                         ids=[p[0][:40] for p in STALE_REGISTER_PHRASES])
def test_stale_register_phrasing_is_actually_absent(phrase, why):
    """
    A5. These phrases were REWRITTEN, not appended to. If one reappears the
    register once again states an overclaim in one place and its correction in
    another, which is the specific failure this test exists to prevent.
    """
    assert phrase.lower() not in _normalised_register(), (
        f"stale phrase {phrase!r} is back in the register — {why}")


def test_the_register_still_states_the_corrected_position():
    """
    The negative test above is only meaningful with a positive counterpart:
    the corrected statements must be PRESENT, not merely the stale ones absent.
    """
    text = _normalised_register()
    for required in (
        "seven** claim levels",
        "dual one-way stratified permutation sensitivity",
        "not joint two-way",
        "unavailable",
        "interval only",
    ):
        assert required.lower() in text, f"missing corrected statement: {required!r}"


def test_the_stale_phrases_are_absent_from_the_audit_source_too():
    """The code must not carry the withdrawn vocabulary either."""
    import re
    sources = [
        BACKEND / "services" / "alpha_engine" / "audit_stats.py",
        BACKEND / "scripts" / "conviction_gate_backtest.py",
    ]
    for src in sources:
        text = re.sub(r"\s+", " ", src.read_text(encoding="utf-8")).lower()
        for bad in ("two_way_cluster_permutation(", "two_way_trend_test(",
                    "cluster_adjusted_mde(", '"p_two_way"'):
            assert bad not in text, f"{src.name} still references {bad!r}"


# ══════════════════════════════════════════════════════════════════════════
# 6. Price acquisition: scaling, retries, snapshot immutability, missingness
# ══════════════════════════════════════════════════════════════════════════

class _Recorder:
    """A fake provider that records how it was called."""

    def __init__(self, fail_symbols=(), fail_times=0):
        self.calls = []
        self.fail_symbols = set(fail_symbols)
        self.fail_times = fail_times

    def __call__(self, tickers, start, end):
        self.calls.append(list(tickers))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("simulated rate limit")
        out = []
        for t in tickers:
            if t in self.fail_symbols:
                continue
            day = start
            while day <= end:
                out.append((t, day, 100.0, 101.0))
                day += _dt.timedelta(days=1)
        return out


def test_requests_scale_with_unique_symbols_and_batch_size_not_observations():
    """
    THE scaling correction. 120 unique symbols at batch size 50 must cost 3
    requests — regardless of how many thousands of observations reference them.
    """
    rec = _Recorder()
    symbols = [f"S{i:03d}" for i in range(120)]
    audit_prices.fetch_panel({"US": symbols}, _dt.date(2026, 7, 1),
                             _dt.date(2026, 7, 3), batch_size=50,
                             downloader=rec, sleep=lambda _s: None)
    assert len(rec.calls) == 3
    assert sum(len(c) for c in rec.calls) == 120


def test_duplicate_symbols_do_not_multiply_requests():
    rec = _Recorder()
    audit_prices.fetch_panel({"US": ["AAA"] * 5000}, _dt.date(2026, 7, 1),
                             _dt.date(2026, 7, 2), batch_size=50,
                             downloader=rec, sleep=lambda _s: None)
    assert len(rec.calls) == 1 and rec.calls[0] == ["AAA"]


def test_india_symbols_get_the_ns_suffix_only_at_provider_call_time():
    rec = _Recorder()
    snap = audit_prices.fetch_panel({"IN": ["RELIANCE"]}, _dt.date(2026, 7, 1),
                                    _dt.date(2026, 7, 2), downloader=rec,
                                    sleep=lambda _s: None)
    assert rec.calls[0] == ["RELIANCE.NS"]
    assert snap.has_symbol("IN", "RELIANCE"), "stored unsuffixed"


def test_a_failing_batch_is_retried_then_recorded_never_silently_dropped():
    rec = _Recorder(fail_times=2)
    snap = audit_prices.fetch_panel({"US": ["AAA"]}, _dt.date(2026, 7, 1),
                                    _dt.date(2026, 7, 2), max_retries=3,
                                    downloader=rec, sleep=lambda _s: None)
    assert snap.meta["retries_made"] == 2
    assert snap.has_symbol("US", "AAA")


def test_an_exhausted_batch_records_every_symbol_as_failed():
    rec = _Recorder(fail_times=99)
    snap = audit_prices.fetch_panel({"US": ["AAA", "BBB"]}, _dt.date(2026, 7, 1),
                                    _dt.date(2026, 7, 2), max_retries=2,
                                    downloader=rec, sleep=lambda _s: None)
    assert set(snap.meta["symbols_failed"]) == {"US:AAA", "US:BBB"}


def test_provider_parameters_are_pinned_explicitly():
    """Library defaults change between releases; the audit must not inherit them."""
    assert audit_prices.PROVIDER_PARAMS["auto_adjust"] is False
    assert audit_prices.PROVIDER_PARAMS["threads"] is False
    assert audit_prices.PROVIDER_PARAMS["interval"] == "1d"


def test_a_frozen_snapshot_cannot_be_mutated():
    snap = audit_prices.PriceSnapshot()
    snap.put("US", "AAA", "2026-07-01", 1.0, 2.0)
    snap.freeze()
    with pytest.raises(RuntimeError):
        snap.put("US", "BBB", "2026-07-01", 1.0, 2.0)


def test_both_measures_read_the_same_snapshot_instance(tmp_path):
    """
    The two measures must be incapable of observing different prices for the
    same symbol and date — they share one frozen panel.
    """
    snap = audit_prices.PriceSnapshot()
    for d in range(1, 40):
        day = _dt.date(2026, 7, 1) + _dt.timedelta(days=d)
        if audit_calendar.is_session("US", day):
            snap.put("US", "AAA", day.isoformat(), 100.0, 110.0)
    snap.freeze()
    row = {"symbol": "AAA", "run_generated_at": _dt.datetime(2026, 7, 6, 6, tzinfo=UTC),
           "reference_session_date": _dt.date(2026, 7, 6), "reference_price": 100.0}
    a, ra, pa = cgb.resolve_research_return(row, "US", "short", snap,
                                            today=_dt.date(2026, 8, 22))
    b, rb, pb = cgb.resolve_executable_return(row, "US", "short", snap,
                                              today=_dt.date(2026, 8, 22))
    assert ra is None and rb is None
    assert pa["price_source"] == pb["price_source"] == "snapshot"
    # A supplies close->close (0%); B supplies open->close (+10%).
    assert a == pytest.approx(0.0)
    assert b == pytest.approx(10.0)


def test_snapshot_save_refuses_to_write_inside_the_repository():
    repo_root = Path(cgb.__file__).resolve().parents[2]
    snap = audit_prices.PriceSnapshot().freeze()
    with pytest.raises(ValueError):
        snap.save(repo_root / "prices.json")


def test_snapshot_checksum_is_stable_and_round_trips(tmp_path):
    snap = audit_prices.PriceSnapshot()
    snap.put("US", "AAA", "2026-07-01", 1.0, 2.0)
    snap.freeze()
    a = snap.save(tmp_path / "s1.json")
    b = snap.save(tmp_path / "s2.json")
    assert a["sha256"] == b["sha256"]
    loaded = audit_prices.PriceSnapshot.load(tmp_path / "s1.json")
    assert loaded.get_close("US", "AAA", "2026-07-01") == 2.0


def test_missingness_guard_trips_on_an_excessive_overall_rate():
    rows = [{"market": "US", "horizon": "short", "reference_session_date": "d",
             "comparison_group": "NON_BUY", "resolved": i < 10} for i in range(100)]
    report = audit_prices.missingness_report(rows, resolved_key="resolved")
    with pytest.raises(audit_prices.MissingnessAbort):
        audit_prices.enforce_missingness(report)


def test_missingness_guard_trips_on_a_between_group_differential():
    """
    Selection between the very groups being compared. This must fail even
    though the OVERALL rate is comfortable — a reconciling denominator does
    not rule out differential missingness.
    """
    rows = ([{"market": "US", "horizon": "short", "reference_session_date": "d",
              "comparison_group": "PUBLISHED", "resolved": i < 60} for i in range(100)]
            + [{"market": "US", "horizon": "short", "reference_session_date": "d",
                "comparison_group": "NON_BUY", "resolved": True} for _ in range(100)])
    report = audit_prices.missingness_report(rows, resolved_key="resolved")
    assert report["unresolved_rate"] < audit_prices.MAX_UNRESOLVED_RATE
    with pytest.raises(audit_prices.MissingnessAbort) as exc:
        audit_prices.enforce_missingness(report)
    assert "selection" in str(exc.value)


def test_missingness_is_broken_out_by_market_horizon_date_and_group():
    rows = [{"market": "US", "horizon": "short", "reference_session_date": "2026-07-01",
             "comparison_group": "PUBLISHED", "resolved": True}]
    report = audit_prices.missingness_report(rows, resolved_key="resolved")
    for key in ("by_market", "by_horizon", "by_date", "by_comparison_group"):
        assert report[key], key


# ══════════════════════════════════════════════════════════════════════════
# 7. Calendar golden tests — concrete, dated, verifiable
# ══════════════════════════════════════════════════════════════════════════

def test_us_observed_independence_day_2026_is_closed():
    """
    4 July 2026 is a SATURDAY, so the NYSE observes the holiday on Friday
    3 July 2026. A naive weekday check would call 3 July a trading day.
    """
    assert not audit_calendar.is_session("US", _dt.date(2026, 7, 3))
    assert audit_calendar.is_session("US", _dt.date(2026, 7, 2))
    assert audit_calendar.is_session("US", _dt.date(2026, 7, 6))


def test_us_session_offset_skips_the_observed_holiday():
    """2 July + 1 session must be 6 July, not 3 July."""
    assert audit_calendar.session_offset("US", _dt.date(2026, 7, 2), 1) == \
        _dt.date(2026, 7, 6)


def test_a_verified_2026_nse_holiday_is_closed():
    """
    Republic Day, 26 January 2026, is a Monday — a weekday on which the NSE
    does not trade. Gandhi Jayanti (2 October 2026, a Friday) is a second
    independent case.
    """
    assert not audit_calendar.is_session("IN", _dt.date(2026, 1, 26))
    assert not audit_calendar.is_session("IN", _dt.date(2026, 10, 2))
    assert audit_calendar.is_session("IN", _dt.date(2026, 1, 27))


def test_us_open_time_shifts_with_dst_and_india_never_does():
    """
    NYSE opens 14:30 UTC in EST and 13:30 UTC in EDT; the NSE opens 03:45 UTC
    all year because India observes no DST. Both are derived from the exchange
    calendar, never from a hardcoded offset.
    """
    def open_hm(market, day):
        _, o = audit_calendar.next_tradable_open(
            market, _dt.datetime.combine(day, _dt.time(0, 1), tzinfo=UTC))
        return o.strftime("%H:%M")

    assert open_hm("US", _dt.date(2026, 3, 6)) == "14:30"    # EST
    assert open_hm("US", _dt.date(2026, 3, 9)) == "13:30"    # EDT
    assert open_hm("US", _dt.date(2026, 11, 2)) == "14:30"   # back to EST
    for day in (_dt.date(2026, 3, 6), _dt.date(2026, 3, 9), _dt.date(2026, 11, 2)):
        assert open_hm("IN", day) == "03:45"


def test_generation_before_the_same_day_open_uses_that_session():
    """A pre-market pick is tradable at that morning's own open."""
    session, open_utc = audit_calendar.next_tradable_open(
        "US", _dt.datetime(2026, 7, 6, 6, 0, tzinfo=UTC))
    assert session == _dt.date(2026, 7, 6)
    assert open_utc > _dt.datetime(2026, 7, 6, 6, 0, tzinfo=UTC)


def test_generation_at_or_after_the_open_rolls_to_the_next_session():
    """A pick that exists only after the bell cannot be filled at that open."""
    session, _ = audit_calendar.next_tradable_open(
        "US", _dt.datetime(2026, 7, 6, 15, 0, tzinfo=UTC))
    assert session == _dt.date(2026, 7, 7)


def test_generation_on_a_weekend_rolls_to_monday():
    saturday = _dt.datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    for market in ("US", "IN"):
        session, _ = audit_calendar.next_tradable_open(market, saturday)
        assert session.weekday() < 5
        assert audit_calendar.is_session(market, session)


def test_a_partial_horizon_window_is_excluded_never_truncated():
    snap = audit_prices.PriceSnapshot().freeze()
    row = {"symbol": "AAA", "run_generated_at": _dt.datetime(2026, 8, 20, 6, tzinfo=UTC),
           "reference_session_date": _dt.date(2026, 8, 20), "reference_price": 1.0}
    ret, reason, _ = cgb.resolve_executable_return(
        row, "US", "short", snap, today=_dt.date(2026, 8, 22))
    assert ret is None and reason == "horizon_window_not_yet_complete"


# ══════════════════════════════════════════════════════════════════════════
# 8. Bundle: manifest, integrity, reconstruction, additive multi-cell writes
# ══════════════════════════════════════════════════════════════════════════

def _synthetic_extract(tmp_path, markets=("US",), n_runs=24, n_syms=40):
    """A synthetic frozen extract in the real transport format."""
    import hashlib
    import random
    rng = random.Random(4)
    runs = []
    rn = 0
    for market in markets:
        days = [d for d in (_dt.date(2026, 6, 1) + _dt.timedelta(days=i)
                            for i in range(70))
                if audit_calendar.is_session(market, d)][:n_runs]
        for day in days:
            rn += 1
            recs = []
            for s in range(n_syms):
                sig = rng.choice("BHS")
                recs.append([f"S{s:03d}", sig, rng.randint(0, 100),
                             round(rng.uniform(-2, 2), 4),
                             round(rng.uniform(10, 500), 2), "0", ""])
            for rank, r in enumerate(
                    sorted([r for r in recs if r[1] == "B" and r[2] >= 85],
                           key=lambda r: -r[3])[:3], 1):
                r[5], r[6] = "1", str(rank)
            packed = ";".join("~".join([r[0], r[1], str(r[2]), str(r[3]),
                                        str(r[4]), r[5], r[6], "0"]) for r in recs)
            runs.append({
                "rn": rn, "market": market, "horizon": "short",
                "run_id": f"{market}-run-{rn}",
                "run_generated_at": _dt.datetime.combine(
                    day, _dt.time(6, 0)).isoformat() + "+00:00",
                "run_session_date": day.isoformat(), "n": len(recs),
                "packed": packed,
                "md5": hashlib.md5(packed.encode()).hexdigest()})
    payload = {"meta": {"source": "synthetic-test",
                        "schema_version_distribution": {"1/1": rn * n_syms},
                        "factor_ic_history_rows": 0,
                        "outcome_columns_present": 0},
               "runs": runs}
    p = tmp_path / "extract.json"
    p.write_text(json.dumps(payload))
    return p, runs, n_syms


def _synthetic_snapshot(markets, n_syms):
    snap = audit_prices.PriceSnapshot()
    import random
    rng = random.Random(9)
    for market in markets:
        days = [d for d in (_dt.date(2026, 5, 1) + _dt.timedelta(days=i)
                            for i in range(150))
                if audit_calendar.is_session(market, d)]
        for s in range(n_syms):
            px = 100.0
            for d in days:
                px *= (1 + rng.uniform(-0.03, 0.03))
                snap.put(market, f"S{s:03d}", d.isoformat(), px * 0.995, px)
    return snap.freeze()


@pytest.fixture(scope="module")
def audit_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("audit")
    ep, runs, n_syms = _synthetic_extract(tmp, markets=("US",))
    snap = _synthetic_snapshot(("US",), n_syms)
    snap_path = tmp / "snap.json"
    snap.save(snap_path)
    result = cgb.run_full_audit(
        ["US"], ["short"], source=f"extract:{ep}", out_dir=str(tmp / "bundle"),
        seed=5, permutation_draws=40, draws=200, today=_dt.date(2026, 8, 22),
        snapshot_in=str(snap_path))
    return {"tmp": tmp, "extract": ep, "snap": snap_path, "result": result,
            "bundle": tmp / "bundle", "n_syms": n_syms}


def test_manifest_contains_every_required_reproducibility_field(audit_run):
    m = json.loads((audit_run["bundle"] / "run_manifest.json").read_text())
    for field in ("commit_sha", "calculation_version", "random_seed",
                  "population_query", "population_cutoff_utc",
                  "run_timestamp_utc", "source", "package_versions",
                  "calendar_version", "calendars_used", "price_provider_params",
                  "price_snapshot", "row_counts", "cells",
                  "policy_era_boundaries", "permutation_draws"):
        assert field in m and m[field] not in (None, {}, []), field


def test_manifest_records_the_price_snapshot_checksum(audit_run):
    m = json.loads((audit_run["bundle"] / "run_manifest.json").read_text())
    assert m["price_snapshot"]["sha256"]


def test_manifest_never_records_a_database_credential(audit_run):
    raw = (audit_run["bundle"] / "run_manifest.json").read_text()
    assert "password" not in raw.lower()
    assert "postgresql://" not in raw


def test_manifest_states_costs_are_deferred_not_modelled(audit_run):
    m = json.loads((audit_run["bundle"] / "run_manifest.json").read_text())
    assert m["cost_model_implemented"] is False
    assert "DEFERRED" in m["transaction_costs_and_taxes"]


def test_cli_actually_populates_the_data_integrity_results(audit_run):
    """
    A placeholder saying "no integrity checks supplied" is NOT acceptable —
    the checks must genuinely execute and report a pass/fail each.
    """
    d = json.loads((audit_run["bundle"] / "data_integrity_results.json").read_text())
    assert "note" not in d
    assert d["_summary"]["n_checks"] >= 10
    for name, check in d.items():
        if name.startswith("_"):
            continue
        assert check["definition"] and "passed" in check and "result" in check
    assert d["_summary"]["n_failed"] == []


def test_integrity_covers_every_required_check(audit_run):
    d = json.loads((audit_run["bundle"] / "data_integrity_results.json").read_text())
    for required in ("row_counts_by_market_horizon",
                     "null_or_non_finite_critical_fields",
                     "duplicate_canonical_keys", "market_label_routing",
                     "score_and_zscore_ranges", "reference_price_validity",
                     "schema_version_distribution",
                     "publication_gate_and_cap_by_policy_era",
                     "factor_ic_history_population", "outcome_column_absence",
                     "production_ranking_does_not_read_alpha_observations"):
        assert required in d, required


def test_production_ranking_does_not_read_alpha_observations(audit_run):
    """The ranking path must APPEND only, or this audit measures a feedback loop."""
    d = json.loads((audit_run["bundle"] / "data_integrity_results.json").read_text())
    check = d["production_ranking_does_not_read_alpha_observations"]
    assert check["passed"], check["result"]
    assert check["result"]["ranking_path_readers"] == []


def test_every_aggregate_reconstructs_from_the_row_file(audit_run):
    r = json.loads((audit_run["bundle"] / "reconstruction_verification.json").read_text())
    assert r["passed"] and r["n_failed"] == 0 and r["n_checks"] > 0


def test_reconstruction_fails_loudly_when_the_row_file_disagrees(audit_run):
    """The proof must be a real check, not a rubber stamp."""
    audits = audit_run["result"]["audits"]
    decisions = [dict(d) for a in audits for d in a["decisions"]]
    decisions.pop()                       # silently lose one row
    with pytest.raises(cgb.ReconstructionError):
        cgb.verify_reconstruction(decisions, audits)


def test_row_decisions_carry_every_field_needed_to_rebuild_the_aggregates(audit_run):
    line = (audit_run["bundle"] / "row_decisions.jsonl").read_text().splitlines()[0]
    rec = json.loads(line)
    for field in ("canonical_key", "run_id", "market", "horizon", "symbol",
                  "policy_era", "populations", "signal", "signal_confidence",
                  "is_daily_pick", "ranking_alpha", "rank_percentile",
                  "rank_quantile", "rank_tie_handling",
                  "research_return_pct", "research_is_win",
                  "research_excluded_reason", "research_provenance",
                  "executable_return_pct", "executable_is_win",
                  "executable_excluded_reason", "executable_provenance",
                  "pub_unpub_group", "gross_of_transaction_costs"):
        assert field in rec, field


def test_research_provenance_records_entry_exit_and_reference_price_treatment(audit_run):
    for line in (audit_run["bundle"] / "row_decisions.jsonl").read_text().splitlines():
        rec = json.loads(line)
        if rec["research_return_pct"] is None:
            continue
        p = rec["research_provenance"]
        assert p["entry_session_date"] and p["exit_session_date"]
        assert p["entry_price"] and p["exit_price"]
        assert p["entry_price_field"] == "close" and p["exit_price_field"] == "close"
        # The audit uses the PROVIDER close and says so explicitly.
        assert p["reference_price_used"] is False
        assert "reference_price_reconciled" in p
        return
    pytest.fail("no resolved research row to inspect")


def test_executable_provenance_records_open_close_sessions_and_calendar(audit_run):
    for line in (audit_run["bundle"] / "row_decisions.jsonl").read_text().splitlines():
        rec = json.loads(line)
        if rec["executable_return_pct"] is None:
            continue
        p = rec["executable_provenance"]
        assert p["entry_price_field"] == "open" and p["exit_price_field"] == "close"
        assert p["entry_session_date"] and p["exit_session_date"]
        assert p["entry_session_open_utc"] and p["calendar"] == "XNYS"
        assert p["price_source"] == "snapshot"
        return
    pytest.fail("no resolved executable row to inspect")


def test_neither_measure_is_ever_called_net_return_or_investor_pnl():
    for name, text in audit_contract.RETURN_MEASURES.items():
        low = text.lower()
        assert "net return" not in low, name
        assert "investor p&l" not in low or "never investor p&l" in low, name
    assert all(m in audit_contract.RETURN_MEASURES
               for m in (audit_contract.RESEARCH_PRIOR_CLOSE,
                         audit_contract.EXECUTABLE_NEXT_OPEN))


def test_a_second_invocation_into_the_same_bundle_is_refused(audit_run, tmp_path):
    """
    A4. Multi-invocation bundles are PROHIBITED, and the prohibition is
    enforced loudly rather than documented.

    The previous contract merged additively: earlier cells survived, but the
    run-level manifest, integrity results, price-snapshot identity and Holm
    family correction were OVERWRITTEN with only the latest invocation's data.
    A bundle like that reports a Holm correction covering a strict subset of
    the tests it contains, which overstates significance. So a second
    invocation into a directory that already holds bundle artefacts must FAIL,
    leaving the existing bundle untouched.
    """
    bundle = audit_run["tmp"] / "bundle"
    before = (bundle / "aggregate_summary.json").read_text()
    manifest_before = (bundle / "run_manifest.json").read_text()

    ep, _runs, n_syms = _synthetic_extract(tmp_path, markets=("IN",), n_runs=22)
    snap = _synthetic_snapshot(("IN",), n_syms)
    sp = tmp_path / "snap_in.json"
    snap.save(sp)
    with pytest.raises(cgb.BundleDirectoryNotEmpty) as exc:
        cgb.run_full_audit(["IN"], ["short"], source=f"extract:{ep}",
                           out_dir=str(bundle), seed=5, permutation_draws=20,
                           draws=100, today=_dt.date(2026, 8, 22),
                           snapshot_in=str(sp))
    assert "EMPTY directory" in str(exc.value)
    # Nothing was mutated on the way to the refusal.
    assert (bundle / "aggregate_summary.json").read_text() == before
    assert (bundle / "run_manifest.json").read_text() == manifest_before


def test_a_fresh_directory_is_accepted_and_declares_the_contract(audit_run, tmp_path):
    """The counterpart: an EMPTY directory works, and says so in the manifest."""
    ep, _runs, n_syms = _synthetic_extract(tmp_path, markets=("IN",), n_runs=22)
    snap = _synthetic_snapshot(("IN",), n_syms)
    sp = tmp_path / "snap_in2.json"
    snap.save(sp)
    fresh = tmp_path / "fresh_bundle"
    cgb.run_full_audit(["IN"], ["short"], source=f"extract:{ep}",
                       out_dir=str(fresh), seed=5, permutation_draws=20,
                       draws=100, today=_dt.date(2026, 8, 22),
                       snapshot_in=str(sp))
    m = json.loads((fresh / "run_manifest.json").read_text())
    assert m["single_invocation_bundle"] is True
    assert m["cells"] == ["IN/short"] == m["cells_written_this_run"]
    assert "ONE BUNDLE == ONE INVOCATION" in m["bundle_contract"]


def test_both_markets_are_processed_in_one_invocation(tmp_path):
    """
    C2/E. The closure run covers India AND the US in ONE invocation. This pins
    that a single `run_full_audit` call really produces both cells, in one
    bundle, under one manifest and one Holm family.
    """
    ep, _runs, n_syms = _synthetic_extract(tmp_path, markets=("IN", "US"),
                                           n_runs=22)
    snap = _synthetic_snapshot(("IN", "US"), n_syms)
    sp = tmp_path / "snap_both.json"
    snap.save(sp)
    out = tmp_path / "both_bundle"
    res = cgb.run_full_audit(["IN", "US"], ["short"], source=f"extract:{ep}",
                             out_dir=str(out), seed=5, permutation_draws=20,
                             draws=100, today=_dt.date(2026, 8, 22),
                             snapshot_in=str(sp))
    m = json.loads((out / "run_manifest.json").read_text())
    assert set(m["cells"]) == {"IN/short", "US/short"}
    # ONE price snapshot, shared by both markets.
    assert m["price_snapshot"]["sha256"]
    # The Holm family spans BOTH markets and BOTH measures.
    keys = set(res["family"])
    for cell in ("IN/short", "US/short"):
        for measure in (audit_contract.RESEARCH_PRIOR_CLOSE,
                        audit_contract.EXECUTABLE_NEXT_OPEN):
            assert any(k.startswith(f"{cell}/{measure}/") for k in keys), \
                f"{cell}/{measure} missing from the Holm family"
    rows = [json.loads(l) for l in
            (out / "row_decisions.jsonl").read_text().splitlines()]
    assert {r["market"] for r in rows} == {"IN", "US"}


def test_bootstrap_draws_actually_reach_the_bootstrap_implementation(tmp_path):
    """
    A3. `--bootstrap-draws` used to be parsed and written into the manifest
    while `run_full_audit` never passed it to `build_audit`, so every run used
    the default no matter what the manifest said. This proves a NON-DEFAULT
    draw count reaches the bootstrap AND that the manifest reports the value
    truly used.
    """
    non_default = 137
    assert non_default != audit_stats.DEFAULT_BOOTSTRAP_DRAWS

    ep, _runs, n_syms = _synthetic_extract(tmp_path, markets=("US",), n_runs=22)
    snap = _synthetic_snapshot(("US",), n_syms)
    sp = tmp_path / "snap_draws.json"
    snap.save(sp)
    out = tmp_path / "draws_bundle"
    res = cgb.run_full_audit(["US"], ["short"], source=f"extract:{ep}",
                             out_dir=str(out), seed=5, permutation_draws=21,
                             draws=non_default, today=_dt.date(2026, 8, 22),
                             snapshot_in=str(sp))

    executed = {c.get("bootstrap_draws_executed")
                for c in cgb._walk_comparisons(res["audits"])} - {None}
    assert executed == {non_default}, (
        f"the bootstrap ran with {executed}, not the requested {non_default}")
    perms = {c.get("permutation_draws_executed")
             for c in cgb._walk_comparisons(res["audits"])} - {None}
    assert perms == {21}

    m = json.loads((out / "run_manifest.json").read_text())
    assert m["bootstrap_draws"] == non_default
    assert m["permutation_draws"] == 21
    v = m["draws_propagation_verified"]
    assert v["verified"] is True
    assert v["bootstrap_draws_observed"] == [non_default]
    assert v["comparisons_checked"] > 0


def test_the_run_fails_rather_than_misreport_the_draw_count():
    """
    The reconciliation is fail-closed: if the executed count ever diverges
    from the declared one, the run must abort rather than emit a manifest that
    documents draws the audit did not perform.
    """
    fake = [{"market": "US", "horizon": "short", "statistics": {"m": {
        "buy_vs_non_buy": {"bootstrap_draws_executed": 10000,
                           "permutation_draws_executed": 2000},
        "conviction_within_buy": {"bootstrap_draws_executed": 10000,
                                  "permutation_draws_executed": 2000},
        "published_vs_unpublished_buy": {"by_policy_era": {}},
    }}}]
    with pytest.raises(RuntimeError, match="misreport the draw counts"):
        cgb.verify_draws_propagated(fake, bootstrap_draws=137,
                                    permutation_draws=2000)


def test_the_holm_family_spans_the_whole_run_not_one_invocation(audit_run):
    fam = json.loads((audit_run["bundle"] / "multiple_testing_family.json").read_text())
    keys = set(fam["family"])
    # Both measures, all primary analyses, every policy era, and ranking lift.
    assert any("RESEARCH_PRIOR_CLOSE" in k for k in keys)
    assert any("EXECUTABLE_NEXT_OPEN" in k for k in keys)
    assert any("buy_vs_non_buy" in k for k in keys)
    assert any("conviction_within_buy" in k for k in keys)
    assert any("ranking_lift_trend" in k for k in keys)
    assert any(f"published_vs_unpublished_buy/{e}" in k
               for k in keys for e in audit_contract.POLICY_ERAS)


def test_waterfalls_reconcile_exactly_for_both_measures(audit_run):
    agg = json.loads((audit_run["bundle"] / "aggregate_summary.json").read_text())
    for cell in agg.values():
        for wf in cell["waterfall"].values():
            assert wf["fetched"] == wf["included"] + wf["excluded"]
            assert sum(wf["exclusion_reasons"].values()) == wf["excluded"]


def test_extract_checksum_mismatch_is_refused(tmp_path):
    """A transported population that does not match what the DB emitted is refused."""
    ep, _runs, _n = _synthetic_extract(tmp_path, markets=("US",), n_runs=2, n_syms=5)
    payload = json.loads(ep.read_text())
    payload["runs"][0]["packed"] = payload["runs"][0]["packed"].replace("~B~", "~S~", 1)
    ep.write_text(json.dumps(payload))
    with pytest.raises(cgb.PopulationSourceError) as exc:
        cgb.load_extract(ep)
    assert "checksum mismatch" in str(exc.value)


def test_extract_row_count_mismatch_is_refused(tmp_path):
    ep, _runs, _n = _synthetic_extract(tmp_path, markets=("US",), n_runs=2, n_syms=5)
    payload = json.loads(ep.read_text())
    payload["runs"][0]["n"] = 999
    payload["runs"][0].pop("md5")
    ep.write_text(json.dumps(payload))
    with pytest.raises(cgb.PopulationSourceError) as exc:
        cgb.parse_extract(cgb.load_extract(ep), "US", "short")
    assert "truncated population" in str(exc.value)
