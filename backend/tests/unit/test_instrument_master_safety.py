"""
Phase C0-A/C0-D — safety tests for the offline instrument-master
foundation.

These tests are the automated proof (not just a claim) that the new
package and CLI cannot reach the network, a database, or any application
persistence/cache module, and (Phase C0-D) that the output-path guard
rejects output inside ANY Git repository by default — not only the ones
an operator happens to name via `--protected-root`. They work by
statically parsing the source files' import statements (ast module — no
execution required) plus real subprocess-level CLI-contract checks
against temporary, non-network fixture-shaped directories.
"""
import ast
import glob
import os
import subprocess
import sys
import tempfile

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
PACKAGE_DIR = os.path.join(BACKEND_DIR, "services", "instrument_master")
CLI_PATH = os.path.join(BACKEND_DIR, "scripts", "instrument_master_offline_cli.py")
FIXTURES_DIR = os.path.join(BACKEND_DIR, "tests", "fixtures", "instrument_master")

FORBIDDEN_TOP_LEVEL_MODULES = {
    "requests",
    "urllib",
    "urllib2",
    "http",
    "httpx",
    "socket",
    "yfinance",
    "aiohttp",
    "psycopg",
    "psycopg2",
    "sqlalchemy",
    "dotenv",
}
FORBIDDEN_DOTTED_PREFIXES = (
    "services.postgres_store",
    "services.daily_picks",
    "services.prediction_engine",
    "services.stock_universe",
    "services.generate_stock_universe",
    "services.alpha_engine",
    "services.intelligence_engine",
    "services.fundamentals_cache",
    "services.fundamentals_refresh",
    "services.us_fundamentals_refresh",
    "services.market_data",
)


def _all_source_files() -> list[str]:
    files = glob.glob(os.path.join(PACKAGE_DIR, "*.py"))
    files.append(CLI_PATH)
    return files


def _imported_names(path: str) -> set[str]:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestNoForbiddenImports:
    @pytest.mark.parametrize("path", _all_source_files())
    def test_no_network_or_db_capable_module_imported(self, path):
        imported = _imported_names(path)
        top_level = {name.split(".")[0] for name in imported}
        forbidden_hits = top_level & FORBIDDEN_TOP_LEVEL_MODULES
        assert not forbidden_hits, f"{path} imports forbidden module(s): {forbidden_hits}"

        for name in imported:
            for forbidden_prefix in FORBIDDEN_DOTTED_PREFIXES:
                assert not name.startswith(forbidden_prefix), (
                    f"{path} imports {name}, which starts with the forbidden "
                    f"production-module prefix {forbidden_prefix}"
                )

    def test_no_dotenv_or_env_file_loading_source_text(self):
        for path in _all_source_files():
            with open(path, encoding="utf-8") as f:
                text = f.read()
            assert "load_dotenv" not in text, f"{path} references load_dotenv"
            assert ".env" not in text, f"{path} references a .env file"

    def test_no_database_url_env_read(self):
        for path in _all_source_files():
            with open(path, encoding="utf-8") as f:
                text = f.read()
            assert "DATABASE_URL" not in text, f"{path} references DATABASE_URL"

    def test_no_git_subprocess_invocation(self):
        # Phase C0-D's any-Git-repository check must be filesystem-only —
        # never shell out to a `git` binary. AST-based (not a naive
        # substring search — this file's own docstrings/comments mention
        # the word "subprocess" precisely to document that it's avoided).
        for path in _all_source_files():
            imported = _imported_names(path)
            top_level = {name.split(".")[0] for name in imported}
            assert "subprocess" not in top_level, f"{path} imports subprocess"


class TestImportTimeSafety:
    def test_importing_package_has_no_side_effects(self):
        proc = subprocess.run(
            [sys.executable, "-c", "import services.instrument_master"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr

    def test_importing_cli_module_does_not_invoke_main(self):
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util, sys; "
                f"spec = importlib.util.spec_from_file_location('cli_mod', {CLI_PATH!r}); "
                "mod = importlib.util.module_from_spec(spec); "
                "spec.loader.exec_module(mod); "
                "print('IMPORTED_WITHOUT_RUNNING')",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert "IMPORTED_WITHOUT_RUNNING" in proc.stdout


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, CLI_PATH] + args, cwd=BACKEND_DIR, capture_output=True, text=True, timeout=30
    )


