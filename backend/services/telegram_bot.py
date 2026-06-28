"""
Telegram Bot — sends daily stock picks after the IN Daily Picks run (2 AM IST).
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as environment variables on Railway.
"""
import os
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_picks_to_telegram(picks: dict) -> bool:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping.")
        return False

    lines = ["🇮🇳 *StockSense360 Daily Picks* — Top BUY Calls\n"]

    labels = {"short": "⚡ Short Term (1–10 days)", "medium": "📈 Medium Term (1–3 months)", "long": "🏦 Long Term (6M–3Y)"}
    for horizon, label in labels.items():
        stocks = picks.get(horizon, [])
        if not stocks:
            continue
        lines.append(f"\n*{label}*")
        for i, s in enumerate(stocks, 1):
            upside = ""
            if s.get("price") and s.get("target"):
                pct = ((s["target"] - s["price"]) / s["price"]) * 100
                upside = f" ↑{pct:.1f}%"
            lines.append(
                f"{i}. *{s['symbol']}* — ₹{s.get('price', '?')} → ₹{s.get('target', '?')}{upside} "
                f"({s.get('confidence', '?')}% confidence)"
            )

    lines.append("\n_AI-generated signals. Not financial advice. Do your own research._")
    message = "\n".join(lines)

    resp = requests.post(
        TELEGRAM_API.format(token=token),
        json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
        timeout=10,
    )
    if resp.ok:
        print("[telegram] Message sent successfully.")
        return True
    else:
        print(f"[telegram] Failed: {resp.status_code} {resp.text}")
        return False
