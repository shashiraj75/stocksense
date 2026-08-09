# Business Continuity & Disaster Recovery (BCDR)

**Status:** Active — first formal BCDR baseline. Evidence-backed, not yet owner-closed (see §21, Formal Baseline Status).
**As of:** 2026-08-09.
**Scope note:** This chapter documents recovery posture for StockSense360's production platform (Vercel frontend, Railway backend, Supabase database, GoDaddy-registered domains, GitHub source control). It reconciles prior BCDR audit evidence (Railway, Vercel, Supabase, GoDaddy — all previously completed and closed as individual technical audits) with one new piece of evidence generated for this baseline: a blank-environment rebuild drill proving the development machine is not a single point of failure for source and build recovery.

---

## 1. Purpose

StockSense360 depends on five external providers (GitHub, Vercel, Railway, Supabase, GoDaddy) and one local development environment. This chapter answers, with evidence rather than assumption: if any one of those is lost, damaged, or inaccessible, can the platform be recovered, in what time, with what data loss, and by whom? It exists to convert several previously separate, provider-specific audits into one coherent, governed BCDR posture, and to add the one piece of evidence those audits could not produce on their own — proof that source and build recovery does not depend on any single physical machine.

## 2. Scope

In scope: source code recovery (GitHub), frontend recovery (Vercel), backend recovery (Railway), database recovery (Supabase), domain/DNS recovery (GoDaddy), account/MFA recovery principles (all five providers), and secret/configuration reconstruction principles (no secret values recorded anywhere in this chapter). Out of scope: this chapter does not evaluate or change application logic, financial/prediction correctness, or any provider's paid-tier purchase decision (e.g. Supabase PITR) — those remain separate, owner-level decisions referenced but not made here.

## 3. BCDR Objectives

- **Development/source rebuild RTO ≤ 2 hours**
- **Backend/frontend redeployment RTO ≤ 2 hours**
- **Full StockSense360 service recovery RTO ≤ 4 hours**
- **Database RPO ≤ 24 hours while PITR remains disabled**

(PITR activation would need a separate cost/risk/owner decision — see §18, Known Limitations, and BCDR-Architecture-and-Strategy.md.)

## 4. Business-Continuity Principles

- Recovery evidence must be **generated or cited**, never assumed. Every claim in this chapter traces to either a prior closed technical audit (cited, not re-run) or the new rebuild drill in §10.
- No recovery procedure requires entering, storing, or displaying a secret, password, API key, or MFA code in any document.
- Every production-mutating recovery step is explicitly gated on **owner approval** — nothing in this chapter authorizes an autonomous agent to perform a live restore, redeploy, or DNS change.
- Read-only verification and production mutation are always kept in clearly separate sections in the runbook (BCDR-Recovery-Runbook.md).

## 5. Recovery Philosophy

