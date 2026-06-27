# SES-003 — Testing Standards

**Status:** Active — governing.
**Applies to:** all code under `backend/tests/`.
**Specializes:** SES-001.
**Reference implementation:** `backend/tests/` as established in Sprint #002 — when in doubt, look at how an existing test file does it before inventing a new pattern.

---

## 1. Framework and layout

- `pytest`, configured via `backend/pytest.ini`. Dev-only dependencies (`pytest`, `pytest-asyncio`) live in `backend/requirements-dev.txt`, never in production `requirements.txt`.
- Four categories, each its own directory under `backend/tests/`, each registered as a pytest marker in `pytest.ini`:

| Category | Marker | What it's for |
|---|---|---|
| `tests/unit/` | `@pytest.mark.unit` | A single function/method, no I/O, fast. |
| `tests/integration/` | `@pytest.mark.integration` | Multiple in-process modules/functions together — still no live network/DB calls. |
| `tests/regression/` | `@pytest.mark.regression` | Locks in behavior for a previously-found bug or a known, deliberately-not-yet-fixed issue, so it can't silently change without an intentional test update. |
| `tests/golden/` | `@pytest.mark.golden` | Snapshot-style: asserts a full engine output against a known-good fixture, not just one field. |

- Shared fixtures live in `tests/conftest.py`. Don't hand-roll a fresh `info`-shaped dict (or any other shared fixture shape) inside an individual test file — extend `conftest.py` if the existing fixtures don't cover the case.
- CI runs the full suite on every push/PR touching `backend/**` (`.github/workflows/backend_tests.yml`). A red run blocks the change from being considered complete — see SES-001 §2.

## 2. What "regression" means here, precisely

A regression test is not only "a bug that was fixed, now locked in." Two equally valid uses, both established in Sprint #002:

1. **A fixed bug, locked in** — the conventional case.
2. **A known, deliberately not-yet-fixed issue, locked in on purpose** — `test_pe_checklist_redundancy.py` is the reference example. It documents the proven `P/E < 35` redundancy from SEAR-001 *without* fixing it (fixing it is out of scope until a stakeholder-reviewed sprint), so that an *accidental* change to the redundancy is caught by CI, while the *intentional* future fix is expected to require an intentional test update at the same time. The test's docstring states explicitly which kind of change it permits and which it doesn't.

A static, source-text-based check (no execution at all) is also valid here — `test_no_raw_threshold_literals.py` is the reference example: it greps two specific already-migrated files for the exact pre-migration literal patterns and fails if any reappear. Use this style when the property under test is about *code shape* (did a constant get inlined back into a literal) rather than runtime *behavior*.

## 3. Test isolation, especially for global state

Any test touching global/process-level state (the `logging` module's root logger, a module-level cache dict, an environment variable) must restore that state afterward, regardless of pass/fail — use a fixture with a `yield` and teardown, not a bare setup line. `test_logging_config.py`'s `_reset_logging_state` fixture is the reference pattern: it snapshots root-logger handlers/level and the module's internal `_CONFIGURED` flag, clears them for the test, then restores them in the fixture's teardown.

A specific trap worth naming because it already bit Sprint #002: pytest's own log-capturing plugin attaches its own `StreamHandler` subclass to the root logger around every test. A dedup/detection check that uses `isinstance(h, logging.StreamHandler)` will match that handler too. If a test needs to find "the handler our own code added," identify it by something more specific than its base type (a tag attribute, as `logging_config.py` was fixed to do — see SES-002 §2) rather than assuming `isinstance` is precise enough.

## 4. Sanity-checking your own test

Before trusting a new regression or static-check test, prove it actually catches the failure it claims to catch — don't assume a passing test means the check is meaningful. The reference method (used for `test_no_raw_threshold_literals.py`): deliberately reintroduce the exact bug/literal the test is meant to catch in a throwaway copy or a temporary edit, confirm the test fails with a clear message, then restore the file and confirm it passes again. Show this verification in the conversation/commit, not just the final green state.

## 5. Coverage expectations, honestly stated

- New gate/decision logic (anything resembling `_quality_gate`, `_compute_risk_penalty`, or `compute_scorecard`) gets unit tests covering: the clean-accept path, each distinct rejection/red-flag reason, and any horizon- or sector-specific branch (an exemption, an exception case) — both the branch applying and not applying.
- "Tests exist for this function" and "this function has full coverage" are different claims — say which one is true. Sprint #002's own report was explicit that 31 (later 78) tests were "a foundation, not coverage," and named exactly which major components (the IC engine, the optimizer, `_confidence_engine`, all of `quality_factors.py`) had zero tests at the time. Do the same: a sprint report's Testing Status section names what's covered and what isn't, not just a pass count.
- A new test file's existence does not retroactively justify skipping verification of files it doesn't cover — if a change touches a function with no tests, that gap gets named, not silently absorbed into "tests pass."

## 6. Golden tests specifically

- Assert the *full* output structure (every field, not just the one you changed), so an unrelated regression elsewhere in the same function is caught too. `test_multibagger_scorecard_golden.py` is the reference example — it asserts `verdict`, `red_flags`, `elite_strong_buy`, `max_score`, `score`, and the exact set of passed-check labels, not just one of them.
- If a golden test's expected snapshot needs to change because of an *intentional* behavior change, update the snapshot in the same commit as the behavior change and say why in the commit message — a golden test that's "fixed" by habit, without explaining why the new value is correct, has stopped doing its job.
