# 0009 — Targets advertise capability flags

**Status:** Accepted

## Context

Not every target supports every feature: brownfield `import` blocks exist for AWS
but not yet Azure/GCP; a future target might lack live pricing or a Pulumi
provider. Encoding these as scattered `if target.name == "aws"` checks in the
packager, renderers, API, and UI is exactly the kind of cloud-specific branching
the [target registry](0003-target-registry.md) was meant to eliminate — and the
UI has no clean way to know which buttons to enable.

## Decision

Each `Target` advertises a set of **capability flags** — `terraform`, `pulumi`,
`gitops`, `live_pricing`, `brownfield_import` — as data. Callers query
capabilities instead of branching on the cloud name; the API exposes them at
`GET /targets` so a UI can enable features declaratively.

## Consequences

- Adding a capability to a cloud (e.g. brownfield import for Azure) becomes a
  one-line change to that target's capability set, with no edits to callers.
- The UI/API gate features on data, not hard-coded cloud names.
- Capability flags are advertised support, not a guarantee a given run succeeds
  (live pricing still needs credentials/keys); they answer "is this feature
  available for this cloud?", which is what callers actually need to branch on.
