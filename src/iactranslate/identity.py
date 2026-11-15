"""Workload identity — an identity per tier, with no permissions attached.

Generated instances landed with **no identity at all**: no instance profile, no
managed identity, no service account. So the first thing a customer had to write
by hand was the security-sensitive part, which is the worst division of labour a
migration tool can pick. It also meant no keyless shell access, no metadata
credentials for an agent, and nothing for a policy to attach to.

**Identity is generated; permissions are not.** This is the whole design.

Creating the role, its trust policy and the instance-profile wiring is
boilerplate that everyone writes and that is easy to get subtly wrong — a trust
policy naming the wrong principal produces a role nothing can assume, and the
error surfaces at runtime rather than at apply. That is worth generating.

*Which* data a workload may reach is not derivable from an inventory. An RVTools
export says a machine has four vCPUs; it does not say the machine legitimately
reads the customer database. Guessing produces either a role too narrow to work —
which the operator widens under time pressure, usually to `*` — or one too broad,
which is a migration tool quietly creating the breach. So the role is emitted
empty and the README says plainly that attaching policies is the operator's job.

The one exception is opt-in: `ssm` attaches AWS's managed policy for Session
Manager. It is offered because it *removes* risk rather than adding it — keyless,
auditable shell access with no SSH port, no bastion and no key pair to rotate —
and because "how do I log in?" is otherwise the first question after a migration.
It stays opt-in because Session Manager access is itself a capability, and a
governed account may well forbid it.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional


class IdentityMode(str, Enum):
    #: No identity resources. The historical behaviour.
    NONE = "none"
    #: A role/identity per tier, wired to the instances, with no permissions.
    BASIC = "basic"
    #: `basic`, plus AWS Session Manager access. AWS only.
    SSM = "ssm"


#: Clouds with a first-class per-instance identity this tool generates.
SUPPORTED_CLOUDS = ("aws", "azure", "gcp")

#: Why the other two are absent, stated rather than silently skipped.
UNSUPPORTED_REASON: Dict[str, str] = {
    "oci": (
        "OCI grants instance identity through dynamic groups and compartment-level "
        "policies, which are written against a tenancy this tool cannot see. "
        "Generating a dynamic-group rule that matched the wrong instances would "
        "hand permissions to workloads that should not have them."
    ),
    "digitalocean": (
        "DigitalOcean Droplets have no per-instance identity service. Applications "
        "authenticate with an API token supplied as configuration."
    ),
}

#: Managed policy for keyless shell access. AWS's own, not one we compose.
SSM_MANAGED_POLICY = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"


class IdentityError(ValueError):
    pass


def parse_mode(value: Optional[str]) -> IdentityMode:
    if value is None:
        return IdentityMode.BASIC
    try:
        return IdentityMode(str(value).strip().lower())
    except ValueError as exc:
        raise IdentityError(
            f"unknown instance-identity mode '{value}' — expected one of "
            f"{', '.join(m.value for m in IdentityMode)}"
        ) from exc


def supported(cloud: str) -> bool:
    return cloud.lower() in SUPPORTED_CLOUDS


def roles_for(compute, mode: IdentityMode) -> List[str]:
    """Distinct tier names needing an identity, in stable order.

    One identity per **tier**, not per instance. Per-instance roles are the
    theoretically tighter choice and the practically unusable one: a 5,000-VM
    estate would emit 5,000 roles, blow through the default IAM role quota, and
    give an operator 5,000 places to attach the same policy. Tier is the level
    at which permissions are actually the same.
    """
    if mode is IdentityMode.NONE:
        return []
    return sorted({c.tier.value for c in compute})


def notes(cloud: str, mode: IdentityMode) -> List[str]:
    """What the operator must know, for the generated README."""
    if mode is IdentityMode.NONE:
        return []
    cloud = cloud.lower()
    if not supported(cloud):
        return [UNSUPPORTED_REASON.get(cloud, "This target has no instance identity model.")]

    out = [
        "Each tier has its own identity with **no permissions attached**. What a "
        "workload may access is not derivable from an inventory, and a guess is "
        "either too narrow to work — widened to `*` under time pressure — or too "
        "broad, which is this tool creating the breach. Attach your policies to "
        "these roles before the workloads need them.",
    ]
    if mode is IdentityMode.SSM and cloud == "aws":
        out.append(
            "Session Manager access is attached, so instances are reachable "
            "without an SSH port, a bastion or a key pair. Remove the policy "
            "attachment if your account forbids it."
        )
    return out
