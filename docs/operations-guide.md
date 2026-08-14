# IaCTranslate — Operations Guide

> The comprehensive reference for running, extending, operating, and
> troubleshooting IaCTranslate. Read the **Summary** and **Architecture** first, then
> jump to whichever How-To or Troubleshooting section you need. For the *why* behind
> the design, see [Architecture & Design](architecture.md) and the [ADRs](adr/).

**Contents**
1. [Executive summary](#1-executive-summary)
2. [Architecture at a glance](#2-architecture-at-a-glance)
3. [Repository map](#3-repository-map)
4. [Core concepts (the vocabulary)](#4-core-concepts)
5. [The pipeline, step by step](#5-the-pipeline-step-by-step)
6. [How-to: run it](#6-how-to-run-it)
7. [How-to: extend it](#7-how-to-extend-it)
8. [Configuration reference](#8-configuration-reference)
9. [API reference](#9-api-reference)
10. [Testing & CI](#10-testing--ci)
11. [Deployment & operations](#11-deployment--operations)
12. [Troubleshooting](#12-troubleshooting)
13. [FAQ & glossary](#13-faq--glossary)
14. [Performance](#14-performance)
15. [Security model](#15-security-model)
16. [Error-handling philosophy](#16-error-handling-philosophy)

> **Design rationale** (principles, the canonical model, request-flow diagrams,
> scope, assumptions, "why not …") lives in [Architecture & Design](architecture.md)
> and the [ADRs](adr/). This guide is the *operations* reference.

---

## 1. Executive summary

**What it is.** IaCTranslate converts **any** infrastructure inventory — VMware (RVTools),
Microsoft Hyper-V, Kubernetes, a CMDB/spreadsheet export (ServiceNow, Device42, Lansweeper, or
hand-rolled), or an existing AWS/Azure fleet — into **production-ready Terraform** for
**AWS, Azure, GCP, OCI, or DigitalOcean**, and can **recommend the best-fit cloud**. It never connects to the
customer environment; it works entirely from exported inventory files.

**The core idea (why it's trustworthy).** It is *not* "an LLM writes Terraform." It's a
**deterministic translation layer**. The AI (optional) makes only *structured decisions*
(which application group, which instance size); Python + Jinja2 templates emit the actual
`.tf`, and a validation layer re-checks every decision. Output is reproducible, auditable,
and — proven by CI — **valid against the real cloud providers** (`tofu validate`).

**The moat.** Competitors are the cloud vendors' own migration tools (AWS Migration Hub,
Azure Migrate, Google Migrate). They are single-cloud (lock-in), never emit portable IaC,
and will never recommend a rival cloud. IaCTranslate is **source-agnostic + cloud-neutral +
unbiased**, and its **generic source** ingests any company's CMDB/spreadsheet with no
bespoke parser — so it works for every company, not just VMware shops.

**Status.** CLI + FastAPI + Next.js web UI. On top of the core translator it ships a full
migration-platform layer — assessment, confidence scoring, executive reports, architecture
diagrams, infrastructure diff, brownfield adoption, load balancer topology,
managed-DB re-platforming advice, migration wave planning, a Kubernetes discovery source,
Pulumi/CloudFormation/Bicep/CDK/Kubernetes renderers, a policy engine, an
Infrastructure Graph IR, async jobs + audit, and opt-in GitOps.
~403 tests, 7 green CI jobs (lint, pytest 3.9/3.11/3.12, Docker health, web build, real
Terraform validate). Repo: `github.com/Kolanupaka92/iactranslate` (private).

---

## 2. Architecture at a glance

```
            INPUT (any inventory)                         OUTPUT (any cloud)
   ┌───────────────────────────────┐              ┌──────────────────────────────┐
   │  sources/  (Source registry)  │              │  targets/ (Target registry)  │
   │  vmware · hyperv · generic ·  │              │  aws · azure · gcp           │
   │  cloud                        │              │  (catalog + mapping +        │
   └──────────────┬────────────────┘              │   Jinja2 templates)          │
                  │ raw records                    └───────────────▲──────────────┘
                  ▼                                                │
   parse → normalize → agents(classify → rightsize → network) → validate → render → package(zip)
                  │            │                                    │         │
             NormalizedVM   provider (rule | anthropic)       PlanValidation  Terraform files
```

- **Everything between `normalize` and `render` is source- and cloud-agnostic.** A `Source`
  only decides *where the estate came from*; a `Target` only decides *where it's going*.
- **Two registries, same shape.** `sources/` and `targets/` are parallel: pick by name or
  auto-detect; add a new one without touching the pipeline.
- **The AI is behind a provider interface** and defaults to a deterministic rule engine, so
  the whole thing runs offline with zero keys and is fully reproducible.

---

## 3. Repository map

```
iactranslate/
├─ src/iactranslate/
│  ├─ models.py            # Pydantic canonical model: NormalizedVM, ComputePlan,
│  │                       #   NetworkPlan, MigrationPlan, enums (Tier, Environment…)
│  ├─ config.py            # Env-driven limits (upload size, VM count, projects, CORS)
│  ├─ normalize.py         # raw records → List[NormalizedVM] (unit coercion, dedupe)
│  ├─ pipeline.py          # run_pipeline(): the end-to-end orchestrator
│  ├─ recommend.py         # deterministic multi-cloud recommender
│  ├─ assessment/          # pre-migration readiness assessment (findings + score + HTML)
│  ├─ packager.py          # write project tree + migration-summary.md + assessment + zip
│  ├─ cli.py               # `iactranslate translate|recommend|assess`
│  ├─ parsers/             # back-compat shim → sources (legacy parse/detect_format)
│  │
│  ├─ sources/             # ── INPUT registry ──
│  │  ├─ base.py           #   Source protocol + detection helpers
│  │  ├─ _columns.py       #   tolerant column matching (find_column, cell)
│  │  ├─ vmware/           #   RVTools .xlsx + vSphere .csv
│  │  ├─ hyperv/           #   Get-VM export
│  │  ├─ generic/          #   ★ any CMDB/spreadsheet (synonym auto-detect + column_map)
│  │  └─ cloud/            #   existing AWS/Azure fleet (type→vCPU/mem via catalogs)
│  │
│  ├─ targets/             # ── OUTPUT registry ──
│  │  ├─ base.py           #   Target protocol + InstanceSpec + smallest_fit
│  │  ├─ aws/              #   catalog.py, mapping.py, templates/*.j2 (EC2/VPC/SG)
│  │  ├─ azure/            #   (VM/VNet/NSG via azurerm)
│  │  └─ gcp/              #   (Compute Engine/VPC/firewalls via google)
│  │
│  ├─ agents/              # classify → rightsize → network
│  │  ├─ __init__.py       #   build_migration_plan()
│  │  ├─ classifier.py, rightsizing.py, network.py, heuristics.py
│  │  └─ providers/        #   rule_engine.py (default) | anthropic_provider.py
│  │
│  ├─ validation/          # validators.py: CIDR/dup/catalog/naming checks
│  ├─ generator/           # renderer.py: MigrationPlan → {filename: content}
│  └─ api/                 # main.py (FastAPI) + store.py (in-memory project store)
│
├─ web/                    # Next.js UI (App Router, Tailwind) — wizard over the API
├─ tests/                  # pytest suite + fixtures + test_e2e.py
├─ scripts/make_fixtures.py# generates the 5 sample inventories
├─ Dockerfile, .dockerignore
├─ .github/workflows/ci.yml
└─ pyproject.toml
```

---

## 4. Core concepts

| Concept | What it is | Where |
|---|---|---|
| **NormalizedVM** | The canonical unit of a workload (name, cpu, memory_gib, disks, os, ip, …). Every source produces these; everything downstream consumes them. | `models.py` |
| **MigrationPlan** | The validated object the generator renders: `network` + `compute[]` + `app_groups[]` + `source_platform` + `target` + `region`. | `models.py` |
| **Source** | Reads one inventory format → raw records that `normalize.py` understands. Has `detect(path)→confidence` and `parse(path, column_map)`. VMware/Hyper-V/CMDB/cloud are tabular; **Kubernetes** reads `kubectl -o json` (containers sized from resource requests). | `sources/base.py` |
| **Target** | One cloud: an instance **catalog**, tier→family/subnet/security **mappings**, OS→image detection, and Jinja2 **templates**. | `targets/base.py` |
| **Provider** | Makes the *structured decisions* (grouping, instance choice). `rule` (deterministic, default) or `anthropic` (Claude) — reachable via `--provider`/API `provider` field/web toggle, not just an env var. Always re-checked by validation; `plan.provider_used` honestly records which one ran. | `agents/providers/` |
| **Narrative** | The executive report's AI-written summary paragraph — only when the plan itself was AI-assisted, else a deterministic paragraph from the same facts. Presentation-layer only; never changes the plan. | `narrative.py` |
| **Recommender** | Runs all clouds on one inventory and scores cost (0.45) + fit (0.30) + OS-affinity (0.25). 2.0 adds decisiveness (clear/moderate/close), annualized cost, and estate notes. | `recommend.py` |
| **Assessment** | Pre-migration read of the estate: risk/cost/data-quality/capacity findings + a 0-100 readiness score. Deterministic, no AI. Emits JSON + a standalone HTML report. | `assessment/` |
| **Confidence Engine** | Scores how sure each decision is (sizing/classification/image/cost) per workload + plan-level, from observable signals. | `confidence.py` |
| **Executive Report** | One client-facing HTML page composing plan + cost + assessment + confidence + recommendation + architecture diagram. | `exec_report.py` |
| **Architecture Diagram** | Deterministic SVG + Mermaid of the target topology (VPC → subnets → tiered instances → load balancers). | `diagram.py` |
| **Load Balancer** | Any `(tier, environment, subnet_tier)` group with >1 instance gets one, with listeners from the tier's own security-group ingress (AWS ALB, Azure Standard LB, GCP Network/Internal LB, OCI flexible LB). | `agents/network.py`, `models.py` |
| **Infrastructure Diff** | Drift between two inventory snapshots (added/removed/modified + aggregate deltas). | `diff.py` |
| **Renderer** | Swappable IaC output: `terraform` (default, HCL, all 5 clouds), `pulumi` (Python, AWS/Azure/GCP — not yet OCI/DigitalOcean), `cloudformation` (JSON, AWS-only), `bicep` (Azure-only), `cdk` (Python, AWS-only), or `kubernetes` (JSON/KubeVirt, any cloud) — the latter four render from the Infrastructure Graph, not the plan. | `renderers/` |
| **Brownfield** | Existing cloud fleet with resource ids → Terraform/Pulumi `import` blocks (adopt, don't recreate). | `sources/cloud`, `renderers/` |
| **Re-platforming advisor** | Flags database-tier workloads as managed-DB candidates (RDS/Cloud SQL/Azure SQL) with engine detection + caveats. Advisory-only — never changes the plan. Emits `replatforming.json`. | `replatform.py` |
| **Migration wave planner** | Sequences workloads by tier dependency (data/cache → app → web) + environment order (dev → staging → prod), with `depends_on` chains, rollback strategy, validation checks, LB-aware downtime estimates. Advisory-only. Emits `waves.json`. | `waves.py` |
| **GitOps** | Opt-in CI/CD workflow (plan on PR, apply on merge) + .gitignore, target/renderer-aware. | `gitops.py` |
| **Validation layer** | Never trusts provider output: checks catalog membership, CIDR overlap/containment, duplicate names, referential integrity. | `validation/validators.py` |

**The raw-record contract** (what a `Source.parse` returns, consumed by `normalize`):
`name`, `cpus`, memory as `memory_mib` *or* (`memory_value` + `memory_unit`), disks as
`disks_mib` *or* (`disk_value` + `disk_unit`), plus optional `os`, `network`, `ip`,
`cluster`, `datacenter`, `dns_name`, `powerstate`.

---

## 5. The pipeline, step by step

`run_pipeline()` in `pipeline.py` is the spine. Each stage:

1. **Resolve source & target.** `resolve_source(path, name)` auto-detects (or honors an
   explicit `--source`); `get_target(name)` picks the cloud.
2. **Parse.** `source.parse(path, column_map)` → raw records. (VMware reads RVTools sheets;
   Hyper-V converts bytes→MiB; generic maps columns; cloud looks instance types up in the
   target catalogs to recover vCPU/mem; **Kubernetes** reads `kubectl -o json` and sizes each
   workload from its containers' `resources.requests`, with StatefulSet volume claims as disks.)
3. **Normalize.** `normalize()` coerces units to canonical (MiB→GiB), parses IPs, dedupes by
   name → `List[NormalizedVM]`. Guardrails: empty → error; > `MAX_VMS` → error.
4. **Classify** (`agents/classifier.py`). Provider groups VMs into applications, detecting
   environment (prod/dev/…) and tier (web/app/database/cache).
5. **Rightsize** (`agents/rightsizing.py` + `sizing.py`). Per VM, `effective_demand()` decides
   the requirement: if the source carries **utilization** (CPU/mem %), size to *actual usage*
   at `IACTRANSLATE_TARGET_UTILIZATION` headroom (right-sizing); otherwise size to raw
   allocation with 1.2× headroom (unchanged). The provider proposes an instance; the code
   **re-checks it against the target catalog** (falls back to `smallest_fit`), computes cost,
   and derives subnet tier + security group. Right-sized rows record the before/after and
   surface in the migration summary and API (`right_sized_count`). **Cost** comes from
   `pricing.monthly_cost()`: static catalog rates by default, or live market prices when
   `IACTRANSLATE_PRICING=live` (Azure Retail Prices API needs no credentials; AWS uses boto3
   if creds exist; GCP uses the Cloud Billing Catalog when `IACTRANSLATE_GCP_BILLING_API_KEY`
   is set — summing the machine family's vCPU-core + RAM-GB SKUs; anything unavailable falls
   back to static). `pricing_source` (static/live)
   is surfaced in the summary and API. The **recommender always uses static** rates for a fair
   apples-to-apples comparison.
6. **Network** (`agents/network.py`, deterministic — never the LLM). Allocates VPC/VNet,
   public+private subnets per AZ, and the security groups the tiers imply.
7. **Assemble** `MigrationPlan` (with the real `source_platform`).
8. **Validate** (`assert_valid`). Any issue raises `PlanValidationError`; nothing is written.
9. **Render** (`generator/renderer.py`). Loads the target's Jinja2 templates and produces
   `{filename: content}` — real HCL.
10. **Package** (`packager.py`). Writes the project tree + `documentation/migration-summary.md`
    and (optionally) a `.zip`.

Output tree (per cloud): `versions.tf, provider.tf, variables.tf, terraform.tfvars,
networking.tf, security.tf, loadbalancer.tf, compute.tf, storage.tf, outputs.tf, main.tf,
README.md, graph.json, assessment.json, confidence.json, decisions.json, waves.json,
documentation/migration-summary.md, modules/` — plus `replatforming.json` when any
database-tier workloads are detected.

---

## 6. How-to: run it

### 6.0 Prerequisites — what to install

| Tool | Version | Required for | Install |
|---|---|---|---|
| **Python** | 3.9+ (CI tests 3.9 / 3.11 / 3.12) | The core service, CLI, API — **required** | python.org · `pyenv` · `brew install python@3.12` |
| **pip + venv** | bundled with Python | Installing the package | (comes with Python) |
| **Node.js + npm** | Node **20+**, npm 10+ | The web UI (`web/`) only | nodejs.org · `nvm install 20` · `brew install node` |
| **OpenTofu** *or* **Terraform** | 1.5+ | Validating / deploying the generated `.tf`, and the `tofu validate` E2E test | `brew install opentofu` · opentofu.org · terraform.io |
| **Docker** | any recent | Building/running the container (optional) | docker.com |
| **gh CLI** | any | GitHub / CI operations (optional) | `brew install gh` |

**Python libraries install automatically** via `pip install -e ".[dev]"` — no manual step.
They are: `pandas`, `openpyxl` (read .xlsx/.csv), `jinja2` (templates), `fastapi` + `uvicorn`
+ `python-multipart` (API), `pydantic` (models), and the dev/optional extras `pytest`,
`httpx`, `ruff`, `anthropic`. **No system packages** beyond Python itself are needed for the
core service; `tofu`/`terraform` and `node` are only for the validation and UI paths above.

Verify your toolchain:
```bash
python3 --version     # >= 3.9
node --version        # >= v20   (only if using the web UI)
tofu version          # >= 1.5   (only for validating/deploying output)
```

### 6.1 Local setup
```bash
cd iactranslate
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"              # installs the package + all Python deps
python scripts/make_fixtures.py      # writes 5 sample inventories to tests/fixtures/
```

### 6.2 CLI — translate
```bash
# VMware → AWS, zipped
iactranslate translate tests/fixtures/rvtools_sample.xlsx --target aws --out ./out-aws --zip

# Any source, auto-detected → Azure
iactranslate translate tests/fixtures/hyperv_sample.csv --target azure --out ./out-hv

# A CMDB with non-standard headers → GCP, mapped explicitly
iactranslate translate my-cmdb.csv --source generic \
  --map "name=Hostname,cpu=Cores,memory_gib=RAM GB,disk_gib=Storage GB,os=OS" \
  --target gcp --out ./out-cmdb
```
```bash
# Pulumi output (AWS) instead of Terraform, with a GitOps CI/CD workflow
iactranslate translate rvtools.xlsx --target aws --renderer pulumi --gitops --out ./out-pl

# CloudFormation output (AWS-only), rendered from the Infrastructure Graph
iactranslate translate rvtools.xlsx --target aws --renderer cloudformation --out ./out-cfn

# Bicep output (Azure-only), also rendered from the Infrastructure Graph
iactranslate translate rvtools.xlsx --target azure --renderer bicep --out ./out-bicep

# AWS CDK (Python) output, also rendered from the Infrastructure Graph
iactranslate translate rvtools.xlsx --target aws --renderer cdk --out ./out-cdk

# Kubernetes/KubeVirt output, works for any target (cloud-agnostic)
iactranslate translate rvtools.xlsx --target gcp --renderer kubernetes --out ./out-k8s

# Read containerized workloads off a cluster (kubectl -o json) as the source
kubectl get deployments,statefulsets -A -o json > k8s.json
iactranslate translate k8s.json --source kubernetes --target aws --out ./out-from-k8s
```
Flags: `--target aws|azure|gcp|oci|digitalocean`, `--source auto|vmware|hyperv|kubernetes|generic|cloud`, `--map`,
`--region`, `--name`, `--zip`, `--renderer terraform|pulumi|cloudformation|bicep|cdk|kubernetes`
(CloudFormation and CDK are AWS-only; Bicep is Azure-only; Kubernetes has no
target restriction), `--gitops` (adds `.github/workflows/*` + `.gitignore`).

Every generated project also ships, under `documentation/`, an executive report
(`executive-report.html`), an architecture diagram (`architecture.svg`/`.md`), the
assessment (`assessment.json`, `assessment.html`), and `confidence.json`. A brownfield
export (a cloud fleet CSV with resource ids) additionally yields `imports.tf` /
Pulumi import options so existing infra is adopted, not recreated.

### 6.3 CLI — recommend
```bash
iactranslate recommend tests/fixtures/rvtools_sample.xlsx
# prints a ranked table (score, $/mo, cost/fit/OS) + per-cloud rationale
```

### 6.3b CLI — assess
```bash
iactranslate assess tests/fixtures/rvtools_sample.xlsx
# prints a readiness score (0-100) + categorized findings (risk/cost/data-quality/capacity)
iactranslate assess my-cmdb.csv --json                 # machine-readable
iactranslate assess my-cmdb.csv --html-out report.html # standalone client report
```
A `translate` run also writes the assessment into the project package
(`assessment.json` + `documentation/assessment.html`).

### 6.3c CLI — report & diff
```bash
# Client-facing executive report (HTML): plan + cost + assessment + confidence + recommendation + diagram
iactranslate report rvtools.xlsx --target aws --out report.html
iactranslate report rvtools.xlsx --no-recommend           # skip the 3-cloud compare

# Drift between two inventory snapshots (added/removed/resized + aggregate deltas)
iactranslate diff old-inventory.csv new-inventory.csv
iactranslate diff old.csv new.csv --json
```

### 6.3d Policy enforcement
Enforce organization rules on the plan before rendering. `deny` violations abort
the run; `warn` violations are reported (`policy-report.json`) but don't block.

```bash
# policy.json — activate + parameterize built-in rules
# { "no_public_subnets": {}, "allowed_instance_families": {"families": ["t3","m5"]},
#   "max_vcpu": {"max": 16}, "max_monthly_cost": {"budget_usd": 5000},
#   "naming_prefix": {"prefix": "acme_", "severity": "warn"}, "require_nat": {} }
iactranslate translate rvtools.xlsx --target aws --policy policy.json --out ./out
```

Built-in policies: `no_public_subnets`, `allowed_instance_families`, `max_vcpu`,
`max_monthly_cost`, `naming_prefix`, `require_nat`. Any policy's `severity` can be
overridden in its config (`"severity": "warn"|"deny"`). Policies are read-only —
they never mutate the plan (see [Architecture › Policy engine](architecture.md#policy-engine)).
Over the API: pass `policy` on project create; `GET /policies` lists the rules.

### 6.4 API
```bash
uvicorn iactranslate.api.main:app --port 8000            # add --reload for dev
# with a frontend:
IACTRANSLATE_CORS_ORIGINS=http://localhost:3000 uvicorn iactranslate.api.main:app
```
Full curl walkthrough:
```bash
PID=$(curl -s -X POST localhost:8000/projects -H 'content-type: application/json' \
      -d '{"name":"demo","target":"aws","source":"auto"}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -s -X POST localhost:8000/projects/$PID/upload -F "file=@tests/fixtures/rvtools_sample.xlsx"
curl -s -X POST localhost:8000/projects/$PID/assess           # optional
curl -s -X POST localhost:8000/projects/$PID/recommend        # optional
curl -s -X POST localhost:8000/projects/$PID/run
curl -s -o out.zip localhost:8000/projects/$PID/download
```

**Beyond a laptop** — a persistent store and a bearer token (see ADR 0025; neither is a
substitute for the real Postgres/OIDC roadmap items, both are real and tested today):
```bash
IACTRANSLATE_STORE=sqlite IACTRANSLATE_DB_PATH=./iactranslate.db \
IACTRANSLATE_API_KEY=$(openssl rand -hex 24) \
  uvicorn iactranslate.api.main:app --port 8000
# every project-touching request now needs:
curl -s localhost:8000/projects/$PID -H "Authorization: Bearer $IACTRANSLATE_API_KEY"
```
The same `IACTRANSLATE_STORE=sqlite` switch also persists the **audit trail**, so
`GET /audit` still answers after a restart (ADR 0026).

**Multi-tenant (shared deployment)** — user accounts, login, and per-user project
isolation (ADR 0027). Required for any deployment more than one person uses:
```bash
IACTRANSLATE_AUTH=session \
IACTRANSLATE_STORE=sqlite IACTRANSLATE_DB_PATH=./iactranslate.db \
IACTRANSLATE_CORS_ORIGINS=https://app.example.com \
  uvicorn iactranslate.api.main:app --port 8000
```
```bash
# Register (sets an httponly session cookie), then use it on every call.
curl -s -c jar.txt -XPOST localhost:8000/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"a-long-passphrase"}'
curl -s -b jar.txt localhost:8000/projects        # only *your* projects
```
Three things to get right:
- **`IACTRANSLATE_CORS_ORIGINS` must name real origins, not `*`.** Browsers
  refuse to send credentials to a wildcard origin, so the web UI silently fails
  to authenticate if you use `*`.
- **Serve over HTTPS.** The session cookie carries the `Secure` flag by default;
  `IACTRANSLATE_COOKIE_SECURE=0` disables that and is for local http testing only.
- **`IACTRANSLATE_STORE=sqlite` is required** — accounts and sessions have
  nowhere to live in the in-memory store.

Projects created before multi-tenancy was enabled have no owner and are visible
only in single-tenant mode; the database migrates itself additively on open, so
nothing is lost.

**Monitoring** — `GET /metrics` serves Prometheus exposition format. It is
unauthenticated by design (scrapers don't send bearer tokens; the payload is
aggregate counts only, no project or inventory data):
```bash
curl -s localhost:8000/metrics
```
Counters (`iactranslate_projects_created_total`, `…_jobs_failed_total`, …) are
process-local and reset on restart — correct Prometheus semantics, since `rate()`
handles counter resets. A minimal scrape config:
```yaml
scrape_configs:
  - job_name: iactranslate
    static_configs:
      - targets: ['localhost:8000']
```

### 6.5 Web UI
```bash
# terminal 1 (API with CORS)
IACTRANSLATE_CORS_ORIGINS=http://localhost:3000 uvicorn iactranslate.api.main:app
# terminal 2 (frontend)
cd web && npm install && npm run dev        # http://localhost:3000
```
Wizard: create project (target + source picker) → upload → optional compare → generate →
download. A one-click sample inventory is provided. Point at another API with
`NEXT_PUBLIC_API_URL`.

### 6.6 Docker
```bash
docker build -t iactranslate .
docker run -p 8000:8000 iactranslate        # non-root, healthchecked on /health
```

### 6.7 Use Claude instead of the rule engine
The Anthropic provider (Claude-powered classification + instance sizing) is reachable from
every surface, per-invocation — not just via environment variable (see ADR 0021):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export IACTRANSLATE_ANTHROPIC_MODEL=claude-opus-4-8   # optional

# CLI: explicit per-run, overrides the env-configured default
iactranslate translate rvtools.xlsx --target aws --out ./out --provider anthropic

# API: per-request, so different callers can choose independently
curl -X POST http://localhost:8000/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"acme","target":"aws","provider":"anthropic"}'

# Web UI: "Use AI (Claude) for classification & sizing" toggle in step 1
```
Without a key it silently falls back to `rule` — but never silently: the CLI prints which
engine actually ran (`AI: rule engine (deterministic) [requested 'anthropic' but fell back —
check ANTHROPIC_API_KEY]`), the API's run result carries both `provider_requested` and
`provider_used`, and the web UI shows an amber fallback banner. `MigrationPlan.provider_used`
is the one place this is recorded — read it if you need to confirm what actually ran.
The validation layer + catalog guardrail run regardless, so a bad LLM answer degrades
gracefully either way.

The executive report's "Summary" section is also AI-written when (and only when) the plan
itself was AI-assisted (`plan.provider_used == "anthropic"`) — otherwise it's a deterministic
paragraph built from the same facts. The report always shows which mode produced it; see
`narrative.py` and ADR 0021. This narrative is presentation-layer prose only — it cannot
change the plan or any rendered IaC.

---

## 7. How-to: extend it

### 7.1 Add a new cloud target (e.g. Oracle Cloud)
1. `src/iactranslate/targets/oci/` with:
   - `catalog.py` — `INSTANCE_CATALOG: List[InstanceSpec]` (name, vcpu, memory_gib, family, $/hr) + `index()`.
   - `mapping.py` — `VPC_CIDR`, `FAMILY_BY_TIER`, `SG_BY_TIER`, `SUBNET_BY_TIER`, `DEFAULT_INGRESS`, `image_key(os)`.
   - `__init__.py` — an `OciTarget` class implementing the `Target` protocol (see `aws/__init__.py`).
   - `templates/*.j2` — 11 files (copy `aws/templates/`, swap resources; use `TEMPLATE_MAP`).
2. Register it in `targets/__init__.py` `_REGISTRY`.
3. Add tests in the style of `tests/test_gcp_target.py`; the CLI/API/UI pick it up automatically
   (they read `list_targets()`).

### 7.2 Add a new source (e.g. Nutanix export)
1. `src/iactranslate/sources/nutanix/__init__.py` — a class with `name`, `label`,
   `source_platform`, `detect(path)→float`, `parse(path, column_map)→List[RawRecord]`. Emit the
   raw-record contract (§4). Reuse `.._columns.find_column`.
2. Register in `sources/__init__.py` `_REGISTRY`. Detection uses the confidence scores;
   `generic` stays the floor.
3. Add a fixture in `scripts/make_fixtures.py` and tests in `tests/test_sources.py`.

*No pipeline, normalize, validation, generator, CLI, API, or UI changes are needed for either.*

---

## 8. Configuration reference

All env vars (see `src/iactranslate/config.py`):

| Env var | Default | Purpose |
|---|---|---|
| `IACTRANSLATE_LLM_PROVIDER` | `rule` | Default engine when `--provider`/`provider` isn't given: `rule` or `anthropic`. |
| `ANTHROPIC_API_KEY` | — | Required for `anthropic`; absent → auto-fallback to `rule` (see §6.7, ADR 0021). |
| `IACTRANSLATE_ANTHROPIC_MODEL` | `claude-opus-4-8` | Model for classify/rightsize. |
| `IACTRANSLATE_MAX_UPLOAD_MB` | `25` | Upload cap → `413`. Streamed, never buffered whole. |
| `IACTRANSLATE_MAX_VMS` | `5000` | Inventory size cap → `400`. |
| `IACTRANSLATE_MAX_PROJECTS` | `200` | Store capacity cap; oldest evicted (temp dirs deleted). |
| `IACTRANSLATE_STORE` | `memory` | `memory` (dies on restart, zero setup) or `sqlite` (persists project metadata **and the audit trail** to `IACTRANSLATE_DB_PATH`, surviving a restart — see ADR 0025, 0026). |
| `IACTRANSLATE_DB_PATH` | `./iactranslate.db` | SQLite file path when `IACTRANSLATE_STORE=sqlite`. |
| `IACTRANSLATE_API_KEY` | (none) | Set to require `Authorization: Bearer <key>` on every project-touching endpoint. Unset = no auth (today's default). Not OIDC/SSO — see ADR 0025. |
| `IACTRANSLATE_AUTH` | `none` | `session` enables multi-tenant user accounts, login, and per-user project isolation (ADR 0027). Requires `IACTRANSLATE_STORE=sqlite`. |
| `IACTRANSLATE_COOKIE_SECURE` | `1` | Set `0` **only** for local http testing — it drops the `Secure` flag from the session cookie. |
| `IACTRANSLATE_RATE_AUTH` | `10` | Login/register attempts per minute, per IP **and** per email. `0` disables. Read live — no restart needed. |
| `IACTRANSLATE_RATE_WRITE` | `60` | Write requests/min per IP (upload, run, jobs, report). `0` disables. |
| `IACTRANSLATE_RATE_READ` | `240` | Read requests/min per IP. `0` disables. |
| `IACTRANSLATE_TRUST_PROXY` | `0` | Set `1` **only** behind a proxy you control, to read the client IP from `X-Forwarded-For`. Any client can forge that header, so trusting it without a proxy lets attackers bypass every limit. |
| `IACTRANSLATE_WORKSPACE_ROOT` | (system temp) | Directory for project workspaces (uploads + generated output). Point it at a mounted volume so artifacts survive a container recycle; `/tmp` does not. |
| `IACTRANSLATE_APP_URL` | `http://localhost:3000` | Public origin of the web app, used to build the password-reset link. The API's own origin is usually wrong here — the reset form lives in the frontend. |
| `IACTRANSLATE_SPLIT_COMPUTE_ABOVE` | `50` | Workload count above which compute output is split into `compute-<env>-<tier>.tf` files for reviewability. `0` keeps a single `compute.tf`. Purely organizational — no state impact. |
| `IACTRANSLATE_TARGET_UTILIZATION` | `0.65` | When a source carries utilization, size instances so they run at ~this utilization (right-sizing). |
| `IACTRANSLATE_PRICING` | `static` | `static` (curated catalog rates, offline) or `live` (real market prices, cached, falls back to static). |
| `IACTRANSLATE_GCP_BILLING_API_KEY` | (none) | API key for GCP live pricing (Cloud Billing Catalog). Without it, GCP live falls back to static. |
| `IACTRANSLATE_PRICE_CACHE` | temp file | Path for the on-disk live-price cache (24h TTL). |
| `IACTRANSLATE_CORS_ORIGINS` | (none) | Comma-separated allowed origins for the frontend. `*` = all (dev only). |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Frontend → API base URL (web/). |
| `IACTRANSLATE_E2E_TOFU` | — | Set `1` to run the real `tofu validate` E2E test. |
| `TF_PLUGIN_CACHE_DIR` | — | Provider-plugin cache for the tofu E2E (speeds re-runs). |

---

## 9. API reference

| Method | Path | Body / notes |
|---|---|---|
| `GET` | `/health` | Liveness. `{"status":"ok"}`. |
| `POST` | `/projects` | `{name, target, source?, column_map?, region?}` → 201 project summary. |
| `POST` | `/projects/{id}/upload` | multipart `file` (.xlsx/.csv). 413 if too big, 400 if wrong type. |
| `POST` | `/projects/{id}/run` | Runs the pipeline **synchronously**. 200 summary; 422 validation/policy; 400 bad input. |
| `POST` | `/projects/{id}/jobs` | Runs **asynchronously**; 202 + `job_id`. Poll `/jobs/{id}`. |
| `GET` | `/jobs/{job_id}` | Job status (queued/running/completed/failed) + project summary when done. |
| `GET` | `/audit` | Recent audit events (newest first); `?project_id=` to scope. Persists across restarts under `IACTRANSLATE_STORE=sqlite`. |
| `GET` | `/metrics` | Prometheus exposition (counters + in-flight gauge). Unauthenticated — aggregate counts only. |
| `POST` | `/projects/{id}/assess` | Pre-migration readiness assessment (findings + score) from the uploaded inventory. |
| `POST` | `/projects/{id}/recommend` | Cloud recommendation (with decisiveness, annualized cost, notes). |
| `POST` | `/projects/{id}/report` | Executive report HTML. `?include_recommendation=false` to skip the 3-cloud compare. |
| `GET` | `/policies` | Available policy rules (name → description). |
| `GET` | `/targets` | Targets and their capability flags. |
| `GET` | `/projects/{id}` | Status + summary (includes the plan's confidence + policy warnings). |
| `GET` | `/projects/{id}/download` | The Terraform project ZIP. 409 if not generated yet. |
| `DELETE` | `/projects/{id}` | Deletes the project + its temp workspace. 204. |

Error contract: 4xx return `{"detail": "..."}` (422 validation returns `{"detail":{"message","issues"[]}}`);
unexpected errors return a generic `500` (details are logged server-side, never leaked).
Interactive docs at `/docs` (Swagger) when the server is running.

---

## 10. Testing & CI

```bash
pytest                                   # full suite (fast, offline) — ~184 tests
ruff check src tests                     # lint
cd web && npm run lint && npm run build  # frontend

# Opt-in: validate generated Terraform against the REAL providers
IACTRANSLATE_E2E_TOFU=1 pytest tests/test_e2e.py::test_generated_terraform_validates
```
Test layout: `test_parsers/normalize/rightsizing/validation/generator` (units),
`test_targets/azure_target/gcp_target` (per-cloud), `test_sources` (input abstraction),
`test_recommend`, `test_api_security`, and `test_e2e.py` (full source×target API matrix +
real `tofu validate`).

**CI** (`.github/workflows/ci.yml`, 7 jobs, all green): `lint`, `test` (3.9/3.11/3.12),
`web` (npm build), `docker` (build + container health), `terraform-validate` (installs
OpenTofu, validates aws/azure/gcp output against real providers).

---

## 11. Deployment & operations

- **Container:** multi-stage, **non-root** (uid 10001), `HEALTHCHECK` on `/health`, runs
  uvicorn. Build once, run anywhere.
- **Health/readiness:** `GET /health` for probes.
- **Resource safety (built-in):** streamed upload cap (413), inventory cap (400), thread-safe
  capacity-bounded project store (evicts oldest + deletes temp dirs), generic 500s (no
  traceback leakage), validated project names.
- **Data handling:** on the default `rule` provider, **no customer data leaves the machine**.
  On `anthropic`, inventory metadata is sent to the Claude API — use a zero-retention key for
  enterprise data. Uploaded files live in per-project temp workspaces and are deleted on
  project delete / eviction.
- **Async jobs, events & audit (shipped, single-node):** `POST /projects/{id}/jobs` runs the
  pipeline on a worker and returns a `job_id` to poll (`GET /jobs/{id}`); lifecycle events flow
  through an in-process bus; `GET /audit` returns the trail. These are the interfaces the
  production backends drop into — see [Deployment & Execution](deployment.md).
- **State:** the project store, jobs, and audit are **in-memory** (single-node). Restarting the
  API loses them; durability arrives with the Postgres + object-storage backend (the
  [reference architecture](deployment.md#reference-architecture)).
- **Scale:** the `MAX_VMS` cap bounds request cost today; horizontal scale (stateless API pods +
  Redis/Celery workers + Postgres + object storage) is the documented v2.1 path.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| **CLI**: `unknown target '…'` / `unknown source '…'` | Typo / unsupported name. | Use `aws\|azure\|gcp` and `auto\|vmware\|hyperv\|kubernetes\|generic\|cloud`. |
| **CLI**: `No workloads found …` | Source parsed 0 rows — wrong source, or the generic auto-detect missed the name/cpu columns. | Pass `--source generic --map "name=…,cpu=…,memory_gib=…"` with your real headers. |
| **API**: browser `CORS` error / network fail | API started without CORS for the frontend origin. | Start API with `IACTRANSLATE_CORS_ORIGINS=http://localhost:3000`. |
| **API**: upload returns `413` | File over `IACTRANSLATE_MAX_UPLOAD_MB` (25 by default). | Raise the env var, or trim the export. |
| **API**: upload/recommend returns `400 "could not parse…"` | Corrupt file, wrong extension, or a source forced on a mismatched file (e.g. Hyper-V source on an RVTools xlsx). | Use `source: "auto"`, or match the source to the file. Confirm the file opens in Excel. |
| **API**: `run` returns `422` with `issues[]` | The plan failed validation (bad instance type, CIDR overlap, undefined SG). | Read the `issues` — usually a source producing odd specs; check the offending VM's cpu/mem. |
| **run** succeeds but `terraform apply` fails on AMI/image | Images resolve automatically (AWS `aws_ami` data sources, Azure `source_image_reference`, GCP public image families, OCI `data "oci_core_images"`), but the account/region may lack a match. | AWS: pin a known-good AMI via `ami_overrides` in `terraform.tfvars`. Azure/GCP/OCI: adjust the image reference / `data` filter in `compute.tf`. GCP needs a real `gcp_project`; OCI needs `compartment_id` + API signing key. |
| **Generic source** picks wrong/blank columns | Headers don't match the synonym table. | Provide an explicit `column_map` / `--map`. Canonical keys: `name, cpu, memory_gib\|memory_mib, disk_gib\|disk_mib, os, network, ip, cluster`. |
| **Cloud source**: vCPU/mem come out as defaults | Instance type not in the AWS/Azure catalogs. | Add the type to the relevant `targets/*/catalog.py`, or include explicit `vCPUs`/`Memory` columns in the export. |
| **`tofu validate`** fails locally | `tofu`/`terraform` not installed, or provider download blocked. | `brew install opentofu`; ensure network for `tofu init`. Set `TF_PLUGIN_CACHE_DIR` to reuse providers. |
| **git push** rejected: "…without `workflow` scope" | Pushing `.github/workflows/*` with a token lacking `workflow` scope. | `gh auth refresh -h github.com -s workflow` then push (full path `/opt/homebrew/bin/gh` if not on PATH), or edit the file via GitHub's web UI. |
| **Anthropic provider** seems ignored | No `ANTHROPIC_API_KEY`, or SDK not installed. | It auto-falls back to `rule` — check `provider_used` in the run result (CLI prints it, API/UI show a fallback banner) rather than assuming. Install `anthropic` (in `[dev]`) and export the key. |
| **`iactranslate: command not found`** | venv not activated / package not installed. | `. .venv/bin/activate && pip install -e ".[dev]"`. |
| **Web build/lint fails on a hook dep** | A `useCallback`/`useEffect` missing a dependency. | Add the dep to the array (ESLint names it). |

---

## 13. FAQ & glossary

**Does it need cloud credentials?** No — generation is fully offline. Credentials are only
needed when *you* run `terraform apply` on the output.

**Does it modify the customer environment?** No. It reads exported inventory files only.

**Is the AI required?** No. The default `rule` provider is deterministic and needs no key;
the AI is an optional refinement, always re-validated.

**Can output be deployed as-is?** Essentially yes — OS images resolve automatically
(AWS `aws_ami` data sources, Azure `source_image_reference`, GCP public image families, OCI
`data "oci_core_images"`, DigitalOcean public image slugs), so there are no image IDs to
hand-fill. AWS/Azure need only cloud credentials; GCP also needs a real `gcp_project`; OCI
needs a compartment OCID + API signing key; DigitalOcean needs an API token + an uploaded
SSH key fingerprint (see the generated README in each case). The HCL is provider-valid
(CI proves it with `tofu validate`). DigitalOcean has no Windows Server image at all — see
its generated README for the caveat if the estate has Windows workloads.

**Glossary:** *RVTools* = popular VMware vSphere inventory exporter (.xlsx). *CMDB* =
Configuration Management Database (ServiceNow/Device42/Lansweeper). *Rightsizing* = choosing
the cloud instance that fits a VM's vCPU/memory with headroom. *Target* = destination cloud.
*Source* = origin inventory format. *OpenTofu/`tofu`* = open-source Terraform, used to
validate generated HCL.

---

## 14. Performance

Measured on Apple Silicon (M-series), Python 3.9, the default **rule** provider
(no network, no AI), each inventory size run in an isolated process. "Core" is
`parse → normalize → agents → validate → render`; "end-to-end" additionally
builds the assessment, confidence scoring, executive report, architecture
diagram, and the `.zip`.

| Workloads | Parse | Core | End-to-end | Peak memory |
|---:|---:|---:|---:|---:|
| 500 | ~0.02 s | ~0.05 s | ~0.08 s | ~80 MB |
| 1,000 | ~0.035 s | ~0.08 s | ~0.21 s | ~90 MB |
| 5,000 | ~0.16 s | ~0.31 s | ~0.68 s | ~170 MB |

Notes:
- Scaling is roughly linear in workload count; 5,000 (`IACTRANSLATE_MAX_VMS`
  default) is comfortably sub-second end-to-end.
- The `anthropic` provider and `live` pricing add network latency (per-call, with
  a 24 h price cache) — those paths are I/O-bound, not CPU-bound.
- Reproduce with `scripts/` bench or the snippet in the repo; numbers are
  indicative and hardware-dependent, not a benchmark guarantee.

## 15. Security model

IaCTranslate is designed to be safe to run on untrusted inventory uploads and to
require the minimum possible trust from the operator.

- ✅ **No shell execution** — the pipeline never shells out; input never reaches a shell.
- ✅ **No Terraform/Pulumi execution** — it *generates* IaC; it never `apply`s. Running the
  output is a separate, explicit act by the user with their own credentials.
- ✅ **No cloud credentials required** — generation is fully offline on the default path.
- ✅ **No inventory modification** — sources are read-only; the customer environment is never touched.
- ✅ **Upload size limits** — streamed with a hard cap (`IACTRANSLATE_MAX_UPLOAD_MB`, `413` over).
- ✅ **Workload count limits** — oversized inventories rejected (`IACTRANSLATE_MAX_VMS`).
- ✅ **Temporary, bounded workspaces** — per-project temp dirs, capacity-capped store, evicted + deleted.
- ✅ **Path-traversal protection** — project names are validated against a strict allowlist.
- ✅ **Input validation** — malformed uploads return `400`, never a `500` or a leaked traceback.
- ✅ **No arbitrary template execution** — templates are shipped with the app, never user-supplied;
  Jinja renders data into fixed templates, it does not execute user input.
- ✅ **No secrets in output** — generated GitOps workflows reference GitHub *secrets*; credentials
  are never embedded in generated files.

Unhandled errors return a generic `500` and are logged server-side with context,
never surfaced to the client.

## 16. Error-handling philosophy

**Fail fast. Never emit invalid Terraform.** An invalid plan is stopped at the
validation gate ([ADR 0006](adr/0006-validation-before-render.md)); no files are
written.

Every validation error is actionable — it explains:

- **what** failed (e.g. "instance type `m9.mega` not in the AWS catalog"),
- **why** it failed (not a real SKU),
- **where** it failed (which workload / resource),
- **how** to fix it (choose a catalog type, or correct the source vCPU/memory).

Surfaces:
- **CLI** — non-zero exit code with a readable `error: …` message on stderr.
- **API** — `422` with `{"detail": {"message", "issues": [...]}}` for validation;
  `400` for bad input; `413` for oversized uploads; a generic logged `500` for the unexpected.

Malformed or attacker-influenced input is treated as a normal `4xx`, never a crash.

---

*Keep this doc current: when you add a source or target, update §3, §7, and §12; when you add
an env var, update §8; when you add an endpoint, update §9. When you add a section, add it to
the Contents and (if user-facing) link it from the README.*
