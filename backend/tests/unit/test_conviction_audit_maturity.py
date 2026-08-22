"""
Tests for the PRE-REGISTERED horizon-maturity eligibility rule
(audit_contract section 7), the two-stage denominator waterfall, and the
read-only audit-readiness report.

WHY THESE TESTS ARE THE POINT, NOT A FORMALITY
----------------------------------------------
A maturity rule adopted after seeing which results it produces is not a
methodology, it is a selection. The 2026-08-22 closure attempt aborted on the
missingness guard, and a mature-window restriction was PROBED at that moment
and found to clear the guard for US but not India. That is exactly the
circumstance in which a rule must NOT be adopted — so it was not, and it is
being registered here instead, in code, under test, BEFORE the next
current-policy outcome set matures.

These tests are what makes that registration mean something. Each pins one
property that a post-hoc rule could not satisfy:

  * outcomes cannot change eligibility,
  * signal / conviction / publication status cannot change eligibility,
  * extending the cutoff can only move rows immature -> mature,
  * the exit session comes from the right EXCHANGE calendar,
  * immature rows are dropped BEFORE any outcome or provider lookup,
  * there is no code path that re-selects the population until the guard
    passes,
  * and the maturity boundary cannot be moved from the command line.

No test here queries production, calls a live provider, or hardcodes a live
row count.
"""

from __future__ import annotations

