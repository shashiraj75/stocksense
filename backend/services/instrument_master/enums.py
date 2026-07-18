"""
Validated enums for the offline instrument-master schema (Phase C0).

Every enum here is a closed, explicit set — no free-text classification
value is ever accepted into canonical output. Values are serialized as
their lowercase string names (see canonicalize.py) so canonical JSON is
stable across Python versions and independent of enum member ordering.
"""
from enum import Enum


class InstrumentCategory(str, Enum):
    ORDINARY_COMMON_EQUITY = "ordinary_common_equity"
    SME_EQUITY = "sme_equity"
    ETF = "etf"
    REIT = "reit"
    INVIT = "invit"
    PREFERENCE_SHARE = "preference_share"
    PARTLY_PAID_EQUITY = "partly_paid_equity"
    WARRANT = "warrant"
    RIGHTS_ENTITLEMENT = "rights_entitlement"
    AUXILIARY_TRACKING_INSTRUMENT = "auxiliary_tracking_instrument"
    UNKNOWN_UNCLASSIFIED = "unknown_unclassified"


class ExchangeSeries(str, Enum):
    EQ = "EQ"
    BE = "BE"
    BZ = "BZ"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ClassificationStatus(str, Enum):
    VERIFIED = "verified"
    PROVISIONAL = "provisional"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    SHADOW_ONLY = "shadow_only"
    SURVEILLANCE_SERIES = "surveillance_series"
    INSUFFICIENT_HISTORY = "insufficient_history"
    MISSING_FUNDAMENTALS = "missing_fundamentals"
    STALE_DATA = "stale_data"
    INACTIVE = "inactive"
    UNSUPPORTED_INSTRUMENT = "unsupported_instrument"
    AMBIGUOUS_CLASSIFICATION = "ambiguous_classification"


class ActiveStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELISTED = "delisted"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class FundamentalsCoverageStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    STALE = "stale"
    UNKNOWN = "unknown"


class MarketDataStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    UNKNOWN = "unknown"


class MinimumHistoryBand(str, Enum):
    UNDER_20 = "under_20"
    BAND_20_59 = "band_20_59"
    BAND_60_125 = "band_60_125"
    BAND_126_251 = "band_126_251"
    BAND_252_PLUS = "band_252_plus"
    UNKNOWN = "unknown"


# Reason codes attached to eligibility_status when it is not ELIGIBLE.
# Closed set, extensible only by editing this module (never inferred
# free-text) — matches the enum registry established in the accepted
# Phase B Closure Report §11.
ELIGIBILITY_REASON_CODES = frozenset(
    {
        "t2t_series_be",
        "t2t_series_bz",
        "insufficient_history_momentum",
        "insufficient_history_adtv",
        "insufficient_history_squeeze",
        "fundamentals_cache_absent",
        "fundamentals_cache_stale",
        "newly_listed_no_fundamentals",
        "unclassified_instrument_category",
        "sme_platform_excluded",
        "duplicate_isin",
        "duplicate_symbol",
        "missing_isin",
        "malformed_record",
        "auxiliary_tracking_instrument_excluded",
        "confirmed_non_equity_instrument",
    }
)
