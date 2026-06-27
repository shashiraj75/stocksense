"""
Sector classification and metric-applicability rules for the Business
Quality Engine (SSDS-003 §4, Sprint #004).

This is a NEW, purpose-built taxonomy — deliberately not a reuse of
quality_factors.py's STOCK_SECTOR / SECTOR_INDICES / _SECTOR_KEYWORD_MAP,
because those serve a different question ("which index proxies this
stock's sector momentum") than this module answers ("which fundamental
metrics are meaningful for this stock's industry"). Reusing one taxonomy
for both jobs would couple two unrelated concerns; SEAR-001's broader
finding (duplicated business rules) is about the same SCORING FORMULA
being reimplemented twice, not about two different rules happening to
classify the same company into a same-named bucket.

SSDS-003 Finding 2 confirmed the existing taxonomy has no Telecom
bucket at all — added here.
"""

import re

# The 12 sectors named in SSDS-003 §4, collapsed to the buckets that
# actually need distinct metric-applicability treatment. Banks, NBFCs,
# and Insurance share one bucket (FINANCIAL) because all three already
# share the same is_financial exemption rationale documented in
# prediction_engine.py's quality gate (Ind-AS treatment of loans
# disbursed as operating outflows) — splitting them further would not
# change any rule in the applicability table below.
SECTOR_BUCKETS = (
    "FINANCIAL",       # Banks, NBFCs, Insurance
    "FMCG",
    "IT",
    "PHARMA",
    "MANUFACTURING",   # Manufacturing, Capital Goods
    "UTILITIES_ENERGY",  # Utilities, Energy
    "TELECOM",
    "REAL_ESTATE",
    "OTHER",           # anything not matched — universal rules only
)

_KEYWORD_PATTERNS: dict[str, list[str]] = {
    "FINANCIAL": [r"\bbank\b", r"\bnbfc\b", r"\binsurance\b", r"\bfinancial services\b",
                  r"\bfinance\b", r"\bhousing finance\b", r"\bcredit\b", r"\basset management\b"],
    "FMCG": [r"\bfmcg\b", r"\bconsumer staples\b", r"\bconsumer defensive\b", r"\bfood products\b",
              r"\bbeverages\b", r"\bpersonal care\b", r"\bhousehold\b", r"\btobacco\b"],
    "IT": [r"\binformation technology\b", r"\btechnology\b", r"\bcomputer\b",
           r"\bsoftware\b", r"\bit services\b", r"\bsemiconductor\b", r"\bconsumer electronics\b"],
    "PHARMA": [r"\bpharma\b", r"\bhealthcare\b", r"\bhospital\b", r"\bbiotechnology\b", r"\bdrug\b"],
    "MANUFACTURING": [r"\bmanufactur\w*\b", r"\bcapital goods\b", r"\bengineering\b",
                       r"\bindustrial\w*\b", r"\bauto\w*\b", r"\bmetal\b", r"\bsteel\b", r"\bcement\b",
                       r"\bchemical\w*\b", r"\bbasic materials\b"],
    "UTILITIES_ENERGY": [r"\butilit\w*\b", r"\belectric\b", r"\bpower generation\b", r"\boil\b",
                          r"\bgas\b", r"\bpetroleum\b", r"\benergy\b"],
    "TELECOM": [r"\btelecom\w*\b", r"\bwireless\b", r"\bcommunication services\b"],
    "REAL_ESTATE": [r"\breal estate\b", r"\brealty\b", r"\bproperty\b"],
}


def classify_sector(info: dict | None) -> str:
    """
    Classify a stock into one of SECTOR_BUCKETS from yfinance's
    sector/industry text — works the same way for IN and US, since both
    populate info["sector"]/info["industry"] (IN's screener.in
    augmentation doesn't override these yfinance fields). Returns
    "OTHER" rather than None when nothing matches, so callers never
    need a separate null-check before looking up the applicability
    table below.
    """
    if not info:
        return "OTHER"
    text = f"{info.get('sector') or ''} {info.get('industry') or ''}".lower()
    if not text.strip():
        return "OTHER"
    for bucket, patterns in _KEYWORD_PATTERNS.items():
        if any(re.search(p, text) for p in patterns):
            return bucket
    return "OTHER"


# SSDS-003 §4's Metric Applicability Table, encoded directly. Keys match
# the metric identifiers used in business_quality_engine.py. A bucket
# absent from a metric's "exempt" or "adjusted" set is implicitly
# "universal" for that metric — matching the spec's "a bucket absent
# from this table uses the universal rule" convention.
METRIC_APPLICABILITY: dict[str, dict[str, set[str]]] = {
    "debt_to_equity": {
        "exempt": {"FINANCIAL"},     # leverage is the business model, not a risk signal
        "adjusted": set(),
    },
    "operating_cash_flow": {
        "exempt": {"FINANCIAL"},     # Ind-AS: loans disbursed count as operating outflows
        "adjusted": set(),
    },
    "gross_margin": {
        "exempt": {"FINANCIAL", "UTILITIES_ENERGY"},
        "adjusted": set(),
    },
    "asset_turnover": {
        "exempt": set(),
        "adjusted": {"IT", "PHARMA"},  # less diagnostic for asset-light models — Optional not Mandatory
    },
    "interest_coverage": {
        "exempt": {"FINANCIAL"},     # debt is their raw material, not leverage risk in the usual sense
        "adjusted": set(),
    },
    "working_capital_efficiency": {
        "exempt": {"FINANCIAL", "UTILITIES_ENERGY"},
        "adjusted": {"IT"},           # minimal inventory — less diagnostic, not irrelevant
    },
}


def is_exempt(metric: str, sector_bucket: str) -> bool:
    """True if `metric` should be skipped entirely (not scored, not
    counted against data completeness) for this sector bucket."""
    rule = METRIC_APPLICABILITY.get(metric)
    return bool(rule and sector_bucket in rule["exempt"])


def is_adjusted(metric: str, sector_bucket: str) -> bool:
    """True if `metric` applies but should be treated as Optional rather
    than Mandatory for this sector bucket (per SSDS-003 §4 — e.g. asset
    turnover for IT/Pharma)."""
    rule = METRIC_APPLICABILITY.get(metric)
    return bool(rule and sector_bucket in rule["adjusted"])
