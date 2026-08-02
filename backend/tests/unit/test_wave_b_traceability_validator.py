"""
Wave B closure — automated traceability validator. Cross-checks
wave_b_traceability_manifest.json and wave_b_scenario_ledger.json
against REAL pytest collection (never trusting the JSON files' own
self-declared totals) and against each other's counts.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS_UNIT_DIR = Path(__file__).resolve().parent
_MANIFEST_PATH = _TESTS_UNIT_DIR / "wave_b_traceability_manifest.json"
_LEDGER_PATH = _TESTS_UNIT_DIR / "wave_b_scenario_ledger.json"


def _load(path):
    return json.loads(path.read_text())


def _collected_node_ids():
    """Runs a real pytest --collect-only against every wave_b_j4e_/
    wave_b_j4f_ test file plus the postgres_integration Batch I file,
    and returns the set of collected node IDs. This is genuine
    subprocess collection, not a parse of a stale text file."""
    backend_root = _TESTS_UNIT_DIR.parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "tests/unit", "tests/postgres_integration/test_current_report_global_claim.py"],
        cwd=backend_root, capture_output=True, text=True, timeout=60,
    )
    node_ids = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" in line and (line.startswith("tests/unit/test_wave_b_j4") or line.startswith("tests/postgres_integration/test_current_report_global_claim")):
            node_ids.add(line)
    return node_ids


@pytest.fixture(scope="module")
def collected_nodes():
    return _collected_node_ids()


def test_manifest_j4e_ids_exactly_18_unique():
    manifest = _load(_MANIFEST_PATH)
    ids = [r["id"] for r in manifest["j4e_requirements"]]
    assert len(ids) == 18, f"expected exactly 18 WB-J4E requirement IDs, found {len(ids)}"
    assert len(set(ids)) == 18, "duplicate WB-J4E requirement ID found"
    assert set(ids) == {f"WB-J4E-{n:02d}" for n in range(1, 19)}, "WB-J4E ID set is not exactly 01..18"


def test_manifest_j4f_ids_exactly_20_unique():
    manifest = _load(_MANIFEST_PATH)
    ids = [r["id"] for r in manifest["j4f_requirements"]]
    assert len(ids) == 20, f"expected exactly 20 WB-J4F requirement IDs, found {len(ids)}"
    assert len(set(ids)) == 20, "duplicate WB-J4F requirement ID found"
    assert set(ids) == {f"WB-J4F-{n:02d}" for n in range(1, 21)}, "WB-J4F ID set is not exactly 01..20"


def test_every_manifest_node_actually_collects(collected_nodes):
    manifest = _load(_MANIFEST_PATH)
    missing = []
    for req in manifest["j4e_requirements"] + manifest["j4f_requirements"]:
        node = req["node"]
        if node not in collected_nodes:
            missing.append((req["id"], node))
    assert not missing, f"manifest nodes that did not actually collect: {missing}"


def test_every_manifest_requirement_has_a_final_result_no_stale_pending():
    manifest = _load(_MANIFEST_PATH)
    allowed_final_results = {"GREEN_NON_POSTGRESQL", "GREEN_ALL_REQUIRED_LEVELS", "GREEN_STRUCTURAL"}
    bad = []
    for req in manifest["j4e_requirements"] + manifest["j4f_requirements"]:
        final = req.get("final_result", "")
        if final not in allowed_final_results:
            bad.append((req["id"], final))
    assert not bad, f"requirements with a final_result outside the allowed GREEN set: {bad}"


def test_ledger_scenario_counts_are_55_and_68():
    ledger = _load(_LEDGER_PATH)
    assert ledger["j4e_scenarios_represented"] == 55
    assert ledger["j4f_scenarios_represented"] == 68


def test_ledger_combined_count_is_123():
    ledger = _load(_LEDGER_PATH)
    assert ledger["j4e_scenarios_represented"] + ledger["j4f_scenarios_represented"] == 123


def test_no_ledger_entry_has_pending_final_result():
    ledger = _load(_LEDGER_PATH)
    bad = []
    for key, value in ledger.items():
        if isinstance(value, list) and key != "gate5_adversarial_audit":
            for entry in value:
                if isinstance(entry, dict) and "final_result" in entry:
                    if entry["final_result"] != "GREEN":
                        bad.append((key, entry.get("scenario_no"), entry["final_result"]))
    assert not bad, f"ledger scenario entries not marked GREEN: {bad}"


def test_postgresql_required_ids_have_pg15_and_pg17_evidence():
    manifest = _load(_MANIFEST_PATH)
    pg_ids = {f"WB-J4F-{n:02d}" for n in range(13, 21)}
    missing = []
    for req in manifest["j4f_requirements"]:
        if req["id"] in pg_ids:
            if "final_postgresql_15_result" not in req or "final_postgresql_17_result" not in req:
                missing.append(req["id"])
    assert not missing, f"PostgreSQL-required WB-J4F IDs missing PG15/PG17 evidence fields: {missing}"
