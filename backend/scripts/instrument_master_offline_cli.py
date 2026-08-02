#!/usr/bin/env python3
"""
Offline NSE instrument-master CLI (Phase C0 foundation).

Standalone, offline-only. Does not import `requests`, `urllib`, `socket`,
`yfinance`, or any application data-provider/persistence/cache module —
see backend/tests/unit/test_instrument_master_safety.py, which fails the
build if any such import ever appears in this script's or the
`services.instrument_master` package's import graph.

This CLI has exactly one mode: parse a local, explicitly-supplied fixture
CSV and emit a deterministic canonical snapshot + manifest to an
explicitly-supplied output directory. There is no live-fetch mode in
Phase C0 — none of this script's code paths can reach a network or a
database, by omission, not by a runtime toggle.

Output-path safety (Phase C0-D): `--allowed-output-root` is mandatory and
must be a pre-existing, non-symlink directory that is itself outside
every Git repository or worktree (checked by walking its own ancestor
chain for a `.git` directory/file — no `git` subprocess is ever invoked,
and no filesystem-wide scan occurs). `--output-dir` must be a strict
descendant of that root and is independently re-checked against the same
any-Git-repository rule. This protects the original StockSense360
repository (and any other sibling repository) by default, with no need
for the caller to name it — unlike the superseded blocklist-only model,
safety here does not depend on an optional `--protected-root` argument.
`--protected-root` remains available purely as additional, non-load-
bearing defense in depth.

Run from the backend directory, e.g.:
  python scripts/instrument_master_offline_cli.py \\
    --input-file tests/fixtures/instrument_master/fixture_25.csv \\
    --allowed-output-root /tmp/phase-c0-allowed-root \\
    --output-dir /tmp/phase-c0-allowed-root/run-001 \\
    --max-records 25 \\
    --schema-version 1 \\
    --generated-at 2026-07-19T00:00:00Z \\
    --run-id phase-c0-b-run-1 \\
    --fixture-mode \\
    --no-db
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.instrument_master import canonicalize, manifest as manifest_mod, parser as parser_mod  # noqa: E402

_PROTECTED_CACHE_DIR_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", "node_modules", ".next", ".git", "venv", ".venv"}
)
_PROTECTED_DB_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})


class OutputPathRejected(Exception):
    pass


def _this_script_repo_root() -> Path:
    """Walks upward from this file looking for a directory containing
    `.git` (file or dir — a linked worktree's `.git` is a file). This is
    always a protected root, with no argument required. Retained as an
    extra, non-load-bearing defense-in-depth signal alongside
    `_is_inside_any_git_repo()` below, which is now the actual safety
    boundary (Phase C0-D correction — see module docstring)."""
    current = Path(__file__).resolve().parent
    for _ in range(20):
        if (current / ".git").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    raise RuntimeError("could not locate this script's own repository root")


def _nearest_existing_ancestor(path: Path) -> Path:
    """Walks upward from `path` (which need not exist) until it finds an
    ancestor that does exist, and returns that ancestor. `path` itself is
    returned unchanged if it already exists."""
    current = path
    for _ in range(1000):
        if current.exists():
            return current
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


def _is_bare_git_repo_signature(candidate: Path) -> bool:
    """Structural (Phase C0-E) signature check: does `candidate` itself —
    not an ancestor walk, just this one directory — look like the root of
    a bare Git repository? Bare repositories have no `.git` child; their
    repository metadata (`HEAD`, `objects/`, `refs/` or `packed-refs`)
    sits directly at the repository root.

    Deliberately conservative and multi-signal: a bare repo is never
    inferred from `HEAD` alone (a file named `HEAD` can exist for
    unrelated reasons — e.g. an HTTP response fixture, a build artifact).
    The minimum required combination is `HEAD` (regular file) + `objects`
    (directory) + (`refs` directory OR `packed-refs` regular file).
    `config` is not required and is never the sole signal — a
    damaged/minimal bare repo missing a parseable `config` must still be
    caught by the structural signature above.
    """
    if not candidate.is_dir():
        return False
    head = candidate / "HEAD"
    objects_dir = candidate / "objects"
    refs_dir = candidate / "refs"
    packed_refs = candidate / "packed-refs"
    if not (head.is_file() and objects_dir.is_dir()):
        return False
    return refs_dir.is_dir() or packed_refs.is_file()


def _is_inside_any_git_repo(path: Path) -> bool:
    """Filesystem-only check: is `path` (or its nearest existing ancestor,
    if `path` itself doesn't exist yet) inside a Git repository —
    ordinary (`.git` directory), linked worktree (`.git` file), or bare
    (no `.git` child at all; repository metadata directly at the root —
    see `_is_bare_git_repo_signature`)? Walks the ancestor chain checking
    each level against all three shapes.

    `path` must already be fully resolved (symlinks followed, `..`
    collapsed) by the caller — this function does no resolution of its
    own, only ancestor-chain inspection, and never invokes a `git`
    subprocess or scans anything outside `path`'s own ancestor chain.
    """
    current = _nearest_existing_ancestor(path)
    for _ in range(1000):
        if (current / ".git").exists():
            return True
        if _is_bare_git_repo_signature(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent
    return False


def _reject_if_symlink(raw_path: str, label: str) -> None:
    # Checked on the literal, unresolved argument — resolving first would
    # silently follow the symlink and this check exists specifically to
    # refuse the symlink itself, regardless of where it points.
    candidate = Path(raw_path).expanduser()
    if candidate.is_symlink():
        raise OutputPathRejected(f"{label} {candidate} must not be a symlink")


def _validate_allowed_output_root(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise OutputPathRejected("--allowed-output-root must not be empty")

    _reject_if_symlink(raw_path, "--allowed-output-root")

    resolved = Path(raw_path).expanduser().resolve()

    if not resolved.exists():
        raise OutputPathRejected(f"--allowed-output-root {resolved} does not exist")
    if not resolved.is_dir():
        raise OutputPathRejected(f"--allowed-output-root {resolved} is not a directory")
    if _is_inside_any_git_repo(resolved):
        raise OutputPathRejected(
            f"--allowed-output-root {resolved} is inside a Git repository or worktree"
        )
    if resolved.suffix.lower() in _PROTECTED_DB_SUFFIXES:
        raise OutputPathRejected(f"--allowed-output-root {resolved} looks like a database file path")
    for part in resolved.parts:
        if part in _PROTECTED_CACHE_DIR_NAMES:
            raise OutputPathRejected(
                f"--allowed-output-root {resolved} passes through a protected cache/vcs directory name ({part})"
            )
    return resolved


def _validate_output_dir(
    raw_path: str, allowed_root: Path, protected_roots: list[Path]
) -> Path:
    if not raw_path or not raw_path.strip():
        raise OutputPathRejected("output-dir must not be empty")

    resolved = Path(raw_path).expanduser().resolve()

    if resolved == allowed_root:
        raise OutputPathRejected(
            f"output-dir must not equal --allowed-output-root itself ({allowed_root})"
        )
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        raise OutputPathRejected(
            f"output-dir {resolved} is not a descendant of --allowed-output-root ({allowed_root})"
        )

    # Primary, always-on safety boundary: reject if the resolved path (or
    # its nearest existing ancestor) is inside ANY Git repository at all —
    # this does not depend on the caller knowing any specific repository's
    # path, and is what actually protects the original StockSense360 repo
    # (and any other sibling repository) by default.
    if _is_inside_any_git_repo(resolved):
        raise OutputPathRejected(
            f"output-dir {resolved} is inside a Git repository or worktree"
        )

    # Defense-in-depth only, per Phase C0-D §5: retained but not load-bearing.
    for root in protected_roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        raise OutputPathRejected(
            f"output-dir {resolved} is inside a protected repository root ({root})"
        )

    if resolved.suffix.lower() in _PROTECTED_DB_SUFFIXES:
        raise OutputPathRejected(f"output-dir {resolved} looks like a database file path")

    for part in resolved.parts:
        if part in _PROTECTED_CACHE_DIR_NAMES:
            raise OutputPathRejected(
                f"output-dir {resolved} passes through a protected cache/vcs directory name ({part})"
            )

    return resolved


def _validate_input_file(raw_path: str, protected_roots: list[Path]) -> Path:
    resolved = Path(raw_path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"input-file does not exist: {resolved}")
    return resolved


def _assert_no_network_capable_modules_loaded() -> None:
    """Defense-in-depth runtime check: fails loudly if any network
    library somehow ended up imported into this process (e.g. via a
    future accidental import elsewhere in the import graph)."""
    forbidden = {"requests", "urllib3", "httpx", "yfinance", "socket"}
    loaded = forbidden & set(sys.modules.keys())
    if loaded:
        raise RuntimeError(
            f"network-capable module(s) unexpectedly loaded, refusing to run: {sorted(loaded)}"
        )


def _assert_no_application_db_modules_loaded() -> None:
    forbidden = {
        "psycopg",
        "psycopg2",
        "sqlalchemy",
        "services.postgres_store",
        "services.alpha_engine.store",
        "services.alpha_engine.outcome_logger",
        "services.alpha_engine.alpha_observations",
        "services.intelligence_engine.telemetry",
    }
    loaded = forbidden & set(sys.modules.keys())
    if loaded:
        raise RuntimeError(
            f"application persistence module(s) unexpectedly loaded, refusing to run: {sorted(loaded)}"
        )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Offline NSE instrument-master CLI (Phase C0 foundation, no network, no database).",
    )
    p.add_argument("--input-file", required=True, help="Local fixture CSV path (never a URL).")
    p.add_argument(
        "--allowed-output-root",
        required=True,
        help="Pre-existing, non-symlink directory outside every Git repository/worktree. "
        "--output-dir must be a strict descendant of this root. Required — omitting it is an error.",
    )
    p.add_argument("--output-dir", required=True, help="Output directory; must be a strict descendant of --allowed-output-root and outside every Git repository.")
    p.add_argument("--max-records", type=int, required=True, help="Hard cap on records processed. Required — omitting it is an error.")
    p.add_argument("--schema-version", type=int, required=True)
    p.add_argument("--generated-at", required=True, help="Explicit ISO-8601 timestamp; never derived from the wall clock.")
    p.add_argument("--run-id", required=True, help="Explicit deterministic run identifier.")
    p.add_argument("--dry-run", action="store_true", help="Parse and validate only; write no output files.")
    p.add_argument("--fixture-mode", action="store_true", required=True, help="Must be passed — Phase C0 has no non-fixture mode.")
    p.add_argument("--no-db", action="store_true", required=True, help="Must be passed — this tool never connects to a database, by omission of any DB import.")
    p.add_argument("--protected-root", action="append", default=[], help="Additional repository root(s) to reject as an output-dir target. Repeatable.")
    p.add_argument("--source-identifier", default="offline_fixture", help="Provenance label for the input file (never a live source name in Phase C0).")
    p.add_argument("--source-publication-date", default=None)
    p.add_argument("--secondary-classifications-file", default=None, help="Optional local, offline CSV of ISIN -> instrument_category/classification_status.")
    p.add_argument("--prior-input-file", default=None, help="Optional local, offline prior-snapshot CSV, for symbol/series history detection.")
    p.add_argument("--prior-generated-at", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    protected_roots = [_this_script_repo_root()]
    for raw in args.protected_root:
        protected_roots.append(Path(raw).expanduser().resolve())

    try:
        allowed_root = _validate_allowed_output_root(args.allowed_output_root)
        output_dir = _validate_output_dir(args.output_dir, allowed_root, protected_roots)
        input_path = _validate_input_file(args.input_file, protected_roots)
    except (OutputPathRejected, FileNotFoundError) as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 2

    _assert_no_network_capable_modules_loaded()
    _assert_no_application_db_modules_loaded()

    t0 = time.perf_counter()
    input_bytes = input_path.read_bytes()
    input_sha256 = canonicalize.sha256_hex(input_bytes)
    csv_text = input_bytes.decode("utf-8")

    try:
        parse_result = parser_mod.parse_source_csv(csv_text, source_identifier=args.source_identifier)

        if parse_result.total_rows > args.max_records:
            print(
                f"REJECTED: input has {parse_result.total_rows} rows, exceeding --max-records={args.max_records}",
                file=sys.stderr,
            )
            return 2

        t1 = time.perf_counter()

        secondary = {}
        if args.secondary_classifications_file:
            secondary_path = _validate_input_file(args.secondary_classifications_file, protected_roots)
            secondary = parser_mod.load_secondary_classifications(secondary_path.read_text(encoding="utf-8"))

        prior_map = None
        if args.prior_input_file:
            prior_path = _validate_input_file(args.prior_input_file, protected_roots)
            prior_parse = parser_mod.parse_source_csv(
                prior_path.read_text(encoding="utf-8"), source_identifier="offline_fixture_prior"
            )
            prior_map = parser_mod.candidates_by_isin(prior_parse)
    except parser_mod.SourceValidationError as exc:
        # Whole-file structural failure (empty source, missing mandatory
        # column, or the same class of error in the prior-snapshot file).
        # Caught here — before output_dir is ever touched — so this path
        # can never create a directory or write a partial/corrupt
        # canonical snapshot or manifest. No traceback, no stack frames,
        # no internal path details beyond the validation reason itself
        # (SourceValidationError's own message never embeds a filesystem
        # path — see services/instrument_master/parser.py).
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 2

    records, stats = parser_mod.build_records(
        parse_result,
        schema_version=args.schema_version,
        source_identifier=args.source_identifier,
        source_publication_date=args.source_publication_date,
        secondary_classifications=secondary,
        prior_candidates_by_isin=prior_map,
        prior_generated_at=args.prior_generated_at,
        current_generated_at=args.generated_at,
    )

    canonical_snapshot = canonicalize.build_canonical_snapshot(
        schema_version=args.schema_version,
        generated_at=args.generated_at,
        run_id=args.run_id,
        source_identifier=args.source_identifier,
        source_publication_date=args.source_publication_date,
        input_sha256=input_sha256,
        records=records,
    )
    canonical_bytes = canonicalize.canonical_json_bytes(canonical_snapshot)
    canonical_output_sha256 = canonicalize.sha256_hex(canonical_bytes)

    t2 = time.perf_counter()

    canonical_manifest = manifest_mod.build_canonical_manifest(
        schema_version=args.schema_version,
        generated_at=args.generated_at,
        run_id=args.run_id,
        input_file=str(input_path),
        input_sha256=input_sha256,
        max_records=args.max_records,
        fixture_mode=args.fixture_mode,
        stats=stats,
        canonical_output_sha256=canonical_output_sha256,
    )
    canonical_manifest_bytes_ = manifest_mod.canonical_manifest_bytes(canonical_manifest)
    canonical_manifest_hash = canonicalize.sha256_hex(canonical_manifest_bytes_)

    peak_memory_bytes = None
    try:
        import resource

        ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_memory_bytes = ru_maxrss * 1024 if sys.platform != "darwin" else ru_maxrss
    except Exception:
        peak_memory_bytes = None

    benchmark_envelope = manifest_mod.build_benchmark_envelope(
        parse_seconds=t1 - t0,
        normalize_seconds=t2 - t1,
        total_seconds=t2 - t0,
        peak_memory_bytes=peak_memory_bytes,
    )
    safety_proof = manifest_mod.build_safety_proof(
        network_disabled=True,
        db_disabled=True,
        env_file_loaded=False,
        output_dir_outside_repos=True,
    )

    full_manifest = {
        "canonical": canonical_manifest,
        "canonical_manifest_sha256": canonical_manifest_hash,
        "benchmark_envelope": benchmark_envelope,
        "safety_proof": safety_proof,
    }

    summary = {
        "run_id": args.run_id,
        "total_rows": stats["total_rows"],
        "accepted_records": stats["accepted_records"],
        "rejected_records": stats["rejected_records"],
        "ambiguous_records": stats["ambiguous_records"],
        "duplicate_isin_detections": stats["duplicate_isin_detections"],
        "duplicate_symbol_detections": stats["duplicate_symbol_detections"],
        "reason_code_counts": stats["reason_code_counts"],
        "canonical_output_sha256": canonical_output_sha256,
        "canonical_manifest_sha256": canonical_manifest_hash,
        "output_dir": str(output_dir),
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if args.dry_run:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "canonical_snapshot.json").write_bytes(canonical_bytes)
    (output_dir / "manifest.json").write_bytes(
        canonicalize.canonical_json_bytes(full_manifest)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
