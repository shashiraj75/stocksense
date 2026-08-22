"""
AUDIT READINESS — a read-only answer to one question: is there yet enough
MATURE data for the conviction audit's closure run to be attempted at all?

Why this module exists
----------------------
The 2026-08-22 closure attempt aborted on the price-missingness guard. The
guard was right to abort, but the reason it fired was misread: the dominant
"missing" category was `horizon_window_not_yet_complete` — rows whose horizon
had simply not finished yet. That is administrative immaturity, not
missingness (see `audit_contract` section 7), and no amount of re-running
resolves it. It resolves by WAITING, on a date that is computable in advance.

So the audit needs a cheap, deterministic instrument that says "not yet, and
here is the earliest date it could possibly be yes" WITHOUT computing a
single return, win rate, effect size or p-value. That is this module.

The hard rule, and the reason readiness is a separate module
------------------------------------------------------------
READINESS MAY ONLY EVER DEPEND ON PRE-REGISTERED COUNTS, DATES AND COVERAGE.
It may never depend on an observed outcome. If readiness could see a win
rate, "are we ready?" would collapse into "do I like the answer yet?", and
the pre-registration in `audit_contract` section 7 would be worth nothing.

This module is therefore structurally incapable of seeing an outcome: the
record type it consumes (`ReadinessRow`) has no return field, no win flag and
no p-value, and `assess()` accepts nothing else. A future change that wanted
to make readiness outcome-dependent would have to widen that type, in a diff,
in public.

`price_resolved` IS permitted, and is not an outcome: it records only whether
a price EXISTS for a row whose window has closed. It is the coverage input to
the missingness guard, it is measured over the mature cohort only, and it
carries no information about the direction or size of any return.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from services.alpha_engine import audit_contract as _c

# Status values. Deliberately only two: a readiness instrument that can say
# "sort of" is one an impatient analyst can argue with.
READY = "READY_FOR_CLOSURE_RUN"
NOT_READY = "NOT_READY"

# Coverage verdicts for the mature-cohort missingness guard.
COVERAGE_PASS = "MATURE_COVERAGE_WITHIN_GUARD"
COVERAGE_BREACH = "MATURE_COVERAGE_BREACHES_GUARD"
COVERAGE_UNKNOWN = "MATURE_COVERAGE_UNKNOWN_NO_PRICE_PANEL"

# The comparisons a closure run must be able to make. Each names the two
# contract populations it contrasts and whether it is restricted to a single
# publication-policy era (contract section 6).
#
# The published-vs-unpublished minimums are the EXISTING contract floors
# (`MIN_RUNS_PER_ERA_FOR_ESTIMATE`, `MIN_RESOLVED_PER_GROUP_FOR_ESTIMATE`),
# carried over unchanged. They are NOT lowered here, and lowering them to
# reach an earlier verdict would be the same post-hoc move the maturity
# pre-registration exists to prevent.
REQUIRED_COMPARISONS = (
    {"key": "buy_vs_non_buy",
     "groups": (_c.P_BUY, _c.P_NON_BUY),
     "era_restricted": False},
    {"key": "conviction_within_buy",
     "groups": (_c.P_BUY_HIGH_CONV, _c.P_BUY_LOW_CONV),
     "era_restricted": False},
    {"key": "published_vs_unpublished_buy",
     "groups": (_c.P_PUBLISHED, _c.P_UNPUBLISHED_BUY),
     "era_restricted": True},
)


@dataclass(frozen=True)
class ReadinessRow:
    """
    Everything readiness is ALLOWED to know about one observation.

    Note what is absent: no return, no win flag, no p-value, no effect size,
    no claim level. That absence is the guarantee, not the docstring.
    """

    market: str
    horizon: str
    run_id: str
    run_session_date: _dt.date
    policy_era: str
    symbol: str
    populations: tuple
    mature: bool
    exit_session: _dt.date | None
    price_resolved: bool | None = None


def _counts(rows, population):
    return sum(1 for r in rows if population in r.populations)


def _clusters(rows):
    return (len({r.run_session_date for r in rows}), len({r.symbol for r in rows}))


def _earliest_identifiable_date(all_rows, population_a, population_b,
                                min_runs, min_rows, era=None):
    """
    The earliest CALENDAR DATE on which a comparison could become
    identifiable using ONLY runs that already exist.

    Walks the already-generated runs in the order their horizon windows
    close and reports the date at which every floor is first met. Returns
    None when the existing runs can never satisfy the floors — in which case
    the honest answer is "not from data that exists yet", and no projected
    date is invented.

    This is a DATE arithmetic function over exit sessions. It reads no
    outcome and cannot.
    """
    from services.alpha_engine import audit_stats

    pool = [r for r in all_rows if era is None or r.policy_era == era]
    pool = [r for r in pool if r.exit_session is not None]
    if not pool:
        return None
    for boundary in sorted({r.exit_session for r in pool}):
        matured = [r for r in pool if r.exit_session <= boundary]
        if len({r.run_id for r in matured}) < min_runs:
            continue
        if _counts(matured, population_a) < min_rows:
            continue
        if _counts(matured, population_b) < min_rows:
            continue
        n_date, n_symbol = _clusters(matured)
        if min(n_date, n_symbol) < audit_stats.MIN_CLUSTERS_FOR_INFERENCE:
            continue
        # The window closes during that session; the data is usable from the
        # following calendar day at the earliest.
        return boundary + _dt.timedelta(days=1)
    return None


def assess(rows: list[ReadinessRow], *, cutoff: _dt.datetime | None = None,
           coverage: dict | None = None) -> dict:
    """
    The readiness report for one market x horizon x measure cell.

    `coverage` is an optional pre-computed mature-cohort missingness report
    (`audit_prices.missingness_report` + `enforce_missingness`, over MATURE
    ROWS ONLY). When it is absent, coverage is reported UNKNOWN and the cell
    is NOT_READY — readiness fails closed, because "we did not look" is not
    evidence that coverage is fine.
    """
    cutoff = cutoff or _c.AUDIT_PRICE_DATA_CUTOFF_UTC
    mature = [r for r in rows if r.mature]
    immature = [r for r in rows if not r.mature]

    runs_by_era: dict[str, dict] = {}
    for r in rows:
        slot = runs_by_era.setdefault(r.policy_era, {"runs": set(), "rows": 0,
                                                     "mature_runs": set(),
                                                     "mature_rows": 0})
        slot["runs"].add(r.run_id)
        slot["rows"] += 1
        if r.mature:
            slot["mature_runs"].add(r.run_id)
            slot["mature_rows"] += 1
    runs_by_era = {
        era: {"runs": len(v["runs"]), "rows": v["rows"],
              "mature_runs": len(v["mature_runs"]), "mature_rows": v["mature_rows"]}
        for era, v in sorted(runs_by_era.items())
    }

    current_era = _c.ERA_GATE_PLUS_CAP
    comparisons = {}
    for spec in REQUIRED_COMPARISONS:
        era = current_era if spec["era_restricted"] else None
        pool = [r for r in mature if era is None or r.policy_era == era]
        a, b = spec["groups"]
        n_a, n_b = _counts(pool, a), _counts(pool, b)
        n_runs = len({r.run_id for r in pool})
        n_date, n_symbol = _clusters(pool)
        from services.alpha_engine import audit_stats
        meets = (
            n_runs >= _c.MIN_RUNS_PER_ERA_FOR_ESTIMATE
            and n_a >= _c.MIN_RESOLVED_PER_GROUP_FOR_ESTIMATE
            and n_b >= _c.MIN_RESOLVED_PER_GROUP_FOR_ESTIMATE
            and n_date >= audit_stats.MIN_CLUSTERS_FOR_INFERENCE
            and n_symbol >= audit_stats.MIN_CLUSTERS_FOR_INFERENCE
        )
        earliest = None if meets else _earliest_identifiable_date(
            rows, a, b, _c.MIN_RUNS_PER_ERA_FOR_ESTIMATE,
            _c.MIN_RESOLVED_PER_GROUP_FOR_ESTIMATE, era=era)
        comparisons[spec["key"]] = {
            "groups": [a, b],
            "policy_era": era,
            "mature_runs": n_runs,
            "mature_rows": {a: n_a, b: n_b},
            "date_clusters": n_date,
            "symbol_clusters": n_symbol,
            "minimums": {
                "runs": _c.MIN_RUNS_PER_ERA_FOR_ESTIMATE,
                "rows_per_group": _c.MIN_RESOLVED_PER_GROUP_FOR_ESTIMATE,
                "date_clusters": audit_stats.MIN_CLUSTERS_FOR_INFERENCE,
                "symbol_clusters": audit_stats.MIN_CLUSTERS_FOR_INFERENCE,
            },
            "meets_minimums": meets,
            "earliest_possible_identifiable_date": (
                earliest.isoformat() if earliest else None),
            "earliest_date_basis": (
                "already met" if meets else
                "earliest date the ALREADY-GENERATED runs satisfy every floor"
                if earliest else
                "unreachable from runs that exist today — more runs must be "
                "generated before any date can be computed"),
        }

    if coverage is None:
        coverage_status = COVERAGE_UNKNOWN
        coverage_detail = {
            "note": "no frozen price panel supplied; coverage was not measured. "
                    "Readiness fails closed rather than assuming it is fine."}
    elif coverage.get("passed"):
        coverage_status = COVERAGE_PASS
        coverage_detail = coverage
    else:
        coverage_status = COVERAGE_BREACH
        coverage_detail = coverage

    blockers = [f"{k}: below minimums" for k, v in comparisons.items()
                if not v["meets_minimums"]]
    if coverage_status != COVERAGE_PASS:
        blockers.append(f"mature-cohort price coverage: {coverage_status}")

    return {
        "maturity_rule_id": _c.MATURITY_RULE_ID,
        "cutoff_utc": cutoff.isoformat(),
        "rows_fetched": len(rows),
        "rows_horizon_mature": len(mature),
        "rows_administratively_immature": len(immature),
        "runs_by_policy_era": runs_by_era,
        "comparisons": comparisons,
        "mature_cohort_coverage_status": coverage_status,
        "mature_cohort_coverage": coverage_detail,
        "blockers": blockers,
        "status": READY if not blockers else NOT_READY,
        "outcome_inputs_used": [],
        "note": (
            "This report is computed from counts, dates and price COVERAGE "
            "only. No return, win rate, effect size or p-value was read, and "
            "the record type it consumes has no field for one."),
    }
