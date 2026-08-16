# BCDR — Recovery Runbook

**Status:** Active. **As of:** 2026-08-09. See [README.md](README.md) for scope and objectives.

**Labeling convention used throughout:** every step is marked either **[READ-ONLY VERIFICATION]** — safe to perform at any time, mutates nothing in production — or **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** — must not be performed by an agent autonomously; requires explicit, contemporaneous owner sign-off before execution.

---

## Section A — Lost MacBook

**Scenario:** the developer's laptop is lost, stolen, or destroyed.

1. **[READ-ONLY VERIFICATION]** Confirm no secret or credential existed only on the lost machine. StockSense360's design (see BCDR-Architecture-and-Strategy.md §8) keeps all durable secrets in provider dashboards (Supabase, Railway, Vercel, GoDaddy), not in local files — so a lost machine should not itself be a secret-loss event. If a `.env` file with real values existed locally and is now unrecoverable, treat every secret it held as **potentially exposed** and rotate it via its provider dashboard — this is a security response, not a BCDR data-loss response.
2. **[READ-ONLY VERIFICATION]** Obtain a new/replacement machine and proceed to Section B.
3. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** If any provider account's MFA depended on an authenticator app installed only on the lost device, use that provider's documented account-recovery flow (fallback phone, recovery codes held securely and separately, or provider support) — this is an account-security action requiring owner action directly with the provider, not something this runbook can perform on the owner's behalf.

## Section B — Clean-Computer Reconstruction

**Scenario:** starting from a machine with no pre-existing project state. This baseline's drill directly proved independence from the prior local working tree/project state (isolated temp directory, no reused `node_modules`/venv/caches/local DBs) — see BCDR-Recovery-Evidence-Matrix.md. It did not test a physically separate blank computer, VM, or container, so this scenario is classified STRONGLY EVIDENCED, not VERIFIED.

1. **[READ-ONLY VERIFICATION]** Install prerequisites: Git, Python 3 (a modern 3.x), Node.js (a modern LTS or newer), npm.
2. **[READ-ONLY VERIFICATION]** `git clone git@github.com:shashiraj75/stocksense.git` (or the HTTPS remote, if SSH keys aren't yet set up on the new machine) — see Section C for authenticating this step.
3. **[READ-ONLY VERIFICATION]** Backend: `cd backend && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt`.
4. **[READ-ONLY VERIFICATION]** Frontend: `cd frontend && npm ci`.
5. **[READ-ONLY VERIFICATION]** Copy `backend/.env.example` to `backend/.env` and `frontend/.env.example` to `frontend/.env.local`; fill in real values obtained separately from each provider's dashboard — never from a document. See Section L.
6. **[READ-ONLY VERIFICATION]** Verify locally: backend `python3 -m pytest -m unit -q`; backend local startup (`uvicorn api.main:app`) and a local `GET /health`; frontend `npx tsc --noEmit`; frontend `npm run build`. This exact sequence was run end-to-end for this baseline — see the Evidence Matrix for the result.

## Section C — GitHub / Source Recovery

1. **[READ-ONLY VERIFICATION]** Confirm repository reachability: `git ls-remote git@github.com:shashiraj75/stocksense.git main` or `git clone` it fresh.
2. **[READ-ONLY VERIFICATION]** Confirm the clone's `HEAD`/`origin/main` SHA matches the last-known-good SHA (check GitHub's own commit history in a browser, or a previously recorded SHA from release documentation such as `Operations/Current-Release-Status.md`).
3. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** If GitHub access itself is lost (account lockout, not repository loss), use GitHub's own account-recovery flow — outside this runbook's control.

## Section D — Vercel Reconstruction

