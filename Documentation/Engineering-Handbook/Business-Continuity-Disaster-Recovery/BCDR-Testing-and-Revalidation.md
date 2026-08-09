# BCDR — Testing and Revalidation

**Status:** Active. **As of:** 2026-08-09. See [README.md](README.md) for scope and objectives.

## 1. Clean Rebuild Drill

**Definition:** a fresh `git clone` of `origin/main` into an isolated, non-repo directory, followed by full backend dependency installation, backend unit-test execution, a local (non-production) `/health` check, and a full frontend `npm ci` → `tsc --noEmit` → production `next build`. **Last run:** 2026-08-09, ≈3m50s end-to-end, all steps passed — see BCDR-Recovery-Evidence-Matrix.md. **Cadence:** quarterly, risk-based (see §12) — cheap and non-production-impacting, so full re-runs are low-cost and encouraged more often than the minimum.

## 2. DB Restore Drill

**Definition:** restoring a real Supabase physical backup into a new, isolated, disposable Supabase project, then reconciling schema (table/column/index counts, extensions) and representative row counts against production, followed by deleting the disposable project. **Last run:** 2026-08-09 (prior evidence, cited and reconciled by this baseline, not re-run) — PASSED. **Cadence:** quarterly is the default target, but this is the single most expensive and Production-adjacent drill in this program — per SES-006 §19A's risk-based reuse principle, a full live restore is not mandated every quarter if no material change has occurred (see out-of-cycle triggers, §13) and the prior PASS remains representative. A lighter-weight quarterly check (confirming backup cadence continuity, §8) substitutes in quarters where a full restore is not warranted.

## 3. Deployment Recovery Validation

**Definition:** confirming a Railway (backend) or Vercel (frontend) deployment reaches a healthy state from source, via config-as-code, without manual UI reconstruction. **Last run:** Railway — PR #42 merge, deployment SUCCESS (prior evidence, cited). Vercel — Git-connected auto-redeploy is continuously exercised by normal operation (every merge to `main`), which is itself a standing form of this validation. **Cadence:** effectively continuous for Vercel (every production merge); quarterly explicit review for Railway's config-as-code correctness (confirm `railway.json`/`Dockerfile` still match documented behavior).

## 4. Healthcheck Validation

