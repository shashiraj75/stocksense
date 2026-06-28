"""
StockSense360 SEC EDGAR Adapter (SSDS-006, Epic 002 Sprint #004).

The first provider built under SSDS-006's Data Fabric & Provider
Architecture. Confirmed live during Epic 002 Sprint #002 (the Data
Independence & Provider Strategy report): SEC EDGAR's `companyfacts`
XBRL API is free, requires no API key, and returned full, correctly
tagged data for a non-financial US company (17 years of history,
vs. yfinance's 4-5-year cap) and correctly showed the *absence* of
current-asset/liability tags for a financial-sector company (a real
fact about bank reporting, not a data gap) — independently confirming
the same FINANCIAL-sector finding yfinance's own statements already
showed.

This module is a Provider Adapter exactly as SSDS-006 Section 4
specifies: it owns SEC EDGAR's authentication (a descriptive
User-Agent, not a secret), retry/backoff, self-throttled rate limiting,
raw-schema validation, and provider/schema versioning. Per SSDS-006's
hard rule ("no provider-specific logic should leak beyond this
layer"), nothing outside this file knows that "us-gaap", "10-K", or
"data.sec.gov" exist.

Per this sprint's explicit scope:
  - This module does NOT modify business_quality_engine.py, any other
    intelligence engine, or any India provider.
  - This module is purely additive — no existing yfinance/us_fundamentals
    code path is changed. SEC EDGAR is introduced as one more provider,
    not a replacement, per SSDS-006 Section 9's resolution rules and
    this sprint's "no provider replacement yet" rule.
  - Per SSDS-006 Section 6, every normalized field carries a full
    provenance record (provider, source taxonomy, original concept
    name, fiscal period, filing date, confidence, derivation status).
    A missing field is recorded as UNAVAILABLE — never fabricated,
    per SSDS-005's and SSDS-003's shared missing-data philosophy.
"""

import logging
import threading
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── Provider Adapter Standard (SSDS-006 §4) ─────────────────────────────────

PROVIDER_NAME = "sec_edgar"
ADAPTER_VERSION = "sec_edgar_adapter_v1"  # bump if the field-mapping table
                                           # below changes in a way that would
                                           # alter previously-cached output

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# SEC's fair-access policy requires a descriptive User-Agent identifying the
# requester and a contact method — confirmed live (Epic 002 Sprint #002):
# a generic/non-compliant User-Agent triggered an immediate
# "Request Rate Threshold Exceeded" response. This is NOT a secret, so it
# is fine to default it, but production should set SEC_EDGAR_CONTACT so SEC
# can reach StockSense360's operator if needed, per their own policy.
_DEFAULT_CONTACT = "StockSense360-research stocksense360-research@example.com"

# SEC's documented rate limit is ~10 requests/second. Self-throttling here
# (not relying on the caller to pace requests) is the Provider Adapter
# Standard's explicit requirement (SSDS-006 §4) — mirrors the same
# discipline screener_data.py already applies for its own provider.
_MIN_REQUEST_INTERVAL = 0.12  # ~8.3 req/sec, a margin under the ~10 req/sec limit
_RETRY_COUNT = 3
_RETRY_BACKOFF_SECONDS = 1.5
_REQUEST_TIMEOUT = 15

_rate_lock = threading.Lock()
_last_request_at = 0.0

# Ticker → CIK map cache (SEC's own bulk file; ~10k tickers, ~800KB).
_TICKER_MAP_TTL = 24 * 3600  # 24 hours — SEC's own mapping changes rarely
_ticker_map_lock = threading.Lock()
_ticker_map_cache: tuple[float, dict[str, int]] | None = None

# companyfacts response cache, per CIK. Fundamentals are filed quarterly/
# annually (SSDS-006 §10's freshness reasoning) — a same-day re-fetch is
# never useful, so this TTL is deliberately longer than screener_data.py's
# 4-hour TTL, which exists for a daily-updating source.
_FACTS_TTL = 12 * 3600
_facts_lock = threading.Lock()
_facts_cache: dict[int, tuple[float, Optional[dict]]] = {}

