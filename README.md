# IaCTranslate

**Turn any infrastructure inventory into production-ready Infrastructure-as-Code
for any major cloud — deterministically, offline, and unbiased.**

Feed it a VMware (RVTools) export, a Hyper-V dump, a CMDB/spreadsheet, or an
existing AWS/Azure fleet. Get back reviewable **Terraform, Pulumi, or
CloudFormation** for **AWS, Azure, or GCP**, plus a migration assessment, a
cloud recommendation, and a client-ready report — without ever connecting to
the customer environment.

It's **not** "an LLM writes Terraform." It's a deterministic translation layer:

```
parse → normalize → agents(classify · rightsize · network) → validate → render → package
```

AI (optional — `--provider anthropic`, per-request via the API, or a web UI
toggle) makes only *structured decisions*; templates emit the actual IaC; a
validation layer re-checks every decision, and `plan.provider_used` always
reports which engine actually ran. Same input → same output, every time — and
CI proves the output is valid against the real cloud providers (`tofu validate`).

## Key features

- **Any source → any cloud.** Source registry (VMware · Hyper-V · Kubernetes ·
  generic CMDB · existing cloud fleet) and target registry (AWS · Azure · GCP · OCI · DigitalOcean),
  both behind protocols — new ones need no pipeline changes.
- **Unbiased cloud recommendation.** Ranks all three clouds on cost, sizing fit,
  and OS affinity, with explicit weights and plain-English rationale. No vendor
  gets a thumb on the scale.
- **Right-sized from real usage.** When utilization data is present, instances
  are sized to actual demand, not to over-provisioned allocations.
- **Load balancer topology.** Any tier with more than one instance gets fronted
  by a load balancer (ALB / Standard LB / Network LB, per cloud) — modeled
  once, rendered by all six IaC formats and the diagram.
- **Managed-DB re-platforming advice.** Database-tier workloads are flagged as
  RDS / Cloud SQL / Azure SQL candidates (`replatforming.json`) — advisory only;
  the plan still lift-and-shifts them, because data migration is out of scope.
- **Migration wave planning.** Workloads are sequenced by tier dependency
  (data/cache → app → web) and environment order (dev → staging → prod), with
  `depends_on` chains, rollback strategy, and validation checks per wave
  (`waves.json`) — honest about not discovering cross-application dependencies
  it has no signal for.
- **Persistent store + bearer auth (stopgap).** Project metadata survives a
  process restart via an opt-in SQLite store (`IACTRANSLATE_STORE=sqlite`),
  and endpoints require `Authorization: Bearer <key>` when
  `IACTRANSLATE_API_KEY` is set — both stdlib-only, both verified with a real
  kill-and-restart, neither pretending to be Postgres or OIDC/RBAC.
- **Multi-tenant.** Opt in with `IACTRANSLATE_AUTH=session` for user accounts,
  session-cookie login, and per-project ownership — one customer cannot see
  another's inventory, and a project owned by someone else returns 404 rather
  than 403 so ids can't be enumerated. Username/password, not OIDC/SSO yet.
- **Password change + reset.** Both evict every existing session, so a stolen
  cookie stops working the moment a password changes. Tokens are hashed,
  single-use, and expire in an hour. Reset *delivery* is intentionally not
  implemented — the seam logs the link rather than shipping untested SMTP.
- **Rate limited.** Token buckets per route class, with login throttled by
  source address *and* by target account — per-IP alone does nothing against
  credential stuffing. Limits retune at runtime without a restart; per-process,
  so multi-replica enforcement still needs shared state.
- **Operable at runtime.** The audit trail survives a restart under the same
  `sqlite` switch, and `GET /metrics` serves Prometheus counters plus an
  in-flight gauge. Both are event-bus subscribers, so routes and the pipeline
  stay free of instrumentation — it's counters and durable history, not
  distributed tracing.
- **A full migration-platform layer.** Pre-migration **assessment** (readiness +
  risks), **confidence** scoring, an **executive report**, **architecture
  diagrams**, **infrastructure diff**, **brownfield** adoption (import blocks),
  **Pulumi**, **CloudFormation** (AWS-only), **Bicep** (Azure-only),
  **AWS CDK** (Python, AWS-only), and **Kubernetes/KubeVirt** (any cloud)
  renderers — the latter four walking the
  [Infrastructure Graph](docs/adr/0010-infrastructure-graph.md) — and opt-in
  **GitOps** CI/CD.
