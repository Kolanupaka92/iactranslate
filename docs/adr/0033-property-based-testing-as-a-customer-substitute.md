# 0033 — Property-based testing as a substitute for customer data

**Status:** Accepted

## Context

The project is pre-customer, so there is no real estate to validate against.
Every fixture in the repository was written by the same people who wrote the
code, which means the test suite only ever asks questions the authors already
thought to ask.

That is not a hypothetical weakness. [ADR 0031](0031-sanitize-untrusted-inventory-at-normalize.md)
documents a proven Terraform template injection and an Azure naming bug that
made output invalid for any realistic estate — **both survived 380+ passing
tests**, and both were found within minutes of feeding the pipeline input that
did not look like `prod-web-01`. The fixtures were not wrong; they were
*friendly*, and friendliness is the blind spot.

"Get a design partner" is the right long-term answer and not something that can
be actioned today. The question this ADR answers: what is the best available
substitute for a real customer's messy 5,000-row export?

## Decision

Adopt **property-based testing** (Hypothesis) for the input path.

Instead of asserting a specific output for a known input, each test states an
**invariant that must hold for every possible estate**, and the generator hunts
for a counter-example — exploring empty names, zero-CPU rows, `Inf` cells,
Unicode whitespace, duplicate machines, and control characters that nobody
would think to write into a fixture by hand.

The framing that makes this worth the runtime: **an invariant here is a promise
to a customer we do not have yet.** If one fails, the product is broken for some
real estate somewhere, and we would rather find that now than during a pilot.

The invariants asserted are the ones whose violation would be most damaging:
parsing never raises, no workload is silently dropped or duplicated, Terraform
resource labels are unique and syntactically valid, generated code is never
injectable, costs are never negative or NaN, and normalization is deterministic.

## What it found immediately

Four real bugs on the first run, none of which the existing suite could see:

1. **Zero memory crashed the entire upload.** `memory_gib` is constrained
   `> 0`, but a row reporting `0` returned `0.0` and raised a `ValidationError`
   out of `normalize()`. Templates, powered-off shells, and half-filled CMDB
   rows all produce zero-memory rows — one of them would have failed a
   5,000-VM file. The author had already intended a floor here ("*a sane
   floor; a VM always has some memory*") but it only fired when memory was
   **missing**, not when it was **zero**.
2. **The sanitizer was not idempotent.** `sanitize_identifier` matched
   `\x00-\x1f`, but `.strip()` also removes Unicode whitespace such as U+0085
   (NEL) that the class does not cover, so a second pass could shorten the
   result again. This matters more than it looks: a name that changes between
   runs changes the Terraform resource label, and Terraform treats a renamed
   resource as **destroy-and-recreate**.
3. **`Inf` in a numeric cell raised `OverflowError`.** `int(round(float("Inf")))`
   is not caught by `(TypeError, ValueError)`, so the exception escaped and
   failed the upload. pandas produces NaN for blank numeric cells, making this
   ordinary input rather than an exotic one.
4. **Distinct machines collided onto one Terraform label.** `terraform_safe_name`
   maps every non-alphanumeric run to `_`, so `web-01`, `web.01`, `WEB_01`, and
   `web 01` all become `web_01` — emitting duplicate `resource` blocks that
   Terraform rejects. A CMDB and a DNS zone rarely agree on separators or case,
   so this is close to guaranteed in a real estate. Resolved by suffixing
   collisions deterministically at plan-build time, where the whole set is
   known, and re-checking so a machine genuinely called `web_01_2` is also safe.

## Consequences

- The input path is now tested against generated adversarial data rather than
  only against fixtures the authors imagined, with the failing example shrunk
  to a minimal reproduction automatically.
- Bug 4 in particular was a **guaranteed** first-pilot failure. Finding it cost
  minutes; finding it in front of a design partner would have cost the pilot.
- **This does not replace a real customer.** Hypothesis validates *invariants*,
  not *semantics* — it cannot tell us whether a VM was classified into the
  right tier, whether the cost estimate resembles a real bill, or whether the
  recommended cloud is the one an architect would pick. Those still need real
  data and a human who knows the estate. The claim here is narrower: the
  product should no longer *crash, lose a machine, or emit invalid Terraform*
  for any input shape.
- Runtime cost is real (~10s for the property suite) and accepted; it runs in
  CI alongside everything else.
