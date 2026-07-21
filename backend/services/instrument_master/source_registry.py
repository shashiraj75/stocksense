"""
Typed, immutable NSE source registry (Phase C1-D1).

This module is a pure, offline data-and-validation module. It performs
no network I/O, no database I/O, reads no environment variable, and
imports nothing from any production application module. See
test_source_registry_no_network.py for an automated guard against that
ever regressing.

Phase C1-A3 correction (post C1-A2 live reconnaissance): every entry's
single `automated_reachability_status` field previously conflated two
distinct claims — "reachable from whatever environment last checked it"
and "approved for production automation." C1-A2 fetched and inspected
all 11 approved URLs live, from this repository's own execution
environment, but that environment is neither Railway nor GitHub
Actions. To make the scope of that evidence impossible to
misread, reachability is now tracked on three separate fields:

  - `current_environment_reachability_status`: this repository's own
    execution environment, as directly evidenced by the C1-A2
    reconnaissance (all 11 sources: VERIFIED).
  - `production_environment_reachability_status`: Railway and GitHub
    Actions specifically. C1-A2 ran in neither, so this remains
    NOT_VERIFIED for all 11 entries regardless of current-environment
    result — reachability from one environment is never treated as
    evidence for another.
  - `automated_reachability_status`: kept for backward compatibility
    with existing callers/tests. Its meaning is now pinned to
    `production_environment_reachability_status` exactly (enforced in
    `__post_init__`) — the more conservative of the two, so an existing
    caller reading only this field can never be misled into believing
    production approval where none exists.

`content_contract_status` is tracked separately again: whether the
declared header/field-count contract for a source has actually been
checked against a real response body (CONTENT_VERIFIED_LIVE, as C1-A2
did for all 11) versus only ever documented from static/secondary
evidence (SCHEMA_ONLY_UNVERIFIED). None of this implies production
ingestion approval — that remains a separate, not-yet-made decision.

Every field on every entry is fixed at module-import time from the
literals in SOURCE_REGISTRY below — there is no mechanism in this
module to override a URL, host, or any other field at runtime (no
environment variable, no config file, no constructor argument on the
public accessors). This is deliberate: the exact 11 approved URLs are
the ratified Phase C1-C2 decision, not a runtime-configurable value.

Host policy (ratified Phase C1-C2 §5, decision D-01/D-03):
  - nsearchives.nseindia.com: AUTOMATED_FETCH — the only host any
    SourceRegistryEntry.exact_url may resolve to.
  - www.nseindia.com: DISCOVERY_ONLY — human navigation/discovery only,
    never assigned to a SourceRegistryEntry, present here purely so
    validate_source_url() can classify (and reject as non-automated) a
    URL against it.
  - archives.nseindia.com: NOT_SELECTED — "Not selected for automated
    ingestion based on repeated failures from the tested local, Railway
    and GitHub Actions environments." This is not a claim that the host
    is globally dead or permanently unusable — only that it is not
    selected under the current evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .canonicalize import canonical_json_bytes, sha256_hex
from .enums import (
    AutomatedReachabilityStatus,
    ContentContractStatus,
    DataRole,
    HeaderMode,
    HostRole,
    PublicationBlockingStatus,
    SourceCriticality,
    SourceId,
)

# ---------------------------------------------------------------------------
# Host policy (offline URL-contract validation only — no transport, no
# redirect-following; see validate_source_url()).
# ---------------------------------------------------------------------------

AUTOMATED_FETCH_HOSTS = frozenset({"nsearchives.nseindia.com"})
DISCOVERY_ONLY_HOSTS = frozenset({"www.nseindia.com"})
NOT_SELECTED_HOSTS = frozenset({"archives.nseindia.com"})

ARCHIVES_HOST_STATUS_NOTE = (
    "Not selected for automated ingestion based on repeated failures from the "
    "tested local, Railway and GitHub Actions environments."
)

# All hosts this module has any opinion about, for lookalike/suffix-confusion
# detection (e.g. "nsearchives.nseindia.com.evil.example" must not match by
# substring — only an exact host-string match against one of these three
# frozensets is ever accepted).
_KNOWN_HOSTS = AUTOMATED_FETCH_HOSTS | DISCOVERY_ONLY_HOSTS | NOT_SELECTED_HOSTS


class SourceUrlValidationError(ValueError):
    """Raised by validate_source_url() for any URL-contract violation.
    Never raised by network activity — this module never performs any."""


# The 11 approved source URLs, declared independently of SOURCE_REGISTRY
# below (a module-level assertion after SOURCE_REGISTRY confirms the two
# stay in sync). Per the Phase C1-D3 correction: for registry
# construction, the strongest acceptable rule is exact membership in
# this set — no case, whitespace, slash, or percent-encoding
# normalization may ever turn a different string into an approved URL.
APPROVED_SOURCE_URLS = frozenset(
    {
        "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
        "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
        "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
        "https://nsearchives.nseindia.com/content/equities/REITS_L.csv",
        "https://nsearchives.nseindia.com/content/equities/INVITS_L.csv",
        "https://nsearchives.nseindia.com/content/equities/PREF.csv",
        "https://nsearchives.nseindia.com/content/equities/WARRANT.csv",
        "https://nsearchives.nseindia.com/content/equities/IDR_W9.csv",
        "https://nsearchives.nseindia.com/content/equities/eq_ilseclist.csv",
        "https://nsearchives.nseindia.com/content/equities/symbolchange.csv",
        "https://nsearchives.nseindia.com/content/equities/namechange.csv",
    }
)


def validate_exact_approved_url(url: str) -> None:
    """The strongest acceptable rule for registry construction and for
    validating any caller-supplied URL that claims to be one of the 11
    approved sources: exact, byte-for-byte membership in
    APPROVED_SOURCE_URLS. No normalization of any kind is applied before
    the comparison — a URL that would resolve to an approved URL only
    after case-folding, whitespace-stripping, slash-collapsing, or
    percent-decoding is rejected exactly as strictly as an unrelated URL."""
    if url not in APPROVED_SOURCE_URLS:
        raise SourceUrlValidationError(
            f"URL is not an exact match for any of the {len(APPROVED_SOURCE_URLS)} approved source URLs: {url!r}"
        )


# Characters this module treats as never permitted anywhere in a URL
# string, checked before any splitting occurs — whitespace and control
# characters have no legitimate place in any of the 11 approved URLs,
# and their presence is rejected outright regardless of position.
_DISALLOWED_RAW_CHARS = frozenset(" \t\r\n\x00")
_DISALLOWED_CONTROL_RANGE = range(0x00, 0x20)  # all ASCII control chars, plus...
_DEL_CHAR = "\x7f"


def _contains_disallowed_raw_chars(url: str) -> bool:
    if any(ch in _DISALLOWED_RAW_CHARS for ch in url):
        return True
    if any(ord(ch) in _DISALLOWED_CONTROL_RANGE for ch in url):
        return True
    if _DEL_CHAR in url:
        return True
    return False


def _contains_percent_encoding(url: str) -> bool:
    # Phase C1-D5 correction: this module never decodes percent-encoding
    # before delimiter detection (it deliberately never imports
    # urllib.parse — see _split_url()'s docstring), so a percent-encoded
    # '?' (%3F), '#' (%23), '/' (%2F), '.' (%2E), '\' (%5C), or any other
    # encoded character could previously hide a real delimiter from the
    # scheme/host/query/fragment/path checks below. None of the 11
    # approved automated NSE source URLs contains a percent-encoded
    # character in their exact literal form (confirmed against
    # APPROVED_SOURCE_URLS), so the safest offline policy — simpler and
    # safer than selectively decoding only the delimiters this module
    # currently knows about — is to reject any '%' anywhere in an
    # automated source URL outright. Approved URLs must always be
    # supplied in their exact literal, unencoded representation; exact
    # approved-URL matching (validate_exact_approved_url()) remains the
    # primary gate regardless.
    return "%" in url


def _split_url(url: str) -> tuple[str, str, str, str, str]:
    """Minimal, hand-rolled URL splitter: (scheme, netloc, path, query, fragment).

    Deliberately NOT `urllib.parse` — the existing, unmodified Phase C0
    safety test (test_instrument_master_safety.py) blocks the entire
    `urllib` top-level import anywhere under instrument_master/ as a
    bright-line rule, regardless of submodule, so this module never
    imports it even though urllib.parse itself performs no I/O."""
    if "://" not in url:
        raise SourceUrlValidationError(f"URL missing scheme: {url!r}")
    scheme, _, rest = url.partition("://")
    rest, _, fragment = rest.partition("#")
    rest, _, query = rest.partition("?")
    netloc, sep, path = rest.partition("/")
    path = (sep + path) if sep else ""
    return scheme, netloc, path, query, fragment


_TRAVERSAL_SEGMENTS = frozenset({".", "..", "%2e", "%2E", "%2e%2e", "%2E%2E", "%2e.", ".%2e", "%2e%2E", "%2E%2e"})


def _path_contains_traversal(path: str) -> bool:
    for segment in path.split("/"):
        if segment.lower().replace("%2e", ".") in (".", ".."):
            return True
    return False


def validate_source_url(url: str, *, allow_hosts: frozenset[str] = AUTOMATED_FETCH_HOSTS) -> None:
    """Purely offline URL-contract validation. Never opens a socket,
    never follows a redirect (there is nothing to follow — no request is
    ever made). Raises SourceUrlValidationError on any violation.

    This is a general-purpose structural parser for any URL a caller
    might supply (e.g. a future operator-assisted submission) — it is
    NOT the sole gate for registry construction, which additionally
    requires exact membership via validate_exact_approved_url().

    Rejects: HTTP (non-HTTPS); embedded credentials (user:pass@host);
    unexpected/explicit ports; IP-address hosts; lookalike/suffix-confusion
    hosts; arbitrary subdomains of an allowed host; query parameters or
    URL fragments; leading/trailing/embedded whitespace and control
    characters anywhere in the URL; path-traversal segments (`.`/`..`);
    backslashes; duplicate path separators; any percent-encoded
    character ('%' anywhere), since this module never decodes
    percent-encoding and a hidden encoded delimiter (%3F, %23, %2F, ...)
    would otherwise bypass the checks above.
    """
    if _contains_disallowed_raw_chars(url):
        raise SourceUrlValidationError("whitespace or control character rejected anywhere in the URL")
    if _contains_percent_encoding(url):
        raise SourceUrlValidationError(
            "percent-encoding ('%') is rejected anywhere in an automated source URL — "
            "approved URLs must use their exact literal, unencoded representation"
        )
    if "\\" in url:
        raise SourceUrlValidationError("backslash rejected — URLs must use forward slashes only")

    scheme, netloc, path, query, fragment = _split_url(url)

    if scheme != "https":
        raise SourceUrlValidationError(f"non-https scheme rejected: {scheme!r}")

    if "@" in netloc:
        raise SourceUrlValidationError("embedded credentials in URL are rejected")

    if netloc.startswith("["):
        # Bracketed IPv6 literal — reject outright below via the
        # "contains ':'" check; extract just enough to report a host.
        host = netloc
        port_str = ""
    elif ":" in netloc:
        host, _, port_str = netloc.partition(":")
    else:
        host, port_str = netloc, ""

    host = host.lower()
    if not host:
        raise SourceUrlValidationError("URL has no parseable host")

    if port_str:
        raise SourceUrlValidationError(f"unexpected explicit port rejected: {port_str!r}")

    # Reject IP-literal hosts (IPv4-shaped or bracketed IPv6) outright —
    # every approved source is a named host, never a bare IP.
    octets = host.split(".")
    if len(octets) == 4 and all(o.isdigit() for o in octets):
        raise SourceUrlValidationError(f"IP-address host rejected: {host!r}")
    if host.startswith("[") or ":" in host:
        raise SourceUrlValidationError(f"IP-address host rejected: {host!r}")

    # Exact match only — no substring/suffix matching. This rejects both
    # lookalike hosts ("nsearchives.nseindia.com.evil.example") and
    # arbitrary subdomains ("api.nsearchives.nseindia.com") of an
    # otherwise-known host, since neither is an exact member of the set.
    if host not in allow_hosts:
        if host in _KNOWN_HOSTS:
            raise SourceUrlValidationError(
                f"host {host!r} is a known host but not permitted for this operation "
                f"(allowed: {sorted(allow_hosts)})"
            )
        raise SourceUrlValidationError(f"host {host!r} is not on any known allowlist")

    if query:
        raise SourceUrlValidationError("query parameters are rejected (no approved contract uses them)")
    if fragment:
        raise SourceUrlValidationError("URL fragments are rejected (no approved contract uses them)")

    # Path validation — no approved source contract uses path traversal
    # or duplicate separators; neither is ever accepted, regardless of
    # whether the resulting normalized path might coincidentally match
    # an approved one. This module never normalizes a path to compare
    # it — only exact, unmodified equality (validate_exact_approved_url)
    # is ever treated as a match.
    if _path_contains_traversal(path):
        raise SourceUrlValidationError("path-traversal segment rejected")
    if "//" in path:
        raise SourceUrlValidationError("duplicate path separators are rejected (no approved contract uses them)")


@dataclass(frozen=True)
class SourceRegistryEntry:
    """One immutable registry record. See module docstring — every field
    is a fixed literal from SOURCE_REGISTRY, never runtime-overridable."""

    source_id: SourceId
    category: str
    exact_url: str
    publisher: str
    host_role: HostRole
    data_role: DataRole
    criticality: SourceCriticality
    required_for_complete_taxonomy: bool
    primary_identity_field: str
    secondary_identity_fields: tuple[str, ...]
    header_mode: HeaderMode
    expected_headers: tuple[str, ...]
    minimum_field_count: int
    maximum_field_count: int
    valid_empty_allowed: bool
    parser_contract_id: str
    validator_contract_id: str
    automated_reachability_status: AutomatedReachabilityStatus
    content_contract_status: ContentContractStatus
    current_environment_reachability_status: AutomatedReachabilityStatus
    production_environment_reachability_status: AutomatedReachabilityStatus
    fallback_policy: str
    freshness_warning_days: int
    hard_stale_days: int
    publication_blocking_status: PublicationBlockingStatus
    grain: str = "PER_SECURITY"
    unresolved_risks: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Fail fast at import time (module-level SOURCE_REGISTRY construction)
        # rather than at first use — a malformed literal in this file is a
        # code defect, not a runtime/data condition. Per the Phase C1-D3
        # correction, the strongest rule (exact membership in
        # APPROVED_SOURCE_URLS) is the primary gate; the general
        # structural parser is also run as defense-in-depth.
        validate_exact_approved_url(self.exact_url)
        validate_source_url(self.exact_url, allow_hosts=AUTOMATED_FETCH_HOSTS)
        if self.minimum_field_count > self.maximum_field_count:
            raise ValueError(
                f"{self.source_id}: minimum_field_count > maximum_field_count"
            )
        if self.header_mode is HeaderMode.HEADERLESS and self.expected_headers:
            raise ValueError(
                f"{self.source_id}: headerless source must not declare expected_headers"
            )
        if self.header_mode is HeaderMode.HEADER_PRESENT and not self.expected_headers:
            raise ValueError(
                f"{self.source_id}: header-present source must declare expected_headers"
            )
        # Phase C1-A3: the legacy `automated_reachability_status` field is
        # kept only as an alias for the more conservative of the two
        # environment-scoped fields — production. It must never be able
        # to drift from `production_environment_reachability_status` and
        # independently claim something more optimistic.
        if self.automated_reachability_status != self.production_environment_reachability_status:
            raise ValueError(
                f"{self.source_id}: automated_reachability_status "
                f"({self.automated_reachability_status!r}) must exactly equal "
                f"production_environment_reachability_status "
                f"({self.production_environment_reachability_status!r}) — "
                "the legacy field is a pinned alias, never an independent claim"
            )


SOURCE_REGISTRY: tuple[SourceRegistryEntry, ...] = (
    SourceRegistryEntry(
        source_id=SourceId.NSE_EQUITY_CURRENT,
        category="ordinary_current_equity_segment",
        exact_url="https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.CRITICAL_REQUIRED,
        required_for_complete_taxonomy=True,
        primary_identity_field="ISIN NUMBER",
        secondary_identity_fields=("SYMBOL", "SERIES"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=(
            "SYMBOL",
            "NAME OF COMPANY",
            "SERIES",
            "DATE OF LISTING",
            "PAID UP VALUE",
            "MARKET LOT",
            "ISIN NUMBER",
            "FACE VALUE",
        ),
        minimum_field_count=8,
        maximum_field_count=8,
        valid_empty_allowed=False,
        parser_contract_id="equity_current_v1",
        validator_contract_id="equity_current_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=1,
        hard_stale_days=3,
        publication_blocking_status=PublicationBlockingStatus.BLOCKS_SNAPSHOT,
        unresolved_risks=(),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_ETF_CURRENT,
        category="etf",
        exact_url="https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.REQUIRED_FOR_COMPLETE_TAXONOMY,
        required_for_complete_taxonomy=True,
        primary_identity_field="ISINNumber",
        secondary_identity_fields=("Symbol", "Underlying"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=(
            "Symbol",
            "Underlying",
            "SecurityName",
            "DateofListing",
            "MarketLot",
            "ISINNumber",
            "FaceValue",
        ),
        minimum_field_count=7,
        maximum_field_count=7,
        valid_empty_allowed=False,
        parser_contract_id="etf_current_v1",
        validator_contract_id="etf_current_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=1,
        hard_stale_days=3,
        publication_blocking_status=PublicationBlockingStatus.BLOCKS_TAXONOMY_COMPLETE,
        unresolved_risks=(),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_SME_CURRENT,
        category="sme_equity",
        exact_url="https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
        publisher="NSE (NSE Emerge platform)",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.REQUIRED_FOR_COMPLETE_TAXONOMY,
        required_for_complete_taxonomy=True,
        primary_identity_field="ISIN_NUMBER",
        secondary_identity_fields=("SYMBOL", "SERIES"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=(
            "SYMBOL",
            "NAME_OF_COMPANY",
            "SERIES",
            "DATE_OF_LISTING",
            "PAID_UP_VALUE",
            "ISIN_NUMBER",
            "FACE_VALUE",
        ),
        minimum_field_count=7,
        # Phase C1-A2 live correction: every real SME_EQUITY_L.csv row (and
        # the header itself) carries a trailing comma after FACE_VALUE,
        # producing an 8th, always-empty raw CSV field — confirmed against
        # the live response body, not merely inferred. maximum_field_count
        # reflects that observed 8-wide raw shape; validate_sme() tolerates
        # exactly one optional trailing field and requires it to be empty
        # (see source_validators.py's optional_trailing_empty_field), never
        # treating it as a real business column. The declared 7-column
        # expected_headers/semantic schema is unchanged.
        maximum_field_count=8,
        valid_empty_allowed=False,
        parser_contract_id="sme_current_v1",
        validator_contract_id="sme_current_v1",
        # Per the ratified Phase C1-C2 terminology correction: NOT_VERIFIED
        # means the corrected endpoint has not yet succeeded through either
        # tested automated execution environment (Railway or GitHub Actions,
        # 3 attempts each) — it does NOT mean globally unavailable. Phase
        # C1-A2 has since retrieved this exact URL successfully via a plain
        # unauthenticated HTTPS GET, with no cookies or session priming,
        # from this repository's own execution environment — see
        # current_environment_reachability_status below. Railway/GitHub
        # Actions specifically remain untested; automated_reachability_status
        # (this field) stays pinned to production_environment_reachability_status
        # and is therefore unchanged at NOT_VERIFIED.
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good_plus_operator_assisted_refresh",
        freshness_warning_days=3,
        hard_stale_days=7,
        publication_blocking_status=PublicationBlockingStatus.BLOCKS_TAXONOMY_COMPLETE,
        unresolved_risks=(
            # "automated_reachability_mechanism_unknown" removed: C1-A2
            # confirmed the mechanism is simply a plain HTTPS GET, same as
            # every other nsearchives.nseindia.com source — no special
            # cookie/session/header requirement was found.
            "grain_may_differ_from_other_current_sources_if_platform_specific_rows_appear",
            "production_automation_reachability_from_railway_and_github_actions_still_untested",
        ),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_REIT_CURRENT,
        category="reit",
        exact_url="https://nsearchives.nseindia.com/content/equities/REITS_L.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.OPTIONAL_ENRICHMENT,
        required_for_complete_taxonomy=False,
        primary_identity_field="ISIN NUMBER",
        secondary_identity_fields=("SYMBOL", "SERIES"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=(
            "SYMBOL",
            "NAME OF COMPANY",
            "SERIES",
            "DATE OF LISTING",
            "PAID UP VALUE",
            "MARKET LOT",
            "ISIN NUMBER",
            "FACE VALUE",
        ),
        minimum_field_count=8,
        maximum_field_count=8,
        valid_empty_allowed=False,
        parser_contract_id="reit_invit_v1",
        validator_contract_id="reit_invit_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=30,
        hard_stale_days=90,
        publication_blocking_status=PublicationBlockingStatus.NEVER_BLOCKS,
        # Phase C1-A2 live confirmation: the real response body is exactly
        # 3 data rows + 1 blank row + 1 "Note: the Market lot is updated..."
        # footnote row — matching this contract's footnote_filter handling
        # (see source_validators.py's _is_known_footnote_row) exactly.
        unresolved_risks=("known_trailing_footnote_row_requires_isin_format_filter",),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_INVIT_CURRENT,
        category="invit",
        exact_url="https://nsearchives.nseindia.com/content/equities/INVITS_L.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.OPTIONAL_ENRICHMENT,
        required_for_complete_taxonomy=False,
        primary_identity_field="ISIN NUMBER",
        secondary_identity_fields=("SYMBOL", "SERIES"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=(
            "SYMBOL",
            "NAME OF COMPANY",
            "SERIES",
            "DATE OF LISTING",
            "PAID UP VALUE",
            "MARKET LOT",
            "ISIN NUMBER",
            "FACE VALUE",
        ),
        minimum_field_count=8,
        maximum_field_count=8,
        valid_empty_allowed=False,
        parser_contract_id="reit_invit_v1",
        validator_contract_id="reit_invit_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=30,
        hard_stale_days=90,
        publication_blocking_status=PublicationBlockingStatus.NEVER_BLOCKS,
        # Phase C1-A2 live confirmation: same 3-data-row + blank + footnote
        # shape as REITS_L.csv (see above).
        unresolved_risks=("known_trailing_footnote_row_requires_isin_format_filter",),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_PREFERENCE,
        category="preference_share",
        exact_url="https://nsearchives.nseindia.com/content/equities/PREF.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.OPTIONAL_ENRICHMENT,
        required_for_complete_taxonomy=False,
        # Phase C1-A2 live confirmation: a single symbol (JSWSTEEL,
        # observed) can legitimately appear across multiple rows sharing
        # the SAME trailing ISIN, differing only by REDEMPTION DATE (one
        # row per redemption tranche of the same preference-share
        # issuance) — see `grain` below. Identity must therefore be the
        # trailing ISIN combined with REDEMPTION DATE and CONVERSION DATE,
        # never the trailing ISIN alone.
        primary_identity_field="trailing_undeclared_isin_field+REDEMPTION_DATE+CONVERSION_DATE",
        secondary_identity_fields=("SYMBOL", "SERIES"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=(
            "SYMBOL",
            "NAME OF COMPANY",
            "SERIES",
            "SECURITY NAME",
            "FACE VALUE",
            "PAID UP VALUE",
            "MKT LOT",
            "DIVIDEND %",
            "DATE OF LISTING",
            "DATE OF ALLOTMENT",
            "REDEMPTION DATE",
            "REDEMPTION AMT",
            "CONVERSION DATE",
            "CONVERSION AMT",
        ),
        minimum_field_count=14,
        # Declared header has 14 fields; every valid data row has 15 (the
        # trailing undeclared ISIN) — this is the confirmed, real source
        # defect (Phase C1-A3). maximum_field_count reflects DATA rows,
        # not the header row itself.
        maximum_field_count=15,
        valid_empty_allowed=False,
        parser_contract_id="pref_trailing_isin_v1",
        validator_contract_id="pref_trailing_isin_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=180,
        hard_stale_days=365,
        publication_blocking_status=PublicationBlockingStatus.NEVER_BLOCKS,
        # Phase C1-A2 resolved this grain question with live evidence: 4
        # real rows, all symbol JSWSTEEL, all sharing ISIN INE019A04016,
        # each with a distinct REDEMPTION DATE — confirms per-tranche, not
        # per-security. validate_preference() must never collapse these
        # into one record by ISIN/symbol alone (see source_validators.py).
        grain="PER_REDEMPTION_TRANCHE",
        unresolved_risks=(),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_WARRANT,
        category="warrant",
        exact_url="https://nsearchives.nseindia.com/content/equities/WARRANT.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.OPTIONAL_ENRICHMENT,
        required_for_complete_taxonomy=False,
        # Phase C1-A2 live confirmation: the trailing undeclared field is
        # explicitly an ISIN (validate_warrant() enforces strict ISO 6166
        # shape/check-digit on it, not merely "any extra column").
        primary_identity_field="trailing_undeclared_isin_field",
        secondary_identity_fields=("SYMBOL", "SERIES"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=(
            "SYMBOL",
            "NAME OF COMPANY",
            "SERIES",
            "DATE OF LISTING",
            "MARKET LOT",
        ),
        minimum_field_count=5,
        # Declared header has 5 fields; every valid data row has 6 (the
        # trailing undeclared ISIN) — same confirmed defect pattern as
        # PREF.csv, independently confirmed and independently contracted
        # (never a generic "extra column" rule — see source_validators.py).
        maximum_field_count=6,
        valid_empty_allowed=False,
        parser_contract_id="warrant_trailing_isin_v1",
        validator_contract_id="warrant_trailing_isin_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=180,
        hard_stale_days=365,
        publication_blocking_status=PublicationBlockingStatus.NEVER_BLOCKS,
        unresolved_risks=(),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_IDR,
        category="idr",
        exact_url="https://nsearchives.nseindia.com/content/equities/IDR_W9.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.OPTIONAL_ENRICHMENT,
        required_for_complete_taxonomy=False,
        primary_identity_field="ISIN NUMBER",
        secondary_identity_fields=("SYMBOL", "SERIES"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=(
            "SYMBOL",
            "NAME OF COMPANY",
            "SERIES",
            "DATE OF LISTING",
            "PAID UP VALUE",
            "MARKET LOT",
            "ISIN NUMBER",
            "FACE VALUE*",
        ),
        minimum_field_count=8,
        maximum_field_count=8,
        valid_empty_allowed=False,
        parser_contract_id="idr_v1",
        validator_contract_id="idr_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=180,
        hard_stale_days=365,
        publication_blocking_status=PublicationBlockingStatus.NEVER_BLOCKS,
        # Phase C1-A2 live confirmation: exactly 1 real IDR row (STAN /
        # Standard Chartered PLC) + 2 blank rows + 1 "*"-prefixed footnote
        # row, matching this contract's footnote_filter handling exactly.
        unresolved_risks=("file_contains_footnote_blank_line_structure_beyond_valid_rows",),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_IL_SERIES,
        category="illiquid_securities",
        exact_url="https://nsearchives.nseindia.com/content/equities/eq_ilseclist.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.SECURITY_LEVEL,
        criticality=SourceCriticality.OPTIONAL_ENRICHMENT,
        required_for_complete_taxonomy=False,
        primary_identity_field="n/a_header_only_to_date",
        secondary_identity_fields=("Symbol", "Se"),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=("Symbol", "Se", "Sec Name"),
        minimum_field_count=3,
        maximum_field_count=3,
        valid_empty_allowed=True,
        parser_contract_id="il_series_v1",
        validator_contract_id="il_series_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=30,
        hard_stale_days=90,
        publication_blocking_status=PublicationBlockingStatus.NEVER_BLOCKS,
        # Phase C1-A2 live confirmation: the real response body was
        # header-only (zero illiquid securities currently listed) and
        # matched IL_SERIES_VALID_EMPTY_RAW_BYTES exactly — a correctly
        # empty result, not a source failure.
        unresolved_risks=(),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_SYMBOL_HISTORY,
        category="symbol_change_history",
        exact_url="https://nsearchives.nseindia.com/content/equities/symbolchange.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.HISTORY_LEVEL,
        criticality=SourceCriticality.HISTORY_ONLY,
        required_for_complete_taxonomy=False,
        primary_identity_field="none_no_isin_in_this_source",
        # Phase C1-A3 correction: the live response body's real column
        # order is (company_or_scheme_name, old_symbol, new_symbol,
        # effective_date) — NOT (old_symbol, new_symbol, company_name,
        # date) as previously declared here. See
        # source_validators.py's SymbolChangeRecord/parse_symbol_change_row
        # for the single, named-field source of truth this metadata now
        # mirrors; validate_symbol_history() uses that same function
        # internally, so this ordering cannot silently drift from the
        # actual parser again.
        secondary_identity_fields=("company_or_scheme_name", "old_symbol", "new_symbol", "effective_date"),
        header_mode=HeaderMode.HEADERLESS,
        expected_headers=(),
        minimum_field_count=4,
        maximum_field_count=4,
        valid_empty_allowed=False,
        parser_contract_id="symbol_history_v1",
        validator_contract_id="symbol_history_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=7,
        hard_stale_days=30,
        publication_blocking_status=PublicationBlockingStatus.NEVER_BLOCKS,
        grain="PER_HISTORICAL_EVENT",
        unresolved_risks=(),
    ),
    SourceRegistryEntry(
        source_id=SourceId.NSE_NAME_HISTORY,
        category="company_name_change_history",
        exact_url="https://nsearchives.nseindia.com/content/equities/namechange.csv",
        publisher="NSE",
        host_role=HostRole.AUTOMATED_FETCH,
        data_role=DataRole.HISTORY_LEVEL,
        criticality=SourceCriticality.HISTORY_ONLY,
        required_for_complete_taxonomy=False,
        primary_identity_field="none_no_isin",
        secondary_identity_fields=("NCH_SYMBOL",),
        header_mode=HeaderMode.HEADER_PRESENT,
        expected_headers=("NCH_SYMBOL", "NCH_PREV_NAME", "NCH_NEW_NAME", "NCH_DT"),
        minimum_field_count=4,
        maximum_field_count=4,
        valid_empty_allowed=False,
        parser_contract_id="name_history_v1",
        validator_contract_id="name_history_v1",
        automated_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        content_contract_status=ContentContractStatus.CONTENT_VERIFIED_LIVE,
        current_environment_reachability_status=AutomatedReachabilityStatus.VERIFIED,
        production_environment_reachability_status=AutomatedReachabilityStatus.NOT_VERIFIED,
        fallback_policy="last_known_good",
        freshness_warning_days=7,
        hard_stale_days=30,
        publication_blocking_status=PublicationBlockingStatus.NEVER_BLOCKS,
        grain="PER_HISTORICAL_EVENT",
        # Phase C1-A2 live confirmation: exact-duplicate rows (e.g. BCG,
        # CHENNPETRO, DHANILOANS, DHANIPP, INCRED) were directly observed
        # in the real response body — matches validate_name_history()'s
        # existing deterministic exact-duplicate handling.
        unresolved_risks=("known_exact_duplicate_rows_present_in_source",),
    ),
)

_REGISTRY_BY_ID: dict[SourceId, SourceRegistryEntry] = {
    entry.source_id: entry for entry in SOURCE_REGISTRY
}

if len(_REGISTRY_BY_ID) != len(SOURCE_REGISTRY):
    raise RuntimeError("duplicate source_id detected in SOURCE_REGISTRY literal")

if set(_REGISTRY_BY_ID) != set(SourceId):
    raise RuntimeError(
        "SOURCE_REGISTRY does not contain exactly one entry per SourceId member"
    )

if {entry.exact_url for entry in SOURCE_REGISTRY} != APPROVED_SOURCE_URLS:
    raise RuntimeError(
        "SOURCE_REGISTRY's exact_url set does not match APPROVED_SOURCE_URLS "
        "— these two independently-declared literals must stay in sync"
    )


def registry_by_id(source_id: SourceId) -> SourceRegistryEntry:
    return _REGISTRY_BY_ID[source_id]


def all_entries() -> tuple[SourceRegistryEntry, ...]:
    """Returns entries in a fixed, deterministic order (SourceId
    declaration order) — never dict-iteration order, which is
    insertion-order-dependent and not a documented ordering contract."""
    return tuple(_REGISTRY_BY_ID[sid] for sid in SourceId)


def _entry_to_canonical_dict(entry: SourceRegistryEntry) -> dict:
    return {
        "source_id": entry.source_id.value,
        "category": entry.category,
        "exact_url": entry.exact_url,
        "publisher": entry.publisher,
        "host_role": entry.host_role.value,
        "data_role": entry.data_role.value,
        "criticality": entry.criticality.value,
        "required_for_complete_taxonomy": entry.required_for_complete_taxonomy,
        "primary_identity_field": entry.primary_identity_field,
        "secondary_identity_fields": list(entry.secondary_identity_fields),
        "header_mode": entry.header_mode.value,
        "expected_headers": list(entry.expected_headers),
        "minimum_field_count": entry.minimum_field_count,
        "maximum_field_count": entry.maximum_field_count,
        "valid_empty_allowed": entry.valid_empty_allowed,
        "parser_contract_id": entry.parser_contract_id,
        "validator_contract_id": entry.validator_contract_id,
        "automated_reachability_status": entry.automated_reachability_status.value,
        "content_contract_status": entry.content_contract_status.value,
        "current_environment_reachability_status": entry.current_environment_reachability_status.value,
        "production_environment_reachability_status": entry.production_environment_reachability_status.value,
        "fallback_policy": entry.fallback_policy,
        "freshness_warning_days": entry.freshness_warning_days,
        "hard_stale_days": entry.hard_stale_days,
        "publication_blocking_status": entry.publication_blocking_status.value,
        "grain": entry.grain,
        "unresolved_risks": list(entry.unresolved_risks),
    }


def canonical_registry_bytes() -> bytes:
    """Deterministic serialization of the entire registry: same code ->
    byte-identical output, every time, on every machine. Reuses the
    Phase C0 canonical_json_bytes (sorted keys, fixed separators, UTF-8,
    trailing newline) rather than duplicating serialization logic."""
    return canonical_json_bytes(
        {"source_registry": [_entry_to_canonical_dict(e) for e in all_entries()]}
    )


def canonical_registry_hash() -> str:
    return sha256_hex(canonical_registry_bytes())
