# IaCTranslate — Roadmap

What's shipped, and what's next. "Shipped" means implemented, tested, and green
in CI on `main`.

## Shipped

**Core pipeline**
- ✅ Deterministic `parse → normalize → agents → validate → render → package`
- ✅ Source registry: VMware, Hyper-V, Kubernetes, generic CMDB/spreadsheet, existing cloud fleet
- ✅ Target registry: AWS, Azure, GCP, **OCI**, **DigitalOcean** (Terraform) —
  OCI's Flex-shape sizing, honest capability sets, and DigitalOcean's real
  platform gaps (no subnets, no Windows images) are documented in
  [ADR 0022](adr/0022-oci-target.md) and [ADR 0023](adr/0023-digitalocean-target.md)
- ✅ Rule-engine and Anthropic providers behind one interface (offline default),
  reachable from CLI/API/web (not just an env var) with an honest
  `provider_used` record and an AI-written executive-report narrative when
  AI actually ran (see [ADR 0021](adr/0021-ai-integration-reachable-and-honest.md))
- ✅ Validation layer (catalog membership, CIDR overlap, referential integrity)
- ✅ Cloud recommender (cost / fit / OS-affinity, unbiased) — and **Recommendation 2.0**
  (decisiveness, annualized cost, estate notes)
- ✅ Utilization-based right-sizing
- ✅ Automatic OS-image resolution (no placeholder AMI/image IDs)
- ✅ Live pricing: Azure (no creds), AWS (boto3), GCP (Cloud Billing Catalog)

**Migration-platform layer**
- ✅ Assessment engine (readiness score + risk/cost/data-quality findings)
- ✅ Confidence engine (per-decision certainty)
- ✅ Executive report (client-facing HTML)
- ✅ Architecture diagrams (SVG + Mermaid)
- ✅ Infrastructure diff (drift between two snapshots)
- ✅ Brownfield support (Terraform/Pulumi `import` for existing fleets)
- ✅ Multi-renderer: Pulumi, CloudFormation, Bicep, AWS CDK, Kubernetes/KubeVirt
  alongside Terraform — the latter four consume the Infrastructure Graph
  directly rather than the plan, proving the IR seam (ADR 0010, 0013-0017)
- ✅ **Load balancer topology** — multi-instance tiers front behind a load
  balancer (ALB / Standard LB / Network LB per cloud), modeled once in
  `NetworkPlan.load_balancers` and rendered by all 6 renderers + the diagram
- ✅ **Kubernetes workload discovery** — read `kubectl get … -o json` exports
  as a discovery *source* (containers sized from resource requests) — the input
  mirror of the KubeVirt renderer (see [ADR 0019](adr/0019-kubernetes-source.md))
- ✅ **Managed-database re-platforming (advisory)** — flags database-tier
  workloads as RDS / Cloud SQL / Azure SQL candidates in `replatforming.json`
  without changing the plan (see [ADR 0020](adr/0020-managed-db-replatforming.md))
- ✅ **Migration wave planning** — sequences workloads by tier dependency
  (data/cache → app → web) and environment promotion order (dev/test →
  staging → production), with `depends_on` chains, rollback strategy,
  validation checks, and LB-aware downtime estimates in `waves.json` +
  the executive report (see [ADR 0024](adr/0024-migration-wave-planning.md))
- ✅ GitOps: opt-in CI/CD workflow (plan on PR, apply on merge)
- ✅ **Policy engine** — pluggable, read-only org rules (`deny`/`warn`) before rendering
- ✅ **Capability flags** — targets advertise supported features (`GET /targets`)
- ✅ **Explainability** — per-decision `reason` + `decisions.json` (why + how sure)
- ✅ **Infrastructure Graph** — renderer-neutral topology IR (`graph.json`); the diagram renders from it
- ✅ **Named, timed pipeline stages** — per-stage `pipeline-trace.json` + structured log (observability)
- ✅ **Async jobs + event bus + audit trail** (single-node) — `POST /jobs` → poll → download,
  event-sourced lifecycle, `GET /audit`; the seam Postgres/Redis/Celery/S3 drop into (ADR 0012)
- ✅ **Persistent audit + Prometheus metrics** — the audit trail survives a
  restart under `IACTRANSLATE_STORE=sqlite` (verified with a live
  kill-and-restart), and `GET /metrics` serves counters + an in-flight gauge in
  Prometheus exposition format; both are event-bus subscribers, so the routes
  and the pipeline stay uninstrumented (see [ADR 0026](adr/0026-persistent-audit-and-metrics.md))
- ✅ **Multi-tenancy** — user accounts (PBKDF2 passwords), session-cookie login,
  and `Project.owner_id` scoping on every endpoint; cookies work for the
  `<a href>` download/report links that bearer tokens structurally cannot
  authenticate (see [ADR 0027](adr/0027-multi-tenancy-and-session-auth.md))
- ✅ **Rate limiting + security headers** — token buckets per route class, with
  auth throttled by source address *and* by target account (per-IP alone does
  nothing against credential stuffing); limits retunable at runtime without a
  restart (see [ADR 0028](adr/0028-rate-limiting-and-security-headers.md))
- ✅ **Durable artifacts** — `IACTRANSLATE_WORKSPACE_ROOT` puts generated files
  on a real volume instead of `/tmp`, so a download still works after a restart
  (single node; object storage is still the multi-replica answer, see
  [ADR 0029](adr/0029-durable-artifact-workspaces.md))
- ✅ **Password change + reset** — both evict every existing session (a change
  that leaves a stolen cookie working is theatre); tokens hashed, single-use,
  1h TTL, no account enumeration. Reset **delivery is not implemented** — the
  seam ships a log backend rather than unverified SMTP
  (see [ADR 0030](adr/0030-password-change-and-reset.md))