def _base_args(input_file: str, output_dir: str, allowed_root: str | None = None) -> list[str]:
    args = [
        "--input-file",
        input_file,
        "--output-dir",
        output_dir,
        "--max-records",
        "25",
        "--schema-version",
        "1",
        "--generated-at",
        "2026-07-19T00:00:00Z",
        "--run-id",
        "safety-test-run",
        "--fixture-mode",
        "--no-db",
        "--dry-run",
    ]
    if allowed_root is not None:
        args = ["--allowed-output-root", allowed_root] + args
    return args


FIXTURE_25 = os.path.join(FIXTURES_DIR, "fixture_25.csv")


class TestCliRequiredFlags:
    def test_max_records_is_mandatory(self, tmp_path):
        args = [
            "--allowed-output-root",
            str(tmp_path),
            "--input-file",
            FIXTURE_25,
            "--output-dir",
            str(tmp_path / "out"),
            "--schema-version",
            "1",
            "--generated-at",
            "2026-07-19T00:00:00Z",
            "--run-id",
            "safety-test-run",
            "--fixture-mode",
            "--no-db",
        ]
        proc = _run_cli(args)
        assert proc.returncode != 0
        assert "max-records" in proc.stderr

    def test_fixture_mode_is_mandatory(self, tmp_path):
        args = _base_args(FIXTURE_25, str(tmp_path / "out"), str(tmp_path))
        args = [a for a in args if a != "--fixture-mode"]
        proc = _run_cli(args)
        assert proc.returncode != 0

    def test_no_db_is_mandatory(self, tmp_path):
        args = _base_args(FIXTURE_25, str(tmp_path / "out"), str(tmp_path))
        args = [a for a in args if a != "--no-db"]
        proc = _run_cli(args)
        assert proc.returncode != 0

    def test_allowed_output_root_is_mandatory(self, tmp_path):
        args = _base_args(FIXTURE_25, str(tmp_path / "out"))  # no allowed_root
        proc = _run_cli(args)
        assert proc.returncode != 0
        assert "allowed-output-root" in proc.stderr


def _make_git_dir_repo(path) -> None:
    """Creates a minimal, filesystem-only fake repository: a directory
    containing a `.git` subdirectory. No `git` binary is invoked."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


def _make_git_file_worktree(path, gitdir_target: str) -> None:
    """Creates a minimal, filesystem-only fake linked-worktree: a
    directory containing a `.git` FILE (not directory) pointing at
    another gitdir path, mirroring real `git worktree add` output. No
    `git` binary is invoked — this is purely a fixture shape."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").write_text(f"gitdir: {gitdir_target}\n")