- **Policy engine.** Enforce org rules — no public subnets, approved instance
  families, budget caps, naming conventions — as pluggable, read-only policies
  (`deny` blocks rendering, `warn` reports) before any IaC is written.
- **Offline & auditable.** Runs with no internet and no API keys; the output is
  reproducible and reviewable.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python scripts/make_fixtures.py            # sample inventories for testing

# Translate a VMware export into an AWS Terraform project (zipped)
iactranslate translate tests/fixtures/rvtools_sample.xlsx --target aws --out ./out --zip
```

Other clouds and formats are auto-detected: `--target azure|gcp|oci|digitalocean`,
`--source auto|vmware|hyperv|kubernetes|generic|cloud`,
`--renderer terraform|pulumi|cloudformation|bicep|cdk|kubernetes`, `--gitops`.
See the [Operations Guide](docs/operations-guide.md) for the full CLI, API, and web UI.

## Example: input → output

**In** — a row from any inventory (here a CMDB/spreadsheet):

| name | cpu | memory_gib | disk_gib | os | cpu_util_pct | mem_util_pct |
|---|---|---|---|---|---|---|
| prod-web-01 | 8 | 32 | 100 | Ubuntu 22.04 | 22 | 30 |

**Out** — `compute.tf` (image resolved automatically, sized to real usage):

```hcl
resource "aws_instance" "prod_web_01" {
  ami                    = data.aws_ami.ubuntu_22_04.id
  instance_type          = "t3.xlarge"     # right-sized from 8 vCPU/32 GiB @ ~22-30% usage
  subnet_id              = aws_subnet.public_a.id
  vpc_security_group_ids = [aws_security_group.web_sg.id]
  root_block_device { volume_size = 100, volume_type = "gp3" }
  tags = { Name = "prod-web-01", Tier = "web", Environment = "production" }
}
```

…plus a VPC, subnets, security groups, an assessment, a confidence score, an
architecture diagram, and an executive report — all in the generated project.

## Architecture

Two narrow waists (`NormalizedVM` for input, `MigrationPlan` for output) keep the
pipeline source- and cloud-agnostic:

```
 sources/  ─▶  normalize  ─▶  agents  ─▶  validate  ─▶  render  ─▶  package
 (registry)     (waist:        (rule │      (hard      (targets/    (+ reports,
  vmware·        Normalized      anthropic)  gate)       terraform│   assessment,
  hyperv·        VM)                                     pulumi)     diagram, zip)
  generic·                                     ▲
  cloud)                                   MigrationPlan (waist)
```

Full rationale — design principles, the canonical model, sequence diagrams,
scope, and assumptions — is in **[docs/architecture.md](docs/architecture.md)**.

## Documentation

| Doc | For |
|---|---|
| [Operations Guide](docs/operations-guide.md) | Running, extending, operating, troubleshooting; CLI/API/config reference; performance & security |
| [Architecture & Design](docs/architecture.md) | Why it's built this way — principles, canonical model, scope, assumptions, "why not …" |
| [Architecture Decision Records](docs/adr/) | The record of load-bearing decisions |
| [Deployment & Execution](docs/deployment.md) | Execution model, stages, state machine; single-node + reference architecture for scale |
| [Roadmap](docs/roadmap.md) | Shipped vs planned |

## Test & lint

```bash
pytest                 # ~363 tests: parsers, sizing, validation, all 3 clouds, renderers, API
ruff check src tests
```

CI runs lint, pytest on 3.9/3.11/3.12, a Docker build/health check, the web
build, and a real `tofu validate` of generated Terraform for AWS, Azure, and GCP.

## Security posture (at a glance)

No shell or Terraform execution, no cloud credentials required to generate, no
inventory modification, streamed upload caps, bounded temp workspaces, path-
traversal protection, and no secrets embedded in output. Details in
[Operations Guide § Security model](docs/operations-guide.md#15-security-model).
