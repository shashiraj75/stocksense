"""
AI Equity Research Analyst — Phase 1 package (Epic 008B).

Deterministic snapshot data structures, a read-only builder over already-
computed predict()-shaped dicts, and a contract validator. No production
call site exists in Phase 1: nothing under backend/services or
backend/api imports this package (enforced by regression test), and this
package imports no provider, engine, router, or I/O facility.

Explicit exports only; importing this package has no side effects beyond
loading the three submodules.
"""

from services.research_analyst.contract_validator import (
    ValidationResult,
    Violation,
    validate_snapshot,
)
from services.research_analyst.intelligence_snapshot import (
    EvidenceItem,
    EvidenceState,
    EvidenceTier,
    LiveIntelligenceSnapshot,
    SnapshotAvailability,
    SnapshotScope,
    SnapshotTimestamps,
    compute_snapshot_hash,
    snapshot_to_dict,
)
from services.research_analyst.snapshot_builder import (
    ScopeMismatchError,
    UnsupportedScopeError,
    build_live_intelligence_snapshot,
)

__all__ = [
    "EvidenceItem",
    "EvidenceState",
    "EvidenceTier",
    "LiveIntelligenceSnapshot",
    "SnapshotAvailability",
    "SnapshotScope",
    "SnapshotTimestamps",
    "ScopeMismatchError",
    "UnsupportedScopeError",
    "ValidationResult",
    "Violation",
    "build_live_intelligence_snapshot",
    "compute_snapshot_hash",
    "snapshot_to_dict",
    "validate_snapshot",
]
