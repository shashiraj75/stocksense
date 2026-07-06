"""
Paper trade target / stop-loss proximity email notifications, plus Auto
Close trigger-confirmation emails.

Runs periodically (see _paper_trade_notify_loop in api/main.py). Two
independent things happen per cycle:

1. "Near price" proximity emails — for every *open* trade with a
   target_price or stop_loss set AND trade_management_mode != 'auto',
   fetches the live quote and emails the owner once price comes within
   PROXIMITY_PCT of either level (or crosses it). Auto Close trades are
   deliberately excluded from this scan: a user who chose Auto Close asked
   the system to just close the position at the trigger, not to be emailed
   about it approaching — the only email an Auto Close trade should ever
   generate is the one below, confirming what already happened. This scan
   also respects each user's paper_portfolio.email_notifications_enabled
   preference — these are routine "you might want to look at this" alerts,
   which the Notifications toggle is meant to govern.
2. Auto Close trigger-confirmation emails — for every *closed* trade with
   trade_management_mode == 'auto' and an exit_reason of STOP_LOSS or
   TARGET_HIT, emails the owner once that the position was closed
   automatically, with the trigger price, % vs entry, and realized P&L.
   Deduped by reusing the same target_notified_at/stop_notified_at columns
   the proximity path already uses — a timestamp older than the trade's own
   closed_at means "not yet notified about this particular close", so it
   still works correctly even for a trade that received a proximity email
   while still open (e.g. before being switched from Manual to Auto) and
   later closed. UNLIKE (1), this ignores email_notifications_enabled —
   it's a trade-event confirmation (something already happened), not a
   routine alert, so the Notifications toggle does not suppress it.

Each trigger is deduped via a *_notified_at timestamp (+ a cooldown for the
proximity path) so a price hovering near the line doesn't spam the same
email every cycle, and a trade can't receive its own auto-close
confirmation email twice.

Requires RESEND_API_KEY to be set (same Resend account used for invite
emails, just a distinct sender address so users can tell the two apart).
"""
import os
import asyncio
import requests
from datetime import datetime, timezone, timedelta

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
ALERT_FROM = "StockSense360 Alerts <alerts@stocksense360.com>"
PROXIMITY_PCT = 2.0     # email once price is within 2% of target/stop (or has crossed it)
COOLDOWN_HOURS = 6      # don't re-send the same trigger more than once per cooldown window


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def _send_email(to_email: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY or not to_email:
        return False
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": ALERT_FROM, "to": [to_email], "subject": subject, "html": html},
            timeout=10,
        )
        return r.status_code < 300
    except Exception as e:
        print(f"[trade_notifier] email send failed: {e}")
        return False


def _email_html(symbol: str, market: str, price: float, level: float, kind: str) -> str:
    currency = "₹" if market == "IN" else "$"
    crossed = (price >= level) if kind == "target" else (price <= level)
    verb = "reached" if crossed else "approaching"
    color = "#22c55e" if kind == "target" else "#ef4444"
    label = "target price" if kind == "target" else "stop loss"
    emoji = "🎯" if kind == "target" else "⚠️"
    return f"""
    <div style="font-family:-apple-system,sans-serif; background:#0a0a0f; padding:32px;">
      <div style="max-width:480px; margin:0 auto; background:#13131c; border:1px solid #232333; border-radius:16px; padding:28px;">
        <p style="color:#6366f1; font-weight:800; font-size:18px; margin:0 0 16px;">📈 StockSense360</p>
        <h2 style="color:#fff; font-size:18px; margin:0 0 12px;">{emoji} {symbol} is {verb} your {label}</h2>
        <p style="color:#cbd5e1; font-size:14px; line-height:1.6;">
          <strong style="color:{color};">{symbol}</strong> is trading at
          <strong style="color:#fff;">{currency}{price:,.2f}</strong>, {verb} your {label} of
          <strong style="color:{color};">{currency}{level:,.2f}</strong>.
        </p>
        <p style="color:#9ca3af; font-size:13px; margin-top:16px;">
          Review this paper trade and decide whether to close the position on StockSense360.
        </p>
        <p style="color:#4b5563; font-size:11px; margin-top:24px;">
          This is a paper-trading simulation alert — no real funds are involved.
        </p>
      </div>
    </div>
    """


