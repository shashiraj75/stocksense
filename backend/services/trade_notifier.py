"""
Paper trade target / stop-loss proximity email notifications.

Runs periodically (see _paper_trade_notify_loop in api/main.py). For every
open paper trade with a target_price or stop_loss set, fetches the live
quote and emails the trade's owner once price comes within PROXIMITY_PCT
of either level (or crosses it). Each trigger is deduped via a
*_notified_at timestamp + cooldown so a price hovering near the line
doesn't spam the same email every cycle.

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
    """Scan open paper trades and email owners whose price is near target/stop."""
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
                 AND p.email IS NOT NULL"""
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
