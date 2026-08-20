"""Built-in policies. Each reads the plan and returns violations — never mutates.

Activate and parameterize them through a policy config (see `policy.evaluate`):

    {
      "no_public_subnets": {},
      "allowed_instance_families": {"families": ["t3", "m5"]},
      "max_vcpu": {"max": 16},
      "max_monthly_cost": {"budget_usd": 5000},
      "naming_prefix": {"prefix": "acme_"},
      "require_nat": {"severity": "warn"}
    }

Every policy accepts an optional ``"severity"`` in its config to override the
default (e.g. downgrade a `deny` to `warn`).
"""
from __future__ import annotations

from typing import List

from ..costing import estimate_costs
from ..models import MigrationPlan, SubnetTier
from ..targets.base import Target
from .base import PolicyViolation, Severity, register


@register("no_public_subnets", Severity.DENY,
          "No workload may be placed in a public subnet.")
def no_public_subnets(plan: MigrationPlan, target: Target, config: dict, sev: Severity) -> List[PolicyViolation]:
    out = []
    for c in plan.compute:
        if c.subnet_tier == SubnetTier.PUBLIC:
            out.append(PolicyViolation(
                policy="no_public_subnets", severity=sev, resource=c.vm_name,
                message=f"'{c.vm_name}' is in a public subnet",
            ))
    return out


@register("allowed_instance_families", Severity.DENY,
          "Instance types must belong to an approved family (by prefix).")
def allowed_instance_families(
    plan: MigrationPlan, target: Target, config: dict, sev: Severity,
) -> List[PolicyViolation]:
    families = config.get("families") or []
    if not families:
        return []
    out = []
    for c in plan.compute:
        if not any(c.instance_type.startswith(p) for p in families):
            out.append(PolicyViolation(
                policy="allowed_instance_families", severity=sev, resource=c.vm_name,
                message=f"'{c.instance_type}' is not in an approved family {families}",
            ))
    return out


@register("max_vcpu", Severity.DENY,
          "No instance may exceed a maximum vCPU count.")
def max_vcpu(plan: MigrationPlan, target: Target, config: dict, sev: Severity) -> List[PolicyViolation]:
    limit = config.get("max")
    if limit is None:
        return []
    out = []
    for c in plan.compute:
        if c.vcpu > limit:
            out.append(PolicyViolation(
                policy="max_vcpu", severity=sev, resource=c.vm_name,
                message=f"'{c.vm_name}' has {c.vcpu} vCPU, over the limit of {limit}",
            ))
    return out


@register("max_monthly_cost", Severity.DENY,
          "Total estimated monthly spend must stay within a budget.")
def max_monthly_cost(plan: MigrationPlan, target: Target, config: dict, sev: Severity) -> List[PolicyViolation]:
    budget = config.get("budget_usd")
    if budget is None:
        return []
    # Gating on compute alone let a plan pass a $20,000 budget while actually
    # costing $21,866 — storage, Windows licensing and load balancers are real
    # spend and a budget policy that ignores them does not enforce the budget.
    total = estimate_costs(plan).total
    if total > budget:
        return [PolicyViolation(
            policy="max_monthly_cost", severity=sev, resource=None,
            message=f"estimated ${total:,.2f}/mo exceeds the ${budget:,.2f}/mo budget",
        )]
    return []


@register("naming_prefix", Severity.WARN,
          "Resource names must start with an approved prefix.")
def naming_prefix(plan: MigrationPlan, target: Target, config: dict, sev: Severity) -> List[PolicyViolation]:
    prefix = config.get("prefix")
    if not prefix:
        return []
    out = []
    for c in plan.compute:
        if not c.resource_name.startswith(prefix):
            out.append(PolicyViolation(
                policy="naming_prefix", severity=sev, resource=c.vm_name,
                message=f"resource '{c.resource_name}' does not start with '{prefix}'",
            ))
    return out


@register("require_nat", Severity.DENY,
          "Private subnets must have NAT for controlled egress.")
def require_nat(plan: MigrationPlan, target: Target, config: dict, sev: Severity) -> List[PolicyViolation]:
    if not plan.network.nat_gateway:
        return [PolicyViolation(
            policy="require_nat", severity=sev, resource=None,
            message="network has no NAT gateway (private workloads cannot reach the internet)",
        )]
    return []
