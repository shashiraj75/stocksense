# SES-002 — Python Coding Standards

**Status:** Active — governing.
**Applies to:** all code under `backend/`.
**Specializes:** SES-001.

These standards are descriptive of what Sprint #002 already established as the working pattern in this codebase, made explicit so future code follows the same shape without having to reverse-engineer it from existing files.

---

## 1. No bare hardcoded thresholds

Any numeric cutoff that drives a financial decision (an accept/reject gate, a red flag, a scoring tier, a checklist pass/fail) lives in `services/thresholds.py`, not inline in the function that uses it.

- One frozen dataclass per concept family (`DebtToEquityThresholds`, `ProfitabilityThresholds`, etc.), exposed as a module-level singleton (`DEBT_TO_EQUITY = DebtToEquityThresholds()`).
- Every constant's comment cites the exact file:line it replaced (or, for new thresholds, the file:line where it's first used) and, where the original code documented a reason for the specific value, that reason is preserved in the comment.
- If two genuinely different concepts happen to share a numeric value today (e.g. `TURNAROUND_EXCEPTION_MAX` and `ELEVATED_PENALTY_MIN`, both 150), they get **separate named constants**, not one constant reused in two call sites — a future change to one must not silently move the other.
- A static test (see SES-003, and `tests/regression/test_no_raw_threshold_literals.py` as the reference implementation) must exist for any newly migrated threshold, asserting the old literal form doesn't reappear and the registry import is present.
- Scope discipline applies here specifically: migrating a threshold to the registry is its own change, separate from whatever feature/fix prompted touching that file. Don't quietly migrate unrelated thresholds while passing through a function for another reason — call it out as its own commit.

## 2. No bare `print()` for anything that runs in production

Every module that executes outside of a one-off script uses `logging.getLogger(__name__)`, configured once via `services.logging_config.configure_logging()` (called at process startup in `api/main.py`).

- Level discipline: `log.info` for normal operational milestones, `log.warning` for "something degraded but execution continued" (a fallback path taken, a non-fatal API failure), `log.error` for "this operation failed outright" (a crash, an unhandled exception that aborted the unit of work).
- Module-level statements that run at import time should still go through the logger, not `print()` — see `daily_picks.py`'s universe-size line for the pattern. It's fine that this fires before `configure_logging()` completes in some import orders; the root logger's defaults still apply.
- Don't write a one-off ad hoc cache, retry loop, or rate-limit handler if an existing one already exists for the same pattern elsewhere in the codebase — SEAR-001 found six independent in-memory TTL-cache implementations; that's the failure mode this rule exists to prevent. Check `services/` for an existing pattern before writing a new one.

## 3. Engine return shape

New scoring/decision functions should return `services.engine_contract.EngineResponse` (score, grade, confidence, strengths, weaknesses, risks, explanation, metadata) rather than inventing a new ad hoc dict shape.

- Existing engines (`PredictionEngine`, `quality_factors.py`, `multibagger_scorecard.py`) are not required to migrate retroactively just because this standard exists — that migration is deliberately staged (see SEAR-001 roadmap item 1.6, pairing it with the typed `info`-dict contract) and happens its own sprint, not as a side effect of unrelated work.
- A *new* engine, or a substantial rewrite of an existing one, uses `EngineResponse` from the start.

## 4. Type hints and data shape

- New functions get type hints on parameters and return values. This codebase already does this inconsistently (mix of typed and untyped functions); new code should not add to the untyped pile.
- Prefer a `dataclass` over a bare dict for any structure with a fixed, known set of fields (the threshold registry and `EngineResponse` are the reference examples) — dicts remain appropriate for genuinely dynamic, externally-sourced data (the yfinance `info` dict, JSON API payloads) where the shape isn't fully in this codebase's control.
- Validate at construction time where a value has a hard domain constraint (`EngineResponse.__post_init__`'s `0 <= score <= 100` check is the reference pattern) rather than trusting callers to pass valid data.

## 5. Imports and module structure

- New, single-purpose modules (`thresholds.py`, `logging_config.py`, `engine_contract.py`) are preferred over adding another unrelated concept to an existing god-file (`prediction_engine.py`, `quality_factors.py`). Don't make the god-file problem worse while it's still on the roadmap to be addressed.
- A new module's docstring states: what problem it solves, what (if anything) it replaces, and what is explicitly out of scope for it. `thresholds.py`'s docstring is the reference example — it names the audit that motivated it, the exact migration scope, and the file it deliberately left un-migrated.

## 6. Error handling

- Distinguish "this external dependency is temporarily unavailable" (log a warning, degrade gracefully, e.g. fall back to a secondary data source) from "this is a bug in our own code" (log an error, let it surface — don't swallow it into the same bare `except Exception: pass` used for the first case).
- A bare `except Exception` is acceptable only at a genuine fault-isolation boundary (e.g. one stock's prediction failing shouldn't abort a 50-stock batch) — and even then, log what happened at `warning` or `error` level rather than silently passing.
