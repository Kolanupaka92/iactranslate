# 0073. An opt-in `for_each` layout, shipped with its own migration

**Status:** Accepted
**Date:** 2026-11-13

## Context

The generated Terraform has one resource block per machine, and per disk and
attachment. At 500 VMs on AWS that is about 30,200 lines, 23,178 of them
compute and storage blocks that differ only in their values. ADR 0032 split
those per environment and tier so no single file is unreadable. But a reviewer
still reads the same 20-line block 500 times, and a change to the resource
logic is 500 identical edits.

`for_each` writes the logic once and puts each machine's values in a map.
Three things made it a decision rather than a refactor:

1. **Every address changes.** `aws_instance.prod_web_01` becomes
   `aws_instance.vm["prod_web_01"]`. Anyone who applied the current output
   would see Terraform plan to destroy and recreate their entire estate.
2. **The map key decides rename behaviour.** Keying by VM UUID would make a
   rename never change an address, but `aws_instance.vm["421a0001-…"]` is
   unreadable in review.
3. **It changes what reviewers read**: a row in a map instead of a resource
   block.

## Decision

- **Opt-in, as a renderer:** `--renderer terraform-foreach`, registered AWS-only
  in the renderer registry. Validation, `GET /targets` and the console's format
  picker pick it up from there. The default `terraform` output is
  **byte-for-byte unchanged**. Three estates (realistic, brownfield, 75-VM
  split) × seven targets were rendered before and after and compared.
- **Keyed by the classic labels**, so `aws_instance.vm["prod_web_01"]` is
  readably the machine `aws_instance.prod_web_01` was. Renames stay handled by
  `diff --moved-tf` (ADR 0072).
- **What it covers:** instances (`aws_instance.vm`), data volumes
  (`aws_ebs_volume.data`) and their attachments (`aws_volume_attachment.data`).
  These are the per-machine resources. Templates that only *reference* an
  instance (load-balancer attachments, outputs, brownfield `import` blocks)
  take a precomputed address, so the same template serves both layouts.
- **Safe to switch.** Every render ships `migrate-from-classic.tf`, with one
  `moved` block per classic instance, volume and attachment. It lives in the
  root folder, not somewhere optional. A `moved` block whose source is not in
  state does nothing, so on a fresh deployment the file is inert. A step
  someone could forget would leave a plan that destroys every machine. A file
  that is safe to delete later can't fail that way.
- One resource over a map has nothing to split, so ADR 0032's per-file split
  does not apply to this layout.

## Verification

- With OpenTofu and `terraform_data` resources in the classic shape: switching
  to the `for_each` shape plans **3 destroy / 3 add** without the migration
  file and **0 / 0 / 0** with it ("has moved"). A fresh deployment with the
  same files plans a clean 3 to add.
- In CI, `test_foreach_layout_validates` renders the realistic estate with this
  renderer and runs `tofu validate` against the real AWS provider, migration
  file included.
- On a 500-VM estate, compute and storage go from 23,178 lines to 6,186
  (**−73%**). The migration file adds 8,705 lines of mechanical `moved` blocks
  that can be deleted once every state has been migrated, or immediately on a
  fresh deployment. Without it the total is about 13,200 lines against 30,200.

## Consequences

- Reviewers of large estates read a table of machines and one resource
  definition, not hundreds of near-identical blocks.
- AWS only for now. Each other target needs its own two templates and its own
  migration map. That is on the roadmap.
- Two Terraform layouts now exist for AWS. Both are generated from the same
  plan and validated by the same real-provider job, which keeps that affordable.
