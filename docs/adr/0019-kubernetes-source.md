# 0019 — Kubernetes as a discovery *source*: containers read as workloads

**Status:** Accepted

## Context

Every existing source (`vmware`, `hyperv`, `generic`, `cloud`) reads a
VM-shaped inventory. But a real estate increasingly includes containerized
workloads on Kubernetes, and "what would it cost / look like to move these off
this cluster" is a legitimate migration question. The input side of the tool
had no way to read them.

Note this is the *mirror image* of the Kubernetes **renderer** (ADR 0017,
KubeVirt output): 0017 writes VMs *into* Kubernetes; this reads Kubernetes
workloads *out* as migratable units. Same product, opposite direction.

Two honest problems had to be answered, not hidden:

1. **A container is not a VM.** It has no "allocated vCPU/RAM" — it has
   *resource requests* (the scheduler's reservation) and optional *limits*.
2. **The source data shape is JSON, not CSV/XLSX.** Every prior source is
   tabular via pandas; there was no JSON precedent under `sources/`.

## Decision

1. **Read the JSON `kubectl` already emits** (`kubectl get
   deployments,statefulsets,pods -A -o json`) — no live cluster access, no
   Kubernetes client dependency, no credentials. Consistent with the whole
   tool's offline, file-in posture. Added `is_json`/`load_json` helpers to
   `sources/base.py` (the first non-tabular source).
2. **One workload object = one migratable record.** A Deployment /
   StatefulSet / DaemonSet / ReplicaSet / Pod becomes one raw record — the
   *app*, not the individual replica — so the rest of the pipeline (which is
   VM-shaped) treats it exactly like a host. Replica count is deliberately
   *not* multiplied into N records: the migration question is "this workload,"
   and the load-balancer logic already fronts multi-instance tiers downstream.
3. **Size from `resources.requests`, falling back to `limits`.** Summed across
   a pod's containers. This is the faithful reading of what the workload
   actually declared it needs — not an invented VM allocation. CPU quantities
   (`500m` → 0.5 cores) are ceil'd to whole vCPU (you provision whole vCPUs);
   memory quantities (`Gi`/`Mi`/`M`/plain bytes) convert to MiB.
4. **StatefulSet `volumeClaimTemplates` storage becomes the workload's disk.**
   The one place Kubernetes does declare durable storage; Deployments (which
   are typically stateless) get no disks, which is correct.
5. **Names are namespace-qualified** (`namespace/name`) so two same-named
   workloads in different namespaces don't collide, and the namespace also
   flows through as the `cluster` field. Tier/environment classification then
   works off the name exactly as it does for VMs — no special-casing.
6. **OS defaults to Linux**, overridden only by an explicit
   `spec.os.name` / `nodeSelector["kubernetes.io/os"]` — containers are Linux
   unless a Windows node pool is deliberately selected.

## Consequences

- Adds a genuinely new *input* class (containers) behind the existing `Source`
  protocol with zero changes to `normalize.py` or anything downstream — the
  raw-record contract absorbed it, which is the point of that contract.
- Auto-detection is clean: every tabular source returns 0.0 for a `.json`
  file, and the Kubernetes source scores 0.0 for anything that isn't
  recognizable Kubernetes JSON, so there's no contention.
- **Honest limitations, stated in the module docstring:** requests aren't a
  perfect proxy for a right-sized VM (a workload with no requests set reads as
  minimal, and bursty limits aren't captured); PersistentVolumeClaims bound to
  Deployments (as opposed to StatefulSet templates) aren't attributed; and this
  reads *declared* config, not live utilization (the utilization-based
  right-sizing path needs metrics the JSON doesn't carry).
- This is a discovery source, not "Kubernetes workload migration" in the full
  sense (no manifest translation, no Helm/Kustomize awareness) — it answers
  "size and cost these workloads as cloud VMs," which is the question the rest
  of the tool is built to answer.
