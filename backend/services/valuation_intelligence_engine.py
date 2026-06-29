"""
StockSense360 Valuation Intelligence Engine v1
(SSDS-008, Epic 004 Sprint #003).

Answers exactly one question: "is this stock currently trading below,
near, or above its fair value?" — deliberately distinct from Business
Quality, Financial Strength, and Growth Intelligence, all of which judge
the business itself, never the price paid for it (SSDS-008's Evidence
Checkpoint). A higher score here means "more undervalued," not "better
business" — STRONG_BUY/AVOID describe valuation richness, not quality.

v1 scope is exactly the Recommended V1 Metric Set confirmed by Sprint
#002's India Data Feasibility Study: Earnings Multiple (Trailing/Forward
P/E), EV/Sales, Dividend Yield + Sustainability — full-market; EV/EBITDA,
Free Cash Flow Yield, PEG Ratio — gated to the non-Bank/NBFC population
per that sprint's precisely-attributed evidence. Price/Book is included
but sector-gated to FINANCIAL/REAL_ESTATE only, per SSDS-008's Design
Philosophy ("no single-ratio reduction") and the Research Report's own
asset-based-valuation rationale. Sector-relative percentile is explicitly
DEFERRED — Sprint #002 confirmed raw ratio availability, but never built
or confirmed a sector-benchmark/peer-aggregation data source, which is a
genuinely different kind of feasibility question this sprint's evidence
does not answer; implementing it now would be exactly the "speculative
metric" this sprint's rules forbid. Price/Tangible Book, Price/NAV, full
10-year historical bands, and Absolute/Intrinsic valuation (DCF, Graham,
EPV) remain deferred per SSDS-008's own Methodology Checkpoint conclusion
and Sprint #002's named gaps — none implemented here.

Provider-independent per SSDS-006/SSDS-007's precedent: this module has
no knowledge of yfinance or screener.in. It reads only a pre-resolved
`fields` dict built by india_valuation_adapter.py or
us_valuation_adapter.py.
"""

import logging

from services.engine_contract import EngineResponse, Grade
from services.thresholds import VALUATION_INTELLIGENCE as VI

log = logging.getLogger(__name__)

# Core fields confirmed available across virtually the entire universe in
# both markets (Sprint #002: trailing P/E ~99%, EV/Sales-equivalent ~99%,
# dividend yield ~99%) — used for the REJECTED gate. EV/EBITDA, FCF Yield,
# and PEG are population-gated (Banks/NBFC) or provider-gated (PEG), not
# part of the reject gate, exactly mirroring Growth Intelligence's own
# CORE_FIELDS/EXTENDED_FIELDS split.
CORE_FIELDS = ["pe_ratio", "ev_sales", "dividend_yield_pct", "market_cap"]
EXTENDED_FIELDS = ["ev_ebitda", "fcf_yield_pct", "peg_ratio", "price_book", "forward_pe", "payout_ratio"]
ALL_FIELDS = CORE_FIELDS + EXTENDED_FIELDS

PRICE_BOOK_APPLICABLE_SECTORS = {"FINANCIAL", "REAL_ESTATE"}


def _val(fields: dict, name: str):
    """Reads one field's value, or None if unavailable/absent — never
    fabricates, mirroring growth_intelligence_engine.py's identical helper.

    Graceful-degradation finding (Sprint #003 regression testing): a
    malformed, non-numeric provider value (a scraper parse-edge-case
    artifact) used to pass through unfiltered into every scoring
    function's `> 0` comparison, raising TypeError instead of degrading
    gracefully. Every numeric field this engine reads is filtered to
    int/float here, once, at the single shared boundary — `eps_trend`-style
    categorical fields don't exist in this engine's catalogue, so this
    filter is safe to apply universally, unlike Growth Intelligence's
    engine (which has to special-case its one categorical field)."""
    rec = fields.get(name)
    if rec is None:
        return None
    val = rec.get("value") if isinstance(rec, dict) else rec
    return val if isinstance(val, (int, float)) else None


def _earnings_multiple(fields: dict) -> dict:
    """Earnings Multiple (±15). Trailing P/E is the primary signal; if
    Forward P/E is also available, the two are averaged before banding —
    a forward multiple meaningfully below the trailing one (earnings
    expected to grow) softens an expensive-looking trailing P/E, and vice
    versa, without needing a separate growth input."""
    pe = _val(fields, "pe_ratio")
    fpe = _val(fields, "forward_pe")
    score = 0.0
    reasons = []
    blended = None
    if pe is not None and pe > 0:
        blended = (pe + fpe) / 2 if (fpe is not None and fpe > 0) else pe
        if blended <= VI.PE_CHEAP_MAX:
            score = VI.EARNINGS_MULTIPLE_STRONG_SCORE
            reasons.append(f"P/E {blended:.1f} — trading at a low earnings multiple")
        elif blended >= VI.PE_EXPENSIVE_MIN:
            score = VI.EARNINGS_MULTIPLE_WEAK_SCORE
            reasons.append(f"P/E {blended:.1f} — trading at a rich earnings multiple")
    return {"score": score, "pe_ratio": pe, "forward_pe": fpe, "blended_pe": blended, "reasons": reasons}


