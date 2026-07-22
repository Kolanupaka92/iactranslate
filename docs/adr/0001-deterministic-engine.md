# 0001 — A deterministic engine; AI is optional and gated

**Status:** Accepted

## Context

The obvious way to "translate infrastructure with AI" is to prompt an LLM to
emit Terraform. That is non-deterministic (the same inventory yields different
HCL run to run), unauditable (no explanation for a given resource), and unsafe
for production infrastructure, where a hallucinated resource or a silently
dropped disk is a real outage. Enterprises evaluating a migration tool ask
"can I trust and review the output?" before "how clever is it?".

## Decision

The core is a **deterministic pipeline**: the same input always produces the same
output. AI, when enabled, makes only *structured decisions* (which application
group, which instance type) that are re-validated; it never writes IaC and never
bypasses validation. The default path uses a rule engine and runs with no network
and no API keys.

## Consequences

- Output is reproducible and reviewable — diffs in code review are meaningful.
- The tool runs fully offline; AI becomes a quality lever, not a dependency.
- We accept that the rule engine's decisions are simpler than a top-tier LLM's;
  the validation layer and catalogs bound the blast radius either way.
- Every downstream feature (assessment, confidence, diff) inherits determinism
  for free.
