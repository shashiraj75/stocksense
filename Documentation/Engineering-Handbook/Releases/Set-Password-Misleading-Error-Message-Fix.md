# Set-Password Misleading Error Message Fix

**Status:** Implemented.

## 1. Trigger

Real user report with a screenshot: a newly-invited user (`psrocks14@gmail.com`) saw "Unable to update the password. Please request a new reset link and try again." on the set-password screen, while the header already showed them signed in ("P psrocks14"). Reported as "no new users are allowed to create their account" plus a question about whether an invite acceptance time limit exists and, if so, why.

## 2. Investigation — the real cause, confirmed directly via Supabase auth logs (not assumed)

Queried the production Supabase project's own `auth_logs` directly (`mcp__.../query_logs`) rather than guessing from the screenshot alone. The exact sequence for this user:

| Time (UTC) | Event |
|---|---|
| 15:41:24 | `user_invited` — invite sent, 200 |
| 15:43:16 | `/verify` 303 + `user_signedup` + `login` — the invite link was clicked and worked correctly; a real session was established |
| 15:43:47 | `PUT /user` **422**: `"Password is known to be weak and easy to guess, please choose a different one."` |

**This was never a link-expiry or account-creation bug.** The invite worked, the session was valid, and the user was already authenticated (exactly why the header showed them logged in). The only real failure was Supabase's own leaked-password protection (a HaveIBeenPwned-backed check — a legitimate security feature, not a defect) rejecting the specific password they chose. The bug was purely in the frontend: `set-password/page.tsx`'s `catch` block showed the same generic "request a new reset link" message for **every** failure, including this one — actively misleading the user into thinking their invite was broken when the fix was simply "choose a different password."

A second, genuinely separate incident was also found in the same logs: a different, unrelated user's `/verify` returned `"email link has expired"` on 2026-09-13. This confirms invite/reset links do have a real expiry — addressed in §4 below, separately from the bug this release fixes.

## 3. Fix (`frontend/src/app/auth/set-password/page.tsx`)

The `catch` block now distinguishes Supabase's typed `AuthWeakPasswordError` (via the SDK's own `isAuthWeakPasswordError` type guard, re-exported from `@supabase/supabase-js`) from every other failure:

- **Weak/leaked password** → "That password is too easy to guess (it matches a known leaked/weak password). Please choose a different, stronger password." — accurate, actionable, no mention of links at all.
- **Everything else** (a genuine session/token failure) → the original "request a new reset link" message, unchanged — this remains the correct message for that category of failure.

## 4. Tests

New `frontend/src/app/auth/set-password/__tests__/setPasswordError.test.tsx` (3 tests, mocking `next/navigation` and `@/lib/supabase` — no existing test precedent in this codebase for a router/Supabase-dependent page, so this establishes the pattern): a real `AuthWeakPasswordError` shows the new message and never the reset-link one; a generic thrown error still shows the reset-link message; a successful update navigates with no error message shown. Sanity-checked per SES-003 §4 (the weak-password branch was disabled, confirmed the corresponding test failed, restored, reconfirmed green). 878/878 full frontend suite passing, clean `tsc --noEmit`.

## 5. The invite-link time-limit question — answered, not fixed (no code to fix)

Confirmed: invite/reset links genuinely do expire — this is a **Supabase Auth platform-level setting** ("Email OTP Expiration" / link TTL, configured in the Supabase Dashboard under Authentication settings), not something governed by any code in this repository. Direct code search confirms there is no application-level invite-sending code at all in this backend — invites are issued directly via Supabase's own admin API, so the expiry window is entirely Supabase's own configured value, not something `stocksense-predictor` controls or can override in code.

**This session could not determine the exact current TTL value** — no available tool exposes Supabase's Auth configuration for reading or writing (only project metadata, logs, and advisories were accessible), and the one real expiry incident found in the logs didn't have its corresponding invite-send event within the 24-hour log query window, so the actual elapsed time to expiry couldn't be computed from evidence.

**Recommendation, not implemented this session:** in the Supabase Dashboard → Authentication → Emails (or Auth settings, depending on dashboard version) → the OTP/link expiration field can be increased — Supabase's hosted platform allows this up to a multi-day window, it does not have to stay at whatever short default is currently configured. It cannot be set to fully "never expire" (an unlimited-lifetime auth token is a real security anti-pattern — a leaked invite email would grant account access forever), but a much longer window (e.g. 3-7 days, giving a new user ample time even if they don't check their email immediately) is a reasonable, low-risk change. This requires dashboard access this session did not have a tool for — flagged for the user to make directly, or for a future session with the appropriate Supabase configuration tool.