def _ev_sales(fields: dict) -> dict:
    """EV/Sales (±12). Capital-structure-neutral; useful for companies
    without positive earnings where P/E is undefined."""
    ev_sales = _val(fields, "ev_sales")
    score = 0.0
    reasons = []
    if ev_sales is not None and ev_sales > 0:
        if ev_sales <= VI.EV_SALES_CHEAP_MAX:
            score = VI.EV_SALES_STRONG_SCORE
            reasons.append(f"EV/Sales {ev_sales:.2f} — inexpensive relative to revenue")
        elif ev_sales >= VI.EV_SALES_EXPENSIVE_MIN:
            score = VI.EV_SALES_WEAK_SCORE
            reasons.append(f"EV/Sales {ev_sales:.2f} — richly valued relative to revenue")
    return {"score": score, "ev_sales": ev_sales, "reasons": reasons}


def _price_book(fields: dict, sector_bucket: str) -> dict:
    """Price/Book (±10). Sector-gated to FINANCIAL/REAL_ESTATE — for any
    other sector, book value doesn't meaningfully anchor the business's
    economic value (Research Report §3), so this returns a neutral 0
    with no reasons rather than scoring an inapplicable ratio."""
    pb = _val(fields, "price_book")
    score = 0.0
    reasons = []
    applicable = sector_bucket in PRICE_BOOK_APPLICABLE_SECTORS
    if applicable and pb is not None and pb > 0:
        if pb <= VI.PRICE_BOOK_CHEAP_MAX:
            score = VI.PRICE_BOOK_STRONG_SCORE
            reasons.append(f"Price/Book {pb:.2f} — trading near or below book value")
        elif pb >= VI.PRICE_BOOK_EXPENSIVE_MIN:
            score = VI.PRICE_BOOK_WEAK_SCORE
            reasons.append(f"Price/Book {pb:.2f} — trading well above book value")
    return {"score": score, "price_book": pb, "applicable": applicable, "reasons": reasons}


def _ev_ebitda(fields: dict, sector_bucket: str) -> dict:
    """EV/EBITDA (±12). Population-gated to non-FINANCIAL companies —
    Sprint #002 found this structurally unavailable for Banks/NBFC, not
    merely unreliable ("Unknown, not Low" — same distinction Growth
    Intelligence's own Sprint #002 established), so this scores 0/neutral
    for that population rather than penalizing a structural absence."""
    ev_ebitda = _val(fields, "ev_ebitda")
    score = 0.0
    reasons = []
    applicable = sector_bucket != "FINANCIAL"
    if applicable and ev_ebitda is not None and ev_ebitda > 0:
        if ev_ebitda <= VI.EV_EBITDA_CHEAP_MAX:
            score = VI.EV_EBITDA_STRONG_SCORE
            reasons.append(f"EV/EBITDA {ev_ebitda:.1f} — inexpensive on an enterprise-value basis")
        elif ev_ebitda >= VI.EV_EBITDA_EXPENSIVE_MIN:
            score = VI.EV_EBITDA_WEAK_SCORE
            reasons.append(f"EV/EBITDA {ev_ebitda:.1f} — richly valued on an enterprise-value basis")
    return {"score": score, "ev_ebitda": ev_ebitda, "applicable": applicable, "reasons": reasons}


def _dividend_income(fields: dict) -> dict:
    """Dividend Income (±10 yield, ±5 sustainability modifier). A high
    yield is rewarded; a ZERO or absent yield is never penalized (many
    legitimate growth companies pay no dividend at all) — only a
    confirmed unsustainable payout ratio on top of an existing yield is."""
    yield_pct = _val(fields, "dividend_yield_pct")
    payout = _val(fields, "payout_ratio")
    score = 0.0
    reasons = []
    if yield_pct is not None and yield_pct >= VI.DIVIDEND_YIELD_ATTRACTIVE_MIN_PCT:
        score = VI.DIVIDEND_YIELD_STRONG_SCORE
        reasons.append(f"Dividend yield {yield_pct:.2f}% — attractive income return")
        if payout is not None:
            if payout <= VI.PAYOUT_RATIO_SUSTAINABLE_MAX:
                score += VI.DIVIDEND_SUSTAINABILITY_BONUS
                reasons.append(f"Payout ratio {payout:.0%} — well covered, sustainable")
            elif payout >= VI.PAYOUT_RATIO_RISKY_MIN:
                score += VI.DIVIDEND_SUSTAINABILITY_PENALTY
                reasons.append(f"Payout ratio {payout:.0%} — high, dividend cut risk")
    return {"score": score, "dividend_yield_pct": yield_pct, "payout_ratio": payout, "reasons": reasons}