- ✅ **Untrusted-input sanitizing** — uploaded inventory is sanitized at the
  `normalize` waist, closing a proven template injection (a VM named
  `${file("/etc/passwd")}` was evaluated by Terraform) and fixing Azure, which
  produced invalid names for ordinary CMDB data like `web server 01`
  (see [ADR 0031](adr/0031-sanitize-untrusted-inventory-at-normalize.md))
- ✅ **Reviewable output at scale** — above 50 workloads, compute is split per
  environment/tier (`compute-production-web.tf`) instead of one 95k-line file.
  Purely organizational: Terraform still loads the directory as one config, so
  no resource address or state changes
  (see [ADR 0032](adr/0032-split-compute-output-for-reviewability.md))
- ✅ Model schema versioning (`NormalizedVM` / `MigrationPlan`)

**Surfaces & delivery**
- ✅ CLI, FastAPI, Next.js web UI
- ✅ Docker image (non-root, healthchecked)
- ✅ CI: lint, pytest (3.9/3.11/3.12), Docker, web build, real `tofu validate`

## Planned

**Enterprise-platform maturity** (the runtime seams — events, jobs, audit, stages,
capability flags, the `ProjectStore` interface — now exist; these are the durable
backends and integrations that plug into them, via the
[reference architecture](deployment.md#reference-architecture)):

| Milestone | Focus | Builds on |
|---|---|---|
| v2.1 | PostgreSQL store + object-storage artifact store + **durable** job queue (Redis/Celery) — ✅ *metadata* persistence and ✅ *single-node durable artifacts* (`IACTRANSLATE_WORKSPACE_ROOT`, [ADR 0029](adr/0029-durable-artifact-workspaces.md)) shipped now via an opt-in `SqliteProjectStore` (`IACTRANSLATE_STORE=sqlite`), a real stdlib-only stepping stone verified with an actual kill-and-restart, not just Postgres itself (see [ADR 0025](adr/0025-persistent-store-and-bearer-auth.md)) | in-memory store, `JobQueue`, event bus (shipped seams) |
| v2.2 | Desktop app (Tauri) over the same core engine | CLI/API (shipped) |
| v2.3 | AuthN (OIDC/SAML) + RBAC + persistent audit — ✅ **multi-tenant accounts, session-cookie login, and per-project ownership** ([ADR 0027](adr/0027-multi-tenancy-and-session-auth.md)), ✅ a **restart-surviving audit trail** ([ADR 0026](adr/0026-persistent-audit-and-metrics.md)), and ✅ a single-token API key for machine callers ([ADR 0025](adr/0025-persistent-store-and-bearer-auth.md)). Still open: OIDC/SSO, orgs/teams, RBAC roles, email verification + a reset email backend | audit trail (shipped) |
| v2.4 | Notifications (Slack/Teams/Email) + metrics (Prometheus/Grafana/OTel) — ✅ **Prometheus `GET /metrics`** shipped now (counters + in-flight gauge, stdlib-only); OTel distributed tracing and notifications still open (see [ADR 0026](adr/0026-persistent-audit-and-metrics.md)) | event bus + `pipeline-trace` (shipped) |
| v2.5 | CI/CD pipeline generation (Jenkins/GitHub/GitLab/Azure DevOps) | GitOps workflow (shipped for GH Actions) |
| v2.6 | Ticketing (Jira/ServiceNow/Azure DevOps) from the assessment | assessment (shipped) |
| v3.0 | Multi-tenant SaaS + plugin ecosystem + OPA-compatible policy | policy engine, source/target registries (shipped) |

**Renderers via the Infrastructure Graph** (the IR seam now proven four times):
- ✅ CloudFormation (AWS) — walks `graph.json`, not the plan
- ✅ Bicep (Azure) — subscription-scope + module, also walks `graph.json`
- ✅ AWS CDK (Python) — L1 `Cfn*` constructs, also walks `graph.json`
- ✅ Terraform/Pulumi placement (subnet assignment) unified onto the graph —
  fixed a subnet-collapse bug this surfaced (see [ADR 0016](adr/0016-terraform-pulumi-placement-from-graph.md))
- ✅ Kubernetes/KubeVirt — VMs as `VirtualMachine` CRDs, SG ingress as
  `NetworkPolicy`, cloud-agnostic (see [ADR 0017](adr/0017-kubernetes-from-graph.md))
- ◻ Migrate the rest of Terraform/Pulumi's resource generation onto the graph
- ◻ **Emit reusable modules with `for_each`** — the per-environment/tier split
  ([ADR 0032](adr/0032-split-compute-output-for-reviewability.md)) made output
  reviewable; modules would compress ~8k-line files dramatically, but change
  what the customer reads, so it is a separate piece of work

**Considered, deliberately deferred** (tracked so the reasoning is explicit):

- ◻ **`src/` reorg into `core/ decision/ analysis/ renderers/`** — the decision/
  analysis separation is real and documented; the physical move is churn with
  import-breakage risk that isn't justified yet.
- ◻ **Distributed/resumable stages** — the named-stage model is the substrate;
  resume-from-failure needs the persistent store + queue from v2.1 first.

## Not planned (out of scope)

These are deliberate boundaries — see [Architecture › Current scope](architecture.md#current-scope).

- ✗ IAM / identity migration
- ✗ Database schema & data migration
- ✗ Serverless / PaaS migration
- ✗ VMware NSX overlay networking

> Priorities are not commitments; this list reflects direction, not a dated plan.
