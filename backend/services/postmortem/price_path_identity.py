"""
Trade Postmortem Sprint 3A, Stage J3 — the single authoritative source
of every price-path version constant. Every module that needs "the
current version" of any price-path contract axis (evidence bundle
shape, acquisition source, source-manifest schema, symbol-normalization
policy, boundary policy, report schema, calculator rules) imports from
here — never redeclares its own literal.

Why this module exists: before Stage J3, the compatible-evidence LOOKUP
site (price_path_generation.load_generation_context's own
`source_version: str = "1.0.0"` default parameter) was a bare literal,
completely independent of price_path_acquisition.SOURCE_VERSION (the
constant every acquisition and the report/outbox identity actually used).
Both happened to read "1.0.0" and so happened to agree — but nothing
enforced that agreement. A bump to one without the other would have
silently produced a report/outbox identity requesting one version while
evidence lookup kept searching for a different one, exactly the drift
this module exists to make structurally impossible.

Deliberately has ZERO imports from any other services.postmortem.price_path_*
module — every one of those modules imports FROM here, never the
other way around. This is what avoids a circular import: this is the
one leaf all price-path version constants live at.
"""

from dataclasses import dataclass

EVIDENCE_BUNDLE_SCHEMA_VERSION = "1.0.0"
SOURCE_ID_YFINANCE_DAILY = "yfinance_daily"

# Stage J3 — bumped from 1.0.0. The final source-manifest/compatibility
# semantics (manifest-integrity hashing, symbol-normalization version,
# boundary-policy version, the complete required-field validation
# contract added across the Stage J Final Semantic Reconciliation and
# Stage J3 passes) are materially different from the original 1.0.0
# contract, which predates all of that governance. Bumping here — the
# one place every acquisition, lookup, and identity construction reads
# from — is what lets a fully-compliant 1.1.0 evidence row be acquired
# and persisted beside any old 1.0.0 row without the old row ever
# blocking, being repaired, or being mistaken for compatible.
SOURCE_VERSION = "1.1.0"

SOURCE_MANIFEST_SCHEMA_VERSION = "1.0.0"
SYMBOL_NORMALIZATION_VERSION = "1.0.0"
BOUNDARY_POLICY_VERSION = "1.0.0"

# Already bumped to 1.1.0 in an earlier Sprint 3A pass (Stage I), distinct
# from Sprint 1/2's own "1.0.0" report_schema_version — unrelated to this
# pass's SOURCE_VERSION bump, restated here only so it participates in
# the one authoritative identity object below.
PRICE_PATH_REPORT_SCHEMA_VERSION = "1.1.0"

# price_path_calculator.py's own touch/excursion classification rules —
# unchanged this pass (the observed-touch-pattern/governed-touch-order
# redesign is explicitly out of scope for Stage J3; see the ADR's
# "remaining Stage J touch-semantics scope" section).
CALCULATION_RULES_VERSION = "1.0.0"


@dataclass(frozen=True)
class PricePathSourceIdentity:
    """One immutable snapshot of every version axis a price-path
    evidence/report/outbox identity depends on. `CURRENT_PRICE_PATH_
    SOURCE_IDENTITY` below is the ONLY instance production code should
    ever construct from live constants — tests may build their own for
    a specific legacy/future scenario, but the live path never does."""

    evidence_bundle_version: str
    source_id: str
    source_version: str
    source_manifest_schema_version: str
    symbol_normalization_version: str
    boundary_policy_version: str
    report_schema_version: str
    calculation_rules_version: str


CURRENT_PRICE_PATH_SOURCE_IDENTITY = PricePathSourceIdentity(
    evidence_bundle_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
    source_id=SOURCE_ID_YFINANCE_DAILY,
    source_version=SOURCE_VERSION,
    source_manifest_schema_version=SOURCE_MANIFEST_SCHEMA_VERSION,
    symbol_normalization_version=SYMBOL_NORMALIZATION_VERSION,
    boundary_policy_version=BOUNDARY_POLICY_VERSION,
    report_schema_version=PRICE_PATH_REPORT_SCHEMA_VERSION,
    calculation_rules_version=CALCULATION_RULES_VERSION,
)
