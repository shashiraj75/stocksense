# SES-001 — Engineering Standards

**Status:** Active — governing.
**Applies to:** every change to `backend/` and `frontend/` from this point forward.
**Supersedes:** ad hoc practice. Where this document and existing code disagree, existing code is wrong, not this document — fix it forward, don't treat the gap as license to keep diverging.

This is the top-level engineering standard. SES-002 (Python), SES-003 (Testing), and SES-004 (Documentation) are its specializations — read this one first.

---

## 1. Scope discipline

- A change does the thing it was asked to do, and nothing else. Sprint #002's brief ("do not redesign investment methodology, do not change business logic unless required by the engineering improvement") is the model: every sprint/task brief should draw an explicit line around what's in scope, and engineering work stays inside it even when a tempting adjacent fix is sitting right there.
- Found something real but out of scope? Name it in the sprint report's Risks section (see SES-004) or flag it as a follow-up task. Don't fix it inline. SEAR-001's audit and the Sprint #002 report are the reference examples — both name several real issues (the `quality_factors.py` ROE/ROCE convention gap, the `P/E < 35` redundancy) explicitly as *not* fixed in-scope, with a stated reason and a stated future owner (a sprint number).
- No silent broadening. If a migration is scoped to two files, it stays scoped to two files unless the task explicitly says otherwise. `services/thresholds.py`'s own docstring is the reference pattern: it states which files were migrated and which (`quality_factors.py`) deliberately weren't, and why.

## 2. No regressions, ever

- Before calling any change done: the full test suite passes, the full application still imports/builds, and no existing behavior changed unless the task explicitly asked for that behavior to change.
- "No behavior changed" needs *proof*, not assertion. The Sprint #002 threshold migration didn't just claim values were unchanged — it added `test_thresholds.py`'s value-pinning tests, which assert the exact migrated numbers, and a static literal-scan (`test_no_raw_threshold_literals.py`) that fails if the old hardcoded form ever comes back. A refactor's safety claim is only as good as the test that would catch it being wrong.
- A green CI run is the gate, not a courtesy. Every push/PR-triggering workflow (see `.github/workflows/backend_tests.yml`) must pass before a change is considered complete — confirmed by reading the actual run result via the GitHub API or UI, not assumed from a clean local run.

## 3. Evidence over assertion

- Don't write "this should work" — run it and show the output. Don't write "no values changed" — point at the test that proves it. Don't write "logging works" — paste a real log line produced by actually invoking the logger.
- When investigating a bug or auditing a system (see SEAR-001 as the model), every claim about the codebase is backed by a file:line citation or a command output, not a general impression. "SEAR-001 found X" should always be traceable to a specific grep, a specific test run, or a specific API response — and was, throughout that audit.
- Validation claims get re-verified, not assumed durable. Sprint #002's own closing validation pass caught two real, missed gaps (no logging tests, no enforcement that the threshold registry stays used) precisely because someone asked "do we actually know this, or do we just believe it" — that question should be asked again at the end of every sprint, not only when prompted.

## 4. Reversibility and risk-matching

- Match the care taken to the blast radius of the change. A new, additive file (`services/thresholds.py`, `services/engine_contract.py`) is low-risk and can be built directly. A change to a god-file used by every prediction (`prediction_engine.py`) is high-risk and gets extra verification: full-app import check, full suite run, and — once available — a diff against golden-test snapshots.
- Decompositions and architectural moves (SEAR-001's roadmap items like the `PredictionEngine` multi-agent split) are explicitly sequenced *after* test coverage exists for the code being moved, never before. Don't refactor code with no safety net just because the refactor itself seems mechanical.
- Destructive or hard-to-reverse actions (force-push, `git reset --hard`, deleting data, suspending a production service) require explicit user confirmation in the moment, every time — a prior approval for one such action does not generalize to the next one.

## 5. Commit and PR hygiene

- Commit in logical units. A threshold migration, a logging migration, and a new test framework are three commits, not one — exactly as Sprint #002 did. Each commit's message states what changed and, critically, *why* — not just what files moved.
- Every commit message that touches engineering-standard-governed code states explicitly whether it changes behavior, and if so, what proves the new behavior is correct (a test, a CI run, a manual verification with output shown).
- Never use `--no-verify`, force-push to `main`, or skip a failing hook to make a commit land. If a hook or test fails, the fix is to fix the underlying issue, not to bypass the gate.

## 6. When standards and reality disagree

If you find code that violates these standards (a bare hardcoded threshold, a `print()` instead of a logger, a feature with no spec), that is not itself a blocker to unrelated work — but it is always worth naming (a sprint-report Risk, a flagged follow-up task), and it is always in scope to fix *as part of* whatever task already has you touching that exact code.
