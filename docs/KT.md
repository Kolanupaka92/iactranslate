# IaCTranslate — Knowledge Transfer & Operations Guide

> The single source of truth for understanding, running, extending, operating, and
> troubleshooting IaCTranslate. Read the **Summary** and **Architecture** first, then
> jump to whichever How-To or Troubleshooting section you need.

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

---

## 1. Executive summary

**What it is.** IaCTranslate converts **any** infrastructure inventory — VMware (RVTools),
Microsoft Hyper-V, a CMDB/spreadsheet export (ServiceNow, Device42, Lansweeper, or
hand-rolled), or an existing AWS/Azure fleet — into **production-ready Terraform** for
**AWS, Azure, or GCP**, and can **recommend the best-fit cloud**. It never connects to the
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

**Status.** CLI + FastAPI + Next.js web UI. 79 tests, 7 green CI jobs (lint, pytest 3.9/
3.11/3.12, Docker health, web build, real Terraform validate). Repo:
`github.com/Kolanupaka92/iactranslate` (private).

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
│  ├─ packager.py          # write project tree + migration-summary.md + zip
│  ├─ cli.py               # `iactranslate translate|recommend`
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
| **Source** | Reads one inventory format → raw records that `normalize.py` understands. Has `detect(path)→confidence` and `parse(path, column_map)`. | `sources/base.py` |
| **Target** | One cloud: an instance **catalog**, tier→family/subnet/security **mappings**, OS→image detection, and Jinja2 **templates**. | `targets/base.py` |
| **Provider** | Makes the *structured decisions* (grouping, instance choice). `rule` (deterministic, default) or `anthropic` (Claude). Always re-checked by validation. | `agents/providers/` |
| **Recommender** | Runs all clouds on one inventory and scores cost (0.45) + fit (0.30) + OS-affinity (0.25). | `recommend.py` |
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
   target catalogs to recover vCPU/mem.)
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
   if creds exist; anything unavailable falls back to static). `pricing_source` (static/live)
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
networking.tf, security.tf, compute.tf, storage.tf, outputs.tf, main.tf, README.md,
documentation/migration-summary.md, modules/`.

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
Flags: `--target aws|azure|gcp`, `--source auto|vmware|hyperv|generic|cloud`, `--map`,
`--region`, `--name`, `--zip`.

### 6.3 CLI — recommend
```bash
iactranslate recommend tests/fixtures/rvtools_sample.xlsx
# prints a ranked table (score, $/mo, cost/fit/OS) + per-cloud rationale
```

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
curl -s -X POST localhost:8000/projects/$PID/recommend        # optional
curl -s -X POST localhost:8000/projects/$PID/run
curl -s -o out.zip localhost:8000/projects/$PID/download
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
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export IACTRANSLATE_LLM_PROVIDER=anthropic
export IACTRANSLATE_ANTHROPIC_MODEL=claude-opus-4-8   # optional
```
Without a key it silently falls back to `rule`. The validation layer + catalog guardrail run
regardless, so a bad LLM answer degrades gracefully.

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
| `IACTRANSLATE_LLM_PROVIDER` | `rule` | `rule` (deterministic) or `anthropic` (Claude). |
| `ANTHROPIC_API_KEY` | — | Required for `anthropic`; absent → auto-fallback to `rule`. |
| `IACTRANSLATE_ANTHROPIC_MODEL` | `claude-opus-4-8` | Model for classify/rightsize. |
| `IACTRANSLATE_MAX_UPLOAD_MB` | `25` | Upload cap → `413`. Streamed, never buffered whole. |
| `IACTRANSLATE_MAX_VMS` | `5000` | Inventory size cap → `400`. |
| `IACTRANSLATE_MAX_PROJECTS` | `200` | In-memory store cap; oldest evicted (temp dirs deleted). |
| `IACTRANSLATE_TARGET_UTILIZATION` | `0.65` | When a source carries utilization, size instances so they run at ~this utilization (right-sizing). |
| `IACTRANSLATE_PRICING` | `static` | `static` (curated catalog rates, offline) or `live` (real market prices, cached, falls back to static). |
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
| `POST` | `/projects/{id}/run` | Runs the pipeline. 200 summary; 422 on validation issues; 400 on bad input. |
| `POST` | `/projects/{id}/recommend` | Cloud recommendation from the uploaded inventory. |
| `GET` | `/projects/{id}` | Status + summary. |
| `GET` | `/projects/{id}/download` | The Terraform project ZIP. 409 if not generated yet. |
| `DELETE` | `/projects/{id}` | Deletes the project + its temp workspace. 204. |

Error contract: 4xx return `{"detail": "..."}` (422 validation returns `{"detail":{"message","issues"[]}}`);
unexpected errors return a generic `500` (details are logged server-side, never leaked).
Interactive docs at `/docs` (Swagger) when the server is running.

