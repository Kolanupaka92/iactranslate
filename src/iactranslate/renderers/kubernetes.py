"""Kubernetes renderer — the fourth to consume the Infrastructure Graph instead
of the MigrationPlan directly (see ADR 0010), and the first genuinely new
resource model rather than a mirror of a cloud's own IaC format.

The design question this renderer actually has to answer: what does a "VM"
become in Kubernetes? The source data (`ComputePlan`/graph instance nodes) is
VM-shaped — an OS image, a root volume, extra volumes, an instance-type-style
CPU/memory spec — not container-shaped. There is no Dockerfile, no entrypoint,
no declared port beyond what a security group's ingress rules imply. Emitting
a plain `Deployment` would require inventing all of that. Instead this
renderer targets **KubeVirt** (`kubevirt.io/v1: VirtualMachine`) — a real,
widely-deployed CRD that runs an actual VM as a Kubernetes-managed workload,
which is the honest translation of what we actually know, not a fabricated
containerization.

Deterministic JSON, not YAML: JSON is valid Kubernetes manifest syntax
(`kubectl apply -f x.json` works), and this avoids adding PyYAML as a project
dependency for a format `json.dumps` already produces correctly — the same
call CloudFormation's renderer already made (ADR 0013).

No local `kubectl`/KubeVirt in this environment to apply against; tests
assert structural validity (required `apiVersion`/`kind`/`metadata` on every
object, referential integrity between generated objects) instead.
"""
from __future__ import annotations

import json
from typing import Dict, List

from ..graph import EdgeKind, InfrastructureGraph, NodeKind, build_graph
from ..models import MigrationPlan, terraform_safe_name
from ..targets.base import Target

# Ports implied by common ingress rules that identify an application tier
# (web tiers typically expose these) - anything else stays ClusterIP-only.
_PUBLIC_SERVICE_PORTS = {80, 443, 8080, 8443}


def _slug(name: str) -> str:
    return terraform_safe_name(name).replace("_", "-")


def _namespace(plan: MigrationPlan) -> Dict[str, object]:
    ns = _slug(plan.project_name)
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": ns,
            "labels": {"iactranslate.io/project": ns, "iactranslate.io/source-target": plan.target},
        },
    }


def _network_policies(graph: InfrastructureGraph, namespace: str) -> Dict[str, object]:
    items: List[Dict[str, object]] = []
    for sg in graph.nodes_of(NodeKind.SECURITY_GROUP):
        ingress_rules = []
        for rule in sg.attributes["ingress"]:
            ingress_rules.append({
                "from": [{"ipBlock": {"cidr": cidr}} for cidr in rule["cidr_blocks"]],
                "ports": [{
                    "protocol": rule["protocol"].upper(),
                    "port": rule["from_port"],
                }] if rule["from_port"] == rule["to_port"] else [
                    {"protocol": rule["protocol"].upper(), "port": p}
                    for p in range(rule["from_port"], rule["to_port"] + 1)
                ],
            })
        items.append({
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": _slug(sg.name), "namespace": namespace},
            "spec": {
                "podSelector": {"matchLabels": {"iactranslate.io/secured-by": _slug(sg.name)}},
                "policyTypes": ["Ingress"],
                "ingress": ingress_rules,
            },
        })
    return {"apiVersion": "v1", "kind": "List", "items": items}


def _virtual_machines(graph: InfrastructureGraph, plan: MigrationPlan, namespace: str) -> Dict[str, object]:
    sg_name_by_instance: Dict[str, str] = {}
    for e in graph.edges:
        if e.kind == EdgeKind.SECURED_BY:
            sg_node = graph.node(e.target)
            if sg_node is not None:
                sg_name_by_instance[e.source] = sg_node.name

    items: List[Dict[str, object]] = []
    for c in plan.compute:
        inst = next(n for n in graph.nodes_of(NodeKind.INSTANCE) if n.name == c.vm_name)
        name = _slug(c.resource_name)
        cores = inst.attributes["vcpu"]
        memory = f"{max(1, round(inst.attributes['memory_gib']))}Gi"
        sg_name = sg_name_by_instance.get(inst.id)

        disks = [{"name": "rootdisk", "disk": {"bus": "virtio"}}]
        volumes = [{"name": "rootdisk", "dataVolume": {"name": f"{name}-root"}}]
        data_volume_templates = [{
            "metadata": {"name": f"{name}-root"},
            "spec": {
                "source": {"blank": {}},
                "pvc": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": f"{inst.attributes['root_volume_gib']}Gi"}},
                },
            },
        }]
        for i, size in enumerate(inst.attributes.get("extra_volumes_gib") or []):
            dvname = f"{name}-data{i + 1}"
            disks.append({"name": f"data{i + 1}", "disk": {"bus": "virtio"}})
            volumes.append({"name": f"data{i + 1}", "dataVolume": {"name": dvname}})
            data_volume_templates.append({
                "metadata": {"name": dvname},
                "spec": {
                    "source": {"blank": {}},
                    "pvc": {
                        "accessModes": ["ReadWriteOnce"],
                        "resources": {"requests": {"storage": f"{size}Gi"}},
                    },
                },
            })

        labels = {
            "iactranslate.io/vm": name,
            "iactranslate.io/tier": inst.attributes["tier"],
            "iactranslate.io/environment": inst.attributes["environment"],
        }
        if sg_name:
            labels["iactranslate.io/secured-by"] = _slug(sg_name)

        items.append({
            "apiVersion": "kubevirt.io/v1",
            "kind": "VirtualMachine",
            "metadata": {"name": name, "namespace": namespace, "labels": labels},
            "spec": {
                "running": True,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "domain": {
                            "cpu": {"cores": cores},
                            "resources": {"requests": {"memory": memory}},
                            "devices": {"disks": disks},
                        },
                        "volumes": volumes,
                    },
                },
                "dataVolumeTemplates": data_volume_templates,
            },
        })
    return {"apiVersion": "v1", "kind": "List", "items": items}


