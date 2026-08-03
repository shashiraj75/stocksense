"""
Wave C traceability validator — Stage T. Reuses the same discipline as
test_wave_b_traceability_validator.py: cross-checks
wave_c_traceability_manifest.json against REAL pytest collection
(genuine subprocess collection, never a stale/trusted text file) and
rejects the failure modes Stage T's governing prompt explicitly lists
(missing mandatory requirements, duplicate IDs, missing implementation/
test paths, nonexistent exact tests, vague/wildcard evidence,
unsupported proof levels, executable requirements supported only by
prose, unclassified requirements).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS_UNIT_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _TESTS_UNIT_DIR.parents[1]
_REPO_ROOT = _BACKEND_ROOT.parent
_MANIFEST_PATH = _TESTS_UNIT_DIR / "wave_c_traceability_manifest.json"

_ALLOWED_PROOF_LEVELS = {
    "unit", "endpoint", "PostgreSQL", "frontend component", "typecheck",
    "production build", "browser smoke", "documentation/runbook",
}
_ALLOWED_CLASSIFICATIONS = {"BLOCKING", "NONBLOCKING", "CLOSED", "NOT APPLICABLE"}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_MANDATORY_IDS = (
    [f"WC-K-{n:02d}" for n in range(1, 16)]
    + [f"WC-N-{n:02d}" for n in range(1, 13)]
    + [f"WC-O-{n:02d}" for n in range(1, 11)]
)


def _load_manifest():
    return json.loads(_MANIFEST_PATH.read_text())


def _collected_backend_node_ids():
    """Genuine subprocess pytest --collect-only over exactly the backend
    test files this manifest cites — never a parse of a stale text
    file, matching the Wave B validator's own discipline."""
    files = sorted({
        node.split("::", 1)[0]
        for req in _load_manifest()["requirements"]
        for node in req.get("nodes", [])
        if node.startswith("tests/") and "::" in node
    })
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *files],
        cwd=_BACKEND_ROOT, capture_output=True, text=True, timeout=120,
    )
    node_ids = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" in line and line.startswith("tests/"):
            node_ids.add(line)
    return node_ids, result.stdout


@pytest.fixture(scope="module")
def collected_nodes():
    node_ids, _ = _collected_backend_node_ids()
    return node_ids


def test_manifest_loads_and_has_requirements():
    manifest = _load_manifest()
    assert isinstance(manifest.get("requirements"), list) and len(manifest["requirements"]) > 0


def test_no_duplicate_requirement_ids():
    manifest = _load_manifest()
    ids = [r["id"] for r in manifest["requirements"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate requirement IDs: {dupes}"


def test_every_mandatory_requirement_id_is_present():
    manifest = _load_manifest()
    ids = {r["id"] for r in manifest["requirements"]}
    missing = [i for i in _MANDATORY_IDS if i not in ids]
    assert not missing, f"missing mandatory Wave C requirement IDs: {missing}"


def test_no_unexpected_requirement_ids_beyond_the_mandatory_set():
    """Catches a typo'd ID silently passing as a extra/unrecognized
    requirement instead of the mandatory one it was meant to satisfy."""
    manifest = _load_manifest()
    ids = {r["id"] for r in manifest["requirements"]}
    unexpected = ids - set(_MANDATORY_IDS)
    assert not unexpected, f"requirement IDs not in the mandatory Wave C set: {unexpected}"


def test_every_requirement_has_a_classification():
    manifest = _load_manifest()
    bad = [r["id"] for r in manifest["requirements"] if r.get("classification") not in _ALLOWED_CLASSIFICATIONS]
    assert not bad, f"requirements with a missing/invalid classification: {bad}"


def test_every_requirement_has_an_allowed_proof_level():
    manifest = _load_manifest()
    bad = [(r["id"], r.get("proof_level")) for r in manifest["requirements"] if r.get("proof_level") not in _ALLOWED_PROOF_LEVELS]
    assert not bad, f"requirements with an unsupported proof_level: {bad}"


def test_every_requirement_has_a_non_empty_implementation_and_behavior_statement():
    manifest = _load_manifest()
    bad = []
    for r in manifest["requirements"]:
        impl = r.get("implementation", "")
        behavior = r.get("expected_behavior", "")
        if not impl.strip() or not behavior.strip():
            bad.append(r["id"])
    assert not bad, f"requirements missing an implementation path or expected_behavior statement: {bad}"


def test_no_vague_or_wildcard_evidence_nodes():
    """Rejects a node citation that is a wildcard/glob or an obviously
    non-specific placeholder — every cited node must be an exact test
    identifier or an exact documentation reference, never '*' or 'TBD'."""
    manifest = _load_manifest()
    bad = []
    for r in manifest["requirements"]:
        for node in r.get("nodes", []):
            if "*" in node or node.strip().upper() in {"TBD", "TODO", "N/A"}:
                bad.append((r["id"], node))
    assert not bad, f"vague/wildcard evidence nodes: {bad}"


def test_postgresql_and_unit_proof_levels_require_at_least_one_node():
    """An executable requirement (unit/endpoint/PostgreSQL) must be
    backed by at least one real test node — never prose alone. A
    documentation/runbook or browser-smoke requirement is exempt (its
    evidence is the document/manual verification itself, already
    recorded in `implementation`)."""
    manifest = _load_manifest()
    executable_levels = {"unit", "endpoint", "PostgreSQL"}
    bad = [
        r["id"] for r in manifest["requirements"]
        if r.get("proof_level") in executable_levels and not r.get("nodes")
    ]
    assert not bad, f"executable requirements with zero cited test nodes (prose-only): {bad}"


def test_every_cited_backend_node_actually_collects(collected_nodes):
    manifest = _load_manifest()
    missing = []
    for r in manifest["requirements"]:
        for node in r.get("nodes", []):
            if not node.startswith("tests/") or "::" not in node:
                continue  # frontend/describe-string citations, validated separately
            # A parametrized/class-grouped citation (e.g. "...py::TestFoo")
            # satisfies collection if ANY collected node starts with it.
            if node in collected_nodes:
                continue
            if any(c.startswith(node + "::") or c.startswith(node + "[") for c in collected_nodes):
                continue
            missing.append((r["id"], node))
    assert not missing, f"manifest nodes that did not actually collect: {missing}"


def test_every_cited_implementation_file_exists():
    manifest = _load_manifest()
    missing = []
    for r in manifest["requirements"]:
        impl = r["implementation"]
        if impl in ("N/A (manual verification against a temporary dev server)",):
            continue
        file_part = impl.split("::", 1)[0]
        candidate_a = _REPO_ROOT / file_part
        candidate_b = _BACKEND_ROOT / file_part.removeprefix("backend/")
        if not candidate_a.exists() and not candidate_b.exists():
            missing.append((r["id"], file_part))
    assert not missing, f"requirements citing a nonexistent implementation file: {missing}"


def test_every_closure_sha_is_a_well_formed_git_sha():
    manifest = _load_manifest()
    bad = []
    for r in manifest["requirements"]:
        sha = r.get("historical_closure_sha", "")
        if not _SHA_RE.match(sha):
            bad.append((r["id"], sha))
    assert not bad, f"requirements with a malformed historical_closure_sha (not a 40-char hex SHA): {bad}"
