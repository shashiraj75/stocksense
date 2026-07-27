"""
Single source of truth for a closed paper trade's realized P&L (absolute and
percent) — Trade Postmortem Engine, Phase 1.

Problem this solves: the identical `(exit_price - entry_price) * quantity`
formula was duplicated at two call sites — `postgres_store._trade_row_to_dict`
(backs GET /portfolio and /closed-trades/older) and `paper_trading.paper_sell`
(the POST /sell response) — with no shared function between them. The Trade
Postmortem Engine (Phase 1) needs a third consumer of this exact number, and
per its architectural requirement there must be only one authoritative
calculation path: this module is that path. Both pre-existing call sites now
import from here instead of recomputing the formula inline; their observable
behavior is byte-for-byte unchanged (see the regression tests proving this).

Explicitly NOT unified here: `postgres_store._summarize_closed_bucket`'s
`avg_realized_return_pct` is a distinct *aggregate* metric (the mean of
several trades' returns, rounded once after averaging) — not a per-trade
figure. Routing it through `compute_realized_pnl_pct` (which rounds each
trade's return individually before it would be averaged) risks a subtly
different result for edge cases where rounding does not commute with
averaging, which would violate "preserve all current behaviour." It is left
untouched by this module. Likewise, `postgres_store._fetch_closed_trade_aggregates`
computes the same per-trade formula directly in SQL (a bounded GROUP BY
aggregate, never materializing a trade-row list) and is not refactored to
call into Python; its SQL expression is a direct mirror of the functions
below and must be kept in sync by hand if this formula ever changes.

No short-selling is supported anywhere in this codebase today — `paper_trades`
has no `direction`/`is_short` column, every position is opened via /buy and
closed via /sell as a long. `entry_price` is always the buy price and
`exit_price` the sell price; there is no sign-flip branch to get wrong here.
"""


def compute_realized_pnl_abs(entry_price: float, exit_price: float | None, quantity: int) -> float:
    """Absolute realized P&L in the trade's own currency, rounded to 2dp
    (money precision used everywhere else in this codebase).

    Matches `_trade_row_to_dict`'s pre-extraction behavior exactly: a falsy
    `exit_price` (`None`, or `0.0`) returns `0.0` rather than a garbage
    negative P&L. `0.0` should never actually occur in practice — both
    POST /buy and POST /sell reject `price <= 0` — but the original code
    guarded against it this way, so this preserves that guard verbatim.
    """
    if not exit_price:
        return 0.0
    return round((exit_price - entry_price) * quantity, 2)


def compute_realized_pnl_pct(entry_price: float | None, exit_price: float | None) -> float | None:
    """Realized return in percent, rounded to 2dp.

    Returns `None` when `entry_price` is missing/non-positive or
    `exit_price` is `None` — a percentage cannot be reliably computed
    against a zero/negative/unknown base. Callers choose their own
    fallback for that case (e.g. `paper_sell`'s legacy response substitutes
    `0`; the postmortem service surfaces `None` as a genuine
    "cannot compute" rather than a real zero).
    """
    if not entry_price or entry_price <= 0 or exit_price is None:
        return None
    return round((exit_price - entry_price) / entry_price * 100, 2)