def _make_bare_git_repo(path, *, use_packed_refs: bool = False) -> None:
    """Creates a minimal, filesystem-only fake BARE Git repository: no
    `.git` child at all — repository metadata directly at `path`'s root
    (`HEAD` file, `objects/` directory, and either a `refs/` directory or
    a `packed-refs` file), mirroring a real `git init --bare` layout. No
    `git` binary is invoked — this is purely a fixture shape, per the
    approved Phase C0-E recommended minimum signature."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n")
    (path / "objects").mkdir()
    if use_packed_refs:
        (path / "packed-refs").write_text("# pack-refs with: peeled fully-peeled sorted\n")
    else:
        (path / "refs").mkdir()


class TestAllowedOutputRootContract:
    def test_option_is_mandatory(self, tmp_path):
        proc = _run_cli(_base_args(FIXTURE_25, str(tmp_path / "out")))
        assert proc.returncode != 0

    def test_allowed_root_must_exist(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        proc = _run_cli(_base_args(FIXTURE_25, str(missing / "out"), str(missing)))
        assert proc.returncode == 2
        assert "REJECTED" in proc.stderr
        assert "does not exist" in proc.stderr

    def test_allowed_root_must_be_a_directory(self, tmp_path):
        a_file = tmp_path / "a_file"
        a_file.write_text("not a directory")
        proc = _run_cli(_base_args(FIXTURE_25, str(a_file / "out"), str(a_file)))
        assert proc.returncode == 2
        assert "is not a directory" in proc.stderr

    def test_allowed_root_cannot_be_a_symlink(self, tmp_path):
        real_target = tmp_path / "real_target"
        real_target.mkdir()
        symlink_root = tmp_path / "symlink_root"
        symlink_root.symlink_to(real_target, target_is_directory=True)
        proc = _run_cli(_base_args(FIXTURE_25, str(symlink_root / "out"), str(symlink_root)))
        assert proc.returncode == 2
        assert "must not be a symlink" in proc.stderr

    def test_output_dir_must_be_a_strict_descendant(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        proc = _run_cli(_base_args(FIXTURE_25, str(sibling), str(allowed)))
        assert proc.returncode == 2
        assert "is not a descendant" in proc.stderr

    def test_output_dir_cannot_equal_allowed_root(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        proc = _run_cli(_base_args(FIXTURE_25, str(allowed), str(allowed)))
        assert proc.returncode == 2
        assert "must not equal" in proc.stderr

    def test_safe_external_temp_child_is_accepted(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        proc = _run_cli(_base_args(FIXTURE_25, str(allowed / "run1"), str(allowed)))
        assert proc.returncode == 0, proc.stderr


class TestAnyGitRepositoryRejection:
    """No path in this class's fixtures relies on knowing the original
    StockSense360 repository's location via --protected-root — every
    rejection must come from the filesystem-only any-Git-repository
    check alone."""

    def test_clean_worktree_root_rejected(self, tmp_path):
        worktree_root = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
        proc = _run_cli(_base_args(FIXTURE_25, worktree_root, str(tmp_path)))
        assert proc.returncode == 2
        assert "REJECTED" in proc.stderr

    def test_clean_worktree_child_rejected(self, tmp_path):
        child = os.path.join(BACKEND_DIR, "services")
        proc = _run_cli(_base_args(FIXTURE_25, child, str(tmp_path)))
        assert proc.returncode == 2

    def test_nonexistent_child_inside_clean_worktree_rejected(self, tmp_path):
        child = os.path.join(BACKEND_DIR, "does", "not", "exist", "yet")
        proc = _run_cli(_base_args(FIXTURE_25, child, str(tmp_path)))
        assert proc.returncode == 2

    def test_original_repository_root_rejected_without_protected_root(self, tmp_path):
        orig_repo = os.path.expanduser("~/Documents/StockSense360/stock-predictor")
        if not os.path.isdir(os.path.join(orig_repo, ".git")):
            pytest.skip("original repository not present at expected path in this environment")
        # allowed-output-root must itself be outside any repo, so use a
        # neutral safe root and try to point --output-dir at the original
        # repo's root — no --protected-root argument is passed anywhere.
        proc = _run_cli(_base_args(FIXTURE_25, orig_repo, str(tmp_path)))
        assert proc.returncode == 2
        assert "REJECTED" in proc.stderr

    def test_original_repository_child_rejected_without_protected_root(self, tmp_path):
        orig_repo_backend = os.path.expanduser("~/Documents/StockSense360/stock-predictor/backend")
        if not os.path.isdir(os.path.join(os.path.dirname(orig_repo_backend), ".git")):
            pytest.skip("original repository not present at expected path in this environment")
        proc = _run_cli(_base_args(FIXTURE_25, os.path.join(orig_repo_backend, "services"), str(tmp_path)))
        assert proc.returncode == 2

    def test_relative_traversal_into_original_repository_rejected(self, tmp_path):
        orig_repo = os.path.expanduser("~/Documents/StockSense360/stock-predictor")
        if not os.path.isdir(os.path.join(orig_repo, ".git")):
            pytest.skip("original repository not present at expected path in this environment")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        rel_traversal = os.path.join(str(allowed), "..", "..", *orig_repo.strip("/").split("/")[-4:])
        proc = _run_cli(_base_args(FIXTURE_25, rel_traversal, str(allowed)))
        assert proc.returncode == 2

    def test_temporary_unrelated_git_repository_is_rejected(self, tmp_path):
        # The fake repo must be a descendant of the allowed root so that
        # the ONLY thing that can reject it is the any-Git-repository
        # check itself, not the separate descendant-of-allowed-root check.
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        fake_repo = allowed / "unrelated_fake_repo"
        _make_git_dir_repo(fake_repo)
        proc = _run_cli(_base_args(FIXTURE_25, str(fake_repo / "sub"), str(allowed)))
        assert proc.returncode == 2
        assert "Git repository" in proc.stderr

    def test_temporary_unrelated_git_repository_as_allowed_root_is_rejected(self, tmp_path):
        fake_repo = tmp_path / "unrelated_fake_repo_as_root"
        _make_git_dir_repo(fake_repo)
        proc = _run_cli(_base_args(FIXTURE_25, str(fake_repo / "out"), str(fake_repo)))
        assert proc.returncode == 2
        assert "Git repository" in proc.stderr

    def test_simulated_linked_worktree_gitfile_root_is_rejected(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        fake_worktree = allowed / "fake_linked_worktree"
        _make_git_file_worktree(fake_worktree, gitdir_target=str(tmp_path / "somewhere-else" / ".git" / "worktrees" / "x"))
        proc = _run_cli(_base_args(FIXTURE_25, str(fake_worktree / "sub"), str(allowed)))
        assert proc.returncode == 2
        assert "Git repository" in proc.stderr

    def test_nonexistent_child_inside_temporary_git_repository_rejected(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        fake_repo = allowed / "unrelated_fake_repo_2"
        _make_git_dir_repo(fake_repo)
        proc = _run_cli(
            _base_args(FIXTURE_25, str(fake_repo / "does" / "not" / "exist" / "yet"), str(allowed))
        )
        assert proc.returncode == 2

    def test_no_operator_supplied_repository_list_is_required(self, tmp_path):
        # This is the headline Phase C0-D regression: rejection of the
        # original repository (and of any Git repo in general) must work
        # with zero --protected-root arguments anywhere in the command.
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        fake_repo = allowed / "yet_another_fake_repo"
        _make_git_dir_repo(fake_repo)
        args = _base_args(FIXTURE_25, str(fake_repo / "sub"), str(allowed))
        assert "--protected-root" not in args
        proc = _run_cli(args)
        assert proc.returncode == 2


class TestBareGitRepositoryRejection:
    """Phase C0-E: a bare Git repository has no `.git` child at all —
    repository metadata sits directly at the repository root. None of
    these fixtures rely on --protected-root anywhere."""

    def test_allowed_root_that_is_itself_a_bare_repo_is_rejected(self, tmp_path):
        bare = tmp_path / "bare_repo_as_root"
        _make_bare_git_repo(bare)
        proc = _run_cli(_base_args(FIXTURE_25, str(bare / "out"), str(bare)))
        assert proc.returncode == 2
        assert "Git repository" in proc.stderr

    def test_output_dir_nested_inside_bare_repo_is_rejected(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        bare = allowed / "nested_bare_repo"
        _make_bare_git_repo(bare)
        proc = _run_cli(_base_args(FIXTURE_25, str(bare / "sub"), str(allowed)))
        assert proc.returncode == 2
        assert "Git repository" in proc.stderr

    def test_nonexistent_child_inside_bare_repo_is_rejected(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        bare = allowed / "bare_repo_for_nonexistent_child"
        _make_bare_git_repo(bare)
        proc = _run_cli(
            _base_args(FIXTURE_25, str(bare / "does" / "not" / "exist" / "yet"), str(allowed))
        )
        assert proc.returncode == 2

    def test_relative_traversal_into_bare_repo_is_rejected(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        bare = tmp_path / "bare_repo_traversal_target"
        _make_bare_git_repo(bare)
        traversal = os.path.join(str(allowed), "..", "bare_repo_traversal_target", "sub")
        proc = _run_cli(_base_args(FIXTURE_25, traversal, str(allowed)))
        assert proc.returncode == 2

    def test_symlink_resolving_into_bare_repo_is_rejected(self, tmp_path):
        try:
            probe_target = tmp_path / "_symlink_probe"
            probe_target.mkdir()
            (tmp_path / "_symlink_probe_link").symlink_to(probe_target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("platform does not support symlinks")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        bare = tmp_path / "bare_repo_symlink_target"
        _make_bare_git_repo(bare)
        link = allowed / "link_to_bare_repo"
        link.symlink_to(bare, target_is_directory=True)
        proc = _run_cli(_base_args(FIXTURE_25, str(link / "probe"), str(allowed)))
        assert proc.returncode == 2

    def test_packed_refs_bare_repo_without_refs_dir_is_rejected(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        bare = allowed / "bare_repo_packed_refs"
        _make_bare_git_repo(bare, use_packed_refs=True)
        assert not (bare / "refs").exists()  # confirm this fixture genuinely has no refs/ dir
        proc = _run_cli(_base_args(FIXTURE_25, str(bare / "sub"), str(allowed)))
        assert proc.returncode == 2
        assert "Git repository" in proc.stderr

    def test_no_protected_root_argument_required_for_bare_repo_rejection(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        bare = allowed / "bare_repo_no_protected_root_needed"
        _make_bare_git_repo(bare)
        args = _base_args(FIXTURE_25, str(bare / "sub"), str(allowed))
        assert "--protected-root" not in args
        proc = _run_cli(args)
        assert proc.returncode == 2


class TestBareGitRepositoryFalsePositiveResistance:
    """A directory must never be misclassified as a bare Git repository
    from a partial or coincidental signature."""

    def test_directory_containing_only_head_is_not_flagged(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        lookalike = allowed / "head_only"
        lookalike.mkdir()
        (lookalike / "HEAD").write_text("just a file named HEAD, not a repo\n")
        proc = _run_cli(_base_args(FIXTURE_25, str(lookalike / "sub"), str(allowed)))
        assert proc.returncode == 0, proc.stderr

    def test_directory_with_head_and_objects_but_no_refs_is_not_flagged(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        lookalike = allowed / "head_and_objects_only"
        lookalike.mkdir()
        (lookalike / "HEAD").write_text("ref: refs/heads/main\n")
        (lookalike / "objects").mkdir()
        assert not (lookalike / "refs").exists()
        assert not (lookalike / "packed-refs").exists()
        proc = _run_cli(_base_args(FIXTURE_25, str(lookalike / "sub"), str(allowed)))
        assert proc.returncode == 0, proc.stderr

    def test_directory_with_objects_and_refs_but_no_head_is_not_flagged(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        lookalike = allowed / "objects_and_refs_no_head"
        lookalike.mkdir()
        (lookalike / "objects").mkdir()
        (lookalike / "refs").mkdir()
        assert not (lookalike / "HEAD").exists()
        proc = _run_cli(_base_args(FIXTURE_25, str(lookalike / "sub"), str(allowed)))
        assert proc.returncode == 0, proc.stderr

    def test_safe_allowed_root_from_c0d_fixture_runs_remains_accepted(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        args = _base_args(FIXTURE_25, str(allowed / "run1"), str(allowed))
        args = [a for a in args if a != "--dry-run"]
        proc = _run_cli(args)
        assert proc.returncode == 0, proc.stderr
        assert (allowed / "run1" / "canonical_snapshot.json").exists()


def _pre_c0e_dot_git_only_predicate(path) -> bool:
    """Reimplementation (not an import, not a revert of the real file) of
    the exact Phase C0-D `_is_inside_any_git_repo` ancestor-walk logic
    BEFORE the Phase C0-E bare-repository signature check was added —
    used only to demonstrate, side-by-side against the same fixture, that
    the old predicate would have missed a bare repository while the
    actual current CLI code (imported/exercised via `_run_cli` elsewhere
    in this file) correctly rejects it."""
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            break
        current = parent
    for _ in range(1000):
        if (current / ".git").exists():
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent
    return False


class TestPreC0EPredicateAdversarialEvidence:
    """Direct, side-by-side evidence for Phase C0-E's approved scope: the
    exact same bare-repository fixture path is evaluated once against the
    reimplemented pre-correction `.git`-only predicate (above) and once
    against the real, current CLI (which now includes the bare-repo
    signature check) — without ever reverting instrument_master_offline_cli.py."""

    def test_old_predicate_missed_it_new_cli_rejects_it(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        bare = allowed / "bare_repo_adversarial_evidence"
        _make_bare_git_repo(bare)
        target = bare / "sub"

        # 1. Before the correction: the reimplemented old predicate does
        #    NOT detect this path as being inside a Git repository.
        old_predicate_result = _pre_c0e_dot_git_only_predicate(target)
        assert old_predicate_result is False, (
            "sanity check: the pre-C0-E .git-only predicate was expected to "
            "MISS a bare repository (no .git child exists) — if this fails, "
            "the adversarial-evidence fixture itself is wrong, not the CLI"
        )

        # 2. After the correction: the real, current CLI rejects the same
        #    path, with no output directory or file ever created.
        proc = _run_cli(_base_args(FIXTURE_25, str(target), str(allowed)))
        assert proc.returncode == 2
        assert "REJECTED" in proc.stderr
        assert not target.exists()
        assert not (target / "canonical_snapshot.json").exists()
        assert not (target / "manifest.json").exists()


class TestSymlinkSafety:
    def _symlinks_supported(self, tmp_path) -> bool:
        try:
            target = tmp_path / "_symlink_probe_target"
            target.mkdir()
            link = tmp_path / "_symlink_probe_link"
            link.symlink_to(target, target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            return False

    def test_symlink_into_original_repository_rejected(self, tmp_path):
        if not self._symlinks_supported(tmp_path):
            pytest.skip("platform does not support symlinks")
        orig_repo_backend = os.path.expanduser("~/Documents/StockSense360/stock-predictor/backend")
        if not os.path.isdir(os.path.join(os.path.dirname(orig_repo_backend), ".git")):
            pytest.skip("original repository not present at expected path in this environment")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        link = allowed / "link_to_orig_repo"
        link.symlink_to(orig_repo_backend, target_is_directory=True)
        proc = _run_cli(_base_args(FIXTURE_25, str(link / "probe"), str(allowed)))
        assert proc.returncode == 2

    def test_symlink_into_clean_worktree_rejected(self, tmp_path):
        if not self._symlinks_supported(tmp_path):
            pytest.skip("platform does not support symlinks")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        link = allowed / "link_to_worktree"
        link.symlink_to(BACKEND_DIR, target_is_directory=True)
        proc = _run_cli(_base_args(FIXTURE_25, str(link / "probe"), str(allowed)))
        assert proc.returncode == 2

    def test_symlink_escaping_allowed_root_rejected(self, tmp_path):
        if not self._symlinks_supported(tmp_path):
            pytest.skip("platform does not support symlinks")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside_but_safe"
        outside.mkdir()
        link = allowed / "escape_link"
        link.symlink_to(outside, target_is_directory=True)
        proc = _run_cli(_base_args(FIXTURE_25, str(link / "probe"), str(allowed)))
        assert proc.returncode == 2
        assert "is not a descendant" in proc.stderr

    def test_symlinked_allowed_root_rejected(self, tmp_path):
        if not self._symlinks_supported(tmp_path):
            pytest.skip("platform does not support symlinks")
        real_target = tmp_path / "real_allowed_target"
        real_target.mkdir()
        symlinked_root = tmp_path / "symlinked_allowed_root"
        symlinked_root.symlink_to(real_target, target_is_directory=True)
        proc = _run_cli(_base_args(FIXTURE_25, str(symlinked_root / "out"), str(symlinked_root)))
        assert proc.returncode == 2
        assert "must not be a symlink" in proc.stderr


class TestSourceValidationErrorHandling:
    def test_empty_source_returns_exit_2(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        out = allowed / "should-not-be-created"
        proc = _run_cli(
            [
                "--allowed-output-root",
                str(allowed),
                "--input-file",
                os.path.join(FIXTURES_DIR, "fixture_empty.csv"),
                "--output-dir",
                str(out),
                "--max-records",
                "5",
                "--schema-version",
                "1",
                "--generated-at",
                "2026-07-19T00:00:00Z",
                "--run-id",
                "sve-empty",
                "--fixture-mode",
                "--no-db",
            ]
        )
        assert proc.returncode == 2
        assert proc.stderr.startswith("REJECTED:")
        assert "Traceback" not in proc.stderr
        assert "Traceback" not in proc.stdout
        assert not out.exists()

    def test_missing_mandatory_column_returns_exit_2(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        out = allowed / "should-not-be-created-2"
        proc = _run_cli(
            [
                "--allowed-output-root",
                str(allowed),
                "--input-file",
                os.path.join(FIXTURES_DIR, "fixture_missing_isin_column.csv"),
                "--output-dir",
                str(out),
                "--max-records",
                "5",
                "--schema-version",
                "1",
                "--generated-at",
                "2026-07-19T00:00:00Z",
                "--run-id",
                "sve-missing-col",
                "--fixture-mode",
                "--no-db",
            ]
        )
        assert proc.returncode == 2
        assert proc.stderr.startswith("REJECTED:")
        assert "missing_mandatory_column" in proc.stderr
        assert "Traceback" not in proc.stderr
        assert not out.exists()

    def test_no_partial_manifest_or_snapshot_on_validation_failure(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        out = allowed / "no-partial-output"
        _run_cli(
            [
                "--allowed-output-root",
                str(allowed),
                "--input-file",
                os.path.join(FIXTURES_DIR, "fixture_empty.csv"),
                "--output-dir",
                str(out),
                "--max-records",
                "5",
                "--schema-version",
                "1",
                "--generated-at",
                "2026-07-19T00:00:00Z",
                "--run-id",
                "sve-partial-check",
                "--fixture-mode",
                "--no-db",
            ]
        )
        assert not (out / "canonical_snapshot.json").exists()
        assert not (out / "manifest.json").exists()


class TestOutputPathGuards:
    def test_worktree_root_is_rejected(self, tmp_path):
        repo_root = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
        proc = _run_cli(
            [
                "--allowed-output-root",
                str(tmp_path),
                "--input-file",
                FIXTURE_25,
                "--output-dir",
                repo_root,
                "--max-records",
                "25",
                "--schema-version",
                "1",
                "--generated-at",
                "2026-07-19T00:00:00Z",
                "--run-id",
                "safety-test-reject-root",
                "--fixture-mode",
                "--no-db",
            ]
        )
        assert proc.returncode == 2
        assert "REJECTED" in proc.stderr

    def test_empty_output_dir_is_rejected(self, tmp_path):
        proc = _run_cli(
            [
                "--allowed-output-root",
                str(tmp_path),
                "--input-file",
                FIXTURE_25,
                "--output-dir",
                "",
                "--max-records",
                "25",
                "--schema-version",
                "1",
                "--generated-at",
                "2026-07-19T00:00:00Z",
                "--run-id",
                "safety-test-reject-empty",
                "--fixture-mode",
                "--no-db",
            ]
        )
        assert proc.returncode == 2

    def test_dry_run_creates_no_output_files(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        tmp_out = allowed / "phase-c0-dry-run-does-not-exist-yet"
        proc = _run_cli(_base_args(FIXTURE_25, str(tmp_out), str(allowed)))
        assert proc.returncode == 0, proc.stderr
        assert not tmp_out.exists(), "dry-run must not create the output directory"

    def test_database_suffix_output_dir_still_rejected(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        proc = _run_cli(_base_args(FIXTURE_25, str(allowed / "probe.db"), str(allowed)))
        assert proc.returncode == 2
        assert "database file path" in proc.stderr

    def test_cache_dir_name_in_output_dir_still_rejected(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        proc = _run_cli(_base_args(FIXTURE_25, str(allowed / "__pycache__" / "sub"), str(allowed)))
        assert proc.returncode == 2
        assert "cache/vcs directory name" in proc.stderr

    def test_external_fixture_run_still_succeeds(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        args = _base_args(FIXTURE_25, str(allowed / "run"), str(allowed))
        args = [a for a in args if a != "--dry-run"]
        proc = _run_cli(args)
        assert proc.returncode == 0, proc.stderr
        assert (allowed / "run" / "canonical_snapshot.json").exists()
        assert (allowed / "run" / "manifest.json").exists()