def check_and_notify() -> None:
    """Scan open paper trades and email owners whose price is near target/stop
    (Manual/AI-assisted trades only — see module docstring), then separately
    email Auto Close trades that have just closed on a real trigger."""
    _notify_near_price()
    _notify_auto_close_triggers()


def _notify_near_price() -> None:
    from services.market_data import MarketDataService
    svc = MarketDataService()

    with _conn() as conn:
        rows = conn.execute(
            """SELECT t.id, t.symbol, t.market, t.target_price, t.stop_loss,
                      t.target_notified_at, t.stop_notified_at, p.email
               FROM paper_trades t
               JOIN paper_portfolio p ON p.user_id = t.user_id
               WHERE t.status = 'OPEN'
                 AND (t.target_price IS NOT NULL OR t.stop_loss IS NOT NULL)
                 AND p.email IS NOT NULL
                 AND p.email_notifications_enabled = true
                 AND t.trade_management_mode != 'auto'"""
        ).fetchall()

    if not rows:
        return

    now = datetime.now(timezone.utc)
    cooldown = timedelta(hours=COOLDOWN_HOURS)
    loop = asyncio.new_event_loop()
    try:
        for (tid, symbol, market, target, stop, t_notif, s_notif, email) in rows:
            quote = loop.run_until_complete(svc.get_quote(symbol, market))
            price = quote.get("price") if quote else None
            if price is None or price <= 0:
                continue

            if target and (t_notif is None or now - t_notif > cooldown):
                dist_pct = abs(price - target) / price * 100
                if price >= target or dist_pct <= PROXIMITY_PCT:
                    sent = _send_email(
                        email, f"🎯 {symbol} is near your target price",
                        _email_html(symbol, market, price, target, "target"),
                    )
                    if sent:
                        with _conn() as conn:
                            conn.execute(
                                "UPDATE paper_trades SET target_notified_at = now() WHERE id = %s", (tid,)
                            )

            if stop and (s_notif is None or now - s_notif > cooldown):
                dist_pct = abs(price - stop) / price * 100
                if price <= stop or dist_pct <= PROXIMITY_PCT:
                    sent = _send_email(
                        email, f"⚠️ {symbol} is near your stop loss",
                        _email_html(symbol, market, price, stop, "stop"),
                    )
                    if sent:
                        with _conn() as conn:
                            conn.execute(
                                "UPDATE paper_trades SET stop_notified_at = now() WHERE id = %s", (tid,)
                            )
    finally:
        loop.close()


