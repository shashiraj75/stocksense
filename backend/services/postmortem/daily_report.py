"""
Daily Trade Postmortem Report — Trade Postmortem Engine, Stage 3.

Aggregates one day's worth of already-computed per-trade postmortems
(`DeterministicPostmortem` + `TradePostmortemNarrative` pairs) into a single
day-level report with a summary. This module performs no evidence
computation of its own — every per-trade fact was already decided by
`deterministic.compute_postmortem` / `causal_analysis.build_causal_analysis`;
this module only aggregates and counts.

P&L aggregation rule (confirmed with the product owner): sum only trades
with a valid `realized_pnl_abs`, and report how many trades were excluded
from that sum (never silently drop the gap, never show nothing when some
trades are unusable). A trade contributes `None` P&L when its own
`DeterministicPostmortem.outcome` is `INDETERMINATE` — that trade counts in
`trade_count` and in the outcome breakdown, but not in
`total_realized_pnl_abs`.

Pure function, no I/O: the caller (the API router) is responsible for
querying `paper_trades`/`paper_trade_entry_snapshot` and constructing the
`ClosedTradeRecord`/`EntrySnapshot` pairs, then calling
`compute_postmortem`/`build_causal_analysis` per trade before handing the
results here.
"""

from dataclasses import dataclass, field

from services.postmortem.causal_analysis import RootCauseCategory, TradePostmortemNarrative
from services.postmortem.deterministic import DeterministicPostmortem, Outcome

DAILY_REPORT_CALCULATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class DailyTradePostmortem:
    trade_id: int
    symbol: str
    market: str
    postmortem: DeterministicPostmortem
    narrative: TradePostmortemNarrative


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
    root_cause_breakdown: dict[str, int]


@dataclass(frozen=True)
class DailyPostmortemReport:
    summary: DailyPostmortemSummary
    trades: list[DailyTradePostmortem]
    calculation_version: str = DAILY_REPORT_CALCULATION_VERSION
    warnings: list[str] = field(default_factory=list)


def build_daily_report(
    date: str, market: str, records: list[DailyTradePostmortem]
) -> DailyPostmortemReport:
    """Pure function: a market-local calendar `date` string (as already
    resolved by the caller — this module performs no timezone conversion
    itself), a `market` filter label ("IN"/"US"/"ALL"), and the list of
    already-computed per-trade results for that day, in. One aggregated
    `DailyPostmortemReport` out."""
    win_count = loss_count = breakeven_count = indeterminate_count = 0
    pnl_sum = 0.0
    pnl_included_count = 0
    root_cause_breakdown: dict[str, int] = {}

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

        root_cause_key = entry.narrative.root_cause.value
        root_cause_breakdown[root_cause_key] = root_cause_breakdown.get(root_cause_key, 0) + 1

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
        root_cause_breakdown=root_cause_breakdown,
    )

    return DailyPostmortemReport(summary=summary, trades=records)
