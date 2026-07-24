"""Infrastructure Graph — a renderer-neutral intermediate representation.

`MigrationPlan` is the canonical *planning* artifact; the graph is the canonical
*topology* artifact derived from it: typed nodes (VPC, subnets, security groups,
instances) and edges (containment, placement, security). It is deterministic and
carries no cloud syntax.

Why a separate IR: it decouples planning from code generation. The architecture
diagram already renders **from this graph** (its natural consumer), and it is the
intended seam for future renderers (CloudFormation, Bicep, CDK, Kubernetes) that
would rather walk a topology than re-derive one from the plan. Terraform and
Pulumi still render from the plan today and migrate to the graph incrementally —
see ADR 0010.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .models import MigrationPlan


class NodeKind(str, Enum):
    VPC = "vpc"
    SUBNET = "subnet"
    SECURITY_GROUP = "security_group"
    INSTANCE = "instance"


class EdgeKind(str, Enum):
    CONTAINS = "contains"        # vpc → subnet
    PLACED_IN = "placed_in"      # instance → subnet
    SECURED_BY = "secured_by"    # instance → security_group


class GraphNode(BaseModel):
    id: str
    kind: NodeKind
    name: str
    attributes: Dict[str, object] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: EdgeKind


class InfrastructureGraph(BaseModel):
    schema_version: int = 1
    project: str
    target: str
    region: str
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)

    def nodes_of(self, kind: NodeKind) -> List[GraphNode]:
        return [n for n in self.nodes if n.kind == kind]

    def node(self, node_id: str) -> Optional[GraphNode]:
        return next((n for n in self.nodes if n.id == node_id), None)

    def out_edges(self, source_id: str, kind: Optional[EdgeKind] = None) -> List[GraphEdge]:
        return [e for e in self.edges if e.source == source_id and (kind is None or e.kind == kind)]


def _vpc_id() -> str:
    return "vpc"


def _subnet_id(resource_name: str) -> str:
    return f"subnet:{resource_name}"


def _sg_id(resource_name: str) -> str:
    return f"sg:{resource_name}"


def _instance_id(resource_name: str) -> str:
    return f"instance:{resource_name}"


def build_graph(plan: MigrationPlan) -> InfrastructureGraph:
    """Derive the topology graph from a validated MigrationPlan."""
    net = plan.network
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []

    # VPC (root).
    nodes.append(GraphNode(
        id=_vpc_id(), kind=NodeKind.VPC, name=f"{plan.project_name}-vpc",
        attributes={
            "cidr": net.vpc_cidr,
            "internet_gateway": net.internet_gateway,
            "nat_gateway": net.nat_gateway,
        },
    ))

    # Subnets, contained by the VPC.
    first_subnet_of_tier: Dict[str, str] = {}
    for sn in net.subnets:
        sid = _subnet_id(sn.resource_name)
        nodes.append(GraphNode(
            id=sid, kind=NodeKind.SUBNET, name=sn.name,
            attributes={"cidr": sn.cidr, "tier": sn.tier.value,
                        "availability_zone_index": sn.availability_zone_index},
        ))
        edges.append(GraphEdge(source=_vpc_id(), target=sid, kind=EdgeKind.CONTAINS))
        first_subnet_of_tier.setdefault(sn.tier.value, sid)

    # Security groups.
    sg_id_by_name: Dict[str, str] = {}
    for sg in net.security_groups:
        sid = _sg_id(sg.resource_name)
        sg_id_by_name[sg.name] = sid
        nodes.append(GraphNode(
            id=sid, kind=NodeKind.SECURITY_GROUP, name=sg.name,
            attributes={
                "description": sg.description,
                "ingress": [
                    {
                        "description": r.description,
                        "protocol": r.protocol,
                        "from_port": r.from_port,
                        "to_port": r.to_port,
                        "cidr_blocks": list(r.cidr_blocks),
                    }
                    for r in sg.ingress
                ],
            },
        ))

    # Instances, placed in a subnet of their tier and secured by their SG.
    any_subnet = _subnet_id(net.subnets[0].resource_name) if net.subnets else None
    for c in plan.compute:
        iid = _instance_id(c.resource_name)
        nodes.append(GraphNode(
            id=iid, kind=NodeKind.INSTANCE, name=c.vm_name,
            attributes={
                "instance_type": c.instance_type,
                "tier": c.tier.value,
                "environment": c.environment.value,
                "subnet_tier": c.subnet_tier.value,
                "vcpu": c.vcpu,
                "memory_gib": c.memory_gib,
                "image_key": c.image_key,
                "root_volume_gib": c.root_volume_gib,
                "extra_volumes_gib": list(c.extra_volumes_gib),
            },
        ))
        subnet_target = first_subnet_of_tier.get(c.subnet_tier.value, any_subnet)
        if subnet_target:
            edges.append(GraphEdge(source=iid, target=subnet_target, kind=EdgeKind.PLACED_IN))
        sg_target = sg_id_by_name.get(c.security_group)
        if sg_target:
            edges.append(GraphEdge(source=iid, target=sg_target, kind=EdgeKind.SECURED_BY))

    return InfrastructureGraph(
        project=plan.project_name, target=plan.target, region=plan.region,
        nodes=nodes, edges=edges,
    )
