"""
Trade Postmortem Sprint 3A, Stage J4B.3 — deterministic traceability
helper. Test-governance code, not production code.

Separates STATIC proof (a manifest-cited pytest node exists and would
be collected) from RUNTIME proof (that node actually ran and passed,
per a JUnit XML report) — a pytest `--collect-only` run can never, by
itself, prove a test passed; this module never conflates the two.

Consumes ALREADY-GENERATED text (a `pytest --collect-only -q` output
file, or a JUnit XML report) — it never launches pytest itself, and in
particular never launches one pytest subprocess per manifest row.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def load_manifest(manifest_path) -> dict:
    return json.loads(Path(manifest_path).read_text())


def check_manifest_ids_unique(manifest: dict) -> list[str]:
    """Returns a list of error strings; empty means every requirement ID
    in the manifest appears exactly once."""
    errors = []
    seen = set()
    for req in manifest.get("requirements", []):
        req_id = req.get("id")
        if req_id in seen:
            errors.append(f"duplicate requirement ID: {req_id}")
        seen.add(req_id)
    return errors


def parse_collect_only_output(text: str) -> set[str]:
    """Extracts pytest node IDs from `pytest --collect-only -q` output.
    That output's node-ID lines look like
    `backend/tests/unit/foo.py::TestClass::test_method` (optionally with
    a trailing `[param-id]` for parametrized tests) — this parser keeps
    only lines matching that `::`-delimited shape, skipping summary
    lines (e.g. "N tests collected in Ns"), warnings, and blank lines."""
    node_ids = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or "::" not in stripped:
            continue
        if stripped[0].isdigit() or stripped.startswith(("=", "-", "collected", "no tests")):
            continue
        # A genuine collected node line has no leading whitespace-free
        # prose before the path; require it to start with a path-like
        # token containing "/" or end in ".py::...".
        if ".py::" not in stripped:
            continue
        node_ids.add(stripped)
    return node_ids


def _node_is_satisfied(cited_node: str, collected_nodes: set[str]) -> bool:
    """A cited node satisfies collection either by an exact match, or —
    for a parametrized test cited by its unparameterized base node ID —
    when a collected node begins with the cited node ID immediately
    followed by "[" (its parametrization bracket). This is the ONLY
    permitted fuzzy match; no other prefix or substring match is
    accepted."""
    if cited_node in collected_nodes:
        return True
    prefix = cited_node + "["
    return any(collected.startswith(prefix) for collected in collected_nodes)


def validate_static(manifest: dict, collected_nodes: set[str], repo_root: Path) -> list[str]:
    """Stage J4B.3, Stage 7B — static collection validation. Returns a
    list of error strings; empty means every manifest node exists in
    the given collected-node set (or is satisfied via the parametrized-
    prefix rule) and every cited file exists on disk."""
    errors = []
    errors.extend(check_manifest_ids_unique(manifest))

    seen_files = set()
    for req in manifest.get("requirements", []):
        for node in req.get("nodes", []):
            file_part = node.split("::", 1)[0]
            seen_files.add(file_part)
            if not (repo_root / file_part.removeprefix("backend/")).exists() and not Path(file_part).exists():
                # Accept either a path relative to the backend/ dir root
                # or relative to the repo root, depending on caller
                # convention -- but require it to exist under at least
                # one of those two roots.
                candidate_a = repo_root / file_part
                candidate_b = repo_root / "backend" / file_part.removeprefix("backend/")
                if not candidate_a.exists() and not candidate_b.exists():
                    errors.append(f"{req['id']}: cited file does not exist: {file_part}")
            if not _node_is_satisfied(node, collected_nodes):
                errors.append(f"{req['id']}: cited node not found in collection: {node}")
    return errors


def parse_junit_xml(junit_xml_path) -> dict[str, str]:
    """Returns {node_id: outcome} where outcome is one of
    "passed"/"failed"/"error"/"skipped". node_id is reconstructed as
    `{file}::{classname-tail}::{name}` matching pytest's own `::`
    node-ID convention as closely as JUnit's `classname`/`name`
    attributes allow."""
    tree = ET.parse(junit_xml_path)
    root = tree.getroot()
    results: dict[str, str] = {}
    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        file_attr = testcase.get("file")
        # pytest's junit `classname` is a dotted path where every
        # segment is a module/package name EXCEPT an optional final
        # segment, which is the test class name when one exists (e.g.
        # "tests.unit.test_foo.TestBar" for a class-based test, vs
        # "tests.unit.test_foo" for a plain function-based test with no
        # class -- the LAST segment starting with an uppercase letter is
        # the sole, reliable signal distinguishing the two, since a
        # class name is the only segment permitted to violate Python's
        # lowercase module/package naming convention here). Blindly
        # replacing every dot with "/" (the original, buggy approach)
        # corrupts this by treating the class-name segment as though it
        # were itself a directory/file path component.
        segments = classname.split(".") if classname else []
        if segments and segments[-1][:1].isupper():
            class_tail = segments[-1]
            module_segments = segments[:-1]
        else:
            class_tail = None
            module_segments = segments

        module_path = file_attr if file_attr else "/".join(module_segments) + ".py"

        if class_tail:
            node_id = f"{module_path}::{class_tail}::{name}"
        else:
            node_id = f"{module_path}::{name}"

        if testcase.find("failure") is not None or testcase.find("error") is not None:
            outcome = "failed"
        elif testcase.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        results[node_id] = outcome
    return results


def validate_runtime(manifest: dict, junit_results: dict[str, str]) -> list[str]:
    """Stage J4B.3, Stage 7C — runtime pass validation against a JUnit
    XML report. Returns a list of error strings; empty means every
    manifest node ran, passed, and was not skipped. Matches on exact
    node ID or the parametrized-prefix rule, same as validate_static."""
    errors = []
    junit_node_ids = set(junit_results.keys())
    for req in manifest.get("requirements", []):
        for node in req.get("nodes", []):
            matches = [n for n in junit_node_ids if n == node or n.startswith(node + "[")]
            if not matches:
                errors.append(f"{req['id']}: cited node did not run (absent from JUnit report): {node}")
                continue
            for matched in matches:
                outcome = junit_results[matched]
                if outcome == "skipped":
                    errors.append(f"{req['id']}: cited node was skipped: {matched}")
                elif outcome != "passed":
                    errors.append(f"{req['id']}: cited node did not pass ({outcome}): {matched}")
    return errors
