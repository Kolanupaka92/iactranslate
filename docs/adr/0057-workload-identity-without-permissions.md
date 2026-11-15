# 0057. Workload identity, without permissions

**Status:** Accepted
**Date:** 2026-08-28

## Context

Generated instances landed with **no identity at all** — no instance profile, no
managed identity, no service account. The architecture review filed this as
CL-03 (HIGH) and the framing was right: the first thing a customer had to write
by hand was the security-sensitive part, which is the worst possible division of
labour for a tool whose pitch is "production-ready IaC".

It also meant no keyless shell access, no metadata credentials for a monitoring
agent, and — most practically — nothing for a policy to attach *to*.

## Decision

One identity per **tier**, wired to the instances, with **no permissions
attached**.

**Why identity is generated.** The role, its trust policy and the
instance-profile wiring are boilerplate that everyone writes and that is easy to
get subtly wrong. A trust policy naming the wrong principal produces a role
nothing can assume, and that failure appears at *runtime*, long after the apply
succeeded. Worth generating.

**Why permissions are not.** Which data a workload may reach is not derivable
from an inventory. An RVTools export says a machine has four vCPUs; it does not
say the machine legitimately reads the customer database. A guess is either too
narrow to work — and an operator under cutover pressure widens it to `*` — or too
broad, which is this tool quietly creating the breach. The role is emitted empty
and the generated README says plainly that attaching policies is the operator's
job.

**Per tier, not per instance.** Per-instance roles are the theoretically tighter
choice and the practically unusable one: a 5,000-VM estate would emit 5,000
roles, exceed the default IAM role quota, and give an operator 5,000 places to
attach the same policy. Tier is the level at which permissions are actually the
same.

**`ssm` is opt-in.** It attaches AWS's own managed policy for Session Manager,
and is offered because it *removes* risk rather than adding it — keyless,
auditable shell access with no SSH port, no bastion and no key pair to rotate,
answering the "how do I log in now?" question that follows every migration. It
stays opt-in because Session Manager access is itself a capability and a governed
account may forbid it.

## Consequences

The default changes from no identity to `basic`. An empty role attached to an
instance does nothing on its own, so the change is inert for anyone not using it,
and `--instance-identity none` restores the previous output exactly — asserted by
a test rather than assumed.

**OCI and DigitalOcean generate nothing, and say why in the README.** OCI grants
instance identity through dynamic groups and compartment-level policies written
against a tenancy this tool cannot see; a dynamic-group rule matching the wrong
instances would hand permissions to workloads that should not have them.
DigitalOcean Droplets have no per-instance identity service at all. Silence in
both cases would read as "your instances have identities", which is worse than
an explanation.

Verified with real `tofu validate` on all five clouds in both `basic` and `ssm`
modes.

Still open: no identity for the load balancers or storage the plan also creates;
no workload-identity federation (GKE/EKS/AKS style), which is the right answer
for container targets and not for the VMs this generates today; and no policy
*scaffolding* — a `--with-policies` mode emitting commented, commonly-needed
statements was considered and rejected, because commented-out IAM in generated
output is uncommented by someone in a hurry.