import datetime as _dt
import inspect
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
for p in (str(BACKEND), str(BACKEND / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import conviction_gate_backtest as cgb  # noqa: E402
from services.alpha_engine import (  # noqa: E402
    audit_calendar, audit_contract, audit_prices, audit_readiness,
)

UTC = _dt.timezone.utc
pytestmark = pytest.mark.unit

# A settled cutoff far enough in the past that every window used below has
# unambiguously closed, so no test depends on the wall clock.
CUTOFF = _dt.datetime(2026, 8, 22, 0, 0, tzinfo=UTC)


def _row(symbol="AAA", *, signal="BUY", confidence=90.0, is_pick=False,
         generated=None, ref=None, run_id="r1"):
    return {
        "run_id": run_id,
        "symbol": symbol,
        "signal": signal,
        "signal_confidence": confidence,
        "is_daily_pick": is_pick,
        "pick_rank": 1 if is_pick else None,
        "ranking_alpha": 1.0,
        "reference_price": 100.0,
        "run_generated_at": generated or _dt.datetime(2026, 7, 20, 6, 0, tzinfo=UTC),
        "run_session_date": ref or _dt.date(2026, 7, 20),
        "reference_session_date": ref or _dt.date(2026, 7, 20),
    }


def _maturity(market="US", measure=audit_contract.EXECUTABLE_NEXT_OPEN, **kw):
    row = _row(**kw)
    return audit_contract.horizon_maturity(
        market, measure,
        reference_session_date=row["reference_session_date"],
        run_generated_at=row["run_generated_at"],
        trading_days=cgb.HORIZON_TRADING_DAYS["short"],
        cutoff=kw.pop("cutoff", CUTOFF))


# ══════════════════════════════════════════════════════════════════════════
# 1. Eligibility cannot depend on the outcome
# ══════════════════════════════════════════════════════════════════════════

def test_maturity_signature_cannot_receive_an_outcome():
    """
    The strongest possible form of "outcomes cannot change eligibility": the
    function has no parameter through which an outcome could arrive.

    A signature test rather than a behavioural one, deliberately. A
    behavioural test proves that today's implementation ignores the outcome;
    this proves that tomorrow's CANNOT read one without a visible signature
    change in a diff.
    """
    params = set(inspect.signature(audit_contract.horizon_maturity).parameters)
    assert params == {"market", "measure", "reference_session_date",
                      "run_generated_at", "trading_days", "cutoff"}
    forbidden = {"signal", "signal_confidence", "conviction", "is_daily_pick",
                 "published", "return_pct", "realized", "win", "snapshot",
                 "prices", "p_value", "missingness"}
    assert not (params & forbidden)


def test_changing_the_outcome_cannot_change_eligibility():
    """Two rows identical but for their realized return are equally mature."""
    winner = _row(symbol="WIN")
    loser = _row(symbol="LOSE")
    winner["realized_return_pct"] = 42.0
    loser["realized_return_pct"] = -42.0
    rows = [winner, loser]
    cgb.classify_maturity(rows, "US", "short", cutoff=CUTOFF)
    verdicts = {r["symbol"]: r["maturity"][audit_contract.EXECUTABLE_NEXT_OPEN].mature
                for r in rows}
    assert verdicts == {"WIN": True, "LOSE": True}


# ══════════════════════════════════════════════════════════════════════════
# 2. Eligibility cannot depend on signal / conviction / publication
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("signal,confidence,is_pick", [
    ("BUY", 99.0, True), ("BUY", 10.0, False),
    ("HOLD", 50.0, False), ("SELL", 1.0, False), ("BUY", 85.0, True),
])
def test_signal_conviction_and_publication_do_not_move_the_boundary(
        signal, confidence, is_pick):
    """
    Every variant of the fields the audit compares must produce the SAME
    maturity verdict and the SAME exit session — otherwise eligibility is
    selecting on the very thing under test.
    """
    baseline = _maturity()
    got = _maturity(signal=signal, confidence=confidence, is_pick=is_pick)
    assert got.mature == baseline.mature
    assert got.entry_session == baseline.entry_session
    assert got.exit_session == baseline.exit_session


def test_immaturity_breakdown_is_reported_by_group_even_though_it_is_not_used():
    """
    Immaturity may not INFLUENCE eligibility, but it must be VISIBLE by
    group — that is how a reader checks the exclusion really is
    administrative rather than a selection wearing an administrative label.
    """
    recent = _dt.datetime(2026, 8, 21, 6, 0, tzinfo=UTC)
    rows = cgb.prepare_rows(
        [_row("AAA", generated=recent, ref=_dt.date(2026, 8, 21)),
         _row("BBB", signal="HOLD", generated=recent, ref=_dt.date(2026, 8, 21))],
        "US", "short")
    cgb.classify_maturity(rows, "US", "short", cutoff=CUTOFF)
    report = cgb._immaturity_breakdown(rows, audit_contract.EXECUTABLE_NEXT_OPEN)
    assert report["n_immature"] == 2
    assert set(report["by_signal"]) == {"BUY", "HOLD"}
    assert set(report["by_publication_group"]) == {"UNPUBLISHED"}
    assert report["by_conviction_group"]


# ══════════════════════════════════════════════════════════════════════════
# 3. Extending the cutoff is MONOTONE: immature -> mature, never back
# ══════════════════════════════════════════════════════════════════════════

def test_extending_the_cutoff_only_ever_adds_mature_rows():
    """
    Walked over real calendar dates, across a span containing weekends and
    a run whose window closes inside it. Once a row is mature at some cutoff
    it must be mature at every later cutoff — the property that makes it
    impossible to drop a row by advancing the boundary once its outcome is
    known.
    """
    generated = _dt.datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
    seen_mature = False
    for offset in range(0, 45):
        cutoff = _dt.datetime(2026, 7, 20, tzinfo=UTC) + _dt.timedelta(days=offset)
        decision = audit_contract.horizon_maturity(
            "US", audit_contract.EXECUTABLE_NEXT_OPEN,
            run_generated_at=generated,
            trading_days=cgb.HORIZON_TRADING_DAYS["short"], cutoff=cutoff)
        if seen_mature:
            assert decision.mature, (
                f"row regressed to immature at cutoff {cutoff.isoformat()} — "
                "the maturity rule is not monotone in the cutoff")
        seen_mature = seen_mature or decision.mature
    assert seen_mature, "the window never matured across a 45-day span"


def test_a_cutoff_before_the_exit_close_is_immature_and_names_the_exit():
    """
    Immaturity is not an error state: the decision still carries the exit
    session and the instant it closes, so the date it WILL mature on is
    computable in advance rather than discovered by retrying.
    """
    decision = audit_contract.horizon_maturity(
        "US", audit_contract.EXECUTABLE_NEXT_OPEN,
        run_generated_at=_dt.datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
        trading_days=cgb.HORIZON_TRADING_DAYS["short"], cutoff=CUTOFF)
    assert not decision.mature
    assert decision.reason == audit_contract.ADMINISTRATIVELY_IMMATURE
    assert decision.exit_session is not None
    assert decision.exit_close_utc > CUTOFF


# ══════════════════════════════════════════════════════════════════════════
# 4. The exit session comes from the right EXCHANGE calendar
# ══════════════════════════════════════════════════════════════════════════

def test_exit_session_uses_the_market_specific_exchange_calendar():
    """
    NSE and NYSE do not share a holiday calendar, so the same trading-day
    offset from the same date must be allowed to land on different sessions.
    The exit session is asserted to be an actual session on the row's OWN
    exchange — never a naive calendar-day offset.
    """
    days = cgb.HORIZON_TRADING_DAYS["short"]
    for market in ("US", "IN"):
        decision = audit_contract.horizon_maturity(
            market, audit_contract.RESEARCH_PRIOR_CLOSE,
            reference_session_date=_dt.date(2026, 7, 20),
            trading_days=days, cutoff=CUTOFF)
        assert decision.mature
        assert audit_calendar.is_session(market, decision.entry_session)
        assert audit_calendar.is_session(market, decision.exit_session)
        # Exactly `days` sessions apart on that exchange's own calendar.
        assert audit_calendar.session_offset(
            market, decision.entry_session, days) == decision.exit_session


def test_maturity_entry_uses_the_exchange_calendar_not_the_price_provider():
    """
    A symbol the provider never returned must still receive a maturity
    verdict on the same terms as every other row, so that it lands in stage B
    as GENUINE MISSINGNESS instead of disappearing at stage A. If maturity
    consulted provider coverage, a coverage gap would silently shrink the
    denominator — the failure mode the two-stage waterfall exists to expose.
    """
    empty = audit_prices.PriceSnapshot().freeze()
    rows = cgb.prepare_rows([_row("NEVER_FETCHED")], "US", "short")
    cgb.resolve_returns(rows, "US", "short", empty, today=CUTOFF.date(),
                        cutoff=CUTOFF)
    assert cgb._is_mature(rows[0], audit_contract.EXECUTABLE_NEXT_OPEN)
    stage = audit_contract.classify_exclusion_stage(
        rows[0]["executable_excluded_reason"])
    assert stage == "genuine_price_missingness"


# ══════════════════════════════════════════════════════════════════════════
# 5. Immature rows are excluded BEFORE any outcome or provider lookup
# ══════════════════════════════════════════════════════════════════════════

class _SpySnapshot:
    """A snapshot that records every price question asked of it."""

    def __init__(self):
        self.calls: list[tuple] = []

    def get_open(self, market, symbol, day):
        self.calls.append(("get_open", symbol, str(day)))
        return None

    def get_close(self, market, symbol, day):
        self.calls.append(("get_close", symbol, str(day)))
        return None

    def first_session_on_or_after(self, market, symbol, day):
        self.calls.append(("first_session_on_or_after", symbol, str(day)))
        return None

    def sessions(self, market, symbol):
        return []


def test_no_price_is_ever_looked_up_for_an_administratively_immature_row():
    """
    Ordering proof, not a claim about ordering. The spy records every price
    question; for an immature row there must be NONE. This is what makes
    "immaturity is not missingness" structural: a lookup that never happens
    cannot fail, and a failure that cannot happen cannot be counted.
    """
    spy = _SpySnapshot()
    rows = cgb.prepare_rows(
        [_row("IMMATURE", generated=_dt.datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
              ref=_dt.date(2026, 8, 21))], "US", "short")
    cgb.resolve_returns(rows, "US", "short", spy, today=CUTOFF.date(),
                        cutoff=CUTOFF)
    assert spy.calls == []
    for measure in (audit_contract.RESEARCH_PRIOR_CLOSE,
                    audit_contract.EXECUTABLE_NEXT_OPEN):
        assert rows[0][measure] is None
        assert not rows[0]["maturity"][measure].mature
    assert rows[0]["executable_provenance"]["price_lookup_performed"] is False


def test_a_mature_row_does_reach_the_provider():
    """The negative control for the test above: maturity is a gate, not a ban."""
    spy = _SpySnapshot()
    rows = cgb.prepare_rows([_row("MATURE")], "US", "short")
    cgb.resolve_returns(rows, "US", "short", spy, today=CUTOFF.date(),
                        cutoff=CUTOFF)
    assert spy.calls, "a mature row must actually be priced"
    assert rows[0]["executable_provenance"]["price_lookup_performed"] is True


# ══════════════════════════════════════════════════════════════════════════
# 6. Immaturity is NOT missingness — the two-stage waterfall
# ══════════════════════════════════════════════════════════════════════════

def test_immaturity_never_enters_the_missingness_percentage():
    """
    The specific conflation that made the 2026-08-22 guard unpassable: an
    immature row counted as an unresolved price. Here 9 of 10 rows are
    immature and the one mature row resolves, so stage B's genuine
    missingness must be ZERO — not 90%.
    """
    records = [{"mature": False,
                "reason": audit_contract.ADMINISTRATIVELY_IMMATURE}] * 9
    records = list(records) + [{"mature": True, "reason": None}]
    wf = audit_contract.two_stage_waterfall(records, stage_label="t")
    assert wf["stage_a"]["administratively_immature"] == 9
    assert wf["stage_a"]["horizon_mature_at_cutoff"] == 1
    assert wf["stage_b"]["mature_eligible"] == 1
    assert wf["stage_b"]["genuine_price_missingness"] == 0
    assert wf["stage_b"]["genuine_missingness_rate"] == 0.0


def test_both_waterfall_stages_reconcile_exactly():
    records = (
        [{"mature": True, "reason": None}] * 7
        + [{"mature": True, "reason": "exit_close_missing"}] * 2
        + [{"mature": True, "reason": "non_positive_entry_price"}]
        + [{"mature": False, "reason": audit_contract.ADMINISTRATIVELY_IMMATURE}] * 4
        + [{"mature": False, "reason": "missing_run_generated_at"}]
    )
    wf = audit_contract.two_stage_waterfall(records, stage_label="t")
    a, b = wf["stage_a"], wf["stage_b"]
    assert a["fetched"] == 15
    assert a["fetched"] == (a["horizon_mature_at_cutoff"]
                            + a["administratively_immature"]
                            + a["other_contract_exclusions"])
    assert a["other_contract_exclusions"] == 1
    assert b["mature_eligible"] == (b["price_resolved"]
                                    + b["genuine_price_missingness"]
                                    + b["non_finite_or_invalid_price"])
    assert b["price_resolved"] == 7
    assert b["genuine_price_missingness"] == 2


def test_an_unbalanced_waterfall_raises_rather_than_reporting():
    with pytest.raises(audit_contract.ReconciliationError):
        audit_contract.assert_reconciles("t", 10, 4, 4)


@pytest.mark.parametrize("reason,stage", [
    (None, "included"),
    (audit_contract.ADMINISTRATIVELY_IMMATURE, "administrative_immaturity"),
    ("exit_close_missing", "genuine_price_missingness"),
    ("entry_open_missing", "genuine_price_missingness"),
    ("provider_calendar_disagrees_with_exchange_calendar",
     "genuine_price_missingness"),
    ("non_positive_entry_price", "invalid_price"),
    ("missing_run_generated_at", "other_contract_exclusion"),
    ("something_nobody_has_seen_before", "other_contract_exclusion"),
])
def test_every_exclusion_reason_lands_in_exactly_one_named_stage(reason, stage):
    """
    Including the unknown reason: an unrecognised code is named as a
    contract exclusion, never silently folded into the missingness rate.
    """
    assert audit_contract.classify_exclusion_stage(reason) == stage


def test_horizon_window_not_yet_complete_is_never_a_missingness_reason():
    """
    The legacy reason string may not appear in the genuine-missingness set.
    It described BOTH conditions at once, which is how the two were conflated.
    """
    assert ("horizon_window_not_yet_complete"
            not in audit_contract.GENUINE_PRICE_MISSINGNESS_REASONS)


def test_the_guard_runs_over_the_mature_cohort_only():
    """
    End to end through `build_audit`: with a mostly-immature population, the
    missingness report's denominator must be the MATURE count, and the
    immature rows must still be reported.
    """
    snap = audit_prices.PriceSnapshot()
    for day, price in (("2026-07-20", 100.0), ("2026-07-21", 100.0),
                       ("2026-07-22", 100.0), ("2026-07-23", 100.0),
                       ("2026-07-24", 100.0), ("2026-07-27", 110.0),
                       ("2026-07-28", 110.0)):
        snap.put("US", "AAA", day, price, price)
    snap = snap.freeze()
    rows = cgb.prepare_rows(
        [_row("AAA")]
        + [_row("AAA", run_id=f"late{i}",
                generated=_dt.datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
                ref=_dt.date(2026, 8, 21)) for i in range(5)],
        "US", "short")
    cgb.resolve_returns(rows, "US", "short", snap, today=CUTOFF.date(),
                        cutoff=CUTOFF)
    audit = cgb.build_audit("US", "short", rows, seed=1,
                            permutation_draws=1, bootstrap_draws=1)
    rep = audit["missingness"][audit_contract.EXECUTABLE_NEXT_OPEN]
    assert rep["cohort"] == "MATURE_ELIGIBLE_ONLY"
    assert rep["n"] == 1
    assert rep["administratively_immature_excluded"] == 5
    wf = audit["waterfall"][audit_contract.EXECUTABLE_NEXT_OPEN]
    assert wf["two_stage"]["stage_a"]["administratively_immature"] == 5
    assert wf["administrative_immaturity"]["n_immature"] == 5


# ══════════════════════════════════════════════════════════════════════════
# 7. No re-selection until the guard passes; no CLI maturity knob
# ══════════════════════════════════════════════════════════════════════════

def test_there_is_no_code_path_that_retries_with_a_different_population():
    """
    The population is selected ONCE, by the pre-registered rule, and the
    guard is applied to whatever that yields. There is deliberately no
    fallback, no widening, no relaxation and no second attempt: a breach
    RAISES.

    Asserted structurally — the guard's only reaction to a breach in
    `run_full_audit` is to raise, and `classify_maturity` is called exactly
    once per cell, from `resolve_returns`, with no alternative caller.
    """
    src = inspect.getsource(cgb.run_full_audit)
    guard_block = src[src.index("if enforce_missingness"):]
    assert "raise audit_prices.MissingnessAbort" in guard_block
    for forbidden in ("except audit_prices.MissingnessAbort", "retry",
                      "fallback", "relax", "widen"):
        assert forbidden not in src, (
            f"run_full_audit mentions {forbidden!r} — a closure run must not "
            "be able to re-select its population after seeing the guard fail")
    # `resolve_returns` is the ONLY caller of the stage-A classifier inside
    # the resolution path, so the population cannot be reclassified per-try.
    assert "classify_maturity(rows, market, horizon, cutoff=cutoff)" in \
        inspect.getsource(cgb.resolve_returns)


def test_the_cutoff_is_not_a_command_line_argument():
    """
    A run whose analyst can retune the maturity boundary between attempts has
    no pre-registration at all. No CLI flag may reach the cutoff.
    """
    parser_src = inspect.getsource(cgb.main)
    for flag in ("--cutoff", "--maturity", "--mature", "--min-maturity",
                 "--horizon-cutoff", "--price-data-cutoff", "--as-of"):
        assert flag not in parser_src


def test_today_is_refused_in_audit_mode():
    """
    `--today` would move the boundary indirectly. In audit mode it is
    REFUSED, loudly, rather than quietly honoured.
    """
    with pytest.raises(cgb.MaturityOverrideRefused) as exc:
        cgb.main(["--audit-out", "/nonexistent", "--markets", "US",
                  "--horizons", "short", "--today", "2026-08-01"])
    assert "AUDIT_PRICE_DATA_CUTOFF_UTC" in str(exc.value)
    # And it is refused BEFORE any work: the run never starts.
    assert "refused in audit mode" in str(exc.value)


def test_the_registered_cutoff_and_rule_id_are_frozen_constants():
    assert audit_contract.AUDIT_PRICE_DATA_CUTOFF_UTC.tzinfo is not None
    assert audit_contract.MATURITY_RULE_ID == "horizon-maturity-eligibility-1.0.0"
    # Registered NOW — before the next current-policy outcome set matures.
    assert (audit_contract.MATURITY_RULE_REGISTERED_UTC
            <= audit_contract.AUDIT_PRICE_DATA_CUTOFF_UTC)


def test_the_contract_documents_the_pre_registration_and_its_prohibitions():
    doc = audit_contract.__doc__
    assert "HORIZON-MATURITY ELIGIBILITY" in doc
    assert "PRE-REGISTERED 2026-08-22" in doc
    for forbidden_input in ("publication status", "realized return",
                            "observed missingness rate"):
        assert forbidden_input in doc


# ══════════════════════════════════════════════════════════════════════════
# 8. Readiness depends on counts and coverage — never on an outcome
# ══════════════════════════════════════════════════════════════════════════

def test_readiness_row_has_no_field_for_any_outcome():
    """
    Same structural argument as the maturity signature test: readiness cannot
    become outcome-dependent without widening this type in a visible diff.
    """
    fields = set(audit_readiness.ReadinessRow.__dataclass_fields__)
    forbidden = {"return_pct", "win", "is_win", "effect", "p_value",
                 "difference_pp", "claim_level", "win_rate"}
    assert not (fields & forbidden)
    assert "price_resolved" in fields  # coverage IS allowed; outcomes are not


def _ready_rows(n_runs, per_run, era=audit_contract.ERA_GATE_PLUS_CAP,
                mature=True):
    rows = []
    for i in range(n_runs):
        day = _dt.date(2026, 8, 17) + _dt.timedelta(days=i)
        for j in range(per_run):
            pops = [audit_contract.P_ALL_ELIGIBLE, audit_contract.P_BUY,
                    audit_contract.P_BUY_HIGH_CONV]
            pops.append(audit_contract.P_PUBLISHED if j == 0
                        else audit_contract.P_UNPUBLISHED_BUY)
            rows.append(audit_readiness.ReadinessRow(
                market="US", horizon="short", run_id=f"r{i}",
                run_session_date=day, policy_era=era, symbol=f"S{j}",
                populations=tuple(pops), mature=mature,
                exit_session=day + _dt.timedelta(days=7)))
    return rows


def test_readiness_is_not_ready_when_a_required_comparison_is_short():
    report = audit_readiness.assess(_ready_rows(2, 5), coverage={"passed": True})
    assert report["status"] == audit_readiness.NOT_READY
    assert any("published_vs_unpublished_buy" in b for b in report["blockers"])


def test_readiness_fails_closed_without_a_price_panel():
    """
    "We did not measure coverage" is not evidence that coverage is fine.
    With no panel the verdict is UNKNOWN and the cell is NOT_READY.
    """
    report = audit_readiness.assess(_ready_rows(30, 40), coverage=None)
    assert report["mature_cohort_coverage_status"] == audit_readiness.COVERAGE_UNKNOWN
    assert report["status"] == audit_readiness.NOT_READY


def test_a_breaching_coverage_report_blocks_readiness():
    report = audit_readiness.assess(
        _ready_rows(30, 40), coverage={"passed": False, "breaches": ["x"]})
    assert report["mature_cohort_coverage_status"] == audit_readiness.COVERAGE_BREACH
    assert report["status"] == audit_readiness.NOT_READY


def test_readiness_counts_only_mature_rows_toward_the_minimums():
    """Immature rows exist and are reported, but never count toward a floor."""
    rows = _ready_rows(30, 40) + _ready_rows(30, 40, mature=False)
    report = audit_readiness.assess(rows, coverage={"passed": True})
    assert report["rows_administratively_immature"] == 30 * 40
    assert report["rows_horizon_mature"] == 30 * 40
    for cell in report["comparisons"].values():
        assert cell["mature_runs"] <= 30


def test_readiness_retains_the_existing_published_unpublished_minimums():
    """
    The floors are the CONTRACT's, not a number this module invented, so they
    cannot be lowered here to reach an earlier verdict.
    """
    report = audit_readiness.assess(_ready_rows(2, 5), coverage={"passed": True})
    mins = report["comparisons"]["published_vs_unpublished_buy"]["minimums"]
    assert mins["runs"] == audit_contract.MIN_RUNS_PER_ERA_FOR_ESTIMATE
    assert mins["rows_per_group"] == audit_contract.MIN_RESOLVED_PER_GROUP_FOR_ESTIMATE


def test_readiness_reports_an_earliest_possible_date_from_existing_runs_only():
    """
    When existing runs cannot satisfy the floors, no date is invented — the
    report says so in words rather than projecting a comforting one.
    """
    report = audit_readiness.assess(_ready_rows(2, 5), coverage={"passed": True})
    cell = report["comparisons"]["published_vs_unpublished_buy"]
    assert cell["earliest_possible_identifiable_date"] is None
    assert "more runs must be generated" in cell["earliest_date_basis"]


def test_readiness_never_reads_an_outcome_even_when_offered_one():
    """
    Behavioural companion to the type test: `assess` is handed rows carrying
    an extra outcome-shaped attribute and its verdict is unchanged.
    """
    rows = _ready_rows(2, 5)
    base = audit_readiness.assess(rows, coverage={"passed": True})
    assert base["outcome_inputs_used"] == []
    assert "No return, win rate, effect size or p-value was read" in base["note"]

    class _WithOutcome:
        """A row that ALSO carries an outcome, to prove it changes nothing."""

        def __init__(self, row, win):
            self._row, self.is_win, self.return_pct = row, win, 99.0 if win else -99.0

        def __getattr__(self, name):
            return getattr(self._row, name)

    winners = audit_readiness.assess([_WithOutcome(r, True) for r in rows],
                                     coverage={"passed": True})
    losers = audit_readiness.assess([_WithOutcome(r, False) for r in rows],
                                    coverage={"passed": True})
    for key in ("status", "blockers", "comparisons", "rows_horizon_mature"):
        assert winners[key] == base[key] == losers[key]
