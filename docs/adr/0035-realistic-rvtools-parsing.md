# 0035 — Parsing a real RVTools export, not our idea of one

**Status:** Accepted

## Context

VMware is the primary source this product exists to read, and the only RVTools
file it had ever parsed was one we wrote ourselves: three sheets, ten columns,
tidy names, every field populated.

A real export is a different artifact. Microsoft's
[Azure Migrate RVTools import spec](https://learn.microsoft.com/en-us/azure/migrate/tutorial-import-vmware-using-rvtools-xlsx)
— authoritative because Microsoft had to build a parser against real files —
documents **14+ sheets** (`vInfo`, `vHost`, `vDatastore`, `vSnapshot`,
`vPartition`, `vMemory`, `vDisk`, `vCD`, `vUSB`, `vNetwork`, `dvPort`, …), a
`vInfo` sheet of ~50 columns, a `VM UUID` on every row, and files of up to
**20,000 servers**.

The gap between those two artifacts is where a first pilot fails. Every other
test in the suite could pass and the product could still break on the first
real file it was handed.

## Decision

Generate a structurally realistic fixture (`scripts/make_rvtools_fixture.py`)
from the documented sheet and column names, at any size, and test against it.
The generator is committed rather than only its output, so the fixture can be
regenerated at 5,000 or 20,000 VMs for performance work without bloating the
repository.

Four real defects surfaced immediately.

### 1. RHEL 8 was silently upgraded to RHEL 9

The image catalog stocked `rhel-9` and no `rhel-8`, on any of the five clouds,
so `image_key` returned the newest RHEL available. A customer's certified
RHEL 8 estate would have been provisioned as RHEL 9 with nothing saying so.
`rhel-8` is now stocked everywhere, and the mapping honours the reported major
version.

### 2. OS substitution was invisible in general

Windows Server 2012 R2 is past end of life and the clouds no longer publish a
base image, so a plan genuinely *has* to fall forward to a supported release.
That is defensible. Doing it silently is not: an application certified against
2012 R2 may not run on 2022, and the person reviewing the plan is the only one
who can judge that.

Substitutions now state themselves in the decision's `reason`, which flows into
`decisions.json` and the executive report. This lives in the planning stage
rather than in `assess()` deliberately — assessment is target-agnostic by
design (it runs *before* a cloud is chosen), and the substitution only exists
relative to a specific target's catalog.

The first implementation cried wolf on every Ubuntu machine: RVTools OS strings
end in an architecture suffix, and the `64` in `"Ubuntu Linux (64-bit)"` read as
a version number. Warnings that fire on healthy machines are worse than no
warnings, because they train the reader to skip them.

### 3. The parser read every sheet and used three

`sheet_name=None` materialised all 14 sheets. On a 5,000-VM file that is ~16,000
rows of `vHost`/`vDatastore`/`vSnapshot`/`dvPort` data nothing looks at — about
a quarter of parse time, and proportionally more memory at the 20,000-server
ceiling Azure documents. `ExcelFile` exposes the sheet list without
materialising anything, so the parser now reads only `vInfo`, `vDisk`, and
`vNetwork`, falling back to the first sheet when `vInfo` is absent (the
non-RVTools workbook path).

### 4. What already worked, and is now pinned

Multi-disk aggregation across `vDisk` rows, powered-off machines being retained
rather than dropped, real RVTools OS strings resolving to images, and detection
of `vInfo` among 14 sheets — all correct, none previously covered by a test
against a realistic file.

## Consequences

- A realistic 14-sheet, 5,000-VM RVTools export now runs end to end in **12.0 s
  at 197 MB peak RSS**, producing Terraform that passes real `tofu validate`
  with all 5,000 instances present, split into reviewable per-environment/tier
  files ([ADR 0032](0032-split-compute-output-for-reviewability.md)).
- The measured footprint is the number to quote to an operator, and it fits the
  512 MB container limit in `docker-compose.yml` with room to spare.
- **The fixture is still synthetic.** It is *shaped* like a real export because
  it was built from a published spec, but no actual customer file has been
  through this. Remaining unknowns are the ones a spec cannot describe: merged
  cells, multiple header rows, localized column names, files edited by hand
  before being sent. This narrows the first-pilot risk considerably; it does
  not eliminate it.
- `VM UUID` is present in real exports and still unused. It is a stabler
  identity than the VM name — which is what de-duplication and resource
  labelling key on today — and adopting it would make re-runs robust against a
  machine being renamed. Left as follow-up rather than folded in here.
