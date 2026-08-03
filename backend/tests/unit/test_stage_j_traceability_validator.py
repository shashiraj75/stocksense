"""
Trade Postmortem Sprint 3A, Stage J Final-Closure-Correction, Stage 7 —
automated traceability-governance validator. Deterministic Markdown-table
parsing only (no arbitrary content execution) against the fixed ADR file;
fails the suite if the Stage J scenario matrix regresses in structure or
reintroduces a placeholder reference. Documentation governance, not
runtime code.
"""
import re
from pathlib import Path

import pytest

ADR_PATH = (
    Path(__file__).resolve().parents[3]
    / "Documentation" / "Engineering-Handbook" / "ADR" / "sprint3a-stage-j-historical-evidence-matrix.md"
)

REQUIRED_COLUMNS = [
    "#", "Description", "Trade validity", "Entry snapshot", "Exit snapshot", "Symbol/source",
    "Evidence availability", "Evidence compatibility", "Replay/acquisition decision",
    "Calculation eligibility", "Report completeness ceiling", "Provider-call expectation",
    "Evidence-row outcome", "Report outcome", "Outbox outcome", "Reason/limitation codes",
    "Test file", "Test function", "Test level", "PG15/17 result",
]

FORBIDDEN_PLACEHOLDERS = ("as row", "covered by suite", "not independently named")