def _fcf_yield(fields: dict, sector_bucket: str) -> dict:
    """Free Cash Flow Yield (±10). Population-gated the same way as
    EV/EBITDA — inherits Growth Intelligence's own confirmed FCF-
    approximation imprecision for India on top of the Bank/NBFC gap."""
    fcf_yield = _val(fields, "fcf_yield_pct")
    score = 0.0
    reasons = []
    applicable = sector_bucket != "FINANCIAL"
    if applicable and fcf_yield is not None:
        if fcf_yield >= VI.FCF_YIELD_ATTRACTIVE_MIN_PCT:
            score = VI.FCF_YIELD_STRONG_SCORE
            reasons.append(f"FCF yield {fcf_yield:.1f}% — strong cash generation relative to price")
        elif fcf_yield <= VI.FCF_YIELD_WEAK_MAX_PCT:
            score = VI.FCF_YIELD_WEAK_SCORE
            reasons.append(f"FCF yield {fcf_yield:.1f}% — weak cash generation relative to price")
    return {"score": score, "fcf_yield_pct": fcf_yield, "applicable": applicable, "reasons": reasons}


def _peg(fields: dict, sector_bucket: str) -> dict:
    """PEG Ratio (±8). Population-gated (inherits EV/EBITDA's gate) and
    additionally depends on a growth-rate input the adapter is
    responsible for sourcing per SSDS-008's "read, don't recompute" rule —
    this function only interprets the ratio it's handed."""
    peg = _val(fields, "peg_ratio")
    score = 0.0
    reasons = []
    applicable = sector_bucket != "FINANCIAL"
    if applicable and peg is not None and peg > 0:
        if peg <= VI.PEG_CHEAP_MAX:
            score = VI.PEG_STRONG_SCORE
            reasons.append(f"PEG {peg:.2f} — attractive relative to growth")
        elif peg >= VI.PEG_EXPENSIVE_MIN:
            score = VI.PEG_WEAK_SCORE
            reasons.append(f"PEG {peg:.2f} — expensive relative to growth")
    return {"score": score, "peg_ratio": peg, "applicable": applicable, "reasons": reasons}


