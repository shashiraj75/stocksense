"""
Server-side price alert enforcement — email backstop for the Alerts page.

Runs periodically (see _price_alerts_check_loop in api/main.py). Without
this, alerts only fire via the frontend's client-side 5s poll, which stops
the moment the tab is closed, the device sleeps, or the browser discards a
backgrounded tab. This scans every non-triggered alert with an email on
file, fetches the live quote, and — once the threshold is actually crossed
— emails the owner and marks it triggered server-side. No time-based
cooldown is needed: once `triggered` flips to TRUE this query no longer
selects the row, so it can't double-send. Resetting (clearing `triggered`)
re-arms it for exactly one more notification, same as the manual reset
button on the Alerts page.

Requires RESEND_API_KEY (same Resend account already used for invite and
paper-trade emails). Set PRICE_ALERTS_ENFORCEMENT=0 to disable this
background check without touching any code — the frontend's client-side
polling keeps working either way, this is purely the email backstop.
"""
import os
import asyncio
import requests
from datetime import datetime, timezone

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
ALERT_FROM = "StockSense360 Alerts <alerts@stocksense360.com>"


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
        print(f"[price_alert_notifier] email send failed: {e}")
        return False


def _email_html(symbol: str, market: str, price: float, target: float, direction: str) -> str:
    currency = "₹" if market == "IN" else "$"
    verb = "risen above" if direction == "above" else "fallen below"
    color = "#22c55e" if direction == "above" else "#ef4444"
    return f"""
    <div style="font-family:-apple-system,sans-serif; background:#0a0a0f; padding:32px;">
      <div style="max-width:480px; margin:0 auto; background:#13131c; border:1px solid #232333; border-radius:16px; padding:28px;">
        <p style="color:#6366f1; font-weight:800; font-size:18px; margin:0 0 16px;">📈 StockSense360</p>
        <h2 style="color:#fff; font-size:18px; margin:0 0 12px;">🔔 {symbol} hit your price alert</h2>
        <p style="color:#cbd5e1; font-size:14px; line-height:1.6;">
          <strong style="color:{color};">{symbol}</strong> has {verb}
          <strong style="color:#fff;">{currency}{target:,.2f}</strong> — now trading at
          <strong style="color:{color};">{currency}{price:,.2f}</strong>.
        </p>
        <p style="color:#9ca3af; font-size:13px; margin-top:16px;">
          This alert won't fire again until you reset it on the Alerts page.
        </p>
      </div>
    </div>
    """


def check_and_notify() -> None:
    """Scan non-triggered alerts and email owners whose threshold has been crossed."""
    from services.market_data import MarketDataService
    svc = MarketDataService()

    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, symbol, market, target_price, direction, email
               FROM price_alerts
               WHERE triggered = FALSE AND email IS NOT NULL"""
        ).fetchall()

    if not rows:
        return

    loop = asyncio.new_event_loop()
    try:
        for (aid, symbol, market, target, direction, email) in rows:
            quote = loop.run_until_complete(svc.get_quote(symbol, market))
            price = quote.get("price") if quote else None
            if price is None or price <= 0:
                continue

            target = float(target)
            hit = price >= target if direction == "above" else price <= target
            if not hit:
                continue

            sent = _send_email(
                email, f"🔔 {symbol} hit your price alert",
                _email_html(symbol, market, price, target, direction),
            )
            if sent:
                with _conn() as conn:
                    conn.execute(
                        "UPDATE price_alerts SET triggered = TRUE, triggered_at = now() WHERE id = %s",
                        (aid,)
                    )
    finally:
        loop.close()
