# 0034 — Zero-friction evaluation: `demo`, compose, and a published image

**Status:** Accepted

## Context

The product was, in practice, un-tryable by anyone outside the repository.

To evaluate it you needed two things: an RVTools or CMDB export of your own,
**and** the willingness to hand that inventory to software you had never run.
The second is a large ask. An infrastructure inventory is a map of a company's
entire estate — hostnames, IPs, OS versions, capacity. Uploading one to an
unknown tool is exactly the kind of thing a security-conscious engineer will
not do on a first look, and it is the point at which most evaluations stop.

The irony is that the capability removing this objection was already built and
never surfaced. The pipeline is offline by default: no internet, no API keys,
no cloud credentials, nothing leaves the machine. That was documented as a
*security property* and never presented as what it actually is — the reason
someone can try this without trusting us at all.

Three concrete gaps followed from that framing:

- No way to run the tool without supplying an inventory.
- No published image, so trying it meant cloning a repo and building.
- No compose file, so running the stack meant reading the Dockerfile.

## Decision

**1. `iactranslate demo`** runs the complete pipeline against a sample estate
bundled inside the package. No input file, no account, no upload.

The sample is deliberately *realistic rather than tidy*: mixed OS (including
Windows Server 2012 R2 and CentOS 7), four tiers, three environments,
utilization data on every row so right-sizing actually engages, multi-disk
machines, and the messy naming real inventories contain — `PROD-DB-01`,
`prod cache 01`, `dev.sandbox.01`. A clean 7-row sample would demo well and
misrepresent what the tool does; this one exercises tier classification,
environment detection, right-sizing, load-balancer topology, and the naming
collision handling from [ADR 0033](0033-property-based-testing-as-a-customer-substitute.md).

The sample ships as `package-data`, not as a repo file. `demo` therefore works
from an installed wheel and inside the container, not only from a git checkout
— a test asserts the path resolves relative to the package rather than the
working directory, because that distinction is invisible in local development
and fatal in a published image.

**2. `docker-compose.yml`** brings up the stack with persistence already wired
(`sqlite` store + a durable artifact volume), so `down && up` keeps projects.
Its memory limit is set from a measurement rather than a guess: a
5,000-workload run — the documented `MAX_VMS` ceiling — peaks at ~185 MB RSS in
4.5 s, so 512 MB is comfortable.

**3. CI publishes to GHCR from `main`**, and smoke-tests the demo path *inside
the container*. That smoke test is not ceremony: if `demo` breaks in the image,
nobody can try the product, and every other green check would still pass.

## Consequences

- A stranger can now go from nothing to generated Terraform in one command,
  without an account, an upload, or a conversation. Verified: the GHCR manifest
  resolves anonymously, so the image is genuinely public.
- The offline property is now load-bearing in the *adoption* story, not just
  the security section. This is the most under-used asset the project had.
- The bundled sample is a maintenance obligation. If a target's catalog or the
  classifier changes materially, the demo output changes with it — which is
  correct, and the CI smoke test will catch it going silently wrong.
- **This does not make the product validated.** It removes the barrier to
  someone else running it; it does not mean anyone has. The open question is
  unchanged and is a distribution question, not an engineering one: getting
  real estates in front of it. What changed is that the answer to "can I try
  it?" is now a command instead of a sales call.
