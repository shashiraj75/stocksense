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


# ---------------------------------------------------------------------------
# Phase C1-D1 additions: source-registry, offline-validation and
# manifest-completeness enums. These are design/contract types only —
# nothing in this module performs network I/O, database I/O, or reads
# an environment variable. See source_registry.py / source_validators.py /
# manifest_contract.py.
# ---------------------------------------------------------------------------


class SourceId(str, Enum):
    """Stable identifiers for the 11 approved NSE source files, per the
    ratified Phase C1-C2 decision package. Never derived at runtime —
    always an explicit member of this closed set."""

    NSE_EQUITY_CURRENT = "nse_equity_current"
    NSE_ETF_CURRENT = "nse_etf_current"
    NSE_SME_CURRENT = "nse_sme_current"
    NSE_REIT_CURRENT = "nse_reit_current"
    NSE_INVIT_CURRENT = "nse_invit_current"
    NSE_PREFERENCE = "nse_preference"
    NSE_WARRANT = "nse_warrant"
    NSE_IDR = "nse_idr"
    NSE_IL_SERIES = "nse_il_series"
    NSE_SYMBOL_HISTORY = "nse_symbol_history"
    NSE_NAME_HISTORY = "nse_name_history"


class HostRole(str, Enum):
    """Per the ratified Phase C1-C2 source-host decision. `www.nseindia.com`
    is DISCOVERY_ONLY and is never assigned to any SourceRegistryEntry —
    it exists only for host-allowlist validation purposes."""

    AUTOMATED_FETCH = "automated_fetch"
    DISCOVERY_ONLY = "discovery_only"
    NOT_SELECTED = "not_selected"


class SourceCriticality(str, Enum):
    CRITICAL_REQUIRED = "critical_required"
    REQUIRED_FOR_COMPLETE_TAXONOMY = "required_for_complete_taxonomy"
    OPTIONAL_ENRICHMENT = "optional_enrichment"
    HISTORY_ONLY = "history_only"
    DISCOVERY_ONLY = "discovery_only"


class DataRole(str, Enum):
    SECURITY_LEVEL = "security_level"
    HISTORY_LEVEL = "history_level"


class HeaderMode(str, Enum):
    HEADER_PRESENT = "header_present"
    HEADERLESS = "headerless"


class AutomatedReachabilityStatus(str, Enum):
    """Per the standing Phase C1-B1/B2/B3 evidence and the Phase C1-C2
    terminology correction: NOT_VERIFIED never means "globally
    unavailable" — it means the corrected automated endpoint has not yet
    succeeded through a tested automated execution environment.

    Phase C1-A3 note: this enum's members are now reused across THREE
    distinct, separately-tracked environment scopes on
    SourceRegistryEntry (`current_environment_reachability_status`,
    `production_environment_reachability_status`, and the legacy
    `automated_reachability_status` alias) — see that dataclass's
    docstring. A bare VERIFIED on the legacy field, by itself, must
    never again be read as "approved for production automation"; only
    `production_environment_reachability_status` carries that meaning."""

    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    UNKNOWN = "unknown"


class ContentContractStatus(str, Enum):
    """Whether a source's declared header/field-count/format contract
    (expected_headers, minimum_field_count, maximum_field_count, and any
    source-specific quirk such as a trailing undeclared field) has been
    checked against a real response body, versus only ever documented
    from static/secondary evidence. Added Phase C1-A3, following the
    C1-A2 live reconnaissance that fetched and inspected all 11 approved
    URLs from a main-site-reachable environment.

    This is independent of reachability: a source can be reachable
    (HTTP 200) while its content contract remains SCHEMA_ONLY_UNVERIFIED
    if the response body was never actually inspected against the
    declared contract, and vice versa is not meaningful (content cannot
    be verified without a successful fetch, but that fetch's outcome is
    tracked separately, on the *_reachability_status fields)."""

    CONTENT_VERIFIED_LIVE = "content_verified_live"
    SCHEMA_ONLY_UNVERIFIED = "schema_only_unverified"


