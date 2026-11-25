# 0089. Disks and probes a plan accepts and a cloud refuses

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-25

## Context

Generated Terraform is planned on every target against mocked providers
(ADR 0084). Preparing a second deployment on Google, with Windows, data disks and
load balancers, the generated code was read against the clouds' own data and
documentation. It planned cleanly and would have failed, or done harm, when
applied:

- **Boot disks smaller than the image.** Nothing compared the two. Google's Windows
  images are 50 GB; Azure's are usually 127, and the Terraform provider says the
  disk must be at least the image; Oracle takes nothing under 50 GB. The floor was
  30 everywhere.
- **Google data disks.** The provider says a machine whose disks are attached by
  their own resource *must* ignore `attached_disk`, or the two "fight for control".
  It didn't, so a second run could have detached every data disk.
- **Google internal load balancers.** Probes come from 35.191.0.0/16. The firewall
  let in the network's own range only, so every backend would look unhealthy.
- **A VMware CSV whose disk column is "Provisioned MB"** (or "Provisioned GB") was
  read as having no disk and silently got 30 GB.

## Decision

- Each target states `min_boot_gib(image_key)`. The boot disk is never below it, on
  every renderer. Oracle's template also raises it to the image's own size, which
  only the provider knows.
- The sizing reason says when a boot disk was raised, and when the inventory had no
  disk and one was assumed.
- Google machines ignore `attached_disk`.
- One Google firewall rule admits the probes, for the ports and tags of the
  internal load balancers. One rule because a name per load balancer passes
  Google's 63 characters on long project names; the ADR 0084 tests caught that.
  Pulumi has the same rule.
- The VMware CSV reader knows the megabyte and gigabyte spellings of both columns.
  A bare "Provisioned" is still not read: its unit would be a guess.

## Sources

Google's sizes were read from its image families. Azure's 127 and 30 are
Microsoft's general figures ("usually", "typically"), not per image; 64 for Red
Hat is from experience, not a document. Too large costs a little; too small fails.

## Consequences

- Compared on six inventories: 14 boot disks changed, all raised, on Azure (9),
  Oracle (4) and Google (1). Totals rise by at most 0.4%.
- **Not yet applied for real.** The second Google deployment is what proves the
  data disk and probe fixes.
- A Nutanix or Proxmox image is the operator's own, so its size isn't checked.
- Reading the code also found these, **not fixed here**:
  - **Pulumi (AWS, Azure, Google) and Bicep leave out data disks** and say nothing.
  - **AWS and Oracle look up the newest image on every run.** When the vendor
    publishes one, the next run proposes to replace every machine using it.
  - **Oracle's image lookup doesn't ask for images that fit the shape,** so it can
    pick an Arm or GPU image.
  - Google load balancer and machine names don't include the project name, so two
    translations in one Google project can collide.
