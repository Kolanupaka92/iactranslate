# 0004 — AI behind a provider interface with a rule-engine default

**Status:** Accepted

## Context

We want the option of higher-quality decisions from a capable LLM (application
grouping, instance selection) without making the product depend on an API key, a
network, or a specific vendor — and without letting model output reach production
IaC unchecked. Determinism ([0001](0001-deterministic-engine.md)) must remain the
default.

## Decision

Put the classify/right-size steps behind an **`LLMProvider` interface** with two
implementations: `rule` (deterministic engine + static catalog, the default, no
key) and `anthropic` (Claude structured tool-use). Selecting `anthropic` without
a key transparently falls back to `rule`. Provider output is *always* re-checked
by the validation layer and the catalog guardrail.

## Consequences

- The pipeline always runs — offline, keyless, reproducible — and AI is a
  drop-in upgrade, not a requirement.
- Adding another model vendor is a new provider, not a pipeline change.
- Because output is re-validated regardless of provider, a bad AI decision
  degrades quality, never correctness.
- We accept maintaining two decision paths; the rule engine must stay good enough
  to ship on its own.
