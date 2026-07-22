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
- ✅ GitOps: opt-in CI/CD workflow (plan on PR, apply on merge)
- ✅ **Policy engine** — pluggable, read-only org rules (`deny`/`warn`) before rendering
- ✅ **Capability flags** — targets advertise supported features (`GET /targets`)
- ✅ Model schema versioning (`NormalizedVM` / `MigrationPlan`)

**Surfaces & delivery**
- ✅ CLI, FastAPI, Next.js web UI
- ✅ Docker image (non-root, healthchecked)
- ✅ CI: lint, pytest (3.9/3.11/3.12), Docker, web build, real `tofu validate`

## Planned

**New targets & renderers**
- ◻ OCI (Oracle Cloud) target
- ◻ DigitalOcean target
- ◻ Bicep renderer
- ◻ AWS CDK renderer

**Deeper migration coverage**
- ◻ Load balancer topology
- ◻ Managed-database re-platforming (RDS / Cloud SQL / Azure SQL) recommendations
- ◻ Kubernetes workload discovery

**Platform**
- ◻ PostgreSQL backend (persistence beyond the in-memory store)
- ◻ Multi-user authentication
- ◻ Async / queued execution for very large estates
- ◻ Cloud Cost Explorer / actual-spend reconciliation

**Considered, deliberately deferred** (would add value but aren't warranted at the
current size — tracked so the reasoning is explicit, not forgotten):

- ◻ **Per-decision explainability** — attach a `reason` to each classification/
  sizing choice (today: confidence scoring per factor, but not a free-text why).
- ◻ **Audit event stream** — emit a structured `AuditEvent` per stage. Valuable
  once runs are long-lived/multi-user; the current pipeline is a sub-second,
  single-shot, deterministic function, so an audit subsystem would be weight
  without a consumer yet.
- ◻ **Event bus** for post-plan fan-out (Slack/email/webhook/telemetry). Deferred
  for the same reason — worth it when there are external subscribers to notify.
- ◻ **`src/` reorg into `core/ decision/ analysis/ renderers/`** — the decision/
  analysis separation is real and documented; the physical move is churn with
  import-breakage risk that isn't justified yet.

## Not planned (out of scope)

These are deliberate boundaries — see [Architecture › Current scope](architecture.md#current-scope).

- ✗ IAM / identity migration
- ✗ Database schema & data migration
- ✗ Serverless / PaaS migration
- ✗ VMware NSX overlay networking

> Priorities are not commitments; this list reflects direction, not a dated plan.