# A small, evidence-based fallback for the representative tickers this
# sprint's brief names, sourced from a live call to _TICKER_MAP_URL during
# this sprint (not guessed) — used only if the live ticker-map download
# itself fails, per SSDS-006's Fail-Soft Engineering principle. This is a
# resilience floor, not a replacement for the live map.
_FALLBACK_CIK_MAP = {
    "AAPL": 320193,
    "MSFT": 789019,
    "JPM": 19617,
    "KO": 21344,
    "ORCL": 1341439,
    "GOOGL": 1652044,
}


def _contact() -> str:
    import os
    contact = os.getenv("SEC_EDGAR_CONTACT", "")
    if not contact:
        log.warning(
            "SEC_EDGAR_CONTACT not set — using a default User-Agent. "
            "Per SEC's fair-access policy, production should set a real "
            "contact identifier."
        )
        return _DEFAULT_CONTACT
    return contact


def _headers() -> dict:
    return {"User-Agent": _contact(), "Accept-Encoding": "gzip, deflate"}


def _throttle() -> None:
    """Self-throttles to stay under SEC's documented rate limit. Never
    relies on the caller to pace requests — SSDS-006 §4's explicit
    requirement."""
    global _last_request_at
    with _rate_lock:
        elapsed = time.time() - _last_request_at
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_at = time.time()


def _get_with_retry(url: str) -> Optional[requests.Response]:
    """Bounded retry with backoff for transient failures (timeout, 5xx,
    429) — never retries a definite failure (404, a clean non-200 that
    isn't rate-limiting), per SES-002 §6's "distinguish temporarily
    unavailable from a bug" rule."""
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_COUNT):
        _throttle()
        try:
            resp = requests.get(url, headers=_headers(), timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as e:
            last_exc = e
            log.warning("[sec_edgar] request error (attempt %d/%d) for %s: %s",
                        attempt + 1, _RETRY_COUNT, url, e)
            time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue

        if resp.status_code == 200:
            return resp
        if resp.status_code == 404:
            log.info("[sec_edgar] 404 for %s — not a transient failure, not retrying", url)
            return resp
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("[sec_edgar] status=%d (attempt %d/%d) for %s",
                        resp.status_code, attempt + 1, _RETRY_COUNT, url)
            time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        # Any other status (e.g. 403) — report and stop; not a retry case.
        log.error("[sec_edgar] unexpected status=%d for %s", resp.status_code, url)
        return resp

    log.error("[sec_edgar] giving up after %d attempts for %s: %s", _RETRY_COUNT, url, last_exc)
    return None


def _fetch_ticker_map() -> dict[str, int]:
    """Downloads SEC's official ticker→CIK bulk mapping. Falls back to the
    small evidence-based map above if the download fails — never raises,
    per Fail-Soft Engineering (SSDS-006 §2)."""
    resp = _get_with_retry(_TICKER_MAP_URL)
    if resp is None or resp.status_code != 200:
        log.warning("[sec_edgar] ticker map download failed — using fallback map (%d tickers)",
                    len(_FALLBACK_CIK_MAP))
        return dict(_FALLBACK_CIK_MAP)

    try:
        payload = resp.json()
    except ValueError as e:
        log.error("[sec_edgar] ticker map response was not valid JSON: %s", e)
        return dict(_FALLBACK_CIK_MAP)

    mapping: dict[str, int] = {}
    for entry in payload.values():
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        if ticker and cik is not None:
            mapping[ticker.upper()] = int(cik)

    if not mapping:
        log.error("[sec_edgar] ticker map parsed but contained zero usable entries — using fallback")
        return dict(_FALLBACK_CIK_MAP)

    return mapping


def resolve_cik(symbol: str) -> Optional[int]:
    """
    Ticker → CIK resolution (Task 2), using a 24-hour-cached copy of SEC's
    own bulk ticker map, with the evidence-based fallback map as a last
    resort. Returns None — never a guessed CIK — if the symbol isn't
    found anywhere, per the "do not fabricate values" rule.
    """
    global _ticker_map_cache
    sym = symbol.upper().strip()

    with _ticker_map_lock:
        cached = _ticker_map_cache
        if cached and (time.time() - cached[0]) < _TICKER_MAP_TTL:
            mapping = cached[1]
        else:
            mapping = _fetch_ticker_map()
            _ticker_map_cache = (time.time(), mapping)

    cik = mapping.get(sym)
    if cik is None:
        log.info("[sec_edgar] no CIK found for symbol %s", sym)
    return cik


def fetch_company_facts(cik: int) -> Optional[dict]:
    """
    Fetches the raw companyfacts payload for a CIK, 12-hour cached.
    Returns None (not an empty dict) on any failure — callers must treat
    None as "unavailable," not as "zero facts," per the schema-validation
    requirement in SSDS-006 §4.
    """
    with _facts_lock:
        cached = _facts_cache.get(cik)
        if cached and (time.time() - cached[0]) < _FACTS_TTL:
            return cached[1]

    resp = _get_with_retry(_FACTS_URL.format(cik=cik))
    result: Optional[dict] = None
    if resp is not None and resp.status_code == 200:
        try:
            payload = resp.json()
            # Raw-schema validation (SSDS-006 §4) — a 200 with the wrong
            # shape is a provider-side change, not success.
            if isinstance(payload, dict) and "facts" in payload and "us-gaap" in payload.get("facts", {}):
                result = payload
            else:
                log.error("[sec_edgar] companyfacts response for CIK %010d did not match expected shape", cik)
        except ValueError as e:
            log.error("[sec_edgar] companyfacts response for CIK %010d was not valid JSON: %s", cik, e)
    else:
        status = resp.status_code if resp is not None else "no response"
        log.warning("[sec_edgar] companyfacts fetch failed for CIK %010d (status=%s)", cik, status)

    with _facts_lock:
        _facts_cache[cik] = (time.time(), result)
    return result


# ── Field Normalization (SSDS-006 §5) ───────────────────────────────────────
# Each unified field maps to an ordered list of us-gaap XBRL tags to try.
# The first tag with a usable value wins — mirrors the existing
# "try the next provider/field" pattern already proven in
# augment_info_with_screener's fallback chain (SSDS-004 §1).
_DIRECT_FIELD_TAGS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "ebit": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestAndDebtExpense"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue",
                              "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "short_term_debt": ["LongTermDebtCurrent", "DebtCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities",
                             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capital_expenditure": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "shareholders_equity": ["StockholdersEquity",
                             "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    # "total_debt", "free_cash_flow" are DERIVED — see _derive_fields below,
    # not part of this direct-tag table.
}

