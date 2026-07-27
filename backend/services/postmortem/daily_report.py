"""
Daily Trade Postmortem Report — Trade Postmortem Engine, Stage 3 (Sprint 1
aggregation-safety update).

Aggregates one day's worth of already-computed per-trade results
(`DeterministicPostmortem` + `EvidenceAttributionResult` pairs) into a
single day-level report with a summary. This module performs no evidence
computation of its own — every per-trade fact was already decided by
`deterministic.compute_postmortem` / `evidence_attribution.
build_evidence_attribution`; this module only aggregates and counts.

Sprint 1 aggregation-safety rule (Stage 13): the daily summary must never
present an unsupported or conflicting causal claim as though it were a
settled fact. There is deliberately no "most common root cause" figure —
`recurring_supported_contributors` counts only contributor categories
whose per-trade `support_level` was STRONGLY_SUPPORTED or SUPPORTED;
`recurring_conflicting_contributors` and `recurring_not_assessable_count`
are tracked as separate, clearly-labeled counts, never folded into the
supported figure or silently dropped.

P&L aggregation rule (unchanged from the Stage 0 prototype, confirmed with
the product owner): sum only trades with a valid `realized_pnl_abs`, and
report how many trades were excluded from that sum.

Pure function, no I/O.
"""

from dataclasses import dataclass, field

from services.postmortem.deterministic import DeterministicPostmortem, Outcome
from services.postmortem.evidence_attribution import ContributorAssessment, EvidenceAttributionResult, SupportLevel

DAILY_REPORT_CALCULATION_VERSION = "2.0.0"

_SUPPORTED_LEVELS = (SupportLevel.STRONGLY_SUPPORTED.value, SupportLevel.SUPPORTED.value)


@dataclass(frozen=True)
class DailyTradePostmortem:
    trade_id: int
    symbol: str
    market: str
    postmortem: DeterministicPostmortem
    attribution: EvidenceAttributionResult


@dataclass(frozen=True)
class DailyPostmortemSummary:
    date: str
    market: str
    trade_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    indeterminate_count: int
    total_realized_pnl_abs: float | None
    pnl_excluded_trade_count: int
    # Only STRONGLY_SUPPORTED/SUPPORTED contributor occurrences, across all
    # trades in the day — labeled "most frequently supported," never
    # "root cause," per Stage 13.
    recurring_supported_contributors: dict[str, int]
    recurring_conflicting_contributors: dict[str, int]
    recurring_not_assessable_count: dict[str, int]
    trades_with_no_supported_contributor: int


@dataclass(frozen=True)
class DailyPostmortemReport:
    summary: DailyPostmortemSummary
    trades: list[DailyTradePostmortem]
    calculation_version: str = DAILY_REPORT_CALCULATION_VERSION
    warnings: list[str] = field(default_factory=list)


def _tally_contributors(
    contributors: list[ContributorAssessment],
) -> tuple[dict[str, int], dict[str, int], dict[str, int], bool]:
    supported: dict[str, int] = {}
    conflicting: dict[str, int] = {}
    not_assessable: dict[str, int] = {}
    any_supported = False
    for c in contributors:
        if c.support_level in _SUPPORTED_LEVELS:
            supported[c.category] = supported.get(c.category, 0) + 1
            any_supported = True
        elif c.support_level == SupportLevel.CONFLICTED.value:
            conflicting[c.category] = conflicting.get(c.category, 0) + 1
        else:
            not_assessable[c.category] = not_assessable.get(c.category, 0) + 1
    return supported, conflicting, not_assessable, any_supported


def build_daily_report(
    date: str, market: str, records: list[DailyTradePostmortem]
) -> DailyPostmortemReport:
    """Pure function: a market-local calendar `date` string (as already
    resolved by the caller), a `market` filter label ("IN"/"US"/"ALL"),
    and the list of already-computed per-trade results for that day, in.
    One aggregated `DailyPostmortemReport` out."""
    win_count = loss_count = breakeven_count = indeterminate_count = 0
    pnl_sum = 0.0
    pnl_included_count = 0
    recurring_supported: dict[str, int] = {}
    recurring_conflicting: dict[str, int] = {}
    recurring_not_assessable: dict[str, int] = {}
    trades_with_no_supported_contributor = 0

    for entry in records:
        outcome = entry.postmortem.outcome
        if outcome == Outcome.WIN:
            win_count += 1
        elif outcome == Outcome.LOSS:
            loss_count += 1
        elif outcome == Outcome.BREAKEVEN:
            breakeven_count += 1
        else:
            indeterminate_count += 1

        if entry.postmortem.realized_pnl_abs is not None:
            pnl_sum += entry.postmortem.realized_pnl_abs
            pnl_included_count += 1

        supported, conflicting, not_assessable, any_supported = _tally_contributors(
            entry.attribution.contributor_assessments
        )
        for k, v in supported.items():
            recurring_supported[k] = recurring_supported.get(k, 0) + v
        for k, v in conflicting.items():
            recurring_conflicting[k] = recurring_conflicting.get(k, 0) + v
        for k, v in not_assessable.items():
            recurring_not_assessable[k] = recurring_not_assessable.get(k, 0) + v
        if not any_supported:
            trades_with_no_supported_contributor += 1

    trade_count = len(records)
    total_realized_pnl_abs = round(pnl_sum, 2) if pnl_included_count > 0 else None
    pnl_excluded_trade_count = trade_count - pnl_included_count

    summary = DailyPostmortemSummary(
        date=date,
        market=market,
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        breakeven_count=breakeven_count,
        indeterminate_count=indeterminate_count,
        total_realized_pnl_abs=total_realized_pnl_abs,
        pnl_excluded_trade_count=pnl_excluded_trade_count,
        recurring_supported_contributors=recurring_supported,
        recurring_conflicting_contributors=recurring_conflicting,
        recurring_not_assessable_count=recurring_not_assessable,
        trades_with_no_supported_contributor=trades_with_no_supported_contributor,
    )

    return DailyPostmortemReport(summary=summary, trades=records)
