# IaCTranslate

AI-powered infrastructure migration translator: convert exported infrastructure
discovery reports (RVTools / VMware) into **production-ready Terraform** for AWS —
without ever connecting to the customer environment.

The value is not "an LLM writes Terraform." It's a **deterministic translation
layer**:

```
parse → normalize → agents(classify/rightsize/network) → validate → render → package
```

The AI produces only *structured decisions* (which application group, which
instance type). Python + Jinja2 emit the actual `.tf`, so the output is
reproducible, auditable, and enterprise-safe. Every AI decision is re-checked by
a validation layer before any Terraform is written.

> **MVP scope:** VMware → AWS (EC2 + VPC networking). The provider interface,
> template registry, and `--target` flag are structured so additional
> clouds/formats (Azure, GCP, Pulumi, OpenTofu) slot in later.

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python scripts/make_fixtures.py   # sample RVTools .xlsx + VMware .csv for testing
```

## CLI

```bash
iactranslate translate tests/fixtures/rvtools_sample.xlsx \
  --target aws --out ./out --zip --name acme-migration
```

Produces a full Terraform project (`main.tf`, `networking.tf`, `compute.tf`,
`security.tf`, `storage.tf`, `variables.tf`, `outputs.tf`, …), a
`documentation/migration-summary.md`, and an optional `out.zip`.

## API

```bash
uvicorn iactranslate.api.main:app --reload
```

```
POST /projects                 { "name": "...", "target": "aws" }
POST /projects/{id}/upload     multipart file (.xlsx / .csv)
POST /projects/{id}/run
GET  /projects/{id}            status + summary
GET  /projects/{id}/download   → Terraform project ZIP
```

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

Covers parsers, normalization, rightsizing, validation, generation, and a full
pipeline + API end-to-end flow (24 tests).

## Terraform validation

`terraform` is not required to generate — output is rendered deterministically
from templates and shape-checked in tests. To validate against the real AWS
provider, fill in the `ami_ids` placeholders in `terraform.tfvars`, then:

```bash
cd out && terraform init && terraform validate && terraform plan
```
