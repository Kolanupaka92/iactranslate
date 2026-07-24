# IaCTranslate — Roadmap

What's shipped, and what's next. "Shipped" means implemented, tested, and green
in CI on `main`.

## Shipped

**Core pipeline**
- ✅ Deterministic `parse → normalize → agents → validate → render → package`
- ✅ Source registry: VMware, Hyper-V, generic CMDB/spreadsheet, existing cloud fleet
- ✅ Target registry: AWS, Azure, GCP (Terraform)
- ✅ Rule-engine and Anthropic providers behind one interface (offline default)
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
- ✅ Multi-renderer: Pulumi (AWS, Azure, GCP) alongside Terraform
- ✅ **CloudFormation renderer** (AWS-only) — the first renderer that consumes the
  Infrastructure Graph directly rather than the plan, proving the IR seam (ADR 0010)
- ✅ GitOps: opt-in CI/CD workflow (plan on PR, apply on merge)
- ✅ **Policy engine** — pluggable, read-only org rules (`deny`/`warn`) before rendering
- ✅ **Capability flags** — targets advertise supported features (`GET /targets`)
- ✅ **Explainability** — per-decision `reason` + `decisions.json` (why + how sure)
- ✅ **Infrastructure Graph** — renderer-neutral topology IR (`graph.json`); the diagram renders from it
- ✅ **Named, timed pipeline stages** — per-stage `pipeline-trace.json` + structured log (observability)
- ✅ **Async jobs + event bus + audit trail** (single-node) — `POST /jobs` → poll → download,
  event-sourced lifecycle, `GET /audit`; the seam Postgres/Redis/Celery/S3 drop into (ADR 0012)
- ✅ Model schema versioning (`NormalizedVM` / `MigrationPlan`)

**Surfaces & delivery**
- ✅ CLI, FastAPI, Next.js web UI
- ✅ Docker image (non-root, healthchecked)
- ✅ CI: lint, pytest (3.9/3.11/3.12), Docker, web build, real `tofu validate`

## Planned

**New targets & renderers**
- ◻ OCI (Oracle Cloud) target
- ◻ DigitalOcean target
- ◻ AWS CDK renderer

**Deeper migration coverage**
- ◻ Load balancer topology
- ◻ Managed-database re-platforming (RDS / Cloud SQL / Azure SQL) recommendations
- ◻ Kubernetes workload discovery

**Enterprise-platform maturity** (the runtime seams — events, jobs, audit, stages,
capability flags, the `ProjectStore` interface — now exist; these are the durable
backends and integrations that plug into them, via the
[reference architecture](deployment.md#reference-architecture)):

| Milestone | Focus | Builds on |
|---|---|---|
| v2.1 | PostgreSQL store + object-storage artifact store + **durable** job queue (Redis/Celery) | in-memory store, `JobQueue`, event bus (shipped seams) |
| v2.2 | Desktop app (Tauri) over the same core engine | CLI/API (shipped) |
| v2.3 | AuthN (OIDC/SAML) + RBAC + persistent audit | audit trail (shipped in-memory) |
| v2.4 | Notifications (Slack/Teams/Email) + metrics (Prometheus/Grafana/OTel) | event bus + `pipeline-trace` (shipped) |
| v2.5 | CI/CD pipeline generation (Jenkins/GitHub/GitLab/Azure DevOps) | GitOps workflow (shipped for GH Actions) |
| v2.6 | Ticketing (Jira/ServiceNow/Azure DevOps) from the assessment | assessment (shipped) |
| v3.0 | Multi-tenant SaaS + plugin ecosystem + OPA-compatible policy | policy engine, source/target registries (shipped) |

**Renderers via the Infrastructure Graph** (the IR seam now proven twice):
- ✅ CloudFormation (AWS) — walks `graph.json`, not the plan
- ✅ Bicep (Azure) — subscription-scope + module, also walks `graph.json`
- ◻ AWS CDK, Kubernetes back-ends consuming `graph.json`
- ◻ Migrate the Terraform/Pulumi renderers onto the graph

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
