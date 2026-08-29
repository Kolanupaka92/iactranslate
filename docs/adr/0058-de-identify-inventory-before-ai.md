# 0058. De-identify inventory before it reaches a remote model

**Status:** Accepted
**Date:** 2026-08-29

## Context

The optional Claude provider sends inventory to a third party. Reading what was
actually in the payload: `vm_name`, `hostname`, `cluster`, `network` (the VLAN or
port-group label), and — for sizing — the VM name again. Addresses and tags were
not sent, but nothing prevented the next field from being added.

That set is a reconnaissance map of the estate. It names the company, the sites,
the network segmentation and often the application. An enterprise security team
that finds it crossing the network blocks the tool, and a zero-retention
agreement does not change the answer: the objection is to the transmission, not
to the retention. This is a deployment blocker at exactly the customers the
product is aimed at, and the AI path is the only part of the pipeline with the
problem — everything else already runs locally and offline.

## Decision

Wrap every **remote** provider in `DeidentifyingProvider`. Identifiers are
replaced on the way in and restored the instant the provider returns.

**Consistent per-token mapping, not blanking.** The naive scrubber replaces
`acme-prod-db-01` with an opaque id, and that destroys the only signal the
classifier has. Grouping is inferred from machines sharing substrings, and
environment and tier are read out of the words inside the name. Blanking them
would degrade the model to worse-than-random while still returning a
well-formed answer — a silent failure, which is the kind this project spends
most of its effort avoiding.

So the name is tokenized and each token mapped consistently:
`acme-prod-db-01` → `w1-prod-db-01`, `acme-prod-web-04` → `w1-prod-web-04`.
`prod`, `db` and `web` are structural vocabulary that identifies nobody and that
the deterministic rule engine already infers unaided. `acme` is the customer and
becomes `w1` **everywhere**, so "these two machines share a prefix" survives
while "the prefix is acme" does not.

**Counters, not hashes.** Hostnames are low-entropy. `sha256("sql-prod-01")` is
recovered by anyone with a wordlist, so hashing would provide the appearance of
protection and none of the substance. A sequential counter carries no
information about its input.

**Addresses, tags and `external_id` are dropped, not masked.** No sizing or
grouping decision reads them; tags routinely carry owner emails and cost
centres, and an `external_id` names a real resource in a real account. Anything
that cannot inform the decision should not be in the payload at all.

**The reverse map never leaves the module.** An earlier framing put the
restoration "just before `MigrationPlan` construction", which would have carried
pseudonyms through the classifier, rightsizing and network stages — three more
places to leak one into rendered Terraform. Restoring at the provider boundary
means the narrow waist (ADR 0007) only ever sees real names.

**Wrapped at `get_provider`, not inside the provider.** De-identification is a
property of *being remote*, not of being Anthropic. Putting it in the factory
means the next provider added gets it without having to remember.

**On by default, opt out with `IACTRANSLATE_AI_DEIDENTIFY=0`.** The failure mode
of forgetting to switch a privacy control on is a silent disclosure, so the
default is the safe one. The rule engine is not wrapped: it runs in this process
and there is nothing to protect against.

## Consequences

The AI path becomes deployable inside an enterprise without an exception from
the security team, which is the point.

Classification quality is preserved in shape but not proven equal — the model
now reasons over an anonymised footprint, and while the grouping signal is
intact by construction, real-world names carry idiosyncratic hints (a product
codename shared by four machines) that a `w7` conveys only as "shared token".
That is the intended trade and is very likely a good one, but it is untested
against a real estate, and the eval harness that would settle it does not exist
yet (AI-01).

Group names come back with token case normalized (`ACME` restores as `acme`)
because tokens are mapped case-insensitively so that `ACME-CL` and `acme-prod`
are recognised as the same fact. VM names are unaffected — they restore through
an exact map, not the token path.

A model that invents a workload now has it dropped rather than admitted, and the
existing reconciliation in `classify` assigns the leftover deterministically.
This was already the behaviour for unknown names; it now also covers pseudonyms
we never issued.
