# IaCTranslate

AI-powered infrastructure migration translator: convert **any** infrastructure
inventory — VMware (RVTools), Microsoft Hyper-V, a CMDB / spreadsheet export
(ServiceNow, Device42, Lansweeper, or hand-rolled), or an existing AWS/Azure
fleet — into **production-ready Terraform** for AWS, Azure, or GCP, without ever
connecting to the customer environment.

The value is not "an LLM writes Terraform." It's a **deterministic translation
layer**:

```
parse → normalize → agents(classify/rightsize/network) → validate → render → package
```

The AI produces only *structured decisions* (which application group, which
instance type). Python + Jinja2 emit the actual `.tf`, so the output is
reproducible, auditable, and enterprise-safe. Every AI decision is re-checked by
a validation layer before any Terraform is written.

> **Sources (input, `src/iactranslate/sources/`):** `vmware` (RVTools .xlsx + vSphere CSV),
> `hyperv` (Get-VM export), `generic` (any CMDB/spreadsheet — auto-detects columns or takes an
> explicit `--map`), `cloud` (existing AWS/Azure fleet; recovers vCPU/mem from the target
> catalogs). `--source auto` detects the right one. The generic source means **any company's
> inventory works without a bespoke parser.**
>
> **Targets (output, `src/iactranslate/targets/`):** **AWS** (EC2 + VPC), **Azure** (VM +
> VNet/NSG), **GCP** (Compute Engine + VPC + firewalls), all via Terraform.
>
> Both are registries behind interfaces; the parser, normalizer, classifier, validation,
> renderer, and packager are source- and cloud-agnostic. New sources (mainframe, live agents)
> and targets (Pulumi, OpenTofu) slot in without touching the pipeline.

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python scripts/make_fixtures.py   # sample RVTools .xlsx + VMware .csv for testing
```

## CLI

```bash
# AWS (EC2 + VPC)
iactranslate translate tests/fixtures/rvtools_sample.xlsx \
  --target aws --out ./out-aws --zip --name acme-migration

# Azure (VM + VNet/NSG)
iactranslate translate tests/fixtures/rvtools_sample.xlsx \
  --target azure --out ./out-azure --zip --name acme-migration

# GCP (Compute Engine + VPC + firewalls)
iactranslate translate tests/fixtures/rvtools_sample.xlsx \
  --target gcp --out ./out-gcp --zip --name acme-migration

# Any source — auto-detected (Hyper-V, cloud fleet, CMDB, …)
iactranslate translate tests/fixtures/hyperv_sample.csv --target azure --out ./out-hv

# A CMDB/spreadsheet with non-standard headers — map them explicitly
iactranslate translate my-cmdb.csv --source generic \
  --map "name=Hostname,cpu=Cores,memory_gib=RAM GB,disk_gib=Storage GB,os=OS" \
  --target aws --out ./out-cmdb
```

## Which cloud? — recommendation

Don't know which cloud to migrate to? Compare all three on the same inventory:

```bash
iactranslate recommend tests/fixtures/rvtools_sample.xlsx
```

A deterministic, auditable scorer ranks AWS/Azure/GCP on **cost** (projected
monthly spend), **fit** (how tightly instances match the source vCPU/memory), and
**OS affinity** (Windows→Azure, Linux→GCP, mixed→AWS), with plain-English rationale
per cloud and explicit weights. No AI is in this loop — the score is inspectable
and defensible. Also available at `POST /projects/{id}/recommend`.

Produces a full Terraform project (`main.tf`, `networking.tf`, `compute.tf`,
`security.tf`, `storage.tf`, `variables.tf`, `outputs.tf`, …), a
`documentation/migration-summary.md`, and an optional `out.zip`.

## API

```bash
uvicorn iactranslate.api.main:app --reload
```

```
POST   /projects                 { "name": "...", "target": "aws" }
POST   /projects/{id}/upload     multipart file (.xlsx / .csv)
POST   /projects/{id}/run
POST   /projects/{id}/recommend  → cloud recommendation
GET    /projects/{id}            status + summary
GET    /projects/{id}/download   → Terraform project ZIP
DELETE /projects/{id}            → delete project + workspace
```

## Web UI

A Next.js app under `web/` gives the full workflow in the browser: create project →
upload export → (optionally) compare clouds → generate → download ZIP. Includes a
one-click sample inventory for demos.

```bash
# terminal 1 — API with CORS for the frontend
IACTRANSLATE_CORS_ORIGINS=http://localhost:3000 uvicorn iactranslate.api.main:app

# terminal 2 — frontend
cd web && npm install && npm run dev   # http://localhost:3000
```

Point the frontend at a non-default API with `NEXT_PUBLIC_API_URL`.

## Deploy (Docker)

```bash
docker build -t iactranslate .
docker run -p 8000:8000 iactranslate      # non-root, healthchecked, /health
```

## Configuration & limits

The API enforces hard limits to bound memory/disk/CPU on attacker-influenced input
(all env-overridable — see `src/iactranslate/config.py`):

| Env var | Default | Purpose |
|---|---|---|
| `IACTRANSLATE_MAX_UPLOAD_MB` | 25 | Reject larger uploads (413) — streamed, never buffered whole. |
| `IACTRANSLATE_MAX_VMS` | 5000 | Reject oversized inventories (bounds plan/output size). |
| `IACTRANSLATE_MAX_PROJECTS` | 200 | Cap the in-memory store; oldest projects + temp dirs are evicted. |
| `IACTRANSLATE_CORS_ORIGINS` | (none) | Comma-separated allowed origins for the planned frontend. |

Other hardening: project names are validated, malformed uploads return `400`
(never a `500`/traceback), unhandled errors return a generic `500` and are logged
server-side, and the store is thread-safe. No customer data leaves the machine on
the default (`rule`) provider.

## AI providers

The classify / rightsize steps run behind a provider interface:

| `IACTRANSLATE_LLM_PROVIDER` | Behavior |
|---|---|
| `rule` (default) | Deterministic rule engine + static AWS catalog. No API key. Reproducible. |
| `anthropic` | Claude structured tool-use via `client.messages.parse`. Needs `ANTHROPIC_API_KEY`. |

Selecting `anthropic` without a key transparently falls back to `rule`, so the
pipeline always runs. See `.env.example`. Regardless of provider, the validation
layer and catalog guardrail re-check every decision.

## Test

```bash
pytest
```

Covers parsers, normalization, rightsizing, validation, generation, all three
cloud targets, the recommender, and API security/robustness (49 tests). Lint with
`ruff check src tests`.

## Terraform validation

`terraform` is not required to generate — output is rendered deterministically
from templates and shape-checked in tests. To validate against the real AWS
provider, fill in the `ami_ids` placeholders in `terraform.tfvars`, then:

```bash
cd out && terraform init && terraform validate && terraform plan
```
