# 0045. Mandated tags, in every cloud's native form

**Status:** Accepted
**Date:** 2026-08-27

## Context

A governed account rejecting `RunInstances` because a resource lacks a
`CostCenter` tag is the single most common way a generated plan fails on apply —
after the architecture review has already passed, which is the expensive moment
to discover it. ADR 0044 gave the landing zone its network range; this is the
other half.

The complication is that "tags" is not one concept:

| Cloud | Mechanism | Constraint |
| --- | --- | --- |
| AWS | provider `default_tags` | broad charset |
| GCP | provider `default_labels` | **lowercase** `[a-z0-9_-]`, must start with a letter |
| Azure | per-resource `tags` | no provider-level default in `azurerm` |
| OCI | per-resource `freeform_tags` | no provider-level default |
| DigitalOcean | `digitalocean_tag` **resources** | flat strings, `[a-z0-9:_-]` only |

## Decision

Use each cloud's native mechanism rather than forcing one shape:

* **AWS and GCP** apply them at the **provider**, so no resource type can be
  missed — precisely the failure a tag policy punishes.
* **Azure and OCI** get a `locals.tf` with `common_tags`, merged into every
  resource's tag block with `merge(local.common_tags, {...})`.
* **DigitalOcean** gets real `digitalocean_tag` resources, encoded as
  `key:value` — DigitalOcean's own documented convention — and attached to every
  Droplet. A Droplet can only carry a tag that already exists, so the resources
  must be generated too.

**Normalise, never drop.** GCP labels and DigitalOcean tags cannot represent
`CostCenter=CC-4417` verbatim. Silently omitting a mandated tag on one cloud
would be exactly the quiet divergence this tool exists to prevent — and it would
fail *only* on that cloud, at apply time. Keys are folded to each platform's
charset instead, so the governance intent survives visibly.

**Customer tags win on conflict.** Their policy is the one that rejects the
deployment; `ManagedBy` is ours to concede.

## Consequences

`locals.tf` joins the shared template map. AWS and GCP render it empty and the
renderer already drops empty files, so they gain nothing they do not need.

**Real `tofu validate` caught a genuine bug here.** `do_tags()` initially only
replaced spaces, so `Owner=platform@acme.com` produced
`Owner:platform@acme.com` and DigitalOcean rejected it outright: *"tags may
contain lowercase letters, numbers, colons, dashes, and underscores"*. Unit
tests asserting a round-trip through our own encoder would have passed. Running
the real tool against the real provider schema is what found it, which is the
argument for keeping `tofu validate` in CI.

Verified with `tofu validate` on all five clouds, generated with
`--vpc-cidr 172.20.8.0/21 --tags CostCenter=CC-4417,Owner=platform@acme.com`.

Still open: `name_prefix` is validated and carried but not yet applied to
generated resource names, and adopting an **existing** VPC remains the larger
unfinished half of landing-zone conformance.
