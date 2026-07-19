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
    succeeded through a tested automated execution environment."""

    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    UNKNOWN = "unknown"


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


# ---------------------------------------------------------------------------
# Phase C2-D2 additions: consumer-approval contract enums, per the ratified
# Phase C2-D1B design correction. These are design/contract types only —
# nothing in this module performs network I/O, database I/O, reads an
# environment variable, reads the wall clock, calculates freshness, or
# derives consumer approval. See consumer_approval_contracts.py.
#
# No real SourceId has a ratified FreshnessBasis as of Phase C2-D2 (see
# consumer_approval_contracts.py module docstring) — the types in this
# section are representable, validated contracts only, not an approval
# engine. Legacy CompletenessContract fields (approved_for_screener,
# approved_for_recommendations, approved_for_daily_picks,
# approved_for_daily_picks_non_sme) are unrelated to and untouched by
# this section — see manifest_contract.py, which is not modified by
# Phase C2-D2.
# ---------------------------------------------------------------------------


class TaxonomyReadinessMode(str, Enum):
    """How much of the core instrument taxonomy (ordinary equity, ETF,
    SME) can currently be reliably classified, independent of any
    consumer's eligibility policy. See ConsumerApprovalMode for the
    separate, consumer-facing eligibility ladder this mode gates."""

    BLOCKED = "blocked"
    # The ordinary-equity source is not usable. No instrument of any
    # class can be reliably classified; no consumer approval is possible.

    EQUITY_ONLY_UNSAFE_FOR_CONSUMERS = "equity_only_unsafe_for_consumers"
    # Ordinary-equity source is usable; ETF classification is unusable.
    # Ordinary equities cannot safely be served because ETFs cannot be
    # reliably excluded from the equity-derived pool.

    EQUITY_ETF_CLASSIFIED = "equity_etf_classified"
    # Ordinary-equity and ETF sources are both usable; SME source is not
    # usable. Restricted non-SME approval may eventually be possible.

    CORE_TAXONOMY_COMPLETE = "core_taxonomy_complete"
    # Ordinary-equity, ETF and SME sources are all usable. This
    # indicates core classification confidence only — it does not
    # expand consumer eligibility beyond ordinary equity.


class ConsumerApprovalMode(str, Enum):
    """A consumer's eligibility tier. CORE_TAXONOMY_COMPLETE describes
    classification confidence, not expanded eligibility — every initial
    mode serves ordinary equity only (see consumer_approval_contracts.py)."""

    BLOCKED = "blocked"
    RESTRICTED_NON_SME = "restricted_non_sme"
    CORE_TAXONOMY_COMPLETE = "core_taxonomy_complete"


class ConsumerType(str, Enum):
    SCREENER = "screener"
    RECOMMENDATIONS = "recommendations"
    DAILY_PICKS = "daily_picks"


class InstrumentClass(str, Enum):
    """The closed set of instrument classes a ConsumerApprovalEvaluation
    can classify or serve. Distinct from Phase C0's InstrumentCategory —
    this enum is scoped to the Phase C2 consumer-approval contract only."""

    ORDINARY_EQUITY = "ordinary_equity"
    ETF = "etf"
    SME = "sme"
    REIT = "reit"
    INVIT = "invit"
    PREFERENCE_SHARE = "preference_share"
    WARRANT = "warrant"
    IDR = "idr"
    ILLIQUID_OR_RESTRICTED = "illiquid_or_restricted"
    UNKNOWN_UNCLASSIFIED = "unknown_unclassified"


class FreshnessBasis(str, Enum):
    """What source_effective_at, if present, is actually derived from.
    UNKNOWN means no trustworthy effective timestamp exists. No real
    SourceId has a ratified ACQUISITION_TIME_PROXY policy as of Phase
    C2-D2 — that value is representable here but not currently ratified
    for production use (a separately authorized future phase's decision).
    Deliberately excludes a "last known good" value: an LKG snapshot
    retains whichever basis it was originally captured under."""

    SOURCE_REPORTED_DATE = "source_reported_date"
    HTTP_LAST_MODIFIED = "http_last_modified"
    ACQUISITION_TIME_PROXY = "acquisition_time_proxy"
    UNKNOWN = "unknown"


class SourceDataOrigin(str, Enum):
    """Whether a SourceAcquisitionRecord's active data is today's
    acquisition attempt or a substituted last-known-good snapshot. Never
    overwrites the record's own freshness_basis."""

    CURRENT = "current"
    LAST_KNOWN_GOOD = "last_known_good"


class SourceErrorSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class AcquisitionResultStatus(str, Enum):
    SUCCESS = "success"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    UNEXPECTED_CONTENT_TYPE = "unexpected_content_type"
    EMPTY_UNEXPECTED = "empty_unexpected"
    NOT_ATTEMPTED = "not_attempted"


class CalibrationStatus(str, Enum):
    """Maturity of a ClassificationConflictPolicy's numerical thresholds.
    Only RATIFIED may ever gate a production consumer-integration
    decision — see ClassificationConflictPolicy's module-level docstring."""

    PROVISIONAL = "provisional"
    SHADOW_VALIDATED = "shadow_validated"
    RATIFIED = "ratified"


class SourceErrorCode(str, Enum):
    """The closed, hand-maintained set of stable error/warning codes a
    ConsumerApprovalEvaluation or SourceAcquisitionRecord may attach.
    Never generated as free text at runtime — always a member of this set."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_INVALID_STRUCTURE = "source_invalid_structure"
    SOURCE_FRESHNESS_UNKNOWN = "source_freshness_unknown"
    SOURCE_STALE_WARNING = "source_stale_warning"
    SOURCE_STALE_HARD = "source_stale_hard"
    SOURCE_FUTURE_DATED = "source_future_dated"
    SOURCE_UNEXPECTED_EMPTY = "source_unexpected_empty"
    LAST_KNOWN_GOOD_MISSING_SNAPSHOT = "last_known_good_missing_snapshot"
    LAST_KNOWN_GOOD_EXPIRED = "last_known_good_expired"
    LAST_KNOWN_GOOD_WARNING_DEADLINE_CROSSED = "last_known_good_warning_deadline_crossed"
    ACQUISITION_READINESS_CONTRADICTION = "acquisition_readiness_contradiction"
    ACQUISITION_VALIDATOR_MISMATCH = "acquisition_validator_mismatch"
    ACQUISITION_URL_MISMATCH = "acquisition_url_mismatch"
    SOURCE_ROW_COUNT_MISMATCH = "source_row_count_mismatch"
    AGGREGATE_DERIVATION_MISMATCH = "aggregate_derivation_mismatch"
    UNKNOWN_UNCLASSIFIED_INSTRUMENT_EXCLUDED = "unknown_unclassified_instrument_excluded"
    CROSS_SOURCE_ISIN_CONFLICT_ISOLATED = "cross_source_isin_conflict_isolated"
    CROSS_SOURCE_SYSTEMIC_CONFLICT = "cross_source_systemic_conflict"
    SCHEMA_OR_MAPPING_INTEGRITY_DEFECT = "schema_or_mapping_integrity_defect"
