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
