# 0072. A machine renamed at the source is moved in state, not recreated

**Status:** Accepted
**Date:** 2026-11-13

## Context

Terraform resource labels follow VM names (ADR 0070 explains why they stay that
way). So renaming a machine in vCenter renames its resources in the regenerated
Terraform, and `terraform plan` shows destroy-and-recreate for the instance, its
disks, their attachments, its NIC and its load-balancer memberships: up to a
dozen resources per machine. Applied, that is an outage dressed as a routine
change. ADR 0070 made the diff *recognise* a rename by VM UUID, but recognising
it does nothing for state.

A Terraform `moved` block says "the object at this old address is the object at
that new one". ADR 0070 assumed generating these would need the previous run's
labels, which the tool doesn't keep. It doesn't: labels are a pure function of
the inventory, so rendering the old snapshot reproduces exactly the addresses
that run produced.

## Decision

`iactranslate diff OLD NEW --moved-tf moved.tf --target T [--region R]` renders
both snapshots for the target, in memory:

1. **Pair machines by VM UUID** and note each one whose label changed.
2. **Attribute every old address to its machine**: its own label, `<label>_data_N`
   (disks), or `<lb>_<port>_<label>` (load-balancer membership). Longest label
   first, so `web_01_2` is never read as `web_01` plus `_2`.
3. **Substitute the new label** and keep the pair only if that address is
   declared in the new render. A block never points at something the config
   doesn't declare.
4. **Never move onto an occupied address.** If the destination existed before
   and isn't itself being vacated, the substitution matched by coincidence and
   is dropped.
5. **Report the rest.** Whatever no rename explains is listed as a genuine
   destruction or creation, never folded into a move.

**Renames that reuse an address are not emitted as blocks.** Distinct names that
collapse onto one label get suffixes (`web_01`, `web_01_2`). Renaming one
machine can hand its label to another that was never renamed. The correct
moves are then `web_01 → api_01` and `web_01_2 → web_01`, but Terraform
*chains* `moved` blocks: it would route `web_01_2` through `web_01` to
`api_01`. OpenTofu refuses such a config ("Moved object still exists"). These
groups come back as a **conflict** carrying an ordered `terraform state mv`
sequence: every object to a temporary address, then each to its final one. No
final address is occupied mid-way, so shifts and swaps resolve the same way.

## Verification

- With OpenTofu and `terraform_data` resources standing in for a machine, its
  disk and its LB membership: renaming plans **3 to destroy, 3 to add** without
  the generated blocks and **0 to destroy, 0 to add** with them.
- For the collision shift: naive chained blocks are rejected by OpenTofu. The
  generated `state mv` sequence gives "No changes", with each object (told
  apart by value) at its correct address.
- In CI, `test_generated_moved_blocks_validate` renders a renamed estate for
  all seven targets, adds the generated `moved.tf`, and runs `tofu validate`
  against the real providers. That rejects any block whose destination is
  undeclared or of another resource type.
- On the realistic 25-VM fixture, renaming a load-balanced web server pairs
  every changed address on AWS, Azure, GCP, OCI and DigitalOcean (9–12
  resources each), with nothing unmatched.

## Consequences

- Renames at the source become in-place state moves, the change a migration
  team expects.
- The command must use the same target and region the Terraform was generated
  for. Its output is only as right as that match.
- A rename that also changes how a machine is classified (`web-01` → `api-01`
  moves it from the web tier to the app tier) still changes real infrastructure:
  the web load balancer goes, app-tier networking arrives. Those show up as
  unmatched additions and removals, correctly.
- Terraform only. Pulumi has the same problem (its equivalent is resource
  `aliases`) and is not covered yet.
