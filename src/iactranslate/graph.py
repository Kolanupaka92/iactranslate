"""Infrastructure Graph — a renderer-neutral intermediate representation.

`MigrationPlan` is the canonical *planning* artifact; the graph is the canonical
*topology* artifact derived from it: typed nodes (VPC, subnets, security groups,
instances) and edges (containment, placement, security). It is deterministic and
carries no cloud syntax.

Why a separate IR: it decouples planning from code generation. The architecture
diagram, CloudFormation, Bicep, and CDK render from this graph; Terraform and
Pulumi get their subnet placement from it too (`assign_subnets`, below) so every
renderer agrees on where an instance actually lands — see ADR 0010 and
ADR 0016 (Terraform/Pulumi placement migrated onto the graph).
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .models import MigrationPlan, SubnetTier


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


def assign_subnets(plan: MigrationPlan) -> Dict[str, str]:
    """Map each compute vm_name -> a subnet resource_name, spread round-robin
    across that tier's subnets (e.g. across availability zones) rather than
    collapsing every instance of a tier onto a single subnet.

    This is the one place placement is decided — every renderer (Terraform,
    Pulumi, CloudFormation, Bicep, CDK) and the diagram gets it from here via
    the graph's `placed_in` edges, so they can't disagree with each other.
    """
    public = [s.resource_name for s in plan.network.subnets if s.tier == SubnetTier.PUBLIC]
    private = [s.resource_name for s in plan.network.subnets if s.tier == SubnetTier.PRIVATE]
    counters = {SubnetTier.PUBLIC: 0, SubnetTier.PRIVATE: 0}
    mapping: Dict[str, str] = {}
    for c in plan.compute:
        pool = public if c.subnet_tier == SubnetTier.PUBLIC else private
        if not pool:  # no subnet of that tier; fall back to any subnet
            pool = [s.resource_name for s in plan.network.subnets]
        if not pool:  # no subnets at all (e.g. a subnet-less plan in a unit test)
            continue
        idx = counters[c.subnet_tier] % len(pool)
        counters[c.subnet_tier] += 1
        mapping[c.vm_name] = pool[idx]
    return mapping


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
    for sn in net.subnets:
        sid = _subnet_id(sn.resource_name)
        nodes.append(GraphNode(
            id=sid, kind=NodeKind.SUBNET, name=sn.name,
            attributes={"cidr": sn.cidr, "tier": sn.tier.value,
                        "availability_zone_index": sn.availability_zone_index},
        ))
        edges.append(GraphEdge(source=_vpc_id(), target=sid, kind=EdgeKind.CONTAINS))

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

    # Instances, placed in a subnet of their tier (spread across AZs via
    # assign_subnets) and secured by their SG.
    subnet_of = assign_subnets(plan)
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
        subnet_resource_name = subnet_of.get(c.vm_name)
        if subnet_resource_name:
            edges.append(GraphEdge(
                source=iid, target=_subnet_id(subnet_resource_name), kind=EdgeKind.PLACED_IN
            ))
        sg_target = sg_id_by_name.get(c.security_group)
        if sg_target:
            edges.append(GraphEdge(source=iid, target=sg_target, kind=EdgeKind.SECURED_BY))

    return InfrastructureGraph(
        project=plan.project_name, target=plan.target, region=plan.region,
        nodes=nodes, edges=edges,
    )