def compute_valuation_intelligence(symbol: str, fields: dict, sector_bucket: str = "", market: str = "US") -> dict:
    """
    The Valuation Intelligence Engine's single public entry point.

    `fields` is a {field_name: value_or_{"value": ...}} dict built by
    india_valuation_adapter.build_india_valuation_fields() or
    us_valuation_adapter.build_us_valuation_fields() — never built by this
    function, which has no provider knowledge.

    Returns an EngineResponse (as a dict, via .to_dict()) per SSDS-008
    §EngineResponse Contract (inherited from engine_contract.py, shared
    with Business Quality, Financial Strength, and Growth Intelligence).
    """
    fields = fields or {}

    core_present = sum(1 for f in CORE_FIELDS if _val(fields, f) is not None)
    if core_present < VI.MIN_CORE_FIELDS_PRESENT:
        return EngineResponse(
            score=0,
            grade=Grade.REJECTED,
            confidence=0.0,
            explanation="Insufficient valuation data available to assess this company.",
            metadata={
                "engine": "valuation_intelligence_engine",
                "engine_version": "v1",
                "market": market,
                "sector_bucket": sector_bucket,
                "rejection_reason": "insufficient_data",
                "missing_core_fields": [f for f in CORE_FIELDS if _val(fields, f) is None],
            },
        ).to_dict()

    # Population-gated fields (EV/EBITDA, FCF Yield, PEG for FINANCIAL;
    # Price/Book for non-FINANCIAL/REAL_ESTATE) are excluded from the
    # completeness denominator when structurally inapplicable, exactly
    # mirroring Growth Intelligence's "Unknown, not Low" treatment of the
    # Bank/NBFC operating-profit gap — a structural absence is not a
    # missing-data penalty.
    applicable_extended = []
    for f in EXTENDED_FIELDS:
        if f == "price_book" and sector_bucket not in PRICE_BOOK_APPLICABLE_SECTORS:
            continue
        if f in ("ev_ebitda", "fcf_yield_pct", "peg_ratio") and sector_bucket == "FINANCIAL":
            continue
        applicable_extended.append(f)
    all_applicable = CORE_FIELDS + applicable_extended
    present = sum(1 for f in all_applicable if _val(fields, f) is not None)
    data_completeness_pct = round(100 * present / len(all_applicable), 1) if all_applicable else 0.0

    earnings = _earnings_multiple(fields)
    ev_sales = _ev_sales(fields)
    price_book = _price_book(fields, sector_bucket)
    ev_ebitda = _ev_ebitda(fields, sector_bucket)
    dividend = _dividend_income(fields)
    fcf_yield = _fcf_yield(fields, sector_bucket)
    peg = _peg(fields, sector_bucket)

    combined = (
        50
        + earnings["score"]
        + ev_sales["score"]
        + price_book["score"]
        + ev_ebitda["score"]
        + dividend["score"]
        + fcf_yield["score"]
        + peg["score"]
    )
    score = round(max(0, min(100, combined)))

    if score >= VI.GRADE_STRONG_BUY_MIN:
        grade = Grade.STRONG_BUY
    elif score >= VI.GRADE_BUY_MIN:
        grade = Grade.BUY
    elif score >= VI.GRADE_HOLD_MIN:
        grade = Grade.HOLD
    elif score >= VI.GRADE_WATCH_MIN:
        grade = Grade.WATCH
    else:
        grade = Grade.AVOID

    categories = {
        "Earnings Multiple": (earnings["score"], earnings["reasons"]),
        "EV/Sales": (ev_sales["score"], ev_sales["reasons"]),
        "Price/Book": (price_book["score"], price_book["reasons"]),
        "EV/EBITDA": (ev_ebitda["score"], ev_ebitda["reasons"]),
        "Dividend Income": (dividend["score"], dividend["reasons"]),
        "Free Cash Flow Yield": (fcf_yield["score"], fcf_yield["reasons"]),
        "PEG Ratio": (peg["score"], peg["reasons"]),
    }
    ranked = sorted(categories.items(), key=lambda kv: kv[1][0], reverse=True)
    strengths = [f"{name}: {reasons[0]}" for name, (val, reasons) in ranked
                 if val >= VI.MIN_NOTABLE_CONTRIBUTION and reasons][:3]
    weaknesses = [f"{name}: {reasons[0]}" for name, (val, reasons) in ranked
                  if val <= -VI.MIN_NOTABLE_CONTRIBUTION and reasons][-3:]

    risks = []
    if dividend["payout_ratio"] is not None and dividend["payout_ratio"] >= VI.PAYOUT_RATIO_RISKY_MIN:
        risks.append(f"Payout ratio {dividend['payout_ratio']:.0%} — dividend sustainability risk")
    if earnings["blended_pe"] is not None and earnings["blended_pe"] >= VI.PE_EXPENSIVE_MIN:
        risks.append(f"P/E {earnings['blended_pe']:.1f} — limited margin of safety if growth disappoints")

    inapplicable_extended = [f for f in EXTENDED_FIELDS if f not in applicable_extended]
    skipped = [f for f in applicable_extended if _val(fields, f) is None]
    explanation = (
        f"Valuation Intelligence Score {score}/100 ({grade.value}). "
        f"Earnings Multiple contributed {earnings['score']:+.1f}, EV/Sales {ev_sales['score']:+.1f}, "
        f"Price/Book {price_book['score']:+.1f}, EV/EBITDA {ev_ebitda['score']:+.1f}, "
        f"Dividend Income {dividend['score']:+.1f}, Free Cash Flow Yield {fcf_yield['score']:+.1f}, "
        f"PEG Ratio {peg['score']:+.1f}."
    )
    if skipped:
        explanation += f" {len(skipped)} metric(s) unavailable for this company and excluded from scoring (not fabricated)."
    if inapplicable_extended:
        explanation += f" {len(inapplicable_extended)} metric(s) structurally inapplicable to this sector and excluded, not penalized."

    return EngineResponse(
        score=score,
        grade=grade,
        confidence=min(data_completeness_pct, 100.0),
        strengths=strengths,
        weaknesses=weaknesses,
        risks=risks,
        explanation=explanation,
        metadata={
            "engine": "valuation_intelligence_engine",
            "engine_version": "v1",
            "market": market,
            "sector_bucket": sector_bucket,
            "data_completeness_pct": data_completeness_pct,
            "category_contributions": {name: val for name, (val, _) in categories.items()},
            "skipped_fields": skipped,
            "inapplicable_fields": inapplicable_extended,
            "pe_ratio": earnings["pe_ratio"],
            "forward_pe": earnings["forward_pe"],
            "ev_sales": ev_sales["ev_sales"],
            "price_book": price_book["price_book"],
            "ev_ebitda": ev_ebitda["ev_ebitda"],
            "dividend_yield_pct": dividend["dividend_yield_pct"],
            "payout_ratio": dividend["payout_ratio"],
            "fcf_yield_pct": fcf_yield["fcf_yield_pct"],
            "peg_ratio": peg["peg_ratio"],
        },
    ).to_dict()
