# 0017 — Kubernetes renders VMs as KubeVirt VirtualMachines, not fabricated Deployments

**Status:** Accepted

## Context

CloudFormation, Bicep, and AWS CDK ([0013](0013-cloudformation-from-graph.md)–
[0015](0015-cdk-from-graph.md)) each proved the Infrastructure Graph seam for
a format that mirrors a cloud's own resource model — a VPC is a VPC, an
instance is an instance, regardless of syntax. Kubernetes is a genuinely
different resource model: Pods, Deployments, Services — nothing in it is a
1:1 analog of a VM, a subnet, or a security group.

The naive move — emit a `Deployment` per instance — requires inventing facts
the pipeline does not have: a container image, an entrypoint, a listening
port beyond whatever a security group's ingress rules imply, a health check.
`ComputePlan`/the graph's instance nodes describe a VM (OS image, root/extra
volumes, an instance-type-style vCPU/memory spec) because the source
inventory (RVTools, Hyper-V exports, CMDB rows) describes VMs. Fabricating
containerization details to force a `Deployment` shape would silently invent
data the plan never asserted.

## Decision

1. **VMs become `kubevirt.io/v1: VirtualMachine` objects**, not Deployments.
   [KubeVirt](https://kubevirt.io) is a real, widely-deployed Kubernetes
   CRD/operator that runs an actual VM as a cluster-managed workload — the
   honest translation of "migrate this VM to run on Kubernetes" rather than a
   fabricated one. `spec.template.spec.domain.cpu.cores` /
   `resources.requests.memory` come straight from the graph instance node's
   `vcpu`/`memory_gib`; root and extra volumes become one `dataVolumeTemplate`
   PVC each.
2. **Security-group ingress becomes `NetworkPolicy`.** Each graph security
   group's `ingress` attribute (already enriched per ADR 0010/0013) maps
   directly to a `networking.k8s.io/v1 NetworkPolicy` — CIDR blocks become
   `ipBlock` sources, port ranges become `ports` entries. This is a clean,
   real analog; unlike the VM-vs-container question there was no ambiguity
   here.
3. **One `Namespace` per project — an explicitly acknowledged approximation.**
   A Kubernetes Namespace is a naming/RBAC boundary, not a network boundary
   the way a VPC is (`NetworkPolicy`, not the Namespace, is what actually
   restricts traffic). Using it as the project's container is the closest
   real analog available and is documented as such rather than presented as
   equivalent.
4. **`Service` per instance** (`LoadBalancer` for public-subnet-tier
   instances exposing a common web port, `ClusterIP` otherwise), with ports
   again derived from the instance's security group — reusing the same
   ingress data the `NetworkPolicy` came from rather than a second, possibly
   inconsistent source.
5. **Cloud-agnostic: no target restriction**, unlike CloudFormation (AWS-only)
   or Bicep (Azure-only). KubeVirt runs on any Kubernetes cluster regardless
   of which cloud the plan targeted; the renderer takes `target` only for
   interface parity with the registry and doesn't gate on it.
6. **Deterministic JSON, not YAML.** JSON is valid Kubernetes manifest syntax
   (`kubectl apply -f x.json` works, and a `kind: List` wrapping multiple
   items is standard). This avoids adding PyYAML as a project dependency for
   a format `json.dumps` (stdlib) already produces correctly — the same
   choice CloudFormation made (ADR 0013).
7. **No image-resolution mechanism, and this is stated plainly rather than
   papered over.** Unlike AWS AMIs or Azure Marketplace images, there is no
   Kubernetes-native catalog to resolve `image_key` against. Each
   `VirtualMachine`'s `dataVolumeTemplates` ships with a `source: {blank: {}}`
   placeholder, correctly sized, with the generated README explicitly
   instructing the operator to replace it with a real CDI import source
   before deploying — the same honesty pattern as CloudFormation's
   operator-supplied AMI parameter for OSes with no public SSM alias.

## Consequences

- Confirms the graph seam holds for a resource model that isn't just another
  cloud's own IaC format wearing different syntax — the graph's VM/SG/subnet
  shape maps cleanly onto VirtualMachine/NetworkPolicy/Namespace with no
  changes to `build_graph` itself.
- **Honest limitation, stated twice over**: no local `kubectl`/KubeVirt to
  validate against (tests assert structural validity and referential
  integrity instead), and no automatic OS image source (the operator must
  supply one via CDI). Both are called out in the generated README, not left
  for the operator to discover at deploy time.
- This renderer is unlikely to be the right choice for most real migrations —
  running a VM inside a VM-on-Kubernetes layer is a narrower use case than a
  native cloud target — but it demonstrates the graph is a genuine
  intermediate representation, not a CloudFormation-shaped one that happened
  to also fit Bicep and CDK.
