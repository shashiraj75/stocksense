# BCDR — Architecture and Strategy

**Status:** Active. **As of:** 2026-08-09. See [README.md](README.md) for scope and objectives.

## 1. GitHub as Canonical Source

GitHub (`shashiraj75/stocksense`) is the single canonical source of truth for all application code, infrastructure-as-code (`railway.json`, `backend/Dockerfile`), CI workflows, and this documentation. Every deployment path (Vercel, Railway) derives from GitHub `main`, not from any locally-held artifact. This is a deliberate single-source-of-truth choice: it means source recovery reduces to "does a clone of `origin/main` reach the same SHA and build," which this baseline directly tested (see BCDR-Recovery-Runbook.md §C).

## 2. Source-Loss and Dev-Machine-Loss Scenarios

Two distinct scenarios are covered, and they are not the same risk:

- **Source loss** (GitHub itself becomes unavailable or the repository is deleted/corrupted): mitigated only by GitHub's own platform-level durability and any developer's locally-cloned working copy, which is itself a de facto mirror. StockSense360 does not currently maintain an independent, deliberate off-GitHub mirror — this is named as a residual risk (BCDR-Testing-and-Revalidation.md), not hidden.
- **Dev-machine loss** (the developer's MacBook is lost, stolen, or destroyed): mitigated entirely by GitHub being canonical — a fresh clone onto any machine reconstructs a working repository with zero dependency on the lost machine's local state. This baseline generated new, direct evidence for this scenario specifically (a real `git clone` into an isolated temp directory, followed by full backend/frontend dependency installation and a local health check) rather than relying on the general principle alone.

## 3. Vercel Recovery Architecture

Vercel's Production source is Git-connected to GitHub `main` — every push to `main` triggers an automatic Production redeploy. Recovery from a lost or misconfigured Vercel project is: create a new Vercel project, link it to the same GitHub repository, restore environment variables from their own source (Supabase dashboard, backend `.env.example` documentation of what's required), and re-attach the verified custom domains (`stocksense360.com`, `stocksense360.in`, `www.stocksense360.in`). No Vercel-specific state exists that isn't reconstructable from GitHub plus provider dashboards.

## 4. Railway Recovery Architecture

