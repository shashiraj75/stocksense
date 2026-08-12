"""
V-VAL1 — Max Drawdown determinism and same-date order-invariance.

Re-derivation rationale (this phase explicitly revisits the earlier
"sort by signal_date" hunk rather than porting it verbatim): a naive sort
by signal_date alone leaves the INTRA-date order of same-day signals
arbitrary — and that order historically originated from
ThreadPoolExecutor completion order (see run_validation()'s
as_completed() loop), which is nondeterministic across runs. A run
evaluates ~100+ stocks per historical date, so same-day BUY signals are
the common case, not an edge case — a metric whose value depends on
which of several same-day signals happens to be compounded first is not
a defensible "peak-to-trough" number.

Contract implemented: group valid (non-null fwd_return_pct) BUY signals
by signal_date, compute one equal-weight mean return per date, sort DATES
chronologically (this is what removes the ambiguity — a date has no
internal ordering ambiguity, only signals within the date did), compound
the date-level series, then compute peak-to-trough drawdown on that
deterministic curve.

This is NOT a capital-allocated portfolio backtest — signals within a
date can represent overlapping/concurrent positions, and this function
makes no capital-allocation, transaction-cost, or execution-timing
assumption. It answers a narrower question: "if every calendar date's
BUY signals were equal-weighted into one date-level return, and those
per-date returns were compounded chronologically, what's the worst
peak-to-trough run?" See the frontend label for the disclosure text.
"""

import pytest

from services.validation_engine import _max_drawdown


def _buy(date, ret):
    return {"signal_date": date, "fwd_return_pct": ret, "predicted": "BUY"}


def test_chronological_permutation_invariance():
    chronological = [_buy("2026-01-01", 10.0), _buy("2026-01-02", -50.0), _buy("2026-01-03", 5.0)]
    shuffled = [chronological[2], chronological[0], chronological[1]]

    assert _max_drawdown(chronological) == _max_drawdown(shuffled)
    # Equity: 100 -> 110 -> 55 -> 57.75. Peak-to-trough = (110-55)/110 = 50.0%.
    assert _max_drawdown(chronological) == 50.0


def test_same_day_input_order_invariance():
    # Two signals on the same date, in one order vs the other — the
    # date-level return (their mean) must be identical either way.
    order_a = [_buy("2026-01-01", 10.0), _buy("2026-01-01", -30.0), _buy("2026-01-02", 5.0)]
    order_b = [_buy("2026-01-01", -30.0), _buy("2026-01-01", 10.0), _buy("2026-01-02", 5.0)]

    assert _max_drawdown(order_a) == _max_drawdown(order_b)


def test_multiple_same_day_signals_are_equal_weighted_into_one_date_return():
    # Day 1: three signals averaging to -10% ((-30 + 10 + 20)/3 = 0).
    # Equity should move 100 -> 100 (flat) on day 1, then -20% on day 2.
    buys = [
        _buy("2026-01-01", -30.0), _buy("2026-01-01", 10.0), _buy("2026-01-01", 20.0),
        _buy("2026-01-02", -20.0),
    ]
    dd = _max_drawdown(buys)
    # Equity: 100 -> 100.0 -> 80.0. Peak-to-trough = 20.0%.
    assert dd == 20.0


def test_missing_invalid_returns_excluded_from_date_aggregation():
    buys = [
        _buy("2026-01-01", 10.0),
        {"signal_date": "2026-01-01", "fwd_return_pct": None, "predicted": "BUY"},
        _buy("2026-01-02", -5.0),
    ]
    # The null-return row must not corrupt day 1's average (stays 10.0,
    # not (10+None)/2 which would error, and not treated as 0.0).
    dd = _max_drawdown(buys)
    # Equity: 100 -> 110 -> 104.5. Peak-to-trough = (110-104.5)/110 = 5.0%.
    assert dd == 5.0


def test_empty_series_returns_none():
    assert _max_drawdown([]) is None


def test_all_null_returns_series_returns_none():
    buys = [{"signal_date": "2026-01-01", "fwd_return_pct": None, "predicted": "BUY"}]
    assert _max_drawdown(buys) is None


def test_all_positive_series_has_zero_drawdown():
    buys = [_buy("2026-01-01", 2.0), _buy("2026-01-02", 3.0), _buy("2026-01-03", 1.0)]
    assert _max_drawdown(buys) == 0.0


