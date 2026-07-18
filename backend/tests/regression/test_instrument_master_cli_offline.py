"""
Phase C0-B/C regression coverage — end-to-end offline CLI runs against the
25-record and 100-record fixtures, run twice each, asserting byte-identical
canonical output and manifest hashes. This is the same mechanism used to
produce the Phase C0-B/C evidence in the closure report; it is re-run here
as a permanent regression guard.

No network. No database. Output only ever goes to a pytest tmp_path,
which is always outside both the clean worktree and the original repo.
`--allowed-output-root` (Phase C0-D) is always the pytest `tmp_path` root
itself, with `--output-dir` a strict descendant of it — this exercises
the corrected allowlisted-output-root model on every run in this file,
not just the dedicated safety tests.
"""
import json
import os
import subprocess
import sys

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
CLI_PATH = os.path.join(BACKEND_DIR, "scripts", "instrument_master_offline_cli.py")
FIXTURES_DIR = os.path.join(BACKEND_DIR, "tests", "fixtures", "instrument_master")


def _run_cli(fixture_name: str, max_records: int, output_dir: str, run_id: str, allowed_root: str):
    args = [
        sys.executable,
        CLI_PATH,
        "--allowed-output-root",
        allowed_root,
        "--input-file",
        os.path.join(FIXTURES_DIR, fixture_name),
        "--output-dir",
        output_dir,
        "--max-records",
        str(max_records),
        "--schema-version",
        "1",
        "--generated-at",
        "2026-07-19T00:00:00Z",
        "--run-id",
        run_id,
        "--fixture-mode",
        "--no-db",
        "--source-identifier",
        f"offline_fixture:{fixture_name}",
    ]
    proc = subprocess.run(args, cwd=BACKEND_DIR, capture_output=True, text=True, timeout=60)
    return proc


class TestTwentyFiveRecordFixture:
    def test_two_runs_produce_byte_identical_canonical_output(self, tmp_path):
        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        proc1 = _run_cli("fixture_25.csv", 25, str(out1), "c0b-run-1", str(tmp_path))
        proc2 = _run_cli("fixture_25.csv", 25, str(out2), "c0b-run-1", str(tmp_path))

        assert proc1.returncode == 0, proc1.stderr
        assert proc2.returncode == 0, proc2.stderr

        snap1 = (out1 / "canonical_snapshot.json").read_bytes()
        snap2 = (out2 / "canonical_snapshot.json").read_bytes()
        assert snap1 == snap2

        manifest1 = json.loads((out1 / "manifest.json").read_bytes())
        manifest2 = json.loads((out2 / "manifest.json").read_bytes())
        assert manifest1["canonical"] == manifest2["canonical"]
        assert manifest1["canonical_manifest_sha256"] == manifest2["canonical_manifest_sha256"]

    def test_canonical_hash_matches_pre_correction_phase_c0_value(self, tmp_path):
        # Phase C0-D must not change parsing/classification/serialization
        # behavior — only output-path safety. This pins the exact hash
        # recorded in the original Phase C0 report and re-verified in the
        # Phase C0 closure audit, run again post-correction.
        out = tmp_path / "run"
        secondary = os.path.join(FIXTURES_DIR, "fixture_secondary_classifications.csv")
        prior = os.path.join(FIXTURES_DIR, "fixture_25_prior.csv")
        args = [
            sys.executable,
            CLI_PATH,
            "--allowed-output-root",
            str(tmp_path),
            "--input-file",
            os.path.join(FIXTURES_DIR, "fixture_25.csv"),
            "--output-dir",
            str(out),
            "--max-records",
            "25",
            "--schema-version",
            "1",
            "--generated-at",
            "2026-07-19T00:00:00Z",
            "--run-id",
            "phase-c0-b-run-1",
            "--fixture-mode",
            "--no-db",
            "--source-identifier",
            "offline_fixture:fixture_25.csv",
            "--secondary-classifications-file",
            secondary,
            "--prior-input-file",
            prior,
            "--prior-generated-at",
            "2026-01-01T00:00:00Z",
        ]
        proc = subprocess.run(args, cwd=BACKEND_DIR, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        summary = json.loads(proc.stdout)
        assert (
            summary["canonical_output_sha256"]
            == "2326910ef732c13d6049d9cf4068cfa56107c23f5a4418122d60ef6f7570f08a"
        )

    def test_summary_counts_match_expected_fixture_composition(self, tmp_path):
        out = tmp_path / "run"
        proc = _run_cli("fixture_25.csv", 25, str(out), "c0b-counts", str(tmp_path))
        assert proc.returncode == 0, proc.stderr
        summary = json.loads(proc.stdout)
        assert summary["total_rows"] == 25
        assert summary["rejected_records"] == 1  # the one malformed row
        assert summary["duplicate_isin_detections"] == 2
        assert summary["duplicate_symbol_detections"] == 2

    def test_output_path_is_outside_both_repositories(self, tmp_path):
        out = tmp_path / "run"
        clean_worktree_root = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
        proc = _run_cli("fixture_25.csv", 25, str(out), "c0b-path-check", str(tmp_path))
        assert proc.returncode == 0, proc.stderr
        resolved_out = str(out.resolve())
        assert not resolved_out.startswith(clean_worktree_root)


class TestOneHundredRecordFixture:
    def test_two_runs_produce_byte_identical_canonical_output(self, tmp_path):
        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        proc1 = _run_cli("fixture_100.csv", 100, str(out1), "c0c-run-1", str(tmp_path))
        proc2 = _run_cli("fixture_100.csv", 100, str(out2), "c0c-run-1", str(tmp_path))

        assert proc1.returncode == 0, proc1.stderr
        assert proc2.returncode == 0, proc2.stderr

        snap1 = (out1 / "canonical_snapshot.json").read_bytes()
        snap2 = (out2 / "canonical_snapshot.json").read_bytes()
        assert snap1 == snap2

    def test_canonical_hash_matches_pre_correction_phase_c0_value(self, tmp_path):
        out = tmp_path / "run"
        proc = _run_cli("fixture_100.csv", 100, str(out), "phase-c0-c-run-1", str(tmp_path))
        assert proc.returncode == 0, proc.stderr
        summary = json.loads(proc.stdout)
        assert (
            summary["canonical_output_sha256"]
            == "0ca0f30ac88b33bb72fc2e8e9ff7f4665f34855fd737e3c8e37c26cf9d3c4385"
        )

    def test_summary_counts_match_expected_fixture_composition(self, tmp_path):
        out = tmp_path / "run"
        proc = _run_cli("fixture_100.csv", 100, str(out), "c0c-counts", str(tmp_path))
        assert proc.returncode == 0, proc.stderr
        summary = json.loads(proc.stdout)
        assert summary["total_rows"] == 100
        assert summary["rejected_records"] == 0
        assert summary["duplicate_isin_detections"] == 2

    def test_max_records_below_actual_row_count_is_rejected(self, tmp_path):
        out = tmp_path / "run"
        proc = _run_cli("fixture_100.csv", 50, str(out), "c0c-cap-check", str(tmp_path))
        assert proc.returncode == 2
        assert not out.exists()
