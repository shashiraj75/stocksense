"""
Server-side stop-loss / target-price Auto Close monitor.

Root-cause fix: before this module existed, Auto Close trade triggering
was entirely client-side (frontend/src/app/paper-trading/page.tsx's
checkExitTrigger + a useEffect firing POST /paper-trading/sell/{id}) — a
trade in trade_management_mode='auto' would only ever close while a
browser tab had that specific trade row mounted and actively polling
live quotes. With no tab open, a trade could sit indefinitely past its
stop/target level, or close late and at a worse price than intended
whenever a tab eventually reconnected. This is a plausible contributor
to worse-than-intended stop-loss fills observed in production paper
trading data (2026-09 investigation).

Runs periodically (see _paper_trade_exit_monitor_loop in api/main.py,
every 5 minutes — shorter than the existing 15-minute
_paper_trade_notify_loop, since actually closing a triggered position is
more time-sensitive than a proximity email). For every OPEN trade with
trade_management_mode == 'auto' in a currently-open market, fetches a
live quote and closes the position through the SAME authoritative
services.postmortem.close_service.close_paper_trade path the manual
/paper-trading/sell/{trade_id} endpoint uses — same row-level locking
(SELECT ... FOR UPDATE inside close_paper_trade), same already-closed
guard (TradeAlreadyClosedError on any race), same exit-snapshot/outbox
recording, same cash-crediting pattern. This module adds no new close
logic of its own; it only supplies the missing server-side trigger.

Position direction: every currently-supported paper trade is a long
position (BUY) — frontend/src/app/paper-trading/page.tsx's own
checkExitTrigger never passes isSellPosition=true anywhere in this
codebase. check_exit_trigger below mirrors that same long-only trigger
direction (stop triggers at/below stop_loss, target triggers at/above
target_price, stop checked first) rather than inventing short-position
handling this codebase doesn't otherwise support.

trade_notifier.py's _notify_auto_close_triggers() already emails the
owner once a trade reaches status='CLOSED' with exit_reason IN
('STOP_LOSS','TARGET_HIT') — it required no changes; it was already
correct, it simply never had anything to find before this module
started actually closing trades server-side.
"""
import asyncio
import logging
import os

import psycopg

from services.market_hours import is_market_open
from services.postmortem.close_service import (
    CloseValidationError,
    TradeAlreadyClosedError,
    TradeNotFoundError,
    TradeNotOwnedError,
    UnsupportedMarketError,
    close_paper_trade,
)
from services.postmortem.exit_snapshot import CloseExitMechanism

log = logging.getLogger(__name__)

_CASH_COL = {"IN": "cash", "US": "cash_usd"}
_SUPPORTED_MARKETS = tuple(_CASH_COL)


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def check_exit_trigger(stop_loss, target_price, live_price):
    """Pure, long-only trigger check — mirrors frontend/src/app/paper-trading/
    page.tsx's checkExitTrigger exactly, including checking stop-loss
    before target (if a single quote instant satisfies both — a large gap
    move — the stop takes priority). Returns "STOP_LOSS", "TARGET_HIT", or
    None. Never raises for bad input; treats it as "no trigger"."""
    if (
        live_price is None
        or not isinstance(live_price, (int, float))
        or isinstance(live_price, bool)
        or live_price != live_price  # NaN
        or live_price <= 0
    ):
        return None
    if stop_loss is not None and stop_loss > 0 and live_price <= stop_loss:
        return "STOP_LOSS"
    if target_price is not None and target_price > 0 and live_price >= target_price:
        return "TARGET_HIT"
    return None


def run_exit_monitor_cycle() -> dict:
    """One monitor pass: check every eligible OPEN Auto Close trade, close
    any that have hit their stop/target. Returns a summary dict
    ({"checked", "closed", "errors"}). Never raises for an individual
    trade's own failure — logged and the cycle continues; a genuine
    database-connectivity failure at the top level still propagates to
    the caller, matching check_and_notify's own non-fatal-per-cycle
    contract at its call site in api/main.py."""
    from services.market_data import MarketDataService

    svc = MarketDataService()
    summary = {"checked": 0, "closed": 0, "errors": 0}

    open_markets = [m for m in _SUPPORTED_MARKETS if is_market_open(m)]
    if not open_markets:
        return summary

    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, user_id, symbol, market, stop_loss, target_price
               FROM paper_trades
               WHERE status = 'OPEN'
                 AND trade_management_mode = 'auto'
                 AND market = ANY(%s)
                 AND (stop_loss IS NOT NULL OR target_price IS NOT NULL)""",
            (open_markets,),
        ).fetchall()

    if not rows:
        return summary

    loop = asyncio.new_event_loop()
    try:
        for (trade_id, user_id, symbol, market, stop_loss, target_price) in rows:
            summary["checked"] += 1
            try:
                quote = loop.run_until_complete(svc.get_quote(symbol, market))
                price = quote.get("price") if quote else None
                trigger = check_exit_trigger(stop_loss, target_price, price)
                if trigger is None:
                    continue

                with _conn() as conn:
                    with conn.transaction():
                        result = close_paper_trade(
                            conn,
                            user_id=user_id,
                            trade_id=trade_id,
                            exit_price=price,
                            exit_mechanism=CloseExitMechanism(trigger),
                            exit_mechanism_raw=trigger,
                            source_request_id=None,
                        )
                        cash_col = _CASH_COL[result.market]
                        conn.execute(
                            f"UPDATE paper_portfolio SET {cash_col} = {cash_col} + %s, "
                            f"updated_at = now() WHERE user_id = %s",
                            (result.proceeds, user_id),
                        )
                summary["closed"] += 1
                log.info(
                    "[paper_trade_exit_monitor] closed trade_id=%s symbol=%s trigger=%s price=%s",
                    trade_id, symbol, trigger, price,
                )
            except TradeAlreadyClosedError:
                # Benign race: a manual/client-side close beat this check —
                # nothing to do, not an error.
                continue
            except (TradeNotFoundError, TradeNotOwnedError, UnsupportedMarketError, CloseValidationError) as e:
                summary["errors"] += 1
                log.warning("[paper_trade_exit_monitor] trade_id=%s close rejected: %s", trade_id, e)
            except Exception as e:
                summary["errors"] += 1
                log.warning("[paper_trade_exit_monitor] trade_id=%s error: %s", trade_id, e)
    finally:
        loop.close()

    return summary