**Definition:** confirming `GET /health` returns 200 both in production and from a local, non-production-secret startup. **Last run:** production — prior audit (200 confirmed). Local — this baseline, 2026-08-09 (200 confirmed, `{"status":"ok","version":"1.0.0"}`, zero production secrets). **Cadence:** production check is effectively continuous (Railway's own healthcheck polling); local check bundled into the quarterly Clean Rebuild Drill (§1).

## 5. DNS Reconstruction Review

**Definition:** a read-only comparison of the live GoDaddy DNS zone for both domains against the documented baseline (BCDR-Recovery-Evidence-Matrix.md rows for `.com`/`.in` DNS reconstruction), confirming no undocumented drift. **Last run:** 2026-08-01 (prior audit, cited). **Cadence:** quarterly, read-only, low-cost.

## 6. Registrar Review

**Definition:** confirming Domain Lock remains ON, renewal dates remain as expected, and registrar-account MFA remains configured, for both domains. **Last run:** 2026-08-01 (prior audit, cited). **Cadence:** quarterly, read-only, low-cost — plus immediately after any renewal-date change or registrar-account modification.

## 7. MFA/Account Recovery Review

**Definition:** confirming each of the five providers (GitHub, Vercel, Railway, Supabase, GoDaddy) still has MFA/passkey protection active on the account controlling StockSense360's production infrastructure — a configuration check, never a secret/seed inspection. **Last run:** 2026-08-01 (prior audit, cited, GoDaddy additionally live-tested). **Cadence:** quarterly, plus immediately after any change of the account owner or any credential rotation.

## 8. Backup Continuity Review

**Definition:** confirming Supabase's daily physical backup cadence continues without new unexplained gaps (the current baseline has one open, unexplained gap — 05 Aug 2026 — that must be watched for repetition, not just noted once). **Last run:** 2026-08-09 (7 of 8 expected days observed). **Cadence:** monthly — more frequent than the general quarterly cadence, because a repeated gap materially changes the RPO risk assessment and should be caught faster than a quarterly cycle would allow.

## 9. RTO Measurement

**Definition:** timing each drill end-to-end against the objectives in README.md §3/§11, using real `date` timestamps (never estimated). **Last run:** Clean Rebuild Drill — 2026-08-09, ≈3m50s (target ≤2h, met with wide margin). DB Restore Drill — PASSED but elapsed time not captured in the evidence handed to this baseline (named gap, BCDR-Recovery-Evidence-Matrix.md). Full five-provider Full-Service Recovery — never measured end-to-end (named gap). **Cadence:** every time a drill runs; the two named gaps should be closed at the next opportunity a drill of that type is run.

## 10. RPO Validation

**Definition:** confirming the actual gap between consecutive good backups does not exceed the ≤24h commitment, using the observed daily backup timestamps. **Last run:** 2026-08-09 — mostly met (7 of 8 consecutive days), with one gap (05 Aug) that, if it represents a missed day rather than an observation artifact, would itself have exceeded 24h for that specific window — flagged, not minimized. **Cadence:** monthly, bundled with Backup Continuity Review (§8).

## 11. Evidence Retention

All BCDR evidence (this documentation set, the prior provider-specific audit reports, and any future drill outputs) is retained in this Documentation/Engineering-Handbook chapter and its governing Operations/Current-Release-Status.md cross-references. No secret, credential, or MFA seed is ever retained in any evidence artifact. Isolated/disposable infrastructure created for a drill (e.g. a restore-test Supabase project, a temp rebuild directory) is deleted or left harmlessly outside the repository after verification — never left as a standing, forgotten liability.

## 12. Quarterly Revalidation Cadence (Risk-Based Reuse, per SES-006 §19A)

The default cadence for this entire program is **quarterly**, but not every drill is blindly re-run in full every quarter. Cheap, non-Production-impacting checks (Clean Rebuild Drill, DNS Reconstruction Review, Registrar Review, MFA/Account Recovery Review) are re-run in full each quarter, because their cost is low and their evidence value is high. The DB Restore Drill — the most expensive and Production-adjacent — is re-run in full only when either the quarterly cycle is due **and** no equivalent, still-representative evidence exists, or one of the out-of-cycle triggers below fires; a lighter-weight Backup Continuity Review substitutes when a full restore is not yet warranted. This is a deliberate application of SES-006 §19A's principle: expensive, Production-impacting drills are not mandated on a fixed calendar irrespective of actual risk change.

## 13. Out-of-Cycle Triggers

A full or targeted revalidation (not necessarily the entire program) is triggered immediately, outside the normal quarterly cycle, by any of:

- A change of hosting provider, database provider, registrar, DNS provider, or the account owner controlling any of the five providers.
- A major architecture or dependency change (e.g. adding a new stateful service, changing the database provider, moving off Railway/Vercel).
- A change to Supabase's backup policy (frequency, retention, or scope).
- PITR activation or deactivation on Supabase Production.
- Any security incident affecting GitHub, Vercel, Railway, Supabase, or GoDaddy account access.
- A failed recovery test of any kind (a drill that does not pass triggers an immediate root-cause review, not just a note for next quarter).
- The introduction of new stateful infrastructure not covered by this chapter's current scope (e.g. a new database, a new object-storage bucket in active use).

## 14. Residual Risks (Accepted, Not Failures)

These are recorded as known, accepted residual risks — not defects to be silently fixed, and not claims of "100% guaranteed" coverage:

- **Database RPO ≤24h while PITR remains disabled.** Backups are daily, not continuous; a worst-case loss immediately before a backup could lose up to a day of data. Accepted pending a future, separate cost/risk/owner decision on PITR.
- **Database physical backups may not include Supabase Storage objects.** Storage-object usage and backup coverage were not confirmed in this baseline — classified NOT VERIFIED, not assumed clean, in the Evidence Matrix.
- **Single Railway replica/single Supabase region.** An HA limitation (not a DR gap — see BCDR-Architecture-and-Strategy.md §11); a regional outage would cause a service interruption even though data durability and eventual recoverability remain intact.
- **External provider dependencies** (GitHub, Vercel, Railway, Supabase, GoDaddy) — no redundancy exists at any layer; a sustained outage at any one provider degrades or removes a corresponding capability.
- **Incomplete `.in` domain auto-renew evidence** — the prior GoDaddy audit could not cleanly confirm auto-renew status for `.in` via the UI; this is recorded conservatively as PARTIAL, not claimed as VERIFIED.
- **Missing 05 Aug 2026 Supabase backup** — an unexplained gap in the observed daily-backup cadence; no cause has been fabricated or assumed, and its recurrence is specifically monitored via the monthly Backup Continuity Review (§8).
- **Off-provider source mirror not independently proven.** This baseline's rebuild drill proved recovery *from* GitHub, but did not establish or test any independent, non-GitHub source mirror — this remains true after the drill, named explicitly rather than implied away.
- **Full five-provider recovery has never been timed end-to-end in a single combined drill.** Each provider's recovery is individually evidenced; the composite ≤4h full-service RTO commitment has not itself been directly measured.

## 15. Closing Statement (Governed, Not a Final Declaration)

Evidence gathered to date, across the prior provider-specific audits and this baseline's new rebuild-drill evidence, supports the following defensible statement — used here as the standard for what this program can currently claim, not as a declaration of final closure (see README.md §24, Formal Baseline Status):

*"StockSense360 BCDR provides verified recovery coverage for all currently identified material failure scenarios. Residual risks, RPO/RTO limitations, external-provider dependencies and scenarios outside the tested recovery envelope are explicitly documented and governed through periodic revalidation."*

Final CLOSED status for this BCDR baseline requires owner review of this documentation set (via PR review, per the governing task's explicit stop condition) — it is not asserted by this document.
