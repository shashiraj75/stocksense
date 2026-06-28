"""
StockSense360 US Provider Precedence (SSDS-006, Epic 002 Sprint #006).

Encodes the field-level US provider precedence decision documented in
Epic-002-Sprint-006-US-Provider-Precedence-Decision.md, evidence-based
from Sprint #005's 76-company live validation comparing SEC EDGAR
against yfinance for every SSDS-005-required field.

Per SSDS-006 §8 ("provider precedence is a per-field, per-market
priority order"), precedence here is decided per field, never as a
single "SEC EDGAR is primary" or "yfinance is primary" blanket rule —
the field-by-field table below mixes both, exactly as the evidence
supports.

This is a deliberately small, standalone precursor to SSDS-006's full
Provider Registry (§15 item 4) — a registry encodes precedence as
configuration data a future engine reads; this module encodes the same
decision as a typed mapping + one resolution function. It does not
implement consensus/agreement-tolerance calibration (SSDS-006 §9) since
Sprint #005 did not gather genuine cross-provider-agreement evidence
(yfinance was used as that validation's comparison baseline, not as a
second registered Fabric provider) — only precedence + fallback.

Per this sprint's explicit scope: this module is NOT imported by any
intelligence engine, by us_fundamentals.py, by sec_edgar_adapter.py
itself, or by any API route. It is additive, standalone, and ready for
a future integration sprint to wire in — not wired in by this one.
"""

from enum import Enum
from typing import Any, Optional


class PrecedenceRule(str, Enum):
    EDGAR_PRIMARY = "edgar_primary"
    YFINANCE_PRIMARY = "yfinance_primary"


# Field -> rule. Evidence: Epic 002 Sprint #005's 76-company live
# validation (see the Sprint #006 decision report's Field-Level
# Ownership Table for the full reasoning behind each entry).
FIELD_PRECEDENCE: dict[str, PrecedenceRule] = {
    "revenue": PrecedenceRule.EDGAR_PRIMARY,
    "net_income": PrecedenceRule.EDGAR_PRIMARY,
    "ebit": PrecedenceRule.YFINANCE_PRIMARY,
    "interest_expense": PrecedenceRule.EDGAR_PRIMARY,  # non-FINANCIAL; see SECTOR_PRECEDENCE_OVERRIDE
    "cash_and_equivalents": PrecedenceRule.YFINANCE_PRIMARY,  # provisional -- see DEFINITIONAL_DECISION_REQUIRED
    "current_assets": PrecedenceRule.EDGAR_PRIMARY,
    "current_liabilities": PrecedenceRule.EDGAR_PRIMARY,
    "total_assets": PrecedenceRule.EDGAR_PRIMARY,
    "total_liabilities": PrecedenceRule.YFINANCE_PRIMARY,
    "short_term_debt": PrecedenceRule.YFINANCE_PRIMARY,
    "long_term_debt": PrecedenceRule.YFINANCE_PRIMARY,
    "total_debt": PrecedenceRule.YFINANCE_PRIMARY,
    "operating_cash_flow": PrecedenceRule.EDGAR_PRIMARY,
    "capital_expenditure": PrecedenceRule.EDGAR_PRIMARY,
    "free_cash_flow": PrecedenceRule.YFINANCE_PRIMARY,
    "shareholders_equity": PrecedenceRule.EDGAR_PRIMARY,
}

# Sector-specific overrides to the general rule above. Confirmed live
# (Sprint #005): banks/insurance show a real, meaningful interest_expense
# disagreement (6-18%) between sources -- a definitional gap (gross vs.
# net interest expense treatment for deposit-taking institutions), not
# noise -- so the FINANCIAL sector flips this one field's precedence.
SECTOR_PRECEDENCE_OVERRIDE: dict[str, dict[str, PrecedenceRule]] = {
    "FINANCIAL": {
        "interest_expense": PrecedenceRule.YFINANCE_PRIMARY,
    },
}

# Confirmed structurally absent on BOTH sources for these sector/field
# combinations (Sprint #005, at full 76-company scale) -- a precedence
# rule cannot fix a concept neither source's filings contain. These
# fields need a sector-specific substitute computation (already named
# in SSDS-005/SSDS-006), not a fallback chain.
SECTOR_SUBSTITUTE_REQUIRED: dict[str, set[str]] = {
    "FINANCIAL": {"current_assets", "current_liabilities", "ebit", "short_term_debt", "long_term_debt"},
    "REIT": {"current_assets", "current_liabilities", "long_term_debt"},
}

