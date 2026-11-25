# Architecture Decision Records

Short records of the *why* behind the load-bearing decisions in IaCTranslate.
Each captures the context at the time, the decision, and its consequences — so
the reasoning survives even when the people don't.

Format: [Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| # | Decision | Status |
|---|---|---|
| [0001](0001-deterministic-engine.md) | A deterministic engine; AI is optional and gated | Accepted |
| [0002](0002-normalizedvm-canonical-model.md) | `NormalizedVM` as the canonical inventory model | Accepted |
| [0003](0003-target-registry.md) | Clouds behind a target registry | Accepted |
| [0004](0004-ai-provider-interface.md) | AI behind a provider interface with rule-engine default | Accepted |
| [0005](0005-jinja-renderer.md) | Templates (Jinja2) emit IaC, not the model or the AI | Accepted |
| [0006](0006-validation-before-render.md) | Validation is a hard gate before rendering | Accepted |
| [0007](0007-immutable-plan.md) | The MigrationPlan is immutable after planning | Accepted |
| [0008](0008-policy-engine.md) | A policy engine for organization-specific rules | Accepted |
| [0009](0009-capability-flags.md) | Targets advertise capability flags | Accepted |
| [0010](0010-infrastructure-graph.md) | An Infrastructure Graph IR between plan and renderers | Accepted |
| [0011](0011-pipeline-stages.md) | The pipeline runs as named, timed stages | Accepted |
| [0012](0012-async-jobs-event-bus-audit.md) | Async jobs, an event bus, and an audit trail | Accepted |
| [0013](0013-cloudformation-from-graph.md) | CloudFormation renders from the graph; AMIs via SSM | Accepted |
| [0014](0014-bicep-from-graph.md) | Bicep renders from the graph; subscription-scope + module | Accepted |
| [0015](0015-cdk-from-graph.md) | AWS CDK renders from the graph via L1 constructs, reusing CloudFormation's AMI logic | Accepted |
| [0016](0016-terraform-pulumi-placement-from-graph.md) | Terraform/Pulumi placement moves onto the graph; fixes a subnet-collapse bug | Accepted |
| [0017](0017-kubernetes-from-graph.md) | Kubernetes renders VMs as KubeVirt VirtualMachines, not fabricated Deployments | Accepted |
| [0018](0018-load-balancer-topology.md) | Load balancer topology: modeled once, rendered six ways | Accepted |
| [0019](0019-kubernetes-source.md) | Kubernetes as a discovery source: containers read as workloads | Accepted |
| [0020](0020-managed-db-replatforming.md) | Managed-database re-platforming is advisory, not automated | Accepted |
| [0021](0021-ai-integration-reachable-and-honest.md) | AI made reachable end-to-end (CLI/API/web), and always honestly labeled | Accepted |
| [0022](0022-oci-target.md) | OCI target: Flex shapes need a synthetic catalog key, capabilities stay honest | Accepted |
| [0023](0023-digitalocean-target.md) | DigitalOcean target: real platform gaps (no subnets, no Windows) stated, not papered over | Accepted |
| [0024](0024-migration-wave-planning.md) | Migration wave planning: tier + environment order, not a fabricated dependency graph | Accepted |
| [0025](0025-persistent-store-and-bearer-auth.md) | A real persistent store (SQLite) and real auth (bearer token), scoped to what's buildable without Docker | Accepted |
| [0026](0026-persistent-audit-and-metrics.md) | A persistent audit trail and Prometheus metrics, both built as event-bus subscribers | Accepted |
| [0027](0027-multi-tenancy-and-session-auth.md) | Multi-tenancy: user accounts, session cookies (not bearer tokens), and per-project ownership | Accepted |
| [0028](0028-rate-limiting-and-security-headers.md) | Per-route rate limiting (per-IP *and* per-account on auth) plus baseline security headers | Accepted |
| [0029](0029-durable-artifact-workspaces.md) | Generated artifacts move off `/tmp` onto a durable volume, so files survive with their metadata | Accepted |
| [0030](0030-password-change-and-reset.md) | Password change and reset: both evict every session; delivery stops short of unverified SMTP | Accepted |
| [0031](0031-sanitize-untrusted-inventory-at-normalize.md) | Untrusted inventory is sanitized once at `normalize`, killing a proven Terraform template injection | Accepted |
| [0032](0032-split-compute-output-for-reviewability.md) | Large estates split compute output per environment/tier so the result is reviewable | Accepted |
| [0033](0033-property-based-testing-as-a-customer-substitute.md) | Property-based testing stands in for customer data pre-launch; found 4 real bugs immediately | Accepted |
| [0034](0034-zero-friction-evaluation.md) | Zero-friction evaluation: `demo` needs no inventory, plus compose and a published image | Accepted |
| [0035](0035-realistic-rvtools-parsing.md) | Test against a structurally real RVTools export; stop silently upgrading a customer's OS | Accepted |
| [0036](0036-workspace-ui-not-a-scrolling-wizard.md) | The web UI becomes a project workspace: completed steps collapse, results come first | Accepted |
| [0037](0037-publish-the-scoring-weights.md) | Publish the recommendation's scoring weights so the ranking is checkable by hand | Accepted |
| [0038](0038-a-cloud-that-cannot-run-the-estate-is-not-a-candidate.md) | A cloud that cannot run the estate is not a candidate: truthful `image_key`, OS-family flags, eligibility gate | Accepted |
| [0039](0039-cost-the-whole-bill-not-just-the-instances.md) | Cost the whole bill — storage, Windows licensing and load balancers, not just instances | Accepted |
| [0040](0040-structured-logging-and-request-correlation.md) | Structured JSON logging and request correlation, OTel field names without the SDK | Accepted |
| [0041](0041-remote-state-backends-in-generated-terraform.md) | Remote state backends in generated Terraform; local state stated, never silent | Accepted |
| [0042](0042-supply-chain-security.md) | Supply-chain security: SBOM, pip-audit, bandit, Trivy, cosign signing, SLSA provenance | Accepted |
| [0043](0043-encryption-at-rest-for-uploaded-inventory.md) | AES-256-GCM encryption at rest for uploaded inventory, failing closed | Accepted |
| [0044](0044-landing-zone-cidr-and-subnet-carving.md) | Landing-zone CIDRs, with subnets carved from them and ingress retargeted | Accepted |
| [0045](0045-mandated-tags-in-every-clouds-native-form.md) | Mandated tags via each cloud's native mechanism; normalise, never drop | Accepted |
| [0046](0046-shared-rate-limit-buckets.md) | Shared rate-limit buckets in Redis, atomic via Lua, degrading to per-replica | Accepted |
| [0047](0047-data-transfer-and-cutover-windows.md) | Data-transfer time and cutover-window fit, per migration wave | Accepted |
| [0048](0048-rank-the-recommendation-on-the-full-bill.md) | Rank the recommendation on the itemized total, not compute alone | Accepted |
| [0049](0049-api-versioning.md) | Version the API at /v1 before it has clients; legacy mount marks itself deprecated | Accepted |
| [0050](0050-project-roles.md) | Ordered project roles (viewer/editor/approver/admin); 404 absent, 403 insufficient | Accepted |
| [0051](0051-idempotent-writes-and-paged-lists.md) | Idempotency-Key replay protection; paging via Link headers to avoid a breaking change | Accepted |
| [0052](0052-pdf-output-for-every-report.md) | PDF for every report: a shared print stylesheet always, WeasyPrint optionally | Accepted |
| [0053](0053-durable-job-queue.md) | A SQLite job queue that survives restart, with leases, backoff and dead letters | Accepted |
| [0054](0054-oidc-single-sign-on.md) | OIDC SSO with PKCE, tested against a mock issuer; validation by PyJWT, not by hand | Accepted |
| [0055](0055-dependencies-from-observed-flows.md) | Application dependencies from a flow export; wave violations and external systems | Accepted |
| [0056](0056-durable-project-grants.md) | Project grants persist; half-durable authorization was a bug, not a limitation | Accepted |
| [0057](0057-workload-identity-without-permissions.md) | An identity per tier, wired to instances, with no permissions — guessing them creates the breach | Accepted |
| [0058](0058-de-identify-inventory-before-ai.md) | Consistent per-token pseudonyms before inventory reaches a remote model — grouping signal survives, identity does not | Accepted |
| [0059](0059-pricing-circuit-breaker.md) | Bound the cost of unreachable billing endpoints, and stop reporting a mostly-static estate as live-priced | Accepted |
| [0060](0060-hybrid-connectivity-from-observed-flows.md) | On-prem networks the estate still needs, from observed flows — including the overlap that no route can fix | Accepted |
| [0061](0061-renderer-selection-through-the-api.md) | All six IaC formats reach the API and console, with per-target support declared once and tested against the renderers | Accepted |
| [0062](0062-encryption-at-rest-in-generated-infrastructure.md) | Generated volumes are encrypted with no off switch; what is configurable is whose key | Accepted |
| [0063](0063-one-sql-layer-for-sqlite-and-postgresql.md) | Durable state runs on SQLite or PostgreSQL through one implementation per component, not one per engine | Accepted |
| [0064](0064-decision-evidence-and-honest-capability-claims.md) | Every sizing decision carries its evidence and basis; validation depth and provider maturity are stated and tested, not claimed | Accepted |
| [0065](0065-on-premises-destinations.md) | Nutanix AHV and Proxmox VE as targets; unpriced destinations are excluded from cost ranking and rendered as "not estimated", never $0 | Accepted |
| [0066](0066-run-lease-for-concurrent-runs.md) | Concurrent runs of one project are serialised by a lease claimed in one conditional UPDATE — correct across instances, and self-healing after a crash | Accepted |
| [0067](0067-nonce-based-content-security-policy.md) | The console's CSP is generated per request with a script nonce; the static header could not refuse inline scripts and so protected nothing | Accepted |
| [0068](0068-database-on-a-private-address-only.md) | Cloud SQL has no public address; Cloud Run reaches it over Direct VPC egress, and the Auth Proxy sidecar is gone because it cannot work that way | Accepted |
| [0069](0069-live-rates-for-every-line-of-the-bill.md) | Live pricing covers storage, Windows licensing and load balancers too, falling back per line, and the estimate names which lines are live | Accepted |
| [0070](0070-key-vm-identity-on-the-vm-uuid.md) | De-duplication, disk/NIC joins and snapshot diffs key on the RVTools VM UUID; same-named machines are both kept, and a rename is reported as one. Terraform labels stay name-derived | Accepted |
| [0071](0071-user-adjustable-scoring-weights.md) | Callers can set the recommendation's cost/fit/OS weights; they are normalised, eligibility still dominates, and a custom ranking is always labelled custom | Accepted |
| [0072](0072-moved-blocks-for-renamed-machines.md) | `diff --moved-tf` generates Terraform `moved` blocks for machines renamed at the source, from the two snapshots alone; renames that reuse an address become a `state mv` sequence, because Terraform chains blocks | Accepted |
| [0073](0073-for-each-layout.md) | Opt-in `terraform-foreach` renderer (every target): one resource per kind over per-machine maps keyed by the classic labels, shipped with `moved` blocks from the classic layout; default output unchanged | Accepted |
| [0074](0074-shared-decisions-not-a-shared-renderer.md) | Decisions renderers would otherwise re-derive (which machines get a public address) live once in the graph; the wholesale Terraform/Pulumi graph migration is not done, since it would fix nothing | Accepted |
| [0075](0075-ci-pipelines-for-four-systems.md) | GitOps pipelines for GitHub Actions, GitLab CI, Azure DevOps and Jenkins from one credentials table per cloud; OCI/DigitalOcean/Nutanix/Proxmox gain credentials, and renderers with their own tooling are refused | Accepted |
| [0076](0076-migration-backlog-as-import-files.md) | The assessment and wave plan as Jira, Azure DevOps and ServiceNow import files, in each tool's documented format — files, not API calls | Accepted |
| [0077](0077-run-notifications.md) | Slack, Teams and email when a run ends: operator-configured, off the request path, outcome figures by default and names only on opt-in | Accepted |
| [0078](0078-password-reset-email.md) | Reset links emailed over the shared SMTP transport, off the request thread so timing doesn't reveal accounts, with forgot/reset pages in the console | Accepted |
| [0079](0079-email-verification.md) | Email verification: single-use link at sign-up confirmed by a click, reset/SSO as proof, optional project gate, SSO reclaims squatted unverified accounts | Accepted |
| [0080](0080-organisations.md) | Organisations: owner/admin/member, org projects follow org roles (highest role wins), consent-and-proof invitations, last-owner protection, project grants read from the database on every check | Accepted |
| [0081](0081-saml-single-sign-on.md) | SAML 2.0 SP on signxml (not python3-saml/xmlsec): pinned cert, signed-copy-only reads, one assertion, no DTDs, browser-bound single-use requests, trusted email domains | Accepted |
| [0082](0082-oidc-sign-in-from-the-console.md) | OIDC from the console: login state in the database, a state cookie against login CSRF, redirect back to the console, PyJWT in the image | Accepted |
| [0083](0083-no-cross-account-eviction.md) | Accounts get a project quota that refuses; only unowned projects are ever evicted | Accepted |
| [0084](0084-plan-the-generated-code.md) | Generated Terraform is planned against mocked providers and `fmt`-checked on every target; project names are made safe for cloud resources | Accepted |
| [0085](0085-size-databases-by-their-shape.md) | Databases and caches get the cheapest fit that doesn't share CPU, not a forced memory-optimized family | Accepted |
| [0086](0086-never-shrink-a-machine-in-silence.md) | Every catalog has larger sizes, and a machine that still doesn't fit is flagged everywhere and makes that cloud not eligible | Accepted |
| [0087](0087-no-headroom-without-usage-data.md) | Without usage data a machine gets its allocation and nothing more; CPU and memory are decided separately | Accepted |
| [0088](0088-an-ocpu-is-two-vcpus.md) | Oracle sizes are counted in vCPUs, generated in OCPUs; its machines had twice the CPU they needed | Accepted |

See also the [Architecture & Design](../architecture.md) overview.
