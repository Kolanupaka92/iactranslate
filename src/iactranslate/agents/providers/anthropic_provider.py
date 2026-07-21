"""Claude (Anthropic) provider.

The LLM makes *structured decisions only* — grouping VMs and picking instance
types — returned via structured outputs (`client.messages.parse`) so the model's
output is parsed straight into a Pydantic schema. Structured outputs disallow
`additionalProperties`/dict maps, so the wire schemas here are deliberately flat
(lists of records); we map them into the rich domain models.

Target-aware: the prompt lists the selected cloud's valid instance types and
image keys. Every decision is re-checked downstream by the validation layer and
the target's catalog guardrail, so a bad LLM answer degrades gracefully. On any
API/parse error we fall back to the deterministic rule engine for that call.
"""
from __future__ import annotations

import os
from typing import List

from pydantic import BaseModel, Field

from ...models import AppGroup, Environment, NormalizedVM, Tier
from ...targets.base import Target
from ..base import RightsizeSuggestion
from .rule_engine import RuleEngineProvider

DEFAULT_MODEL = "claude-opus-4-8"


class _Member(BaseModel):
    vm_name: str
    tier: Tier


class _Group(BaseModel):
    name: str
    environment: Environment
    members: List[_Member] = Field(default_factory=list)


class _Classification(BaseModel):
    groups: List[_Group]


class _RightsizeOut(BaseModel):
    instance_type: str
    image_key: str


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, target: Target) -> None:
        import anthropic  # noqa: F401 — imported lazily; optional dependency

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self._model = os.getenv("IACTRANSLATE_ANTHROPIC_MODEL", DEFAULT_MODEL)
        self._target = target
        self._fallback = RuleEngineProvider(target)

    # -- classify (cloud-agnostic) ------------------------------------------- #

    def classify(self, vms: List[NormalizedVM]) -> List[AppGroup]:
        inventory = [
            {
                "vm_name": v.vm_name,
                "hostname": v.hostname,
                "os": v.os,
                "cpu": v.cpu,
                "memory_gib": v.memory_gib,
                "network": v.network,
                "cluster": v.cluster,
            }
            for v in vms
        ]
        prompt = (
            "You are an infrastructure classifier for a VMware migration.\n"
            "Group these virtual machines into logical applications. For each VM assign:\n"
            "  - an environment: production | staging | development | test | unknown\n"
            "  - a tier: web | app | database | cache | other\n"
            "Infer from names, hostnames, and OS. Return every VM exactly once.\n\n"
            f"VMs (JSON):\n{inventory}"
        )
        try:
            resp = self._client.messages.parse(
                model=self._model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}],
                output_format=_Classification,
            )
            result = resp.parsed_output
        except Exception:  # noqa: BLE001 — any failure -> deterministic fallback
            return self._fallback.classify(vms)

        known = {v.vm_name for v in vms}
        groups: List[AppGroup] = []
        assigned: set[str] = set()
        for g in result.groups:
            members = {}
            for m in g.members:
                if m.vm_name in known and m.vm_name not in assigned:
                    members[m.vm_name] = m.tier
                    assigned.add(m.vm_name)
            if members:
                groups.append(AppGroup(name=g.name, environment=g.environment, members=members))

        missing = [v for v in vms if v.vm_name not in assigned]
        if missing:
            groups.extend(self._fallback.classify(missing))
        return groups

    # -- rightsize (target-specific) ----------------------------------------- #

    def rightsize(
        self, vm: NormalizedVM, tier: Tier, environment: Environment
    ) -> RightsizeSuggestion:
        instances = ", ".join(self._target.instance_names())
        prompt = (
            f"Recommend a {self._target.name.upper()} migration target for one VMware VM.\n"
            "Choose the smallest instance that comfortably fits, leaving ~20% headroom.\n"
            "Prefer memory-optimized instances for database/cache tiers.\n"
            f"instance_type MUST be one of: {instances}.\n"
            "image_key is one of: windows-2022, windows-2019, windows-2016, "
            "amazon-linux-2, ubuntu-22.04, rhel-9, sles-15, centos-7.\n\n"
            f"VM: name={vm.vm_name} vcpu={vm.cpu} memory_gib={vm.memory_gib} "
            f"os={vm.os} tier={tier.value} environment={environment.value}"
        )
        try:
            resp = self._client.messages.parse(
                model=self._model,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
                output_format=_RightsizeOut,
            )
            out = resp.parsed_output
            return RightsizeSuggestion(instance_type=out.instance_type, image_key=out.image_key)
        except Exception:  # noqa: BLE001
            return self._fallback.rightsize(vm, tier, environment)