1. **[READ-ONLY VERIFICATION]** Confirm current Vercel project status and domain attachment via the Vercel dashboard (read-only inspection).
2. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** If the Vercel project itself is lost: create a new Vercel project, connect it to `shashiraj75/stocksense` (GitHub main = Production source, per prior audit), restore environment variables (`NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, and any `NEXT_PUBLIC_*` feature flags — see `frontend/.env.example`) from their own sources, and re-attach the custom domains (`stocksense360.com`, `stocksense360.in`, `www.stocksense360.in`).
3. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Trigger a Production deployment and verify it serves correctly before pointing DNS (if DNS also needs repointing — see Section I).

## Section E — Railway Reconstruction

1. **[READ-ONLY VERIFICATION]** Confirm current Railway service status, `/health` response, and deployment state via the Railway dashboard/API (read-only).
2. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** If the Railway project itself is lost: create a new Railway service pointed at `shashiraj75/stocksense` (backend build root); Railway will pick up `railway.json` and `backend/Dockerfile` automatically (config-as-code, PR #42). Manually set `PORT=8000` (documented requirement — the app binds a fixed port, does not read Railway's injected `$PORT`) and every required secret from `backend/.env.example`'s documented list (`DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_JWT_SECRET` if applicable, `FRONTEND_URL`, `FINNHUB_API_KEY`, `SCREENER_EMAIL`/`SCREENER_PASSWORD`, `RESEND_API_KEY`, `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, `AUDIT_DATABASE_URL`, `PICKS_SECRET`).
3. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Deploy and confirm `/health` returns 200 before considering the backend recovered.

## Section F — Supabase DB Loss

1. **[READ-ONLY VERIFICATION]** Confirm the scope of loss via the Supabase dashboard (project unreachable, data corrupted, specific tables affected) before taking any restore action.
2. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Do not restore over the live production database without first confirming, with the owner, that this is the intended action and that the most recent physical backup is the correct restore point.

## Section G — Supabase Physical-Backup Restore Procedure

*(Procedure reconciled from the prior isolated restore drill; that drill is cited as evidence, not re-executed by this baseline.)*

1. **[READ-ONLY VERIFICATION]** Identify the most recent good physical backup via the Supabase dashboard's backup list.
2. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Restore that backup into a **new, isolated Supabase project** first (never directly over production) — exactly as the prior drill did (`stocksense360-bcdr-restore-test-20260809`, restoring the 09 Aug 2026 02:44:59 UTC backup).
3. **[READ-ONLY VERIFICATION]** Reconcile the restored project against the last-known-good state: table count, column count, index count, extensions, and key row counts (Auth users, `predictions`, `paper_trades`, `daily_picks_jobs`, `sec_pit_facts`, postmortem reports) — the prior drill's exact reconciliation checklist, reusable verbatim.
4. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Only after reconciliation passes and the owner confirms intent: point production `DATABASE_URL` (Railway env var) at the restored project, or restore in-place if Supabase's own tooling supports it for the specific incident.
5. **[READ-ONLY VERIFICATION]** Delete the isolated test-restore project once verification is complete and the real cutover (if any) is done, to avoid leaving disposable infrastructure running indefinitely — as the prior drill did.

## Section H — Full App Recovery After DB Restore

1. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Update Railway's `DATABASE_URL` to the restored/new database connection string.
2. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Redeploy the Railway backend (or restart it) so it picks up the new connection string.
3. **[READ-ONLY VERIFICATION]** Confirm `/health` returns 200 and a smoke-test read (e.g. `/api/validation/status` or an equivalent read-only endpoint) returns expected shape.
4. **[READ-ONLY VERIFICATION]** Confirm the Vercel frontend still resolves and authenticates correctly against the restored backend/database (login flow, one authenticated read).

## Section I — Domain / DNS Reconstruction

1. **[READ-ONLY VERIFICATION]** Confirm current DNS records via the GoDaddy DNS panel (read-only) against the documented baseline: apex A record, `www` CNAME → Vercel, Domain Connect record, SOA, `_acme-challenge` CNAME, and mail records (SES-related MX/SPF, DKIM, DMARC) for `.com`; apex A, `www` CNAME → Vercel, Domain Connect, SOA, DMARC for `.in`.
2. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** If DNS records are lost or incorrect, recreate them to match the documented baseline above, one record at a time, verifying propagation before moving to the next.

## Section J — Registrar-Account Recovery

1. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** If GoDaddy account access is lost, use GoDaddy's own account-recovery flow (the account has 2-Step Verification with a Google Authenticator default and a phone alternate, per the prior audit) — outside this runbook's control; do not attempt to bypass or work around GoDaddy's own verification.
2. **[READ-ONLY VERIFICATION]** Once access is restored, confirm Domain Lock is still ON for both domains and renewal dates are still as expected (`.com` through 19 Jun 2029, `.in` through 20 Jun 2031) before making any further change.

## Section K — Account/MFA Recovery Principles

1. Every provider (GitHub, Vercel, Railway, Supabase, GoDaddy) should be recovered through that provider's own official account-recovery process — never through a workaround, a support-impersonation attempt, or a stored recovery code kept in this repository (none is kept here).
2. Where a provider offers multiple second factors (e.g. GoDaddy's authenticator app plus phone alternate), the fallback factor is the intended recovery path if the primary factor is unavailable — this is a property of the provider's own configuration, not something this runbook manages.
3. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Any MFA reconfiguration (adding/removing a factor, regenerating recovery codes) is itself an account-security mutation and requires the owner to perform it directly.

## Section L — Secret/Config Recovery Without Storing Secrets

1. **[READ-ONLY VERIFICATION]** Consult `backend/.env.example` and `frontend/.env.example` for the full list of required/optional variables, their purpose, and whether each is financial-decision-impacting — both files are already in source control and contain placeholders only.
2. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Obtain each real value from its own originating provider dashboard (Supabase project settings for `SUPABASE_URL`/keys/`DATABASE_URL`; Finnhub account for `FINNHUB_API_KEY`; Resend account for `RESEND_API_KEY`; Telegram BotFather for `TELEGRAM_BOT_TOKEN`; Screener.in account for `SCREENER_EMAIL`/`SCREENER_PASSWORD`) — never from a document, chat log, or this runbook.
3. Set values directly in the target platform's own secret store (Railway environment variables, Vercel environment variables) — never commit them to the repository.

## Section M — Full-Service Recovery Sequence

Evidenced dependency order (see BCDR-Architecture-and-Strategy.md §13 for rationale):

1. **[READ-ONLY VERIFICATION]** Confirm/restore GitHub source access (Section C).
2. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Restore Supabase database if affected (Sections F–G) — can proceed in parallel with step 1.
3. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Redeploy Railway backend from source, pointed at the (restored) database (Sections E, H).
4. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Redeploy Vercel frontend from source, pointed at the (recovered) backend (Section D).
5. **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** Confirm/restore GoDaddy DNS if affected (Sections I–J) — normally unaffected by an application-layer incident, so usually skipped.

## Section N — Post-Recovery Verification

1. **[READ-ONLY VERIFICATION]** `GET /health` on the Railway backend returns 200.
2. **[READ-ONLY VERIFICATION]** The Vercel frontend loads, and a login attempt against Supabase Auth succeeds.
3. **[READ-ONLY VERIFICATION]** A representative authenticated read (e.g. Portfolio or Watchlist) returns expected data, confirming the database connection and RLS policies are intact.
4. **[READ-ONLY VERIFICATION]** Both domains resolve correctly over HTTPS with valid certificates.
5. **[READ-ONLY VERIFICATION]** Record the recovery's actual elapsed time against the RTO objectives in §11 of the README, and file any gap as a finding for the next revalidation cycle (BCDR-Testing-and-Revalidation.md).

## Section O — Rollback / Abort Criteria

1. If a Supabase restore's reconciliation step (Section G, step 3) does not match the expected schema/row-count fingerprint, **abort the cutover** — do not point production at the restored database; escalate to the owner and Supabase support before any further action.
2. If a Railway or Vercel redeploy fails its healthcheck after a defined number of consecutive attempts (Railway: `restartPolicyMaxRetries: 10`, per `railway.json`), **do not force it live** — investigate the failure using deployment logs before retrying, and consider rolling back to the last known-good deployment (Vercel and Railway both retain deployment history for this purpose).
3. If any recovery step would require entering, deriving, or guessing a secret rather than obtaining it from its provider of origin, **stop and escalate** — this runbook never authorizes secret fabrication or reuse of a possibly-compromised value.
4. Any step marked **[OWNER APPROVAL REQUIRED — PRODUCTION MUTATION]** without documented, contemporaneous owner approval is itself an abort condition — do not proceed.
