# 0088. An OCPU is two vCPUs

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-25

## Context

Oracle sells and prices OCPUs. On its x86 shapes an OCPU is a physical core, and
Oracle's documentation says it equals two vCPUs. The catalog treated it as one, so
every Oracle machine was generated with twice the CPU it needed, and its compute
was priced to match. Every other cloud was measured in vCPUs, which made the
comparison unfair to Oracle.

Windows was wrong too, in the other direction. Oracle charges $0.092 per OCPU. The
estimate charged $0.046, the per-vCPU rate, per OCPU, so the bill for the machines
it generated would have been twice the licensing shown.

## Decision

- `InstanceSpec.vcpu` is vCPUs on every target. Oracle's catalog holds two per
  OCPU and prices the OCPU.
- The generated `ocpus` is read from the shape's catalog name, which is in OCPUs.
- The catalog's shapes follow: E4.Flex at 2 and 4 GB per vCPU, E5.Flex at 8, so
  the usual 1:4 and 1:8 machines have an exact size, as on the other clouds.
- The Windows rate stays $0.046 per vCPU, which now is $0.092 per OCPU.

## Consequences

- Compared on six inventories: 57 of 60 Oracle machines changed, none dearer, none
  below its allocation unless usage data says so. Nothing changed on any other
  target.
- Oracle's monthly totals fall 21% to 28% (the realistic estate from $8,676 to
  $6,283). The licensing line is the same figure, now for the right machines.
- Oracle leads the samples by more than before. It was already first.
- **Regenerating an Oracle project halves its machines' OCPUs**, which restarts
  them. Where the old machines had spare CPU, that spare goes.
- Oracle's licensing for other software (its own database, for one) is counted per
  OCPU and isn't in the estimate. Halving OCPUs usually lowers it.
- Oracle's pages disagree on whether an E5.Flex past 94 OCPU is an extended VM.
  126 is allowed either way. Shapes with more than 1,024 GB exist as extended VMs
  and aren't in the catalog.
- Not deployed to a real Oracle account. Planned by Terraform with a mocked
  provider, in both layouts.
