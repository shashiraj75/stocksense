"""
2026-09-06 corrective review of PR #83 — frozen research protocol.

Pure, deterministic, dependency-light functions implementing the
corrections the independent review demanded:

  1. Splits on COMPLETE UNIQUE DATES, never row count (a date must not
     straddle a split boundary with some of its rows on each side).
  2. Excludes any row whose forward-outcome window extends past the
     split boundary — a "train"-side signal near the boundary must not
     have its outcome resolve using price data from the "test" period.
  3. Separates weight/hyperparameter SELECTION (on a development split)
     from FINAL evaluation (on a genuinely untouched test split) —
     selecting a weight using a slice, then reporting results on that
     same slice, makes it development data from that point forward,
     never mentioned as "held-out" again.
  4. Dependence-aware significance testing: a stationary block bootstrap
     over the PER-DATE IC series (not a plain t-test assuming i.i.d.
     observations) — appropriate given medium-horizon's 21-session
     forward outcome windows, sampled roughly every 10 sessions, overlap
     substantially and are therefore serially dependent.

This module intentionally does not fetch data or call any external
provider — it operates on plain Python data (dates, values) supplied by
a caller, so its correctness is fully covered by fast, deterministic
unit tests (tests/unit/test_research_protocol.py) with no network
dependency. A caller wiring this to real validation-engine output is a
SEPARATE, explicitly deferred step — see this module's own
`RESEARCH_PROTOCOL_STATUS` constant and
DAILY-PICKS-IMPLEMENTATION-REGISTER.md for why: running it against live
data has not been done in this pass (an expensive, multi-hour,
network-dependent operation), and this module's job is to make sure
that WHEN it is run, it cannot repeat the confirmed defects (row-count
splits, outcome-window leakage, development-data-relabeled-as-holdout,
plain i.i.d. t-tests on dependent observations).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


RESEARCH_PROTOCOL_STATUS = "QUANTITATIVE ACCEPTANCE PENDING — NO UNTOUCHED HOLDOUT AVAILABLE"


@dataclass(frozen=True)
class SplitConfig:
    """Frozen BEFORE looking at any comparative outcome — the whole
    point of a protocol. `dev_fraction` of unique dates (chronologically
    earliest) are DEVELOPMENT data — used for weight/hyperparameter
    selection, EDA, and any iteration. The remaining dates are the FINAL
    TEST split — evaluated exactly once, with the frozen specification
    the development phase produced, and never revisited afterward."""
    dev_fraction: float = 0.7
    outcome_window_days: int = 21  # medium horizon's forward-return window, in trading days


@dataclass(frozen=True)
class SplitResult:
    dev_dates: tuple
    test_dates: tuple
    purged_dev_dates: tuple    # dev dates excluded for outcome-window leakage
    purged_test_dates: tuple   # test dates excluded for outcome-window leakage


def split_by_unique_date(all_dates: list, config: SplitConfig = SplitConfig()) -> SplitResult:
    """Splits on unique, sorted, chronological dates — never row count.
    A date is assigned ENTIRELY to dev or ENTIRELY to test; no date's
    rows can straddle the boundary.

    Then purges any date within `outcome_window_days` trading-day-index
    positions of the boundary on EITHER side, since a signal issued on
    such a date has its own forward-outcome window resolving using price
    data from the other side of the split — the exact "forward outcome
    windows cross split boundaries" leakage the corrective review named.
    Trading-day positions (not calendar days) are used, consistent with
    how validation_engine.py itself indexes forward windows.
    """
    unique_dates = sorted(set(all_dates))
    n = len(unique_dates)
    if n == 0:
        return SplitResult((), (), (), ())
    cut = int(n * config.dev_fraction)
    dev_dates = unique_dates[:cut]
    test_dates = unique_dates[cut:]

    # Purge zone: outcome_window_days on EITHER side of the boundary,
    # measured in unique-date POSITIONS (a reasonable trading-day proxy
    # when dates are themselves trading days, as validation_engine's
    # signal_date values are).
    purge_lo = max(0, cut - config.outcome_window_days)
    purge_hi = min(n, cut + config.outcome_window_days)

    purged_dev = tuple(d for i, d in enumerate(dev_dates) if i >= purge_lo)
    purged_test = tuple(d for i, d in enumerate(test_dates) if (cut + i) < purge_hi)

    clean_dev = tuple(d for i, d in enumerate(dev_dates) if i < purge_lo)
    clean_test = tuple(d for i, d in enumerate(test_dates) if (cut + i) >= purge_hi)

    return SplitResult(clean_dev, clean_test, purged_dev, purged_test)


def assert_no_date_overlap(result: SplitResult) -> None:
    """A frozen-protocol sanity check callers should run before trusting
    any split — fails loudly rather than silently producing a
    contaminated comparison."""
    dev_set = set(result.dev_dates)
    test_set = set(result.test_dates)
    overlap = dev_set & test_set
    if overlap:
        raise AssertionError(f"split_by_unique_date produced overlapping dates: {sorted(overlap)[:5]}...")


def stationary_block_bootstrap_ci(
    values: list[float],
    block_size: int,
    n_bootstrap: int = 2000,
    seed: int = 42,
    confidence: float = 0.95,
) -> dict:
    """Dependence-aware confidence interval for the mean of a serially
    dependent series (e.g. per-date Fama-MacBeth ICs, whose underlying
    signals share overlapping forward-outcome windows). Uses a
    stationary block bootstrap: resample contiguous blocks of
    `block_size` consecutive observations (with replacement) rather than
    individual points, preserving short-range serial dependence within
    each block — a standard alternative to a plain i.i.d. t-test or a
    parametric HAC correction when the dependence structure isn't
    precisely known.

    `block_size` must be chosen and DOCUMENTED before inspecting results
    — for medium-horizon validation (21-session forward windows sampled
    roughly every 10 sessions), a block size on the order of
    ceil(21/10) + 1 = 3 to 5 dates is a defensible starting point,
    reflecting how many consecutive sampled dates can share overlapping
    outcome windows; this is a judgment call, not derived from the data,
    and must be stated alongside any result that uses it.

    Returns the observed mean, the bootstrap standard error, and a
    percentile confidence interval — deliberately NOT a p-value/t-stat
    alone, per the requirement to report effect size and stability, not
    only significance.
    """
    n = len(values)
    if n < block_size:
        return {"n": n, "mean": None, "ci_low": None, "ci_high": None, "se": None, "block_size": block_size}

    rng = random.Random(seed)
    observed_mean = sum(values) / n
    n_blocks_needed = -(-n // block_size)  # ceil

    boot_means = []
    for _ in range(n_bootstrap):
        resampled = []
        for _ in range(n_blocks_needed):
            start = rng.randrange(0, n - block_size + 1)
            resampled.extend(values[start:start + block_size])
        resampled = resampled[:n]
        boot_means.append(sum(resampled) / len(resampled))

    boot_means.sort()
    se = (sum((m - observed_mean) ** 2 for m in boot_means) / (len(boot_means) - 1)) ** 0.5
    alpha = 1 - confidence
    lo_idx = int(len(boot_means) * (alpha / 2))
    hi_idx = int(len(boot_means) * (1 - alpha / 2)) - 1
    hi_idx = max(hi_idx, lo_idx)

    return {
        "n": n,
        "mean": round(observed_mean, 4),
        "se": round(se, 4),
        "ci_low": round(boot_means[lo_idx], 4),
        "ci_high": round(boot_means[hi_idx], 4),
        "block_size": block_size,
        "n_bootstrap": n_bootstrap,
        "significant_at_95pct": not (boot_means[lo_idx] <= 0.0 <= boot_means[hi_idx]),
    }