def _load_matrix_rows():
    """Parses only the FIRST pipe-table in the ADR whose header contains
    every REQUIRED_COLUMNS entry — deterministic, no Markdown execution,
    no eval of any kind."""
    text = ADR_PATH.read_text()
    lines = text.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("|") and all(col in line for col in REQUIRED_COLUMNS):
            header_idx = i
            break
    assert header_idx is not None, "no table header containing all required columns was found in the ADR"

    header_cells = [c.strip() for c in lines[header_idx].strip("|").split("|")]
    assert len(header_cells) == len(REQUIRED_COLUMNS), (
        f"matrix header has {len(header_cells)} columns, required {len(REQUIRED_COLUMNS)}: {header_cells}"
    )

    # Row after header is the "---|---|..." separator; data rows follow
    # until the first non-table line.
    data_rows = []
    for line in lines[header_idx + 2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(REQUIRED_COLUMNS):
            continue
        data_rows.append(cells)
    return header_cells, data_rows


@pytest.mark.unit
class TestStageJTraceabilityValidator:
    def test_adr_file_exists(self):
        assert ADR_PATH.is_file(), f"expected ADR at {ADR_PATH}"

    def test_matrix_has_all_required_columns(self):
        header_cells, _ = _load_matrix_rows()
        for col in REQUIRED_COLUMNS:
            assert col in header_cells, f"required column {col!r} missing from matrix header"

    def test_matrix_has_at_least_40_rows(self):
        _, rows = _load_matrix_rows()
        assert len(rows) >= 40, f"matrix has {len(rows)} rows, required at least 40"

    def test_every_row_has_a_non_blank_exact_test_function(self):
        header_cells, rows = _load_matrix_rows()
        test_func_idx = header_cells.index("Test function")
        for row in rows:
            n = row[0]
            value = row[test_func_idx]
            assert value and value not in ("—", "-", ""), f"row {n} has a blank Test function field"
            assert "::" in value, f"row {n}'s Test function field {value!r} does not look like an exact function reference"

    def test_every_row_has_a_non_blank_test_file(self):
        header_cells, rows = _load_matrix_rows()
        test_file_idx = header_cells.index("Test file")
        for row in rows:
            n = row[0]
            value = row[test_file_idx]
            assert value and value not in ("—", "-", ""), f"row {n} has a blank Test file field"

    def test_no_forbidden_placeholders_remain(self):
        _, rows = _load_matrix_rows()
        for row in rows:
            n = row[0]
            joined = " ".join(row).lower()
            for placeholder in FORBIDDEN_PLACEHOLDERS:
                assert placeholder not in joined, f"row {n} still contains forbidden placeholder {placeholder!r}"

    def test_every_row_has_an_outcome(self):
        header_cells, rows = _load_matrix_rows()
        report_outcome_idx = header_cells.index("Report outcome")
        outbox_outcome_idx = header_cells.index("Outbox outcome")
        for row in rows:
            n = row[0]
            assert row[report_outcome_idx], f"row {n} has a blank Report outcome"
            assert row[outbox_outcome_idx], f"row {n} has a blank Outbox outcome"

    def test_every_row_has_a_reason_or_limitation_code(self):
        header_cells, rows = _load_matrix_rows()
        reason_idx = header_cells.index("Reason/limitation codes")
        for row in rows:
            n = row[0]
            # "—" is an explicit, deliberate "no reason code applies"
            # marker (e.g. the fully-successful row 1) -- distinct from a
            # genuinely blank cell, which is never acceptable.
            assert row[reason_idx] != "", f"row {n} has a blank Reason/limitation codes cell"

    def test_row_31_uses_corrected_split_wording(self):
        """Stage 4 — row 31 must describe detection without reconciliation,
        never "reconciled deterministically"."""
        _, rows = _load_matrix_rows()
        row_31 = next(r for r in rows if r[0] == "31")
        description = row_31[1].lower()
        assert "reconciled deterministically" not in description
        assert "cross-split calculation" in description or "no reconciliation" in description.lower() or "no price reconciliation" in description
        assert "detected deterministically" in description

    def test_rows_13_and_33_do_not_claim_bundle_level_ambiguous_resolution(self):
        """Stage 4 — same-day/entry==exit rows must not claim
        AMBIGUOUS_RESOLUTION as the bundle-level reason code (that value's
        only live trigger is split-in-window); the real reason is
        EXCURSION_NO_INTERIOR_BARS."""
        header_cells, rows = _load_matrix_rows()
        reason_idx = header_cells.index("Reason/limitation codes")
        for n in ("13", "33"):
            row = next(r for r in rows if r[0] == n)
            assert row[reason_idx] == "EXCURSION_NO_INTERIOR_BARS", (
                f"row {n} reason code is {row[reason_idx]!r}, expected EXCURSION_NO_INTERIOR_BARS"
            )


# ============================================================================
# Stage J4B.3 -- executable requirement-to-test traceability (I-05).
#
# Separates STATIC proof (a manifest-cited node exists / would be
# collected) from RUNTIME proof (it actually ran and passed, per a
# JUnit XML report). These tests exercise the parsing/validation LOGIC
# in j4b3_traceability_helper.py against small synthetic fixtures --
# they never shell out to pytest themselves (that would either be a
# per-row subprocess loop, explicitly prohibited, or a self-referential
# full-suite run from inside the suite itself). The actual external
# `pytest --collect-only` run and the actual full-suite JUnit run are
# performed once, externally, as part of Stage 12 local verification --
# their real output is what the manifest is validated against there,
# not fabricated here.
# ============================================================================
import json as _json
import tempfile as _tempfile

from tests.unit.j4b3_traceability_helper import (
    check_manifest_ids_unique,
    load_manifest,
    parse_collect_only_output,
    parse_junit_xml,
    validate_runtime,
    validate_static,
)

_MANIFEST_PATH = Path(__file__).resolve().parent / "j4b3_traceability_manifest.json"


@pytest.mark.unit
class TestJ4B3TraceabilityHelper:
    def test_manifest_is_valid_json_with_required_keys(self):
        manifest = load_manifest(_MANIFEST_PATH)
        assert manifest["stage"] == "J4B.3"
        assert isinstance(manifest["requirements"], list)
        for req in manifest["requirements"]:
            for key in ("id", "title", "protected_function", "expected_behavior", "nodes"):
                assert key in req, f"requirement missing key {key!r}: {req.get('id')}"

    def test_manifest_ids_appear_exactly_once(self):
        manifest = load_manifest(_MANIFEST_PATH)
        errors = check_manifest_ids_unique(manifest)
        assert errors == []
        ids = [req["id"] for req in manifest["requirements"]]
        assert ids == sorted(set(ids), key=ids.index)  # no duplicates, preserves order
        assert set(ids) == {"I-01", "I-02", "I-03", "I-04", "I-05", "I-06"}

    def test_parse_collect_only_output_extracts_node_ids(self):
        sample = (
            "tests/unit/test_foo.py::TestBar::test_baz\n"
            "tests/unit/test_foo.py::TestBar::test_qux[param-a]\n"
            "\n"
            "3 tests collected in 0.01s\n"
        )
        nodes = parse_collect_only_output(sample)
        assert nodes == {
            "tests/unit/test_foo.py::TestBar::test_baz",
            "tests/unit/test_foo.py::TestBar::test_qux[param-a]",
        }

    def test_static_validation_detects_missing_node(self):
        manifest = {"requirements": [{
            "id": "X-01", "nodes": ["backend/tests/unit/test_nonexistent_file.py::TestX::test_missing"],
        }]}
        collected = {"backend/tests/unit/test_price_path_calculator.py::TestReal::test_present"}
        errors = validate_static(manifest, collected, repo_root=Path(__file__).resolve().parents[3])
        assert any("cited node not found in collection" in e for e in errors)

    def test_static_validation_accepts_parametrized_prefix_match(self):
        manifest = {"requirements": [{
            "id": "X-02", "nodes": ["backend/tests/unit/test_price_path_calculator.py::TestReal::test_param"],
        }]}
        collected = {"backend/tests/unit/test_price_path_calculator.py::TestReal::test_param[case-a]"}
        errors = validate_static(manifest, collected, repo_root=Path(__file__).resolve().parents[3])
        assert not any("cited node not found in collection" in e for e in errors)

    def test_parse_junit_xml_extracts_outcomes(self):
        xml_text = """<?xml version="1.0"?>
<testsuites>
  <testsuite name="pytest">
    <testcase classname="tests.unit.test_foo.TestBar" name="test_pass" file="tests/unit/test_foo.py"></testcase>
    <testcase classname="tests.unit.test_foo.TestBar" name="test_fail" file="tests/unit/test_foo.py">
      <failure message="boom">traceback</failure>
    </testcase>
    <testcase classname="tests.unit.test_foo.TestBar" name="test_skip" file="tests/unit/test_foo.py">
      <skipped message="skipped"></skipped>
    </testcase>
  </testsuite>
</testsuites>"""
        with _tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(xml_text)
            path = f.name
        results = parse_junit_xml(path)
        assert results["tests/unit/test_foo.py::TestBar::test_pass"] == "passed"
        assert results["tests/unit/test_foo.py::TestBar::test_fail"] == "failed"
        assert results["tests/unit/test_foo.py::TestBar::test_skip"] == "skipped"

    def test_runtime_validation_detects_failed_node(self):
        manifest = {"requirements": [{"id": "X-03", "nodes": ["tests/unit/test_foo.py::TestBar::test_fail"]}]}
        junit_results = {"tests/unit/test_foo.py::TestBar::test_fail": "failed"}
        errors = validate_runtime(manifest, junit_results)
        assert any("did not pass" in e for e in errors)

    def test_runtime_validation_detects_skipped_node(self):
        manifest = {"requirements": [{"id": "X-04", "nodes": ["tests/unit/test_foo.py::TestBar::test_skip"]}]}
        junit_results = {"tests/unit/test_foo.py::TestBar::test_skip": "skipped"}
        errors = validate_runtime(manifest, junit_results)
        assert any("was skipped" in e for e in errors)
