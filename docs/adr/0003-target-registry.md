# 0003 — Clouds behind a target registry

**Status:** Accepted

## Context

The product must emit Terraform for AWS, Azure, and GCP — and later, other
clouds — without the pipeline growing cloud-specific branches. Hard-coding
`if target == "aws"` throughout the classifier, right-sizer, and network planner
would make each new cloud a risky, cross-cutting change.

## Decision

Model each cloud as a **`Target`** behind a protocol: an instance **catalog**,
tier→family/subnet/security **mappings**, OS→image resolution, and a set of
**Jinja2 templates**. Targets live in a registry selected by name. The pipeline
depends only on the `Target` interface, never on a concrete cloud.

## Consequences

- Adding a cloud is a self-contained package (catalog + mappings + templates)
  plus one registry entry — no pipeline edits. This is exactly how Azure and GCP
  were added after AWS.
- The recommender can build a plan for *every* registered target from one
  inventory and score them uniformly — enabling unbiased multi-cloud comparison.
- Renderers generalize the same way (see [0005](0005-jinja-renderer.md)): the
  Pulumi renderer reuses each target's catalog and image resolution.
- We accept that the `Target` protocol is a shared contract: adding a capability
  (e.g. image references) means implementing it for every target.
