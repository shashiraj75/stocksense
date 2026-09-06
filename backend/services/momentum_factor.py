"""
2026-09-06 corrective review of PR #83 — shared momentum calculation.

Single source of truth for 12-1 month momentum (Jegadeesh & Titman
1993), used identically by the live path (prediction_engine.py), the
backtest mirror (validation_engine.py), and every research script —
eliminating the research/live parity gap the corrective review found
(compute_momentum_score previously used `iloc[-N]`, which addresses
N-1 steps before the last row, not N; research scripts used explicit
`today_idx - N` arithmetic, which IS correct).

Momentum, standard definition: cumulative return from ~12 months ago to
~1 month ago; the most recent month is deliberately excluded, since it
is well-documented to show short-term REVERSAL, opposite-signed to the
12-1 effect.
"""
from __future__ import annotations

import math

LOOKBACK_DAYS = 252   # ~12 months of trading days
SKIP_DAYS = 21        # ~1 month excluded (short-term reversal)


def momentum_indices(n_rows: int, lookback: int = LOOKBACK_DAYS, skip: int = SKIP_DAYS) -> tuple[int, int] | None:
    """Returns (old_idx, recent_idx) — the two positional indices into a
    0-indexed, chronologically-ordered price series of length `n_rows`
    whose LAST element (index n_rows-1) is the observation date
    ("today"). `old_idx` = today_idx - lookback (~12 months back),
    `recent_idx` = today_idx - skip (~1 month back). Returns None when
    there isn't enough history (old_idx would be negative).

    This is the ONLY place this arithmetic should be written — every
    caller (live, backtest, research) must go through this function so
    "N trading days before today" always means the same thing
    everywhere. Deliberately NOT `iloc[-N]`: for a series of length L
    ending at index L-1 ("today"), `iloc[-N]` addresses L-N, which is
    N-1 steps before today, not N (iloc[-1] is 0 steps before today).
    """
    if n_rows < lookback + 1:
        return None
    today_idx = n_rows - 1
    old_idx = today_idx - lookback
    recent_idx = today_idx - skip
    if old_idx < 0 or recent_idx < 0:
        return None
    return old_idx, recent_idx


def momentum_pct(closes, lookback: int = LOOKBACK_DAYS, skip: int = SKIP_DAYS) -> float | None:
    """Computes the raw 12-1 momentum percentage from any sequence
    supporting integer positional indexing and len() — a pandas Series,
    a plain list, or a numpy array — so research code operating on plain
    arrays and live/backtest code operating on DataFrame columns share
    the exact same function, not just the exact same index arithmetic.

    Returns None (never NaN, never a fabricated 0) when there isn't
    enough history, or when either endpoint price is missing/non-finite/
    non-positive.
    """
    idx = momentum_indices(len(closes), lookback, skip)
    if idx is None:
        return None
    old_idx, recent_idx = idx
    p_old = closes[old_idx]
    p_recent = closes[recent_idx]
    if p_old is None or p_recent is None:
        return None
    try:
        p_old = float(p_old)
        p_recent = float(p_recent)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p_old) or not math.isfinite(p_recent) or p_old <= 0:
        return None
    return (p_recent / p_old - 1.0) * 100.0


# Bucket breakpoints are literature-typical starting points, not yet
# independently calibrated against this dataset's own momentum
# distribution — unchanged from the original implementation, carried
# here so both the score function and any research code bucketing raw
# momentum use the identical breakpoints.
STRONG_POSITIVE_PCT = 30.0
POSITIVE_PCT = 15.0
STRONG_NEGATIVE_PCT = -30.0
NEGATIVE_PCT = -15.0

STRONG_POSITIVE_DELTA = 15.0
POSITIVE_DELTA = 8.0
STRONG_NEGATIVE_DELTA = -15.0
NEGATIVE_DELTA = -8.0

NEUTRAL_SCORE = 50.0


def bucket_momentum_score(pct: float | None) -> float:
    """Maps a raw momentum percentage to this codebase's 0-100 bucketed
    score convention (50=neutral), or NEUTRAL_SCORE when `pct` is None
    (insufficient history / unmeasurable) — the score function's own
    "no contribution" convention, distinct from a genuine 0% momentum
    reading."""
    if pct is None:
        return NEUTRAL_SCORE
    score = NEUTRAL_SCORE
    if pct > STRONG_POSITIVE_PCT:
        score += STRONG_POSITIVE_DELTA
    elif pct > POSITIVE_PCT:
        score += POSITIVE_DELTA
    elif pct < STRONG_NEGATIVE_PCT:
        score += STRONG_NEGATIVE_DELTA
    elif pct < NEGATIVE_PCT:
        score += NEGATIVE_DELTA
    return max(0.0, min(100.0, score))


def compute_momentum_score(closes) -> float:
    """Convenience wrapper: raw momentum -> bucketed 0-100 score in one
    call, for callers (like get_signal_summary) that only need the final
    score, not the intermediate percentage."""
    return bucket_momentum_score(momentum_pct(closes))