def test_known_peak_to_trough_example():
    # 100 -> 120 (+20%) -> 90 (-25%) -> 108 (+20%). Peak 120, trough 90.
    # Drawdown = (120-90)/120 = 25.0%.
    buys = [_buy("2026-01-01", 20.0), _buy("2026-01-02", -25.0), _buy("2026-01-03", 20.0)]
    assert _max_drawdown(buys) == 25.0


def test_deterministic_repeated_execution():
    buys = [_buy("2026-01-02", -10.0), _buy("2026-01-01", 5.0), _buy("2026-01-01", 15.0)]
    results = {_max_drawdown(list(buys)) for _ in range(5)}
    assert len(results) == 1


# ═════════════════════════════════════════════════════════════════════════
# V-VAL1 correction — return-domain validation (fail closed).
#
# For a long-equity percentage return: exactly -100% (total loss) is the
# valid floor. A return below -100%, or a non-finite/non-numeric value, is
# impossible for a long equity position and indicates malformed upstream
# evidence, not a real drawdown event. Any such value must invalidate the
# WHOLE metric (return None), never be clamped, replaced, or silently
# dropped while computing an apparently-valid drawdown from the remaining
# rows — a partial calculation over a corrupted cohort would look
# indistinguishable from a genuine one.
# ═════════════════════════════════════════════════════════════════════════

import math


def test_exactly_minus_100_percent_is_valid():
    buys = [_buy("2026-01-01", -100.0)]
    assert _max_drawdown(buys) is not None


def test_exactly_minus_100_percent_produces_100_percent_drawdown():
    # Equity: 100 -> 0. Peak 100, trough 0. Drawdown = 100.0%.
    buys = [_buy("2026-01-01", -100.0)]
    assert _max_drawdown(buys) == 100.0


def test_return_below_minus_100_percent_is_unavailable():
    buys = [_buy("2026-01-01", -150.0)]
    assert _max_drawdown(buys) is None


def test_nan_return_is_unavailable():
    buys = [_buy("2026-01-01", float("nan"))]
    assert _max_drawdown(buys) is None


def test_positive_infinity_return_is_unavailable():
    buys = [_buy("2026-01-01", math.inf)]
    assert _max_drawdown(buys) is None


def test_negative_infinity_return_is_unavailable():
    buys = [_buy("2026-01-01", -math.inf)]
    assert _max_drawdown(buys) is None


def test_non_numeric_return_is_unavailable():
    # Malformed upstream evidence (e.g. a string) reaching this function —
    # the schema does not enforce a numeric type on fwd_return_pct any more
    # than it enforces NOT NULL, so this is defended the same way.
    buys = [_buy("2026-01-01", "not-a-number")]
    assert _max_drawdown(buys) is None


def test_invalid_value_among_otherwise_valid_rows_invalidates_whole_metric():
    buys = [
        _buy("2026-01-01", 10.0),
        _buy("2026-01-02", -150.0),  # impossible — below -100%
        _buy("2026-01-03", 5.0),
    ]
    # Must NOT silently drop the malformed row and compute a drawdown from
    # the two remaining valid ones — the whole metric is unavailable.
    assert _max_drawdown(buys) is None


def test_invalid_value_in_a_same_date_group_invalidates_whole_metric():
    buys = [
        _buy("2026-01-01", 10.0),
        _buy("2026-01-01", math.nan),  # same date as a valid signal
        _buy("2026-01-02", 5.0),
    ]
    assert _max_drawdown(buys) is None


def test_invalidation_is_input_order_invariant():
    valid = [_buy("2026-01-01", 10.0), _buy("2026-01-03", 5.0)]
    invalid = _buy("2026-01-02", -200.0)

    first = [invalid] + valid
    middle = [valid[0], invalid, valid[1]]
    last = valid + [invalid]

    assert _max_drawdown(first) is None
    assert _max_drawdown(middle) is None
    assert _max_drawdown(last) is None


def test_null_return_still_excluded_before_validation_not_invalidating():
    # A genuinely missing (None) return is excluded from the cohort BEFORE
    # domain validation runs — this is the pre-existing, intentional
    # null-exclusion contract (see test_missing_invalid_returns_excluded_
    # from_date_aggregation above), not a malformed-value case. It must
    # NOT invalidate the whole metric the way a below-floor or non-finite
    # value does.
    buys = [
        _buy("2026-01-01", 10.0),
        {"signal_date": "2026-01-01", "fwd_return_pct": None, "predicted": "BUY"},
        _buy("2026-01-02", -5.0),
    ]
    assert _max_drawdown(buys) == 5.0