UNIFIED_FIELDS = list(_DIRECT_FIELD_TAGS.keys()) + ["total_debt", "free_cash_flow"]


def _best_entry(entries: list[dict]) -> Optional[dict]:
    """
    Picks the most relevant XBRL fact entry from a concept's USD unit list:
    prefer annual (10-K, fp == "FY") filings, most recent `end` date first;
    fall back to the most recent entry of any form if no 10-K exists.
    Never averages or guesses across entries — picks one, real, dated value.
    """
    if not entries:
        return None
    annual = [e for e in entries if e.get("form") == "10-K" and e.get("fp") == "FY"]
    pool = annual if annual else entries
    pool = [e for e in pool if e.get("val") is not None and e.get("end")]
    if not pool:
        return None
    return max(pool, key=lambda e: (e.get("end", ""), e.get("filed", "")))


def _extract_direct(facts: dict, tags: list[str]) -> Optional[dict]:
    """
    Tries each tag in order against the company's us-gaap facts; returns
    the first usable entry plus which tag/taxonomy produced it. Returns
    None — UNAVAILABLE, not a fabricated value — if no tag has data.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        concept = us_gaap.get(tag)
        if not concept:
            continue
        usd_entries = concept.get("units", {}).get("USD", [])
        best = _best_entry(usd_entries)
        if best is not None:
            return {
                "value": float(best["val"]),
                "concept": tag,
                "fiscal_year": best.get("fy"),
                "fiscal_period": best.get("fp"),
                "filed_date": best.get("filed"),
                "period_end": best.get("end"),
                "form": best.get("form"),
            }
    return None


def _provenance(
    field: str,
    extracted: Optional[dict],
    derivation_status: str,
    derivation_note: Optional[str] = None,
) -> dict:
    """
    Builds the SSDS-006 §6 provenance record for one normalized field.
    Confidence is a simple, explicitly-provisional heuristic per SSDS-006's
    own admission that confidence weights are not yet calibrated against
    live multi-provider data (SSDS-006 §9/§15) — DIRECT values from a
    10-K get full confidence; DERIVED values (computed from two DIRECT
    components) get a modest discount; UNAVAILABLE is zero, never guessed.
    """
    if extracted is None:
        return {
            "field": field,
            "value": None,
            "unit": "USD",
            "provider": PROVIDER_NAME,
            "source_taxonomy": None,
            "concept": None,
            "fiscal_year": None,
            "fiscal_period": None,
            "filed_date": None,
            "confidence": 0.0,
            "derivation_status": "UNAVAILABLE",
            "derivation_note": derivation_note,
        }

    confidence = 0.95 if extracted.get("form") == "10-K" else 0.75
    if derivation_status == "DERIVED":
        confidence = round(confidence * 0.85, 4)

    return {
        "field": field,
        "value": extracted["value"],
        "unit": "USD",
        "provider": PROVIDER_NAME,
        "source_taxonomy": "us-gaap",
        "concept": extracted["concept"],
        "fiscal_year": extracted.get("fiscal_year"),
        "fiscal_period": extracted.get("fiscal_period"),
        "filed_date": extracted.get("filed_date"),
        "form": extracted.get("form"),
        "confidence": confidence,
        "derivation_status": derivation_status,
        "derivation_note": derivation_note,
    }


def normalize_fields(facts: dict) -> dict[str, dict]:
    """
    Task 3/4 — normalizes every SSDS-005-required field from a raw
    companyfacts payload into the unified schema, each carrying a full
    SSDS-006 §6 provenance record. Fields with no available tag are
    explicitly UNAVAILABLE (value=None) — never fabricated, per Task 5.
    """
    extracted: dict[str, Optional[dict]] = {
        field: _extract_direct(facts, tags) for field, tags in _DIRECT_FIELD_TAGS.items()
    }

    result: dict[str, dict] = {
        field: _provenance(field, extracted[field], "DIRECT" if extracted[field] else "UNAVAILABLE")
        for field in _DIRECT_FIELD_TAGS
    }

    # ── DERIVED: total_debt = short_term_debt + long_term_debt ─────────────
    # Only derived when at least one component is present — a single-sided
    # sum (e.g. only long-term debt known) is still more informative than
    # UNAVAILABLE and is named as such via derivation_note, never silently
    # presented as if both components were confirmed.
    short_debt = extracted["short_term_debt"]
    long_debt = extracted["long_term_debt"]
    if short_debt or long_debt:
        total = (short_debt["value"] if short_debt else 0.0) + (long_debt["value"] if long_debt else 0.0)
        note = "short_term_debt + long_term_debt"
        if not (short_debt and long_debt):
            note += " (only one component available — the other was treated as 0, not confirmed absent)"
        fake_extracted = {
            "value": total,
            "concept": "LongTermDebtCurrent + LongTermDebtNoncurrent",
            "fiscal_year": (short_debt or long_debt).get("fiscal_year"),
            "fiscal_period": (short_debt or long_debt).get("fiscal_period"),
            "filed_date": (short_debt or long_debt).get("filed_date"),
            "form": (short_debt or long_debt).get("form"),
        }
        result["total_debt"] = _provenance("total_debt", fake_extracted, "DERIVED", note)
    else:
        result["total_debt"] = _provenance("total_debt", None, "UNAVAILABLE")

    # ── DERIVED: free_cash_flow = operating_cash_flow - capital_expenditure ─
    ocf = extracted["operating_cash_flow"]
    capex = extracted["capital_expenditure"]
    if ocf and capex:
        fcf_value = ocf["value"] - capex["value"]
        fake_extracted = {
            "value": fcf_value,
            "concept": "NetCashProvidedByUsedInOperatingActivities - PaymentsToAcquirePropertyPlantAndEquipment",
            "fiscal_year": ocf.get("fiscal_year"),
            "fiscal_period": ocf.get("fiscal_period"),
            "filed_date": ocf.get("filed_date"),
            "form": ocf.get("form"),
        }
        result["free_cash_flow"] = _provenance(
            "free_cash_flow", fake_extracted, "DERIVED",
            "operating_cash_flow - capital_expenditure; precision not independently cross-checked",
        )
    else:
        result["free_cash_flow"] = _provenance("free_cash_flow", None, "UNAVAILABLE")

    return result


# Maps unified fields onto the existing yfinance-`.info`-shaped key
# convention this codebase already uses (business_quality_engine.py,
# us_fundamentals.py) — purely additive: no existing key is renamed or
# removed, this only adds a new, optional projection of SEC EDGAR's
# normalized output for any FUTURE engine integration (explicitly not
# done in this sprint, per its "no production engine integration yet" rule).
_INFO_KEY_MAP = {
    "revenue": "totalRevenue",
    "net_income": "netIncome",
    "ebit": "ebit",
    "interest_expense": "interestExpense",
    "cash_and_equivalents": "totalCash",
    "current_assets": "totalCurrentAssets",
    "current_liabilities": "totalCurrentLiabilities",
    "total_assets": "totalAssets",
    "total_liabilities": "totalLiabilities",
    "short_term_debt": "shortLongTermDebt",
    "long_term_debt": "longTermDebt",
    "total_debt": "totalDebt",
    "operating_cash_flow": "operatingCashflow",
    "capital_expenditure": "capitalExpenditures",
    "free_cash_flow": "freeCashflow",
    "shareholders_equity": "totalStockholderEquity",
}


def build_info_projection(fields: dict[str, dict]) -> dict:
    """Optional, additive yfinance-.info-shaped projection of the
    normalized+provenanced fields above — for a future engine integration
    that isn't part of this sprint's scope. UNAVAILABLE fields are simply
    absent from the dict, exactly as the existing `info.get(...)` /
    `if value is not None` pattern across this codebase already expects."""
    info: dict = {}
    for field, info_key in _INFO_KEY_MAP.items():
        record = fields.get(field)
        if record and record.get("value") is not None:
            info[info_key] = record["value"]
    return info


def fetch_us_fundamentals_sec_edgar(symbol: str) -> dict:
    """
    The SEC EDGAR Adapter's single public entry point (mirrors
    `fetch_us_fundamentals`'s/`fetch_screener_data`'s existing shape so a
    future caller can recognize the pattern immediately).

    Returns `{"available": False, ...}` — never raises, never fabricates
    a value — if CIK resolution or the companyfacts fetch fails, per
    Fail-Soft Engineering (SSDS-006 §2) and this sprint's explicit
    "do not break existing yfinance paths" rule (this function is called
    by nothing in the existing yfinance path; it is purely additive).
    """
    sym = symbol.upper().strip()
    cik = resolve_cik(sym)
    if cik is None:
        return {
            "available": False,
            "symbol": sym,
            "source": PROVIDER_NAME,
            "adapter_version": ADAPTER_VERSION,
            "reason": "CIK not found",
        }

    facts = fetch_company_facts(cik)
    if facts is None:
        return {
            "available": False,
            "symbol": sym,
            "source": PROVIDER_NAME,
            "adapter_version": ADAPTER_VERSION,
            "cik": cik,
            "reason": "companyfacts fetch failed or returned an unexpected shape",
        }

    fields = normalize_fields(facts)
    return {
        "available": True,
        "symbol": sym,
        "source": PROVIDER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "cik": cik,
        "company_name": facts.get("entityName"),
        "fields": fields,
        "info": build_info_projection(fields),
    }
