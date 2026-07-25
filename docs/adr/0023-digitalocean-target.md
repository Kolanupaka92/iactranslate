# 0023 — DigitalOcean target: real platform gaps stated, not papered over

**Status:** Accepted

## Context

The fifth target, and the last item in "New targets" on the roadmap.
DigitalOcean's product surface is deliberately simpler than AWS/Azure/GCP/OCI
— that simplicity is the product's whole pitch — but it means several things
every prior target could assume don't hold here. This ADR is mostly about
those gaps, because pretending they don't exist would be the actual mistake.

## Decision

1. **Droplet sizes are real, stable slugs** (`s-2vcpu-4gb`, `m-4vcpu-32gb`) —
   unlike OCI's Flex shapes (ADR 0022), no synthetic catalog key is needed;
   `InstanceSpec.name` is the literal Terraform `size` value.
2. **No subnet resource, no managed NAT gateway.** DigitalOcean's VPC is one
   flat CIDR per region; Droplets join it directly. `networking.tf` creates
   only `digitalocean_vpc` — nothing else exists to create. The public/private
   tier distinction the plan carries is enforced entirely by firewall rules
   (`security.tf`), not by subnet placement, and this is stated in both the
   template's own comment and the generated README.
3. **Every Droplet gets a public IPv4 by default; there is no flag to
   suppress it.** AWS/Azure/GCP/OCI all support truly private, no-public-IP
   instances; DigitalOcean's `digitalocean_droplet` resource doesn't expose
   that control. Rather than silently under-delivering on "private" tier
   semantics, the generated `compute.tf` and README say so explicitly and
   name the real mitigation (firewall-rule scoping, a jump host / VPN).
4. **Load Balancers are always public too** — DigitalOcean has no internal-LB
   product, unlike the other four targets. Stated the same way, not hidden.
5. **Firewalls attach by tag, not by subnet or NIC.** Each security-group
   tier gets a dedicated `digitalocean_tag` (`web-fw`, `db-fw`, …); each load
   balancer gets its *own* additional tag applied only to its actual target
   instances (not the shared tier tag), so a multi-environment tier (e.g.
   `web-fw` used by both `prod-web` and `dev-web`) can't leak an unrelated
   environment's Droplet into an LB's backend set.
6. **No Windows Server image exists in DigitalOcean's catalog at all** —
   Droplets are Linux-only unless you bring a custom image. Windows source
   VMs still render (as Ubuntu, so the pipeline produces a valid plan), but
   the generated README opens with a loud, unmissable callout naming every
   affected VM and recommending a different target if Windows is a real
   requirement — this is the one gap serious enough to warrant a dedicated
   test (`test_digitalocean_windows_source_vms_flagged_in_readme`) asserting
   the warning actually appears, not just that generation doesn't crash.
7. **Root disk size is bundled into the Droplet size slug**, not
   independently configurable — `c.root_volume_gib` isn't used for the root
   disk (stated in a template comment); extra volumes (`storage.tf`,
   `digitalocean_volume`) remain genuinely independently sized.
8. **`capabilities = {CAP_TERRAFORM, CAP_GITOPS}`** — same honest, narrower
   set as OCI (ADR 0022): no Pulumi renderer, no live pricing integration.

## Consequences

- Verified with real `tofu validate` against the actual
  `digitalocean/digitalocean` provider schema (passed first attempt), and a
  full browser-driven run through the web wizard (create → upload → generate
  → download) against a live API server — smallest-fit instance selection
  correctly fell back from the general-purpose (`s-`) family to
  memory-optimized (`m-`) where the source VMs' memory requirement exceeded
  every general-purpose catalog entry, exercising the existing
  `smallest_fit` fallback path for the first time against a target whose
  general-purpose ceiling is lower than the fixture's largest source VMs.
- The target abstraction needed zero changes for a fifth cloud whose network
  and compute model differs this much from the other four — `recommend()`,
  the CLI, the API, and the web UI all picked it up automatically via
  `list_targets()`, the same result OCI produced (ADR 0022).
- This is now the pattern for future targets with real platform gaps: state
  them in the template, the README, and — for anything a user could
  reasonably miss (like "no Windows") — in a dedicated test that would fail
  if the warning silently disappeared.