def _services(graph: InfrastructureGraph, plan: MigrationPlan, namespace: str) -> Dict[str, object]:
    """One Service per graph load balancer (selecting the whole fronted group by
    tier+environment label), plus one Service per instance NOT fronted by any
    load balancer — mirroring the same "front the group, not the instance"
    decision the other renderers make once a LoadBalancerPlan exists."""
    sg_by_instance: Dict[str, object] = {}
    for e in graph.edges:
        if e.kind == EdgeKind.SECURED_BY:
            sg_by_instance[e.source] = graph.node(e.target)

    items: List[Dict[str, object]] = []
    fronted_vm_names: set = set()
    for lb in graph.nodes_of(NodeKind.LOAD_BALANCER):
        fronts = graph.out_edges(lb.id, EdgeKind.FRONTS)
        member_names = {graph.node(e.target).name for e in fronts if graph.node(e.target) is not None}
        fronted_vm_names |= member_names
        items.append({
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": _slug(lb.name), "namespace": namespace},
            "spec": {
                "selector": {
                    "iactranslate.io/tier": lb.attributes["tier"],
                    "iactranslate.io/environment": lb.attributes["environment"],
                },
                "type": "LoadBalancer" if lb.attributes["internet_facing"] else "ClusterIP",
                "ports": [
                    {
                        "name": f"port-{listener['listener_port']}",
                        "port": listener["listener_port"],
                        "targetPort": listener["target_port"],
                        "protocol": "TCP",
                    }
                    for listener in lb.attributes["listeners"]
                ],
            },
        })

    for c in plan.compute:
        if c.vm_name in fronted_vm_names:
            continue
        inst = next(n for n in graph.nodes_of(NodeKind.INSTANCE) if n.name == c.vm_name)
        name = _slug(c.resource_name)
        sg = sg_by_instance.get(inst.id)
        ports = sorted({r["from_port"] for r in sg.attributes["ingress"]}) if sg else []
        if not ports:
            continue
        is_public = inst.attributes["subnet_tier"] == "public"
        exposed = [p for p in ports if p in _PUBLIC_SERVICE_PORTS] if is_public else ports
        if not exposed:
            exposed = ports
        items.append({
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": namespace},
            "spec": {
                "selector": {"iactranslate.io/vm": name},
                "type": "LoadBalancer" if is_public else "ClusterIP",
                "ports": [
                    {"name": f"port-{p}", "port": p, "targetPort": p, "protocol": "TCP"}
                    for p in exposed
                ],
            },
        })
    return {"apiVersion": "v1", "kind": "List", "items": items}


def _readme(plan: MigrationPlan, namespace: str) -> str:
    return (
        f"# {plan.project_name} — Kubernetes (KubeVirt)\n\n"
        "Generated by IaCTranslate, rendered from the Infrastructure Graph "
        "(see docs/adr/0010-infrastructure-graph.md and 0017). VMs become "
        "`kubevirt.io/v1 VirtualMachine` objects, not plain Deployments — the "
        "source inventory describes VMs (an OS image, disk sizes, a CPU/memory "
        "spec), not containers, so this is the honest translation rather than "
        "an invented one. Security-group ingress rules become `NetworkPolicy` "
        "objects; the plan's project becomes one `Namespace`.\n\n"
        "## Prerequisites\n\n"
        "This cluster must have [KubeVirt](https://kubevirt.io) and the "
        "Containerized Data Importer (CDI) installed.\n\n"
        "**You must supply the OS disk image yourself.** Unlike AWS/Azure "
        "Marketplace images, there is no Kubernetes-native catalog this "
        "renderer can resolve an image from. Each `VirtualMachine`'s "
        "`dataVolumeTemplates` ships with a placeholder blank source "
        "(`source: {blank: {}}`) sized correctly — replace it with a real "
        "`source` (an HTTP/registry/PVC-clone import via CDI) before "
        "deploying, or the VM will boot with an empty disk.\n\n"
        "## Deploy\n\n"
        "```bash\n"
        "kubectl apply -f namespace.json\n"
        "kubectl apply -f networkpolicies.json\n"
        "kubectl apply -f virtualmachines.json   # fix dataVolumeTemplates.source first\n"
        "kubectl apply -f services.json\n"
        "```\n\n"
        f"Namespace: `{namespace}`.\n\n"
        "**Note:** this output is not applied/validated against a real cluster "
        "in this environment (no `kubectl`/KubeVirt here) the way the "
        "Terraform output is proven with `tofu validate`. Run `kubectl apply "
        "--dry-run=server` before deploying for real.\n"
    )


def build_kubernetes_files(plan: MigrationPlan, target: Target) -> Dict[str, str]:
    """Render the plan as KubeVirt/Kubernetes manifests, walking the graph."""
    graph = build_graph(plan)
    namespace = _slug(plan.project_name)
    return {
        "namespace.json": json.dumps(_namespace(plan), indent=2) + "\n",
        "networkpolicies.json": json.dumps(_network_policies(graph, namespace), indent=2) + "\n",
        "virtualmachines.json": json.dumps(_virtual_machines(graph, plan, namespace), indent=2) + "\n",
        "services.json": json.dumps(_services(graph, plan, namespace), indent=2) + "\n",
        "README.md": _readme(plan, namespace),
    }
