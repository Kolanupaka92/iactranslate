# 0070. A machine is its VM UUID, not its name

**Status:** Accepted
**Date:** 2026-10-25

## Context

Every RVTools sheet carries a `VM UUID` column, and we read none of them.
Identity was the VM *name* throughout:

- **De-duplication** in `normalize` merged records by name. vCenter allows the
  same name in different clusters and datacenters, and estates do this
  routinely (`app01` in east and in west). One of the two machines was silently
  dropped from the migration: no warning, no row in the report, and one fewer
  instance in the generated Terraform.
- **vDisk and vNetwork** were joined to vInfo by name, so two same-named
  machines also pooled their disks and shared one NIC.
- **The snapshot diff** matched by name, so a renamed machine was reported as
  one machine removed and another added. Anyone planning migration waves from
  the diff would read that as a decommission plus a new build.

## Decision

- RVTools parsing reads `VM UUID` into `NormalizedVM.source_uuid`, and joins
  vDisk/vNetwork rows by UUID, falling back to the name when either side lacks
  one.
- `normalize` de-duplicates on the UUID when present, the name otherwise.
  Repeated rows for one machine still merge, as before.
- `vm_name` stays unique, because about fifteen places (tier assignment, the
  graph, confidence, every renderer) join on it. Distinct machines that share a
  name each get a suffix from **their own UUID** (`app01-00000017`). It is not
  a file-position counter, so re-exporting the estate in a different order
  names every machine the same way. A machine without a UUID keeps the bare
  name, and a suffix never lands on a real machine's name.
- `diff_inventories` pairs by UUID first, then by name, and reports a new
  `renamed` list. A rename that also resized appears in both `renamed` and
  `modified`.
- De-identification drops `source_uuid` before anything reaches the model. A
  hypervisor UUID is a stable, unique fingerprint of a customer's machine, and
  no decision the model makes reads it. Because de-identification copies the
  record and blanks a list of fields, a new identifying field leaks unless it
  is added to that list. That is worth knowing for the next one.

## What deliberately did not change

**Terraform resource labels stay name-derived.** Keying labels on the UUID
(`aws_instance.vm_421a0001…`) would make every generated file unreadable. It
would also relabel every resource in every existing customer state, which
Terraform plans as destroying and recreating the whole estate: the exact
failure this change exists to prevent, at much larger scale.

What that leaves: when a machine is renamed between two runs, its label still
changes. The diff now *says* it is a rename, which is the information needed to
write a Terraform `moved` block. Generating those blocks needs the previous
run's labels, which the tool does not keep, so it is a separate piece of work.

Estates without UUIDs (CMDB exports, Hyper-V, flat CSVs) behave exactly as
before.