# Fields where the two sources answer genuinely different questions
# (confirmed: yfinance's totalCash includes short-term investments;
# EDGAR's tag is cash-only) -- named as an open product decision, not
# resolved by a precedence rule. The FIELD_PRECEDENCE entry above is a
# provisional default, not a claim that this question is settled.
DEFINITIONAL_DECISION_REQUIRED: set[str] = {"cash_and_equivalents"}

_FALLBACK_CONFIDENCE_DISCOUNT = 0.05  # provisional, per SSDS-006 §7's own
                                       # admission that confidence weights
                                       # are not yet calibrated against
                                       # live multi-provider agreement data
_YFINANCE_BASELINE_CONFIDENCE = 0.85  # provisional -- no yfinance-specific
                                       # confidence model exists in this
                                       # codebase yet


def resolve_field(
    field: str,
    edgar_record: Optional[dict[str, Any]],
    yfinance_value: Optional[float],
    sector_bucket: Optional[str] = None,
) -> dict[str, Any]:
    """
    Resolves one field's value using this module's field-level precedence
    table, with a fallback to the non-primary source on absence (never on
    disagreement -- per the Sprint #006 decision report's Fallback Rules).

    `edgar_record` is the provenance dict sec_edgar_adapter.normalize_fields()
    produces for this field (or None/UNAVAILABLE if EDGAR has no data).
    `yfinance_value` is a bare numeric value (or None) -- no yfinance
    provenance model exists yet in this codebase.

    Returns a single resolved-and-provenanced record. Never fabricates a
    value: if neither source has data (or a sector substitute is
    required), `value` is None.
    """
    if field not in FIELD_PRECEDENCE:
        raise ValueError(f"Unknown field for US provider precedence: {field!r}")

    if sector_bucket and field in SECTOR_SUBSTITUTE_REQUIRED.get(sector_bucket, set()):
        return {
            "field": field,
            "value": None,
            "chosen_source": None,
            "rule_applied": None,
            "primary_available": False,
            "fallback_used": False,
            "sector_substitute_required": True,
            "sector_bucket": sector_bucket,
            "confidence": 0.0,
            "note": (
                f"{sector_bucket} sector: confirmed structurally absent from both "
                "EDGAR and yfinance (Sprint #005) -- needs a sector-specific "
                "substitute computation, not a precedence fallback."
            ),
        }

    rule = SECTOR_PRECEDENCE_OVERRIDE.get(sector_bucket or "", {}).get(field, FIELD_PRECEDENCE[field])
    edgar_available = bool(edgar_record and edgar_record.get("value") is not None)
    yfinance_available = yfinance_value is not None

    if rule is PrecedenceRule.EDGAR_PRIMARY:
        primary_available, primary_source = edgar_available, "sec_edgar"
        fallback_available, fallback_source = yfinance_available, "yfinance"
    else:
        primary_available, primary_source = yfinance_available, "yfinance"
        fallback_available, fallback_source = edgar_available, "sec_edgar"

    if primary_available:
        chosen_source, fallback_used = primary_source, False
    elif fallback_available:
        chosen_source, fallback_used = fallback_source, True
    else:
        chosen_source, fallback_used = None, False

    if chosen_source == "sec_edgar":
        value = edgar_record["value"]
        confidence = edgar_record.get("confidence", _YFINANCE_BASELINE_CONFIDENCE)
    elif chosen_source == "yfinance":
        value = yfinance_value
        confidence = _YFINANCE_BASELINE_CONFIDENCE
    else:
        value = None
        confidence = 0.0

    if fallback_used and chosen_source is not None:
        confidence = round(max(confidence - _FALLBACK_CONFIDENCE_DISCOUNT, 0.0), 4)

    agreement_within_5pct: Optional[bool] = None
    if edgar_available and yfinance_available and yfinance_value:
        diff_pct = abs(edgar_record["value"] - yfinance_value) / abs(yfinance_value) * 100
        agreement_within_5pct = diff_pct <= 5.0

    return {
        "field": field,
        "value": value,
        "chosen_source": chosen_source,
        "rule_applied": rule.value,
        "primary_available": primary_available,
        "fallback_used": fallback_used,
        "sector_substitute_required": False,
        "sector_bucket": sector_bucket,
        "confidence": confidence,
        "both_sources_available": edgar_available and yfinance_available,
        "agreement_within_5pct": agreement_within_5pct,
        "definitional_decision_pending": field in DEFINITIONAL_DECISION_REQUIRED,
        "note": None,
    }
