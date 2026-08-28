# 0044. Landing-zone CIDRs, and carving subnets from them

**Status:** Accepted
**Date:** 2026-08-27

## Context

Enterprises do not deploy into empty accounts. They run Control Tower, Azure
Landing Zones or GCP organisation policies; their network team allocates CIDRs
from an IPAM system; service control policies reject resources that lack
mandated tags. This tool invented `10.0.0.0/16` and assumed greenfield.

`10.0.0.0/16` is among the most commonly used private ranges in existence. In a
real enterprise it will overlap something — a datacenter, a VPN, a peered VPC —
and the generated network will either fail to apply or, worse, apply and create
a routing conflict. Output that violates a customer's guardrails is **worse than
no output**, because it fails after the review has already passed.

## Decision

A `LandingZone` carries the constraints the customer's platform team imposes:
`cidr`, mandated `tags`, and a `name_prefix`. This ADR covers the network range;
tags follow separately.

**Subnets are carved from the VPC range** with `ipaddress`, rather than assumed.
Public subnets take the first *n* `/24` blocks and private the next, so the tiers
cannot overlap and the layout stays predictable for a reviewer.

**Validation happens at configuration time.** A malformed CIDR, a range with host
bits set (`10.0.0.5/16` — a common typo), an IPv6 range, or a range too small to
subnet is rejected immediately with the fix stated: *"a /25 cannot be split into
/24 subnets across tiers. Allocate a /23 or larger."* The alternative is a
provider error thousands of resources into an apply.

## Consequences

**This exposed a latent bug, which is now fixed.** Subnets were hardcoded to
`10.0.{az}.0/24` while the VPC took its CIDR from the target. Every target
happened to use `10.0.0.0/16`, so the two agreed by coincidence — but any other
range would have placed **every subnet outside its own VPC**. Allowing an
IPAM-allocated range turned a latent bug into an immediate one, so the carving
above replaces the hardcoded blocks. A test asserts subnet containment for every
target.

**And it would have introduced a security bug, now prevented.** Each target's
default ingress hardcodes its own `VPC_CIDR` as the source range for internal
traffic — database ports reachable from within the VPC, and so on. With a
customer-supplied range those rules would have permitted a block the VPC does not
use while *failing to permit* the one it does: simultaneously over-permissive and
broken. `_retarget_ingress` rewrites exactly those rules and deliberately leaves
`0.0.0.0/0` on public listeners alone, since that is an intentional choice rather
than a stale default.

Verified with real `tofu validate` against a project generated on
`172.20.8.0/21`, and by asserting that omitting a landing zone reproduces the
previous network plan byte for byte.

Still open, and the larger half of this problem: adopting an **existing** VPC and
its subnets rather than creating them, targeting a specific account or
subscription, centralised egress through a transit gateway, and mandated tags
themselves. A greenfield network in the right address space is a real step
forward; it is not yet conformance with a landing zone that already exists.
