# Paper Trading Notification Opt-In Default and On-Screen Alert Fix

**Status:** Implemented.

## 1. Trigger

Real user report, with direct evidence: Resend's own dashboard showed frequent "⚠️ X is near your stop loss" / "🎯 X is near your target price" emails going out to many distinct accounts (`psrocks14@gmail.com`, `shashiraj75@gmail.com`, `sshetty.uae@gmail.com`, `vinarts@hotmail.com`), none of whom had ever explicitly opted into proximity alerts. Separately, the same user reported that "auto-triggered stocks notifications are not shown on the laptop and mobile screens for a few days now" for both markets, despite the emails still arriving.

## 2. Investigation — two distinct, real defects found by direct code reading

**Defect 1 — silently opt-out-by-default.** `paper_portfolio.email_notifications_enabled` (`backend/services/postgres_store.py`) was `BOOLEAN NOT NULL DEFAULT true` — every user, on every account creation, was silently subscribed to proximity emails with zero explicit choice. This is the direct, confirmed cause of the email volume complaint.

**Defect 2 — the only reliable on-screen channel didn't exist.** `frontend/src/app/paper-trading/page.tsx`'s `OpenTradeRow` had exactly one on-screen mechanism for a proximity alert: a native browser `Notification()` popup, gated behind **both** `notificationsEnabled` (the same email-preference flag) **and** `Notification.permission === "granted"`. Most browsers default permission to `"default"` (not yet asked) until a user explicitly clicks "Allow" — which most users never do. The separate, always-visible in-page banner mechanism already built into this page (`pushNotification`/`notifications` state, rendered at the top of the page for sell-confirmation and mode-change messages) was **never called for proximity events at all**. Combined with Defect 1 (most users' preference silently `true`, so email fired), the result matched the report exactly: the email arrives, but nothing ever appears on screen, for any user who hasn't separately, manually granted browser notification permission — which is most users, on both web and mobile (mobile browsers commonly don't support the Notification API in a home-screen/PWA context at all).

## 3. Fixes

### Backend (`services/postgres_store.py`, `api/routers/paper_trading.py`)

- `email_notifications_enabled`'s schema default changed to `false` (opt-in). Since `ADD COLUMN IF NOT EXISTS`'s own `DEFAULT` clause only takes effect for a column being newly created — this column already exists in production — an explicit `ALTER TABLE ... ALTER COLUMN ... SET DEFAULT false` was added alongside it.
- New `email_notifications_touched_at TIMESTAMPTZ` column (`NULL` = the user has never explicitly changed this preference). `PATCH /api/paper-trading/notifications` now stamps it on every explicit change.
- One-time backfill: `UPDATE paper_portfolio SET email_notifications_enabled = false WHERE email_notifications_enabled = true AND email_notifications_touched_at IS NULL;` — corrects every pre-existing silently-opted-in row. Guarded by `touched_at IS NULL` (not a plain value-based backfill, unlike this file's own `cash_usd` 10000→100000 precedent) specifically so it is safe to re-run on every startup **forever**: a user who later explicitly re-enables notifications gets `touched_at` set, permanently excluding that row from ever being reset by this statement again.
- `trade_notifier.py`'s Auto Close trigger-confirmation emails are unaffected — confirmed unchanged (they deliberately ignore this preference by design, a trade-event confirmation, not a routine alert).
- Corrected a stale docstring on the PATCH endpoint that inaccurately claimed the preference "gates... proximity/auto-close emails" — it never gated auto-close emails, per `trade_notifier.py`'s own accurate docstring.

### Frontend (`app/paper-trading/page.tsx`)

- `OpenTradeRow` now has two independent effects instead of one:
  1. **In-page banner** (new) — always shown, regardless of `notificationsEnabled` or browser permission. Deliberately not gated by the email preference: it costs nothing to display and carries no spam risk, so there's no reason to suppress it for a user who simply doesn't want *email*.
  2. **Native OS popup** (unchanged) — remains an opt-in bonus layer on top of the banner, still gated by both `notificationsEnabled` and `Notification.permission === "granted"`, for a user who wants to be alerted even while the tab isn't focused.
- Both effects share the same module-level `_notifiedThisSession` dedup `Set`, but with distinct keys (`-target-banner`/`-stop-banner` vs. `-target`/`-stop`) so one channel's per-session dedup can never suppress the other.

## 4. Tests

- New `tests/regression/test_paper_trading_notification_preference_opt_in.py` (backend, 5 tests): 4 structural tests against the schema SQL text (default is `false` on both the `ADD COLUMN` and the explicit `ALTER COLUMN`; the `touched_at` column exists; the backfill's `WHERE` clause is correctly guarded) — following this codebase's own established convention for verifying idempotent schema-init SQL with no isolated unit boundary — plus one functional test of the PATCH endpoint (using the existing `_conn`-mocking pattern from `test_paper_trading_authorization.py`) confirming `email_notifications_touched_at = now()` is actually included in the UPDATE statement.
- New `frontend/src/app/paper-trading/__tests__/notificationChannels.test.ts` (5 tests): structural tests against the component source confirming the in-page banner effect is never gated by `notificationsEnabled` or browser permission, calls `onNotify` for both `nearTarget`/`nearStopLoss` with the correct distinct dedup keys, and that the native popup effect remains unchanged (still gated by both conditions) — the same source-text-verification approach, since this page has no existing test infrastructure and mounting `OpenTradeRow` in isolation would require building a substantial new harness this fix's scope doesn't warrant.
- Both new test files sanity-checked per SES-003 §4: a check was deliberately removed from each (the backfill's `touched_at IS NULL` guard on the backend; the banner effect's "never gated" property on the frontend), confirmed the corresponding test failed, then the file was restored byte-identical and the suite re-confirmed green.
- Full targeted backend verification: `test_paper_trading_authorization.py` (28 tests) + `test_paper_trading_idempotency.py` (14 tests) + the new file (5 tests) — **47/47 passing**.
- Full frontend suite: **875/875 passing**, clean `tsc --noEmit` (two pre-existing, unrelated stray-duplicate-file errors confirmed unchanged from before this session).
- No live-browser visual verification was performed this session — port 3000 was occupied by an unrelated project's dev server on this machine, and starting a second copy on another port wasn't judged worth the added scope given the thorough structural/mutation-tested coverage already in place. Named as an open item, not silently skipped.

## 5. What this does not do

- Does not touch Daily Picks alerts — an entirely separate, unrelated notification mechanism, confirmed via the PATCH endpoint's own docstring and direct code search.
- Does not change Auto Close trigger-confirmation email behavior — confirmed still unconditional (never gated by this preference), by design.
- Does not add a separate toggle for the in-page banner itself — it is now unconditionally always-on, matching the fix's own reasoning (no spam cost, so no reason to make it opt-in).
- Does not retroactively notify users about proximity events that occurred *during* the days the on-screen channel was broken — only prevents the gap going forward.

## 6. Rollback

Two independent, low-risk pieces: the schema/backfill change (revert the `ALTER COLUMN`/backfill lines; the `touched_at` column itself can stay, inert, if reverted) and the frontend wiring change (a plain revert of the `OpenTradeRow` effect split, restoring the single gated effect). Neither touches trade execution, portfolio balances, or any other Paper Trading state.
