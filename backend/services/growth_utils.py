"""
Pure-math utilities shared by the Growth Intelligence Engine's market
adapters (Epic 003, Sprint #003). Deliberately provider-independent —
takes only plain numeric lists, never a yfinance/screener.in-shaped
object — so both india_growth_adapter.py and us_growth_adapter.py can
reuse identical logic for "compute a CAGR from a multi-year series" and
"bucket a period-over-period series into a categorical trend label",
rather than each adapter re-deriving its own version (SES-001's "one
computation, one owner" applied across adapters, not just within one).

`compute_categorical_trend` deliberately generalizes the trend-bucketing
logic already inline inside screener_data.py's `augment_info_with_screener`
(quarterly-PAT-based `eps_trend`) — that inline logic is now extracted
into `compute_categorical_trend` and screener_data.py calls this shared
function instead of keeping its own copy, so India's EPS trend and the
US adapter's own EPS-trend derivation (built from annual EPS, not
quarterly PAT, since that's what's actually available for US) both run
through one tested implementation.
"""


def compute_cagr_from_series(series: list[float] | None, years: int) -> float | None:
    """
    CAGR (%) over the most recent `years` periods of an oldest-to-newest
    numeric series. Mirrors the CAGR formula already used in
    screener_data.py's own fallback growth calculation
    (`((latest / oldest) ** (1 / n) - 1) * 100`), generalized to take a
    plain list instead of being inline in one specific parsing path.

    Returns None (never a fabricated number) if the series is missing,
    too short, or either endpoint isn't strictly positive — a CAGR off a
    zero/negative base is mathematically undefined (the DISHTV edge case
    the Growth Feasibility Study found in real data), and a negative
    `latest` raised to a fractional power produces a complex number,
    which crashed `round()` with a TypeError the first time this function
    was exercised against an integration-test fixture with a negative
    final year — found and fixed during this sprint's own test-writing,
    not assumed safe.
    """
    if not series or len(series) < years + 1:
        return None
    window = series[-(years + 1):]
    oldest, latest = window[0], window[-1]
    if oldest is None or latest is None or oldest <= 0 or latest <= 0:
        return None
    return round(((latest / oldest) ** (1 / years) - 1) * 100, 2)


def compute_coefficient_of_variation(series: list[float] | None) -> float | None:
    """
    Coefficient of variation (stdev / mean of absolute values) of the
    year-over-year growth rates implied by an oldest-to-newest numeric
    series — the basis for Growth Durability (SSDS-007 Metric #12/#13).
    Lower = more consistent trend; higher = more erratic.

    Returns None if there's not enough history to compute at least 3
    YoY growth rates (need 4+ raw values), or if growth rates can't be
    computed at all (a zero/negative base year breaks a YoY ratio the
    same way it breaks a CAGR — same reasoning as compute_cagr_from_series).
    """
    if not series or len(series) < 4:
        return None
    growth_rates = []
    for i in range(1, len(series)):
        prev, cur = series[i - 1], series[i]
        if prev is None or cur is None or prev <= 0:
            continue
        growth_rates.append((cur - prev) / prev)
    if len(growth_rates) < 3:
        return None
    mean = sum(growth_rates) / len(growth_rates)
    if mean == 0:
        return None
    variance = sum((g - mean) ** 2 for g in growth_rates) / len(growth_rates)
    stdev = variance ** 0.5
    return round(stdev / abs(mean), 3)


def compute_categorical_trend(series: list[float] | None) -> str | None:
    """
    Buckets the last 4 periods of an oldest-to-newest numeric series into
    one of four categorical labels, based on how many of the 3
    period-over-period differences were positive:
      3/3 positive -> "accelerating"
      0/3 positive -> "decelerating"
      2/3 positive -> "mixed_positive"
      0-1/3 positive (and not 0) -> "mixed_negative"

    Extracted unchanged from the logic that used to live inline inside
    screener_data.py's augment_info_with_screener (quarterly-PAT-based);
    behavior for that exact call site is unchanged — same inputs produce
    the same labels as before this extraction.
    """
    if not series or len(series) < 4:
        return None
    recent = series[-4:]
    diffs = [recent[i + 1] - recent[i] for i in range(len(recent) - 1)]
    positive_diffs = sum(1 for d in diffs if d > 0)
    if positive_diffs == 3:
        return "accelerating"
    elif positive_diffs == 0:
        return "decelerating"
    elif positive_diffs >= 2:
        return "mixed_positive"
    else:
        return "mixed_negative"
