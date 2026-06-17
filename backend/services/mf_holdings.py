"""
Mutual Fund / Institutional holding trend signal.

Uses quarterly DII (Domestic Institutional Investor) and FII shareholding
history from screener.in to detect accumulation or distribution patterns.

In India, DIIs are dominated by domestic mutual funds (HDFC MF, SBI MF,
Nippon, Mirae, etc.) plus insurance (LIC) and pension funds. Consistent DII
increase over 3-4 quarters = MFs are buying = strong conviction signal.

Signal logic:
  - DII trend (primary):  rising MF buying → bullish; falling → bearish
  - FII trend (secondary): foreign capital flow direction
  - Divergence signal:    DII rising + FII falling = domestic conviction
                          (often precedes a re-rating)

Score: 0-100, 50 = neutral
"""

import logging

log = logging.getLogger(__name__)


def compute_mf_signal(screener_d: dict) -> dict:
    """
    Compute MF/institutional holding trend signal from quarterly shareholding data.

    Args:
        screener_d: the _screener_data dict from augment_info_with_screener

    Returns:
        {
          "score": 0-100,
          "reasons": [...],
          "dii_trend": float,   # percentage point change over available quarters
          "fii_trend": float,
          "dii_latest": float,
          "fii_latest": float,
          "available": bool,
        }
    """
    base = {"score": 50, "reasons": [], "available": False}
    if not screener_d:
        return base

    dii_q = screener_d.get("dii_quarterly_pct") or []
    fii_q = screener_d.get("fii_quarterly_pct") or []

    if len(dii_q) < 2:
        return base

    score = 50
    reasons: list[str] = []

    # ── DII trend — primary signal ────────────────────────────────────────────
    dii_change = dii_q[-1] - dii_q[0]          # total change over all quarters
    dii_recent = dii_q[-1] - dii_q[-2]         # last quarter delta
    dii_latest = dii_q[-1]
    n_quarters = len(dii_q) - 1

    if dii_change > 3:
        score += 18
        reasons.append(
            f"DII holding rose {dii_change:.1f}pp over {n_quarters}Q "
            f"(now {dii_latest:.1f}%) — sustained domestic MF accumulation"
        )
    elif dii_change > 1:
        score += 10
        reasons.append(
            f"DII holding up {dii_change:.1f}pp over {n_quarters}Q "
            f"(now {dii_latest:.1f}%) — mild MF accumulation"
        )
    elif dii_change < -3:
        score -= 15
        reasons.append(
            f"DII holding fell {abs(dii_change):.1f}pp over {n_quarters}Q "
            f"(now {dii_latest:.1f}%) — domestic funds reducing exposure"
        )
    elif dii_change < -1:
        score -= 8
        reasons.append(
            f"DII holding down {abs(dii_change):.1f}pp — mild MF distribution"
        )

    # Recent quarter momentum (last quarter delta amplifies or dampens)
    if dii_recent > 1.5:
        score += 6
        reasons.append(f"DII bought +{dii_recent:.1f}pp last quarter — accelerating accumulation")
    elif dii_recent < -1.5:
        score -= 5
        reasons.append(f"DII sold {abs(dii_recent):.1f}pp last quarter — accelerating exit")

    # ── FII trend — secondary signal ─────────────────────────────────────────
    fii_trend = 0.0
    fii_latest = 0.0
    if len(fii_q) >= 2:
        fii_trend = fii_q[-1] - fii_q[0]
        fii_latest = fii_q[-1]

        if fii_trend > 3:
            score += 10
            reasons.append(
                f"FII holding rose {fii_trend:.1f}pp to {fii_latest:.1f}% — foreign buying"
            )
        elif fii_trend < -3:
            score -= 8
            reasons.append(
                f"FII holding fell {abs(fii_trend):.1f}pp to {fii_latest:.1f}% — foreign exit"
            )

    # ── Divergence signal — DII rising + FII falling ─────────────────────────
    # This pattern often precedes a domestic re-rating: Indian MFs buying what
    # foreigners are selling at discounted prices.
    if dii_change > 2 and fii_trend < -2:
        score += 8
        reasons.append(
            "DII buying while FII selling — domestic conviction at discounted price; "
            "historically precedes re-rating"
        )

    # ── High absolute DII holding is itself a quality signal ─────────────────
    if dii_latest >= 30:
        score += 5
        reasons.append(f"High DII base ({dii_latest:.1f}%) — well owned by domestic funds")

    return {
        "score": max(0, min(100, round(score))),
        "reasons": reasons[:4],
        "dii_trend": round(dii_change, 2),
        "fii_trend": round(fii_trend, 2),
        "dii_latest": round(dii_latest, 2),
        "fii_latest": round(fii_latest, 2),
        "available": True,
    }