Railway's deployment behavior is defined by version-controlled files, not manual UI configuration: `railway.json` sets the healthcheck path (`/health`), healthcheck timeout (300s), restart policy (`ON_FAILURE`, max 10 retries), and `backend/Dockerfile` defines the build and the fixed `uvicorn --port 8000` bind (with the accompanying documented requirement that Railway's `PORT` variable be manually set to `8000` to match, since the app does not read `$PORT` dynamically — see `backend/.env.example`). Recovery from a lost Railway project is: create a new Railway service pointed at the same GitHub repository, set the documented non-secret `PORT=8000` and the required secrets (from their own sources), and let config-as-code drive the rest. Config-as-code hardening was already merged (PR #42, SHA `176934a74e98e14f126ae0234f4841c3c594e83a`), and a subsequent deployment reached SUCCESS with `/health` returning 200 — cited, not re-verified, in this baseline.

## 5. Supabase Recovery Architecture

Supabase Production (project ref `doxdexwjeonzigfewfva`, region `ap-south-1`, PostgreSQL 17) takes physical daily backups. Recovery from database loss or corruption is: restore the most recent good physical backup into a new or the same Supabase project. This was directly, physically tested in a prior isolated drill — restoring the 09 Aug 2026 02:44:59 UTC backup into a disposable project (`stocksense360-bcdr-restore-test-20260809`), reaching ACTIVE_HEALTHY, and reconciling schema (56 tables, 776 columns, 193 indexes), extensions, and row-level data (Auth users 4/4, predictions 79,373, paper_trades 68, daily_picks_jobs 64, sec_pit_facts 95,767, postmortem reports 3) against production. The restore project was deleted after verification, per BCDR discipline of not leaving disposable infrastructure lying around. See the Evidence Matrix for the full reconciliation table.

## 6. PITR Status

Point-in-Time Recovery is **not enabled** on Supabase Production. It is a paid, optional Supabase capability that would reduce RPO from "since the last daily backup" (currently ≤24h under normal cadence) to near-continuous. No purchase is authorized by this baseline; enabling it is named explicitly as a future option requiring a separate cost/risk/owner decision (§14).

## 7. GoDaddy Registrar + DNS Role

GoDaddy is the registrar of record for both `stocksense360.com` and `stocksense360.in`, and hosts the authoritative DNS zone for both. Both domains have Domain Lock enabled (blocks unauthorized transfer-out) and multi-year prepaid renewal terms (`.com` through 19 Jun 2029; `.in` through 20 Jun 2031). DNS for both domains points the apex at the application's A record and `www` at Vercel via CNAME, alongside SOA, `_acme-challenge`, and mail-related records (SES MX/SPF, DKIM, DMARC) documented per-domain in the Evidence Matrix. GoDaddy account access itself is hardened with mandatory 2-Step Verification (Google Authenticator default, phone alternate), live-tested via a fresh incognito login that correctly produced and passed an authenticator challenge.

## 8. Secret/Config Reconstruction Boundaries

No BCDR document records a secret value. Reconstruction of secrets after a full loss is bounded to: (a) values that live only in a provider's own dashboard (Supabase keys, Finnhub, Resend, Telegram, Screener.in credentials, `SUPABASE_JWT_SECRET` if still on legacy HS256) — re-obtained individually from each provider by the account owner; (b) values that are deployment-platform-injected and never manually set (`RAILWAY_PUBLIC_DOMAIN`, `RAILWAY_GIT_COMMIT_SHA`); (c) non-secret configuration that is safe to document directly, which `backend/.env.example` and `frontend/.env.example` already do, including which flags are financial-decision-impacting versus purely cosmetic. This boundary is structural, not just a writing convention — it is why `.env.example` ships placeholder values only, and why this chapter never reproduces them with anything but placeholders.

## 9. Provider Dependencies / Failure Domains

StockSense360's live availability depends on five independent providers plus the public internet path between them: GitHub (source, CI), Vercel (frontend hosting, DNS edge for `www`), Railway (backend hosting), Supabase (database, auth), GoDaddy (registrar, DNS apex). A sustained outage at any one materially degrades or removes a capability (e.g. a GitHub outage blocks new deployments but does not take down an already-running Vercel/Railway deployment; a Supabase outage removes data access entirely; a GoDaddy DNS outage removes reachability entirely regardless of Vercel/Railway health). No provider redundancy (e.g. a secondary DNS host, a secondary database region) currently exists — this is named as a limitation in §10, not concealed.

## 10. Single-Region / Single-Replica Limitations

Railway currently runs a single backend replica in a single region; Supabase Production runs in a single region (`ap-south-1`) with no read replica or multi-region failover. **This is explicitly a High-Availability (HA) limitation, not evidence that Disaster Recovery (DR) is absent** — see §11 for the distinction. A regional outage at either provider would cause a service interruption (an HA/uptime event) but does not, on its own, threaten data durability or the ability to eventually recover (a DR event), because Supabase's physical backups and GitHub's source hosting are independent of any single Railway/Supabase region's live availability.

## 11. DR vs. HA Distinction

**High Availability** is about minimizing *interruption* — redundant replicas, multi-region failover, zero-downtime deploys. **Disaster Recovery** is about guaranteeing *recoverability* after loss — can the system be rebuilt, and how much data is lost in doing so. StockSense360's current posture is: **DR-evidenced, HA-limited.** This baseline's evidence (rebuild drill, restore drill, config-as-code deployments) speaks to DR — recoverability — not to HA — continuous uptime during a regional outage. Conflating the two would overstate what has been proven; this chapter deliberately does not.

## 12. Backup Philosophy

Backups are taken at the layer that actually needs restoring, using each provider's own native mechanism, rather than a custom parallel system: Supabase's physical daily backups for data, GitHub's full version history for source, Vercel/Railway's Git-triggered redeploys for build artifacts (nothing else needs backing up — a deployment is always reproducible from source plus config). This avoids the common BCDR failure mode of a backup system that is itself never tested because it's separate from normal operation.

## 13. Recovery Ordering

In a full-loss scenario, the evidenced dependency order is: (1) GitHub source access restored/confirmed first — everything else derives from it; (2) Supabase database restored from the most recent good physical backup (independent of source, can proceed in parallel with 1); (3) Railway backend redeployed from source, pointed at the restored database; (4) Vercel frontend redeployed from source, pointed at the restored backend; (5) GoDaddy DNS confirmed/repointed if domains were affected — normally unaffected by an application-layer incident. See BCDR-Recovery-Runbook.md §M for the full sequenced procedure.

## 14. Off-Provider Resilience Considerations

No off-provider (non-GitHub) source mirror is currently maintained, and this baseline's rebuild drill did not establish one — it proved recovery *from* GitHub, not independence *from* GitHub. This remains an open, named consideration for a future baseline, not a claimed capability. Similarly, no secondary DNS provider or secondary database region is in place. These are not recommended as immediate spend — see §16.

## 15. Accepted Risks

- Single canonical source host (GitHub) with no independent mirror.
- Single-region Supabase, single-replica Railway (HA limitation, not a DR gap — §11).
- RPO bounded by daily backup cadence, not continuous, while PITR is disabled.
- The 05 Aug 2026 Supabase backup gap remains unexplained.
- `.in` domain auto-renew status not cleanly confirmed via the GoDaddy UI.
- Full five-provider recovery has never been timed end-to-end in one combined drill.

## 16. Future Resilience Options (not recommended for immediate spend)

- Supabase PITR activation, if the business decides ≤24h RPO is insufficient — a cost/risk/owner decision, not an engineering default.
- An independent, periodic off-GitHub source mirror (e.g. a second private remote), if source-hosting-provider risk is judged material enough to justify the operational overhead.
- A documented, tested Railway multi-region or Supabase read-replica posture, if HA (not DR) requirements change.

This document does not recommend spend on any of the above purely to raise an audit score — each is named as an option only, contingent on an actual business decision about acceptable risk.
