# 0022 — OCI target: Flex shapes need a synthetic catalog key, and capabilities stay honest

**Status:** Accepted

## Context

A fourth cloud target, chosen over DigitalOcean to match the product's
enterprise-migration positioning (Oracle shops are a real, distinct migration
segment, often paired with Oracle Database workloads). Two design questions
this target raised that AWS/Azure/GCP didn't:

1. **OCI's current-generation Compute shapes are "Flex"** — a fixed shape
   family (`VM.Standard.E4.Flex`, `VM.Standard.E5.Flex`) where the actual
   OCPU/memory is set independently via `shape_config`, not a fixed-size SKU
   the way `t3.xlarge` or `Standard_D4as_v5` are. The shared
   `InstanceSpec(name, vcpu, memory_gib, ...)` contract needs `name` to be a
   stable, unique catalog key — which a bare shape family string isn't (every
   size would collide on the same name).
2. **OCI has no live pricing integration and no Pulumi renderer.** Every prior
   target advertised the full `{TERRAFORM, PULUMI, GITOPS, LIVE_PRICING}`
   capability set; OCI is the first that genuinely can't, honestly.

## Decision

1. **Synthetic catalog names encode shape + size**: `VM.Standard.E4.Flex-2x16`
   (2 OCPUs, 16 GB). The compute template splits on the first `-` to recover
   the real Terraform `shape` value; `shape_config` reads `c.vcpu`/
   `c.memory_gib` directly — already the catalog spec's values post-rightsizing,
   not re-derived from the name. No information is invented; the suffix exists
   only because our model needs a unique key and OCI's shape model doesn't
   have one built in.
2. **`capabilities = {CAP_TERRAFORM, CAP_GITOPS}` only** — `test_every_target_advertises_core_capabilities`
   (previously asserting the full set for every target) was split into two
   tests: one asserting AWS/Azure/GCP keep the full mature set, one asserting
   OCI explicitly lacks `CAP_PULUMI`/`CAP_LIVE_PRICING`. A target with fewer
   capabilities than its siblings is a normal, expected state in a capability-flag
   system (ADR 0009) — the alternative (claiming capabilities that don't
   exist) is the actual bug.
3. **Images resolve via `data "oci_core_images"`** (OS + version filter,
   sorted by creation time), the same pattern as AWS's `data "aws_ami"` —
   OCI image OCIDs are region-specific, so there's no static portable id the
   way GCP's public image *families* allow. RHEL and Amazon Linux source VMs
   map to Oracle Linux (binary-compatible, and OCI's actual default platform
   image for that use case) rather than a fabricated substitute.
4. **Network Security Groups, not Security Lists**, attached directly to
   instance VNICs — the correct analog to an AWS security group or an Azure
   NSG-on-NIC for a per-tier, per-instance model, not OCI's subnet-level
   Security Lists (which would be the wrong granularity here).
5. **One backend set + listener pair per load-balancer port** — OCI backend
   sets are port-scoped, so a listener on 443 forwarding to backend port 443
   can't share a backend set with one on 80 → 80, mirroring the CloudFormation/
   CDK target-group-per-listener pattern already established for the same
   underlying reason.
6. **Instances default to the region's first availability domain.** OCI VCN
   subnets are regional, not AZ-scoped, so there's no existing per-subnet AZ
   index to plumb through for multi-AD placement without new context wiring;
   the first AD is always valid, and the generated `networking.tf` says so
   explicitly rather than silently limiting availability.

## Consequences

- Verified with real `tofu validate` against the actual `oracle/oci` provider
  schema (not just template rendering) — passed on the first attempt across
  all resource types (VCN, subnets, NSGs, instances, load balancers, block
  volumes, image data sources) — and via a full browser-driven run through the
  web wizard (create → upload → generate → download).
- `recommend()` and the CLI/API/web surfaces needed no target-specific code —
  `list_targets()` already drives them dynamically, confirming the target
  abstraction (ADR: target registry) scales to a fourth cloud exactly as
  designed.
- **Honest limitation, stated in the generated README**: single-AD placement
  by default (documented, with the exact line to edit for multi-AD); OCPU/GB
  pricing is ballpark from OCI's published Flex rates, like every other
  catalog in this codebase — not live pricing (OCI has none integrated yet).
- If OCI live pricing or a Pulumi renderer are ever built, `capabilities`
  gains those flags then — not before, and not as an aspirational placeholder.