class PublicationBlockingStatus(str, Enum):
    BLOCKS_SNAPSHOT = "blocks_snapshot"
    BLOCKS_TAXONOMY_COMPLETE = "blocks_taxonomy_complete"
    NEVER_BLOCKS = "never_blocks"


class ValidationResultStatus(str, Enum):
    VALID = "valid"
    VALID_EMPTY = "valid_empty"
    INVALID_TRANSPORT_METADATA = "invalid_transport_metadata"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    INVALID_HEADER = "invalid_header"
    INVALID_FIELD_COUNT = "invalid_field_count"
    INVALID_ISIN = "invalid_isin"
    DUPLICATE_ISIN = "duplicate_isin"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    MALFORMED_DATE = "malformed_date"
    UNEXPECTED_NON_CSV_CONTENT = "unexpected_non_csv_content"
    CROSS_RECORD_CONFLICT = "cross_record_conflict"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"


class ConflictState(str, Enum):
    """Identity/publication conflict states, per the ratified Phase
    C1-C2 identity model. Distinct from `ClassificationStatus`
    (Phase C0) — this enum concerns cross-record/cross-source identity
    conflicts, not per-record classification confidence."""

    UNIQUE_VERIFIED = "unique_verified"
    AMBIGUOUS_CLASSIFICATION = "ambiguous_classification"
    DUPLICATE_SOURCE_RECORD = "duplicate_source_record"
    CROSS_SOURCE_ISIN_CONFLICT = "cross_source_isin_conflict"
    SYMBOL_COLLISION_DISTINCT_ISIN = "symbol_collision_distinct_isin"
    UNKNOWN_UNCLASSIFIED = "unknown_unclassified"
    STALE_SOURCE_CLASSIFICATION = "stale_source_classification"
    SOURCE_UNAVAILABLE = "source_unavailable"
    MALFORMED_REQUIRED_SOURCE = "malformed_required_source"


# States that block publication of the affected record(s), per the
# ratified Phase C1-C2 §11 audit. Never mutated at runtime.
PUBLICATION_BLOCKING_CONFLICT_STATES = frozenset(
    {
        ConflictState.CROSS_SOURCE_ISIN_CONFLICT,
        ConflictState.MALFORMED_REQUIRED_SOURCE,
        ConflictState.DUPLICATE_SOURCE_RECORD,
    }
)

# States that may publish with a warning only, never block. Explicit
# and closed — a state absent from both frozensets is a design error,
# not a "safe by default" state (see test_manifest_contract.py).
WARNING_ONLY_CONFLICT_STATES = frozenset(
    {
        ConflictState.AMBIGUOUS_CLASSIFICATION,
        ConflictState.SYMBOL_COLLISION_DISTINCT_ISIN,
        ConflictState.UNKNOWN_UNCLASSIFIED,
        ConflictState.STALE_SOURCE_CLASSIFICATION,
        ConflictState.SOURCE_UNAVAILABLE,
        ConflictState.UNIQUE_VERIFIED,
    }
)


class StaleState(str, Enum):
    """Per the ratified Phase C1-C2 §7 SME freshness thresholds:
    fresh <= 3 calendar days; stale_warning > 3 and <= 7 calendar days;
    hard_stale > 7 calendar days."""

    FRESH = "fresh"
    STALE_WARNING = "stale_warning"
    HARD_STALE = "hard_stale"


class RetrievalMethod(str, Enum):
    AUTOMATED_FETCH = "automated_fetch"
    LAST_KNOWN_GOOD = "last_known_good"
    OPERATOR_ASSISTED = "operator_assisted"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class SourceCategoryLifecycleStatus(str, Enum):
    """Per the ratified Phase C1-C2 §15: rights entitlements,
    suspension/delisting/relisting status, and any other category
    without a registered source must never be silently coerced into a
    false active/inactive classification."""

    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    NOT_COVERED_BY_CURRENT_SOURCE_SET = "not_covered_by_current_source_set"