StockSense360's recovery strategy is **provider-native first**: each platform's own backup/restore/redeploy mechanism (GitHub for source, Vercel's Git-based redeploy, Railway's config-as-code redeploy, Supabase's physical daily backups) is the primary recovery path, because each is already continuously exercised by normal operation (every push redeploys; every day a backup runs). This chapter's job is to prove those native mechanisms actually work end-to-end, not to build a parallel, rarely-exercised backup system.

## 6. Failure Domains Covered

| Failure domain | Covered by |
|---|---|
| Loss of the developer's MacBook | §10 rebuild drill; Runbook §A–B |
| Loss/corruption of GitHub source | Runbook §C |
| Vercel project/config loss | Runbook §D |
| Railway project/config loss | Runbook §E |
| Supabase database loss/corruption | Runbook §F–G |
| Full-app recovery after DB restore | Runbook §H |
| Domain/DNS loss or registrar lockout | Runbook §I–J |
| Account/MFA loss (any provider) | Runbook §K |
| Secret/config loss | Runbook §L |

Not covered / explicitly out of scope: loss of the owner's personal devices/authenticator apps beyond the documented MFA fallback principles (§K), and any scenario requiring a purchased capability not currently owned (e.g. Supabase PITR, GoDaddy Domain Protection).

## 7. Recovery Architecture

```
GitHub (canonical source, 2FA + passkey)
   ├─→ Vercel  (Git-connected, GitHub main = Production source) ──→ stocksense360.com / .in (GoDaddy DNS)
   └─→ Railway (config-as-code: railway.json, Dockerfile)        ──→ /health-gated deployment
                                                                        │
                                                                        ▼
                                                              Supabase Postgres 17
                                                              (physical daily backups,
                                                               PITR disabled)
```

See BCDR-Architecture-and-Strategy.md for the full architecture discussion, including DR-vs-HA distinction and single-region/single-replica limitations.

## 8. Authoritative Recovery Sources

- **Source of truth for code:** GitHub `shashiraj75/stocksense`, `main` branch.
- **Source of truth for frontend deployment:** Vercel, Git-connected to GitHub `main`.
- **Source of truth for backend deployment:** Railway, config-as-code (`railway.json`, `backend/Dockerfile`), config hardening merged in PR #42 (SHA `176934a74e98e14f126ae0234f4841c3c594e83a`).
- **Source of truth for application data:** Supabase Production, project ref `doxdexwjeonzigfewfva`, region `ap-south-1`.
- **Source of truth for domains:** GoDaddy registrar records for `stocksense360.com` and `stocksense360.in`.

## 9. Recovery Authority / Owner-Approval Boundaries

No agent or automated process is authorized to perform a production-mutating recovery action (restoring a database over production, redeploying to production, changing DNS, changing registrar settings, rotating account credentials) without explicit, contemporaneous owner approval. This is enforced structurally in BCDR-Recovery-Runbook.md — every mutating step is labeled **OWNER APPROVAL REQUIRED** — and every action already taken to produce this baseline's evidence was read-only or occurred in an isolated, disposable environment (the deleted Supabase restore-test project; the local, non-repo temp rebuild directory).

## 10. Current Verified Recovery Posture

| Area | Posture | Evidence |
|---|---|---|
| Source recoverability | VERIFIED | GitHub CI/fresh-runner execution (prior audit) + this baseline's fresh `git clone` from `origin` reaching an exact SHA match with `origin/main` |
| Dev-machine independence | STRONGLY EVIDENCED | New evidence, this baseline: full isolated-temp-directory rebuild drill, 2026-08-09 — see §11. Not classified VERIFIED because the drill ran in an isolated directory on the same physical laptop, not on a separate physical computer, VM, or container — see §20 |
| Frontend (Vercel) recovery | STRONGLY EVIDENCED | Prior Vercel audit (Git-connected, domains verified, 2FA/passkey posture) + this baseline's clean `npm ci` + `tsc --noEmit` + production `next build`, all passing from a fresh clone |
| Backend (Railway) recovery | STRONGLY EVIDENCED | Prior Railway audit (PR #42 config-as-code hardening, `/health` 200, deployment SUCCESS) + this baseline's local venv install + local `/health` 200 from a fresh clone, without any production secret |
| Database (Supabase) recovery | VERIFIED (restore drill) / PARTIAL (RPO, PITR) | Prior isolated restore-drill evidence — PASSED — reconciled in the Evidence Matrix; PITR remains disabled, so RPO is bounded by the daily backup cadence, not zero |
| Registrar/DNS (GoDaddy) recovery | STRONGLY EVIDENCED (.com) / PARTIAL (.in auto-renew) | Prior GoDaddy audit — see Evidence Matrix for the exact split |

## 11. RTO/RPO Commitments

Restated exactly from §3, cross-referenced against actual measured evidence:

- Development/source rebuild RTO ≤ 2 hours — **met with wide margin**: this baseline's full drill (clone → backend install → backend unit tests → local `/health` 200 → frontend `npm ci` → `tsc --noEmit` → production build) completed in **under 4 minutes** end-to-end (2026-08-09, 19:29:31 UTC → 19:33:21 UTC).
- Backend/frontend redeployment RTO ≤ 2 hours — supported by Railway's prior SUCCESS deployment evidence and Vercel's Git-connected auto-redeploy; not independently re-timed in this baseline (would require a production-mutating redeploy, out of scope).
- Full StockSense360 service recovery RTO ≤ 4 hours — a composite of the above plus Supabase restore time; the prior isolated Supabase restore drill reached ACTIVE_HEALTHY and full reconciliation, but its own elapsed-time figure was not captured in the evidence handed to this baseline — recorded as a documentation gap in §19.
- Database RPO ≤ 24 hours while PITR disabled — **structurally true by backup cadence** (daily physical backups observed), with one named exception: the 05 Aug 2026 backup is missing and unexplained (§19).

## 12. Source-Code Recovery Strategy

GitHub is canonical. Recovery = `git clone` from `origin` (or GitHub's own disaster recovery of the hosted repository, outside StockSense360's control) onto any machine with network access and an authorized SSH/HTTPS credential. This baseline directly re-proved this: see §10 rebuild drill evidence and BCDR-Recovery-Runbook.md §C.

## 13. Clean-Computer Reconstruction Strategy

See BCDR-Recovery-Runbook.md §B for the full step-by-step procedure. In summary: clone from GitHub, install backend dependencies from `backend/requirements.txt`/`requirements-dev.txt` into a fresh virtualenv, install frontend dependencies from `frontend/package-lock.json` via `npm ci`, and reconstruct configuration from `backend/.env.example` and `frontend/.env.example` (both present in the repository and non-secret) plus values obtained separately, out-of-band, from each provider's own dashboard (never from a document).

## 14. Frontend Recovery Strategy

Vercel is Git-connected to GitHub `main`; a lost Vercel project is reconstructed by re-linking a new Vercel project to the same GitHub repository, restoring the same environment variables (from the provider dashboards, not from any document), and re-pointing the verified custom domains. Locally, `frontend/.env.example` documents every variable's purpose and required/optional status without secret values.

## 15. Backend Recovery Strategy

Railway is config-as-code: `railway.json` (healthcheck path, timeout, restart policy) and `backend/Dockerfile` are both committed and version-controlled, so a lost Railway project is reconstructed by creating a new Railway service pointed at the same GitHub repository — the deployment behavior (build, healthcheck, restart policy) is fully defined by files already in source control, not by out-of-band Railway UI configuration. The Railway Postgres instance is legacy/unused (§18) and is not part of backend recovery.

## 16. Database Backup/Recovery Strategy

Supabase Production takes physical daily backups (observed cadence: 09, 08, 07, 06, 04, 03, 02 Aug 2026 UTC; 05 Aug 2026 missing and unexplained). A real isolated restore drill (project `stocksense360-bcdr-restore-test-20260809`) previously proved a physical backup restores to a genuinely independent, point-in-time-accurate database — see the Evidence Matrix for the full reconciliation detail. PITR is not enabled; RPO is therefore bounded by the daily backup cadence, not continuous.

## 17. Registrar/DNS Recovery Strategy

GoDaddy is the registrar for both `stocksense360.com` and `stocksense360.in`. Both domains have Domain Lock enabled (preventing unauthorized transfer) and multi-year renewal terms already paid (through 2029 and 2031 respectively). DNS records for both domains are documented in the Evidence Matrix and Runbook §I; reconstruction is a GoDaddy DNS-panel operation, requiring registrar-account access (§K) — labeled OWNER APPROVAL REQUIRED wherever it would mutate live DNS.

## 18. Account/MFA Recovery Principles

Every provider in the recovery chain (GitHub, Vercel, Railway, Supabase, GoDaddy) has multi-factor authentication enabled and, where available, a passkey or authenticator-app second factor plus a documented fallback (e.g. GoDaddy's phone alternate). This chapter records that MFA is configured and, where directly tested, that it functions (GoDaddy's live incognito-login test) — it never records a secret, TOTP seed, or recovery code. Account-recovery procedures depend on each provider's own identity-verification process, which is outside StockSense360's control and cannot be pre-staged in documentation.

## 19. Secret Recovery Principles

No secret is stored in this documentation chapter, in `.env.example`, or in any Runbook step. `.env.example` files (both `backend/` and `frontend/`) document every variable's *purpose*, *required/optional status*, and *safe placeholder shape* — never a real value. Secret reconstruction after a full environment loss depends entirely on each secret's own provider dashboard (Supabase API keys, Finnhub, Resend, Telegram, Screener.in, etc.) — recovery requires re-issuing or re-copying each one from its origin, individually, by the account owner.

## 20. Known Limitations

- Supabase PITR is not enabled (a paid, optional capability) — RPO is bounded by daily backup cadence, not continuous, and no purchase is authorized by this baseline.
- The 05 Aug 2026 Supabase backup is missing from the observed cadence, and no cause has been confirmed — recorded honestly, not fabricated.
- The Railway Postgres instance (~7.7MB, zero app tables) is legacy/unused; documented, not deleted or modified.
- `.in` domain auto-renew status could not be cleanly confirmed via the GoDaddy UI during the prior audit — recorded as PARTIAL, not claimed as VERIFIED.
- This baseline's rebuild drill did not attempt a full production-equivalent backend startup (e.g. with `USE_POSTGRES=1` against a live database) — that would require production secrets and was correctly out of scope; the local health check used SQLite/no-Postgres mode only.
- The rebuild drill ran in an isolated, non-repo temp directory on the *same physical MacBook* used for prior development — it was not executed on a physically separate computer, VM, or container. It directly proves independence from the prior local working tree/project state (no reused `node_modules`, venv, caches, or local DB files), which is why dev-machine independence is classified STRONGLY EVIDENCED, not VERIFIED, in §10. A true blank-computer/VM/container test remains a future strengthening step, not yet performed.
- Full-service (all five providers together) recovery time has never been measured end-to-end in a single drill — each provider's recovery evidence exists independently, but no combined timed exercise has occurred.
- Off-provider (non-GitHub) source mirroring is not independently proven; GitHub itself remains the single canonical source-hosting provider.

## 21. Accepted Residual Risks

See BCDR-Testing-and-Revalidation.md's "Residual Risks" section for the complete, governed list (RPO bound, Storage-object backup scope, single Railway replica/region, external-provider dependency chain, `.in` auto-renew evidence gap, missing 05 Aug backup).

## 22. Companion Documents

- [BCDR-Architecture-and-Strategy.md](BCDR-Architecture-and-Strategy.md) — recovery architecture, DR-vs-HA distinction, provider dependency map, accepted risks and future options.
- [BCDR-Recovery-Runbook.md](BCDR-Recovery-Runbook.md) — step-by-step recovery procedures, sections A–O, with explicit READ-ONLY vs. OWNER-APPROVAL-REQUIRED labeling.
- [BCDR-Recovery-Evidence-Matrix.md](BCDR-Recovery-Evidence-Matrix.md) — the full capability-by-capability evidence table.
- [BCDR-Testing-and-Revalidation.md](BCDR-Testing-and-Revalidation.md) — drill definitions, revalidation cadence, and out-of-cycle triggers.

## 23. Governance / Revalidation Cadence

BCDR evidence is revalidated on a **risk-based quarterly cadence** (SES-006 §19A — not a blind full re-run every quarter) plus a defined set of out-of-cycle triggers (hosting/DB/registrar/DNS/auth-owner change, major architecture change, backup-policy change, PITR activation/deactivation, security incident, failed recovery test, new stateful infrastructure). Full detail in BCDR-Testing-and-Revalidation.md.

## 24. Formal Baseline Status

**This is the first formal BCDR baseline.** It is evidence-backed and internally consistent, but it is **NOT formally CLOSED** — per this task's explicit governance boundary, final closure requires owner review (of this documentation set, via PR review) before the BCDR program can be declared closed. See §21 of this chapter's governing task and the recommendation in the accompanying report for the exact "READY FOR OWNER REVIEW" recommendation and its reasoning.

## 25. Standard Closure Language (for future use, not asserted as final here)

Once owner review is complete and no open findings remain, this chapter may adopt the standard phrase: *"StockSense360 BCDR provides verified recovery coverage for all currently identified material failure scenarios. Residual risks, RPO/RTO limitations, external-provider dependencies and scenarios outside the tested recovery envelope are explicitly documented and governed through periodic revalidation."* This baseline does not assert that language as a completed CLOSED status — see §24.

## 26. Relationship to Prior Audits

This chapter does not re-run or duplicate the previously completed Railway, Vercel, Supabase, or GoDaddy technical audits — it cites and reconciles their evidence (see the Evidence Matrix for exact source attribution per row) and adds exactly one new artifact: the blank-environment rebuild drill in §10/§11, run specifically to prove the developer's own machine is not a single point of failure.

## 27. Document Index

| Document | Purpose |
|---|---|
| README.md (this document) | Executive/architectural overview |
| BCDR-Architecture-and-Strategy.md | Recovery architecture and strategic reasoning |
| BCDR-Recovery-Runbook.md | Step-by-step operational recovery procedures |
| BCDR-Recovery-Evidence-Matrix.md | Full evidence table, per capability |
| BCDR-Testing-and-Revalidation.md | Drill definitions and revalidation governance |
