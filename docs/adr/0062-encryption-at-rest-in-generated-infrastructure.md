# 0062. Encryption at rest in the infrastructure we generate

**Status:** Accepted
**Date:** 2026-08-30

## Context

`grep -rl encrypt src/iactranslate/targets/*/templates/` returned nothing.

Every generated estate — all five clouds — emitted root volumes and data volumes
with no encryption configuration at all. On AWS that is not a stylistic gap: EBS
is **not** encrypted by default unless an account enables it per-region, so the
generated infrastructure produced genuinely unencrypted disks. CIS AWS
Foundations checks for exactly this, and it is the kind of finding that ends an
enterprise security review rather than generating a follow-up question.

The policy engine's six rules covered placement, cost, naming and sizing, and
said nothing about data protection.

The tool's *output* was creating the exposure — the worst possible shape for a
product whose pitch is that it de-risks migration.

## Decision

**Encryption at rest is unconditional and has no off switch.**

There is no configuration flag, because a flag here would only ever be used to
recreate the exposure this exists to close. On AWS the templates set
`encrypted = true` on `root_block_device` and on every `aws_ebs_volume`.

**The other four clouds are deliberately left alone.** Azure managed disks, GCP
persistent disks, OCI block volumes and DigitalOcean volumes are all encrypted at
rest by the provider and cannot be told otherwise. Emitting an `encrypted = true`
there would imply a control the provider does not expose, and reviewers who know
those platforms would read it as cargo-culting. The difference between the clouds
is real and the templates reflect it rather than flattening it.

**What varies is whose key, so that is what is configurable.** `LandingZone`
gains `kms_key_id`, carried onto the immutable plan and rendered per-cloud in the
form each provider actually accepts:

| Cloud | Without a key | With one |
|---|---|---|
| AWS | `encrypted = true`, AWS-managed key | `kms_key_id` |
| Azure | platform-managed SSE (always on) | `disk_encryption_set_id` |
| GCP | Google-managed key (always on) | `kms_key_self_link` |
| OCI, DigitalOcean | provider-managed (always on) | not exposed |

The key is an opaque string validated only for being non-blank. Each provider
uses a different format, and validating shapes here would reject valid keys the
day a provider adds one. A *blank* key is rejected, because it would render
`kms_key_id = ""` and fail on apply long after the review passed.

**The policy checks the thing that can actually fail.** `require_customer_managed_key`
denies when the estate relies on the provider's key. A policy asserting "volumes
are encrypted" could never fail — encryption is unconditional — and a rule that
cannot fail is compliance theatre. Whose key holds the data is the question an
auditor actually asks.

## Consequences

Generated AWS estates are encrypted by default where they previously were not.
This changes existing output: re-running against an already-applied project will
show volume replacement in the plan, because an unencrypted EBS volume cannot be
encrypted in place. That is the correct and unavoidable consequence of fixing it,
and it is better discovered in `plan` than in an audit.

`plan.kms_key_id` records which key protected the estate, so the executive report
and audit trail can answer "whose key" without reading applied infrastructure.

Verified with real `tofu validate` against the actual AWS, azurerm and Google
providers — including all three customer-key paths, which is where a wrong
argument name would otherwise have shipped.
