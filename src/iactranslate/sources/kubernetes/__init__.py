"""Kubernetes source — `kubectl get deployments|statefulsets|pods -A -o json`.

The one source that reads *containerized* workloads rather than VMs. Each
workload object (Deployment / StatefulSet / DaemonSet / ReplicaSet / Pod)
becomes one raw record — the app, not the individual replica — so the rest of
the pipeline (which is VM-shaped) treats it exactly like any other host.

The honest mapping question this source answers: a container has no "allocated
vCPU/RAM" the way a VM does — it has *resource requests* (the scheduler's
reservation) and optionally *limits*. We size from `resources.requests`
(what the workload actually asked for), falling back to `limits`, and treat
StatefulSet `volumeClaimTemplates` storage as the workload's disk. This is a
faithful read of what Kubernetes actually declares, not an invented VM shape.

Input is the JSON `kubectl` already emits — no cluster access, no live API.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from ..base import RawRecord, is_json, load_json

# Workload kinds we treat as one migratable unit each.
_WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod"}

# Binary (Ki/Mi/Gi/Ti/Pi) and decimal (k/M/G/T/P) memory suffixes → bytes.
_MEM_SUFFIX: Dict[str, float] = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5,
    "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15,
}
_BYTES_PER_MIB = 1024 * 1024


def _cpu_to_cores(value: Optional[str]) -> float:
    """Kubernetes CPU quantity → cores. '500m' -> 0.5, '2' -> 2.0."""
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    try:
        if s.endswith("m"):
            return float(s[:-1]) / 1000.0
        if s.endswith("n"):  # nanocores (rare, from metrics)
            return float(s[:-1]) / 1e9
        return float(s)
    except ValueError:
        return 0.0


def _mem_to_bytes(value: Optional[str]) -> float:
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    for suffix, mult in _MEM_SUFFIX.items():
        if s.endswith(suffix):
            try:
                return float(s[: -len(suffix)]) * mult
            except ValueError:
                return 0.0
    try:
        return float(s)  # plain bytes
    except ValueError:
        return 0.0


def _containers_of(obj: dict) -> List[dict]:
    kind = obj.get("kind")
    if kind == "Pod":
        return obj.get("spec", {}).get("containers", []) or []
    # Controller kinds carry a pod template.
    template = obj.get("spec", {}).get("template", {})
    return template.get("spec", {}).get("containers", []) or []

def _pod_spec_of(obj: dict) -> dict:
    if obj.get("kind") == "Pod":
        return obj.get("spec", {}) or {}
    return obj.get("spec", {}).get("template", {}).get("spec", {}) or {}


def _resource(container: dict, dimension: str) -> Optional[str]:
    """A container's requests[dimension], falling back to limits[dimension]."""
    resources = container.get("resources", {}) or {}
    requests = resources.get("requests", {}) or {}
    limits = resources.get("limits", {}) or {}
    return requests.get(dimension) or limits.get(dimension)


def _storage_gib(obj: dict) -> List[float]:
    """StatefulSet volumeClaimTemplates storage requests → per-volume GiB."""
    templates = obj.get("spec", {}).get("volumeClaimTemplates", []) or []
    disks: List[float] = []
    for vct in templates:
        req = vct.get("spec", {}).get("resources", {}).get("requests", {}) or {}
        size = req.get("storage")
        if size is not None:
            gib = _mem_to_bytes(size) / (1024**3)
            if gib > 0:
                disks.append(round(gib, 2))
    return disks


def _os_of(obj: dict) -> str:
    spec = _pod_spec_of(obj)
    # Kubernetes 1.25+ `spec.os.name`, else the nodeSelector hint, else Linux
    # (containers are Linux unless a Windows node pool is explicitly selected).
    os_name = (spec.get("os", {}) or {}).get("name")
    if not os_name:
        os_name = (spec.get("nodeSelector", {}) or {}).get("kubernetes.io/os")
    return str(os_name or "linux")


def _iter_objects(doc: object) -> List[dict]:
    """Flatten kubectl output into a flat list of Kubernetes objects."""
    if isinstance(doc, list):
        return [o for o in doc if isinstance(o, dict)]
    if isinstance(doc, dict):
        if "items" in doc and isinstance(doc["items"], list):
            return [o for o in doc["items"] if isinstance(o, dict)]
        return [doc]
    return []


class KubernetesSource:
    name = "kubernetes"
    label = "Kubernetes (kubectl get … -o json)"
    source_platform = "kubernetes"

    def detect(self, path: str) -> float:
        if not is_json(path):
            return 0.0
        doc = load_json(path)
        objects = _iter_objects(doc)
        if not objects:
            return 0.0
        kinds = {o.get("kind") for o in objects}
        list_kind = doc.get("kind") if isinstance(doc, dict) else None
        if kinds & _WORKLOAD_KINDS or list_kind in {"List", "DeploymentList", "StatefulSetList", "PodList"}:
            # apiVersion present on real k8s objects raises confidence.
            if any("apiVersion" in o or "kind" in o for o in objects):
                return 0.95
            return 0.7
        return 0.0

    def parse(self, path: str, column_map: Optional[Dict[str, str]] = None) -> List[RawRecord]:
        doc = load_json(path)
        records: List[RawRecord] = []
        for obj in _iter_objects(doc):
            if obj.get("kind") not in _WORKLOAD_KINDS:
                continue
            meta = obj.get("metadata", {}) or {}
            name = meta.get("name")
            if not name:
                continue
            namespace = meta.get("namespace")

            containers = _containers_of(obj)
            cores = sum(_cpu_to_cores(_resource(c, "cpu")) for c in containers)
            mem_bytes = sum(_mem_to_bytes(_resource(c, "memory")) for c in containers)

            rec: RawRecord = {
                # namespace-qualified so two same-named workloads don't collide.
                "name": f"{namespace}/{name}" if namespace else str(name),
                "os": _os_of(obj),
                "cluster": namespace,
            }
            if cores > 0:
                rec["cpus"] = max(1, math.ceil(cores))  # whole vCPUs to provision
            if mem_bytes > 0:
                rec["memory_mib"] = mem_bytes / _BYTES_PER_MIB
            disks = _storage_gib(obj)
            if disks:
                rec["disks_mib"] = [round(g * 1024) for g in disks]
            records.append(rec)
        return records