---

## 10. Testing & CI

```bash
pytest                                   # full suite (fast, offline) — 79 tests
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
- **State:** the project store is **in-memory** (deliberate MVP shim). Restarting the API
  loses projects. Persistence (Postgres + S3/R2) is the next planned slice.
- **Scale:** `run` is synchronous. For very large inventories, an async job queue (Celery/
  Redis) is a planned slice; the `MAX_VMS` cap bounds request cost today.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| **CLI**: `unknown target '…'` / `unknown source '…'` | Typo / unsupported name. | Use `aws\|azure\|gcp` and `auto\|vmware\|hyperv\|generic\|cloud`. |
| **CLI**: `No workloads found …` | Source parsed 0 rows — wrong source, or the generic auto-detect missed the name/cpu columns. | Pass `--source generic --map "name=…,cpu=…,memory_gib=…"` with your real headers. |
| **API**: browser `CORS` error / network fail | API started without CORS for the frontend origin. | Start API with `IACTRANSLATE_CORS_ORIGINS=http://localhost:3000`. |
| **API**: upload returns `413` | File over `IACTRANSLATE_MAX_UPLOAD_MB` (25 by default). | Raise the env var, or trim the export. |
| **API**: upload/recommend returns `400 "could not parse…"` | Corrupt file, wrong extension, or a source forced on a mismatched file (e.g. Hyper-V source on an RVTools xlsx). | Use `source: "auto"`, or match the source to the file. Confirm the file opens in Excel. |
| **API**: `run` returns `422` with `issues[]` | The plan failed validation (bad instance type, CIDR overlap, undefined SG). | Read the `issues` — usually a source producing odd specs; check the offending VM's cpu/mem. |
| **run** succeeds but `terraform apply` fails on AMI/image | Templates ship `ami-REPLACE_ME` / `REPLACE_ME` image placeholders by design. | Fill `ami_ids` (AWS) / `source_image_ids` (Azure) / `image_ids` + `gcp_project` (GCP) in `terraform.tfvars` before apply. |
| **Generic source** picks wrong/blank columns | Headers don't match the synonym table. | Provide an explicit `column_map` / `--map`. Canonical keys: `name, cpu, memory_gib\|memory_mib, disk_gib\|disk_mib, os, network, ip, cluster`. |
| **Cloud source**: vCPU/mem come out as defaults | Instance type not in the AWS/Azure catalogs. | Add the type to the relevant `targets/*/catalog.py`, or include explicit `vCPUs`/`Memory` columns in the export. |
| **`tofu validate`** fails locally | `tofu`/`terraform` not installed, or provider download blocked. | `brew install opentofu`; ensure network for `tofu init`. Set `TF_PLUGIN_CACHE_DIR` to reuse providers. |
| **git push** rejected: "…without `workflow` scope" | Pushing `.github/workflows/*` with a token lacking `workflow` scope. | `gh auth refresh -h github.com -s workflow` then push (full path `/opt/homebrew/bin/gh` if not on PATH), or edit the file via GitHub's web UI. |
| **Anthropic provider** seems ignored | No `ANTHROPIC_API_KEY`, or SDK not installed. | It auto-falls back to `rule`. Install `anthropic` (in `[dev]`) and export the key. |
| **`iactranslate: command not found`** | venv not activated / package not installed. | `. .venv/bin/activate && pip install -e ".[dev]"`. |
| **Web build/lint fails on a hook dep** | A `useCallback`/`useEffect` missing a dependency. | Add the dep to the array (ESLint names it). |

---

## 13. FAQ & glossary

**Does it need cloud credentials?** No — generation is fully offline. Credentials are only
needed when *you* run `terraform apply` on the output.

**Does it modify the customer environment?** No. It reads exported inventory files only.

**Is the AI required?** No. The default `rule` provider is deterministic and needs no key;
the AI is an optional refinement, always re-validated.

**Can output be deployed as-is?** After filling the image/project placeholders in
`terraform.tfvars`. The HCL itself is provider-valid (CI proves it with `tofu validate`).

**Glossary:** *RVTools* = popular VMware vSphere inventory exporter (.xlsx). *CMDB* =
Configuration Management Database (ServiceNow/Device42/Lansweeper). *Rightsizing* = choosing
the cloud instance that fits a VM's vCPU/memory with headroom. *Target* = destination cloud.
*Source* = origin inventory format. *OpenTofu/`tofu`* = open-source Terraform, used to
validate generated HCL.

---

*Keep this doc current: when you add a source or target, update §3, §7, and §12; when you add
an env var, update §8; when you add an endpoint, update §9.*
