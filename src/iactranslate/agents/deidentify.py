"""Strip customer identifiers before inventory reaches a remote LLM provider.

An inventory export is a reconnaissance map: hostnames, cluster and datacenter
names, VLAN labels, owner tags. A security team that discovers those crossing
the network to a third party blocks the tool, and a zero-retention agreement
does not change that answer — the objection is to the transmission, not the
storage. So the identifiers do not leave the process.

**What survives, and why it must.** A naive scrubber replaces `acme-prod-db-01`
with an opaque id and destroys the only signal the classifier has: grouping
comes from *shared substrings* between machine names, and environment and tier
come from the words inside them. Blanking the names would silently degrade the
model to worse-than-random while looking like it worked.

So the mapping is **per token and consistent**. `acme-prod-db-01` becomes
`w1-prod-db-01`; `acme-prod-web-04` becomes `w1-prod-web-04`. `prod`, `db` and
`web` are structural vocabulary that any migration tool already infers on its
own and that identify no one; `acme` is the customer, and it becomes `w1`
*everywhere*, so "these two machines share a prefix" survives intact while "the
prefix is acme" does not.

Pseudonyms are sequential counters, not hashes. Hostnames are low-entropy: a
hash of `sql-prod-01` is recovered by anyone with a wordlist and an afternoon,
so hashing would look like protection while providing none. A counter carries no
information about its input at all.

**The reverse map never leaves this module.** Names are restored the instant the
provider returns, so the rest of the pipeline — classifier, rightsizing, network,
and the `MigrationPlan` waist (ADR 0007) — only ever sees real names and cannot
leak a pseudonym into rendered Terraform.

Applied by `get_provider` to remote providers only. The rule engine runs locally
and has nothing to protect against.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from ..models import AppGroup, Environment, NormalizedVM, Tier
from .base import LLMProvider, RightsizeSuggestion

#: Set to "0"/"false" to send raw inventory. Deliberately opt-*out*: the failure
#: mode of forgetting to switch this on is a silent disclosure of customer data.
ENV_DEIDENTIFY = "IACTRANSLATE_AI_DEIDENTIFY"

#: Vocabulary that describes a *role*, not an owner. These pass through so the
#: model can still read environment and tier out of a name. Nothing here names a
#: company, person, site or address.
STRUCTURAL_TOKENS = frozenset({
    # environment
    "prod", "production", "prd", "dev", "development", "stg", "stage", "staging",
    "test", "tst", "qa", "uat", "sit", "perf", "dr", "sandbox", "demo", "nonprod",
    # tier / role
    "web", "www", "http", "https", "nginx", "apache", "iis", "front", "frontend",
    "app", "application", "api", "svc", "service", "mid", "middleware", "backend",
    "db", "database", "sql", "mysql", "postgres", "postgresql", "oracle", "mssql",
    "mongo", "mongodb", "maria", "cache", "redis", "memcache", "memcached",
    "mq", "queue", "kafka", "rabbit", "rabbitmq", "batch", "job", "worker",
    "lb", "proxy", "gateway", "gw", "bastion", "jump", "file", "nfs", "smb",
    "backup", "monitor", "log", "logging", "dns", "ntp", "ad", "ldap",
    # shape
    "vm", "host", "node", "srv", "server", "cluster", "vlan", "net", "network",
    "dc", "datacenter", "site", "zone", "pool", "primary", "secondary",
    "master", "replica", "standby", "active", "passive", "a", "b", "x", "y", "z",
})

_ALNUM_RUN = re.compile(r"[A-Za-z0-9]+")
_LETTERS_THEN_DIGITS = re.compile(r"^([A-Za-z]+)(\d+)$")
_PSEUDO = re.compile(r"\bw\d+\b")


class Pseudonymizer:
    """Consistent, reversible token mapping for one provider call."""

    def __init__(self) -> None:
        self._forward: Dict[str, str] = {}   # lowered source token -> pseudonym
        self._back: Dict[str, str] = {}      # pseudonym -> source token
        self._vm_back: Dict[str, str] = {}   # pseudonymized vm_name -> real one
        self._counter = 0

    # -- tokens ---------------------------------------------------------------

    def _token(self, token: str) -> str:
        # `db01` is one run of alphanumerics but two pieces of meaning; splitting
        # it keeps `db` readable instead of burning the whole thing as unknown.
        split = _LETTERS_THEN_DIGITS.match(token)
        if split:
            return self._token(split.group(1)) + split.group(2)
        if token.isdigit() or token.lower() in STRUCTURAL_TOKENS:
            return token
        key = token.lower()
        if key not in self._forward:
            self._counter += 1
            pseudonym = f"w{self._counter}"
            self._forward[key] = pseudonym
            self._back[pseudonym] = token
        return self._forward[key]

    def mask(self, value: Optional[str]) -> Optional[str]:
        """Replace identifying tokens, preserving separators and structure."""
        if value is None:
            return None
        return _ALNUM_RUN.sub(lambda m: self._token(m.group()), value)

    def unmask(self, value: str) -> str:
        """Restore source tokens. Pseudonyms we did not issue are left alone —
        a model-invented `w9` is not customer data and must not be guessed at."""
        return _PSEUDO.sub(lambda m: self._back.get(m.group(), m.group()), value)

    # -- records --------------------------------------------------------------

    def deidentify(self, vm: NormalizedVM) -> NormalizedVM:
        """A copy safe to transmit. Sizing and OS are untouched: they are what
        the decision is actually made on, and they identify nobody."""
        masked_name = self.mask(vm.vm_name) or vm.vm_name
        self._vm_back[masked_name] = vm.vm_name
        return vm.model_copy(update={
            "vm_name": masked_name,
            "hostname": self.mask(vm.hostname),
            "cluster": self.mask(vm.cluster),
            "datacenter": self.mask(vm.datacenter),
            "network": self.mask(vm.network),
            # Dropped rather than masked. No sizing or grouping decision reads
            # them, and each is directly identifying: addresses map the estate's
            # topology, tags routinely carry owner emails and cost centres, and
            # an external_id names a real account's real resource.
            "ip_addresses": [],
            "tags": {},
            "external_id": None,
        })

    def restore(self, vm_name: str) -> Optional[str]:
        """The real name behind a pseudonym, or None if we never issued it."""
        return self._vm_back.get(vm_name)

    def restore_group(self, group: AppGroup) -> AppGroup:
        """Names back to reality, dropping members we cannot account for.

        A pseudonym we did not issue means the model invented a machine. Passing
        it through would put a workload in the plan that is not in the estate, so
        it is dropped here and the caller's own reconciliation assigns whatever
        is left over deterministically.
        """
        members: Dict[str, Tier] = {}
        for name, tier in group.members.items():
            real = self.restore(name)
            if real is not None:
                members[real] = tier
        return group.model_copy(update={
            # The group name is free text the model wrote, so it can echo
            # pseudonyms; unmasking it keeps the plan readable.
            "name": self.unmask(group.name),
            "members": members,
        })


def deidentification_enabled() -> bool:
    raw = (os.getenv(ENV_DEIDENTIFY) or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


class DeidentifyingProvider:
    """Wraps a remote provider so no customer identifier reaches the wire."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        # Deliberately transparent: `provider_used` in the plan should say which
        # decision engine ran, not that a privacy wrapper was in the way.
        self.name = inner.name

    def classify(self, vms: List[NormalizedVM]) -> List[AppGroup]:
        mapping = Pseudonymizer()
        masked = [mapping.deidentify(vm) for vm in vms]
        groups = self._inner.classify(masked)
        return [mapping.restore_group(group) for group in groups]

    def rightsize(
        self, vm: NormalizedVM, tier: Tier, environment: Environment
    ) -> RightsizeSuggestion:
        # Returns an instance type and image key — no names come back, so there
        # is nothing to restore.
        return self._inner.rightsize(Pseudonymizer().deidentify(vm), tier, environment)
