"""Landing-zone conformance — deploying into an account someone already governs.

The architecture review's sharpest product criticism: enterprises do not deploy
into empty accounts. They have Control Tower, Azure Landing Zones or GCP
organisation policies; CIDRs allocated by an IPAM team; tag policies enforced by
SCPs that reject a `RunInstances` call outright when `CostCenter` is missing.
This tool invented `10.0.0.0/16` and assumed greenfield, which is false for every
target customer — and output that violates a customer's guardrails is worse than
no output, because it fails on apply after the review has already passed.

This module carries the constraints the *customer's* platform team imposes:

``cidr``
    The address space the network team allocated. Subnets are carved from it
    rather than assumed — see `carve_subnets`.

``tags``
    Tags mandated by policy, applied to every taggable resource. This is the
    single most common reason a governed account rejects a deployment.

``name_prefix``
    Many organisations enforce a resource-naming convention in policy.

Deliberately *not* a full landing-zone implementation. Adopting an existing VPC
and its subnets, targeting a specific account or subscription, and centralised
egress through a transit gateway are the larger half of this problem and are
tracked separately.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DEFAULT_CIDR = "10.0.0.0/16"

#: Subnet size carved out of the VPC range. /24 (254 usable hosts) is the
#: conventional default and leaves room for many tiers in a /16.
SUBNET_PREFIX = 24

#: Tag keys must be conservative enough for every target: AWS allows a broad
#: set, GCP *labels* are far stricter (lowercase, limited punctuation). Rejecting
#: at configuration time beats a provider error thousands of resources later.
_TAG_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/=+\-@]{0,127}$")
_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,31}$")
#: DigitalOcean tag charset — enforced by the API, not just convention.
_DO_TAG_RE = re.compile(r"[^a-z0-9:_-]+")


class LandingZoneError(ValueError):
    pass


@dataclass
class LandingZone:
    """Constraints imposed by the customer's existing cloud governance."""

    cidr: str = DEFAULT_CIDR
    tags: Dict[str, str] = field(default_factory=dict)
    name_prefix: Optional[str] = None
    #: Customer-managed key for volume encryption. Encryption itself is never
    #: optional — see ADR 0062 — so this only selects *whose* key protects the
    #: data. Regulated estates usually require their own; without one the cloud
    #: provider's managed key is used, which is still encrypted at rest.
    #:
    #: Format is per-cloud (AWS KMS ARN or alias, an Azure disk encryption set
    #: id, a GCP KMS key name), so it is carried as an opaque string and only
    #: checked for being non-blank. Validating each provider's shape here would
    #: reject valid keys the day a provider adds a format.
    kms_key_id: Optional[str] = None

    def __post_init__(self) -> None:
        try:
            network = ipaddress.ip_network(self.cidr, strict=True)
        except ValueError as exc:
            raise LandingZoneError(f"invalid CIDR '{self.cidr}': {exc}") from exc
        if network.version != 4:
            raise LandingZoneError("only IPv4 CIDRs are supported")
        if network.prefixlen > SUBNET_PREFIX - 1:
            raise LandingZoneError(
                f"CIDR {self.cidr} is too small — a /{network.prefixlen} cannot be "
                f"split into /{SUBNET_PREFIX} subnets across tiers. Allocate a "
                f"/{SUBNET_PREFIX - 1} or larger."
            )
        for key, value in self.tags.items():
            if not _TAG_KEY_RE.match(key):
                raise LandingZoneError(
                    f"tag key '{key}' is not portable across the supported clouds "
                    "(letters, digits, and _ . : / = + - @, starting with a letter)"
                )
            if len(value) > 256:
                raise LandingZoneError(f"tag value for '{key}' exceeds 256 characters")
        if self.kms_key_id is not None and not self.kms_key_id.strip():
            raise LandingZoneError("kms_key_id must not be blank — omit it to use "
                                   "the provider's managed key")
        if self.name_prefix is not None and not _PREFIX_RE.match(self.name_prefix):
            raise LandingZoneError(
                f"name prefix '{self.name_prefix}' must start with a letter and "
                "contain only letters, digits and hyphens (max 32 characters)"
            )

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(self.cidr, strict=True)

    @property
    def is_default(self) -> bool:
        """True when nothing was actually customised — used to decide whether the
        generated README should explain the assumption it is making."""
        return self.cidr == DEFAULT_CIDR and not self.tags and not self.name_prefix

    def do_tags(self) -> List[str]:
        """DigitalOcean tags are a flat list of strings, not key/value pairs.

        `key:value` is DigitalOcean's own documented convention for encoding
        structure into them. Silently dropping mandated tags on one cloud would
        be exactly the kind of quiet divergence this tool exists to avoid.

        DigitalOcean accepts only lowercase letters, digits, colons, dashes and
        underscores — `tofu validate` rejects anything else outright, which is
        how `Owner:platform@acme.com` was caught. Everything outside that set is
        folded to a dash rather than dropped, so the tag still identifies its
        key.
        """
        return [
            _DO_TAG_RE.sub("-", f"{k}:{v}".lower()).strip("-")[:255]
            for k, v in sorted(self.tags.items())
        ]


def carve_subnets(cidr: str, count_per_tier: int) -> Tuple[List[str], List[str]]:
    """Carve public and private subnet CIDRs out of `cidr`.

    Subnets used to be hardcoded to `10.0.{az}.0/24` while the VPC took its CIDR
    from the target — so any VPC range other than `10.0.0.0/16` produced subnets
    **outside their own VPC**. Every target happened to use `10.0.0.0/16`, which
    kept the bug latent; allowing an IPAM-allocated range makes it immediate.

    Public subnets take the first `count_per_tier` blocks and private ones the
    next, so the two tiers never overlap and the layout stays predictable for a
    reviewer reading the plan.
    """
    network = ipaddress.ip_network(cidr, strict=True)
    available = list(network.subnets(new_prefix=SUBNET_PREFIX))
    needed = count_per_tier * 2
    if len(available) < needed:
        raise LandingZoneError(
            f"{cidr} yields {len(available)} /{SUBNET_PREFIX} subnets but "
            f"{needed} are needed ({count_per_tier} public + {count_per_tier} private)"
        )
    public = [str(n) for n in available[:count_per_tier]]
    private = [str(n) for n in available[count_per_tier:needed]]
    return public, private


def merge_tags(zone: Optional[LandingZone], project: str) -> Dict[str, str]:
    """Mandated tags plus the ones this tool always sets.

    Customer tags win on conflict: their policy is the one that rejects the
    deployment, and `ManagedBy` is ours to concede.
    """
    base = {"Project": project, "ManagedBy": "IaCTranslate"}
    base.update(zone.tags if zone else {})
    return base


_GCP_LABEL_RE = re.compile(r"[^a-z0-9_-]+")


def gcp_labels(tags: Dict[str, str]) -> Dict[str, str]:
    """Mandated tags as GCP labels.

    GCP labels are far stricter than tags elsewhere: lowercase letters, digits,
    hyphens and underscores only, and they must start with a letter. Normalising
    rather than dropping keeps the governance intent — a `CostCenter` tag that
    silently vanished on one cloud is exactly the quiet divergence this tool
    exists to avoid — while making it visible in the output that a rename
    happened.
    """
    out: Dict[str, str] = {}
    for key, value in sorted(tags.items()):
        clean = _GCP_LABEL_RE.sub("-", key.lower()).strip("-")
        if clean and not clean[0].isalpha():
            clean = f"k-{clean}"
        if clean:
            out[clean[:63]] = _GCP_LABEL_RE.sub("-", value.lower()).strip("-")[:63]
    return out