def _trigger_email_html(
    symbol: str, market: str, entry_price: float, exit_price: float,
    pct: float, kind: str, pnl: float | None,
) -> str:
    currency = "₹" if market == "IN" else "$"
    label = "target achieved" if kind == "target" else "stop loss hit"
    color = "#22c55e" if kind == "target" else "#ef4444"
    emoji = "🎯" if kind == "target" else "⚠️"
    pnl_html = ""
    if pnl is not None:
        pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"
        pnl_sign = "+" if pnl >= 0 else "−"
        pnl_html = f"""
        <p style="color:#cbd5e1; font-size:14px; margin:4px 0 0;">
          Realized P&L: <strong style="color:{pnl_color};">{pnl_sign}{currency}{abs(pnl):,.2f}</strong>
        </p>"""
    return f"""
    <div style="font-family:-apple-system,sans-serif; background:#0a0a0f; padding:32px;">
      <div style="max-width:480px; margin:0 auto; background:#13131c; border:1px solid #232333; border-radius:16px; padding:28px;">
        <p style="color:#6366f1; font-weight:800; font-size:18px; margin:0 0 16px;">📈 StockSense360</p>
        <h2 style="color:#fff; font-size:18px; margin:0 0 12px;">{emoji} {symbol} {label} — paper trade closed</h2>
        <p style="color:#cbd5e1; font-size:14px; line-height:1.6; margin:0;">
          <strong style="color:{color};">{symbol}</strong> {label} at
          <strong style="color:#fff;">{currency}{exit_price:,.2f}</strong>
          (<strong style="color:{color};">{"+" if pct >= 0 else "−"}{abs(pct):.1f}%</strong>).
        </p>
        <p style="color:#cbd5e1; font-size:14px; margin:8px 0 0;">Position closed automatically.</p>
        <p style="color:#9ca3af; font-size:13px; margin:12px 0 0;">Entry: {currency}{entry_price:,.2f}</p>
        <p style="color:#9ca3af; font-size:13px; margin:2px 0 0;">Exit: {currency}{exit_price:,.2f}</p>
        {pnl_html}
        <p style="color:#4b5563; font-size:11px; margin-top:24px;">
          This was a paper-trading simulation. No real funds were involved.
        </p>
      </div>
    </div>
    """


def _notify_auto_close_triggers() -> None:
    """Email owners of Auto Close trades that have just closed on a genuine
    stop-loss/target trigger (not a manual close) — the only notification an
    Auto Close trade should ever generate, per the module docstring.

    Deliberately NOT filtered by email_notifications_enabled: this is a
    trade-event confirmation (something already happened to the user's
    money, even if only virtual), not a routine "you might want to look at
    this" alert — the Notifications toggle governs the latter category
    only (see _notify_near_price above), never this one."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT t.id, t.symbol, t.market, t.entry_price, t.exit_price, t.quantity,
                      t.exit_reason, t.closed_at, t.target_notified_at, t.stop_notified_at, p.email
               FROM paper_trades t
               JOIN paper_portfolio p ON p.user_id = t.user_id
               WHERE t.status = 'CLOSED'
                 AND t.trade_management_mode = 'auto'
                 AND t.exit_reason IN ('STOP_LOSS', 'TARGET_HIT')
                 AND t.exit_price IS NOT NULL
                 AND p.email IS NOT NULL"""
        ).fetchall()

    for (tid, symbol, market, entry_price, exit_price, qty, exit_reason, closed_at, t_notif, s_notif, email) in rows:
        kind = "target" if exit_reason == "TARGET_HIT" else "stop"
        # Reuses target_notified_at/stop_notified_at (the same columns the
        # proximity path uses) rather than a new column — a notified_at that
        # is null, or that predates this trade's own closed_at, means "not
        # yet notified about *this* close", so this can't double-send even
        # if the trade received a proximity email earlier while still open.
        already_notified = (
            (t_notif is not None and closed_at is not None and t_notif >= closed_at)
            if kind == "target" else
            (s_notif is not None and closed_at is not None and s_notif >= closed_at)
        )
        if already_notified:
            continue
        if not entry_price or entry_price <= 0:
            continue

        pct = (exit_price - entry_price) / entry_price * 100
        pnl = (exit_price - entry_price) * qty if qty else None
        subject_emoji = "🎯" if kind == "target" else "⚠️"
        subject_label = "target achieved" if kind == "target" else "stop loss hit"
        sent = _send_email(
            email, f"{subject_emoji} {symbol} {subject_label} — paper trade closed",
            _trigger_email_html(symbol, market, entry_price, exit_price, pct, kind, pnl),
        )
        if sent:
            col = "target_notified_at" if kind == "target" else "stop_notified_at"
            with _conn() as conn:
                conn.execute(f"UPDATE paper_trades SET {col} = now() WHERE id = %s", (tid,))
