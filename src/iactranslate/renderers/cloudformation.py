"""CloudFormation renderer — the first renderer to consume the Infrastructure Graph
instead of the MigrationPlan directly (see ADR 0010).

AWS-only: CloudFormation has no cross-cloud equivalent. Where Terraform's
`data "aws_ami"` resolves an AMI by name-filter at plan time, CloudFormation has
no such lookup; instead we use AWS-published SSM public parameters
(`{{resolve:ssm:...}}`) for the OS images that have one (Amazon Linux, Windows,
Ubuntu), and fall back to a plain `AWS::EC2::Image::Id` template Parameter
(no default — the operator supplies an AMI id at deploy time) for OSes AWS
doesn't publish an SSM alias for (RHEL, SLES, CentOS). Both are real, common
CloudFormation patterns; neither is fabricated.

Deterministic string generation — no AI. Output is a single JSON template
(`template.json`), validated in tests with `json.loads` + `cfn-lint`-shaped
structural assertions (Type/Properties on every resource).
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ..graph import EdgeKind, InfrastructureGraph, NodeKind, build_graph
from ..models import MigrationPlan, stack_name
from ..targets.aws.mapping import root_device
from ..targets.base import Target

# image_key -> AWS-published SSM public parameter holding the latest AMI id.
# Resolved by CloudFormation itself at stack create/update time via a dynamic
# reference; no template Parameter required.
_SSM_IMAGE_PARAMS: Dict[str, str] = {
    "amazon-linux-2": "/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
    "windows-2022": "/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base",
    "windows-2019": "/aws/service/ami-windows-latest/Windows_Server-2019-English-Full-Base",
    "windows-2016": "/aws/service/ami-windows-latest/Windows_Server-2016-English-Full-Base",
    "ubuntu-22.04": (
        "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
    ),
}


class RendererNotSupportedError(ValueError):
    pass


def _cid(prefix: str, name: str) -> str:
    """A valid CloudFormation logical id: alphanumeric only, starts with a letter."""
    parts = re.sub(r"[^a-zA-Z0-9]+", " ", name).split()
    camel = "".join(p[:1].upper() + p[1:] for p in parts) or "Resource"
    return prefix + camel


#: The parameter that turns on HTTPS listeners, and the condition it sets.
CERT_PARAM = "AcmCertificateArn"
HAS_CERT = "HasCertificate"


#: The README note on what deleting a stack leaves behind.
KEPT_DISKS_NOTE = (
    "\n**Data disks outlive the stack.** They are kept when their instance ends, so "
    "data is never lost with a server, and deleting the stack leaves them, still "
    "billed. To remove them too, list them before deleting the stack, then delete "
    "them after:\n\n"
    "```bash\n"
    "aws ec2 describe-volumes --filters Name=attachment.instance-id,Values=<instance ids> "
    "--query 'Volumes[?Attachments[0].DeleteOnTermination==`false`].VolumeId'\n"
    "```\n"
)


def instance_cids(names: List[str]) -> Dict[str, str]:
    """A distinct logical id per machine, in plan order.

    `_cid` drops punctuation, so "uat cache 01" and "uat-cache-01" both became
    `InstanceUatCache01`: CloudFormation silently kept one machine of the two and
    CDK refused to build (ADR 0093). The first keeps the plain id, so a stack
    that already holds it isn't renamed; later ones get a number.
    """
    out: Dict[str, str] = {}
    used = set()
    for name in names:
        base = candidate = _cid("Instance", name)
        n = 2
        while candidate in used:
            candidate, n = f"{base}N{n}", n + 1
        used.add(candidate)
        out[name] = candidate
    return out


def _ami_dynamic_ref(image_key: str) -> Optional[str]:
    path = _SSM_IMAGE_PARAMS.get(image_key)
    return f"{{{{resolve:ssm:{path}}}}}" if path else None


def _ami_parameter_name(image_key: str) -> str:
    return _cid("AmiId", image_key)


def _build_template(graph: InfrastructureGraph, plan: MigrationPlan) -> Dict[str, object]:
    resources: Dict[str, object] = {}
    parameters: Dict[str, object] = {}
    conditions: Dict[str, object] = {}
    outputs: Dict[str, object] = {}

    vpc_node = graph.nodes_of(NodeKind.VPC)[0]
    vpc_id = _cid("Vpc", vpc_node.name)
    resources[vpc_id] = {
        "Type": "AWS::EC2::VPC",
        "Properties": {
            "CidrBlock": vpc_node.attributes["cidr"],
            "EnableDnsHostnames": True,
            "EnableDnsSupport": True,
            "Tags": [{"Key": "Name", "Value": vpc_node.name}],
        },
    }

    igw_id = None
    if vpc_node.attributes.get("internet_gateway"):
        igw_id = _cid("Igw", vpc_node.name)
        attach_id = _cid("VpcGatewayAttachment", vpc_node.name)
        resources[igw_id] = {
            "Type": "AWS::EC2::InternetGateway",
            "Properties": {"Tags": [{"Key": "Name", "Value": f"{vpc_node.name}-igw"}]},
        }
        resources[attach_id] = {
            "Type": "AWS::EC2::VPCGatewayAttachment",
            "Properties": {"VpcId": {"Ref": vpc_id}, "InternetGatewayId": {"Ref": igw_id}},
        }

    public_route_table_id = None
    subnet_id_by_node: Dict[str, str] = {}
    public_subnet_ids: List[str] = []
    private_subnet_ids: List[str] = []

    for subnet in graph.nodes_of(NodeKind.SUBNET):
        sid = _cid("Subnet", subnet.name)
        subnet_id_by_node[subnet.id] = sid
        is_public = subnet.attributes["tier"] == "public"
        resources[sid] = {
            "Type": "AWS::EC2::Subnet",
            "Properties": {
                "VpcId": {"Ref": vpc_id},
                "CidrBlock": subnet.attributes["cidr"],
                "AvailabilityZone": {
                    "Fn::Select": [
                        subnet.attributes["availability_zone_index"],
                        {"Fn::GetAZs": ""},
                    ]
                },
                # Each machine says whether it gets a public address (ADR 0095).
                "MapPublicIpOnLaunch": False,
                "Tags": [
                    {"Key": "Name", "Value": subnet.name},
                    {"Key": "Tier", "Value": subnet.attributes["tier"]},
                ],
            },
        }
        (public_subnet_ids if is_public else private_subnet_ids).append(sid)

    if igw_id and public_subnet_ids:
        public_route_table_id = _cid("RouteTable", "public")
        resources[public_route_table_id] = {
            "Type": "AWS::EC2::RouteTable",
            "Properties": {"VpcId": {"Ref": vpc_id}},
        }
        resources[_cid("Route", "public-internet")] = {
            "Type": "AWS::EC2::Route",
            "DependsOn": _cid("VpcGatewayAttachment", vpc_node.name),
            "Properties": {
                "RouteTableId": {"Ref": public_route_table_id},
                "DestinationCidrBlock": "0.0.0.0/0",
                "GatewayId": {"Ref": igw_id},
            },
        }
        for sid in public_subnet_ids:
            resources[_cid("RouteTableAssoc", sid)] = {
                "Type": "AWS::EC2::SubnetRouteTableAssociation",
                "Properties": {"SubnetId": {"Ref": sid}, "RouteTableId": {"Ref": public_route_table_id}},
            }

    if vpc_node.attributes.get("nat_gateway") and public_subnet_ids and private_subnet_ids:
        eip_id = _cid("NatEip", "nat")
        nat_id = _cid("NatGateway", "nat")
        resources[eip_id] = {"Type": "AWS::EC2::EIP", "Properties": {"Domain": "vpc"}}
        resources[nat_id] = {
            "Type": "AWS::EC2::NatGateway",
            "Properties": {
                "AllocationId": {"Fn::GetAtt": [eip_id, "AllocationId"]},
                "SubnetId": {"Ref": public_subnet_ids[0]},
            },
        }
        private_route_table_id = _cid("RouteTable", "private")
        resources[private_route_table_id] = {
            "Type": "AWS::EC2::RouteTable",
            "Properties": {"VpcId": {"Ref": vpc_id}},
        }
        resources[_cid("Route", "private-nat")] = {
            "Type": "AWS::EC2::Route",
            "Properties": {
                "RouteTableId": {"Ref": private_route_table_id},
                "DestinationCidrBlock": "0.0.0.0/0",
                "NatGatewayId": {"Ref": nat_id},
            },
        }
        for sid in private_subnet_ids:
            resources[_cid("RouteTableAssoc", sid)] = {
                "Type": "AWS::EC2::SubnetRouteTableAssociation",
                "Properties": {"SubnetId": {"Ref": sid}, "RouteTableId": {"Ref": private_route_table_id}},
            }

    sg_id_by_node: Dict[str, str] = {}
    for sg in graph.nodes_of(NodeKind.SECURITY_GROUP):
        gid = _cid("Sg", sg.name)
        sg_id_by_node[sg.id] = gid
        ingress = [
            {
                "Description": rule["description"],
                "IpProtocol": rule["protocol"],
                "FromPort": rule["from_port"],
                "ToPort": rule["to_port"],
                "CidrIp": cidr,
            }
            for rule in sg.attributes["ingress"]
            for cidr in rule["cidr_blocks"]
        ]
        resources[gid] = {
            "Type": "AWS::EC2::SecurityGroup",
            "Properties": {
                "GroupDescription": sg.attributes.get("description") or sg.name,
                "VpcId": {"Ref": vpc_id},
                "SecurityGroupIngress": ingress,
                "SecurityGroupEgress": [
                    {"IpProtocol": "-1", "CidrIp": "0.0.0.0/0", "Description": "All outbound"}
                ],
                "Tags": [{"Key": "Name", "Value": sg.name}],
            },
        }

    image_keys = sorted({n.attributes["image_key"] for n in graph.nodes_of(NodeKind.INSTANCE)})
    for key in image_keys:
        if _ami_dynamic_ref(key) is None:
            pname = _ami_parameter_name(key)
            parameters[pname] = {
                "Type": "AWS::EC2::Image::Id",
                "Description": f"AMI id for image '{key}' (no public SSM alias for this OS)",
            }

    instance_ids: List[str] = []
    instance_id_by_name: Dict[str, str] = {}
    iid_of = instance_cids([n.name for n in graph.nodes_of(NodeKind.INSTANCE)])
    for inst in graph.nodes_of(NodeKind.INSTANCE):
        iid = iid_of[inst.name]
        instance_ids.append(iid)
        instance_id_by_name[inst.name] = iid
        key = inst.attributes["image_key"]
        dyn = _ami_dynamic_ref(key)
        image_id = dyn if dyn is not None else {"Ref": _ami_parameter_name(key)}

        placed_in = graph.out_edges(inst.id, EdgeKind.PLACED_IN)
        secured_by = graph.out_edges(inst.id, EdgeKind.SECURED_BY)
        subnet_ref = subnet_id_by_node[placed_in[0].target] if placed_in else None
        sg_refs = [sg_id_by_node[e.target] for e in secured_by if e.target in sg_id_by_node]

        # Encrypted, as Terraform's are, and data disks outlive their instance:
        # a volume made at launch is otherwise deleted when it ends (ADR 0093).
        block_devices = [
            {
                "DeviceName": root_device(inst.attributes["image_key"]),
                "Ebs": {"VolumeSize": inst.attributes["root_volume_gib"], "VolumeType": "gp3",
                        "Encrypted": True},
            }
        ]
        for i, size in enumerate(inst.attributes.get("extra_volumes_gib") or []):
            block_devices.append({
                "DeviceName": f"/dev/xvd{chr(ord('f') + i)}",
                "Ebs": {"VolumeSize": size, "VolumeType": "gp3", "Encrypted": True,
                        "DeleteOnTermination": False},
            })

        resources[iid] = {
            "Type": "AWS::EC2::Instance",
            # A newer image can make a stack update replace the server. Keep the
            # old one and its disks when it does (ADR 0090).
            "UpdateReplacePolicy": "Retain",
            "Properties": {
                "InstanceType": inst.attributes["instance_type"],
                "ImageId": image_id,
                "SubnetId": {"Ref": subnet_ref} if subnet_ref else None,
                "SecurityGroupIds": [{"Ref": r} for r in sg_refs],
                "BlockDeviceMappings": block_devices,
                "Tags": [
                    {"Key": "Name", "Value": inst.name},
                    {"Key": "Tier", "Value": inst.attributes["tier"]},
                    {"Key": "Environment", "Value": inst.attributes["environment"]},
                ],
            },
        }
        if inst.attributes["public_ip"] and subnet_ref:
            # Only a machine with no load balancer in front gets its own public address (ADR 0095).
            props = resources[iid]["Properties"]
            props["NetworkInterfaces"] = [{
                "DeviceIndex": "0", "AssociatePublicIpAddress": True,
                "SubnetId": {"Ref": subnet_ref}, "GroupSet": props.pop("SecurityGroupIds"),
            }]
            props.pop("SubnetId")
        resources[iid]["Properties"] = {
            k: v for k, v in resources[iid]["Properties"].items() if v is not None
        }

    for lb in graph.nodes_of(NodeKind.LOAD_BALANCER):
        lb_id = _cid("Lb", lb.name)
        lb_subnet_refs = [
            {"Ref": subnet_id_by_node[e.target]} for e in graph.out_edges(lb.id, EdgeKind.PLACED_IN)
        ]
        lb_sg_refs = [sg_id_by_node[e.target] for e in graph.out_edges(lb.id, EdgeKind.SECURED_BY)]
        resources[lb_id] = {
            "Type": "AWS::ElasticLoadBalancingV2::LoadBalancer",
            "Properties": {
                "Type": "application",
                "Scheme": "internet-facing" if lb.attributes["internet_facing"] else "internal",
                "Subnets": lb_subnet_refs,
                "SecurityGroups": [{"Ref": r} for r in lb_sg_refs],
                "Tags": [{"Key": "Name", "Value": lb.name}],
            },
        }
        target_ids = [
            instance_id_by_name[graph.node(e.target).name]
            for e in graph.out_edges(lb.id, EdgeKind.FRONTS)
            if graph.node(e.target) is not None
        ]
        for listener in lb.attributes["listeners"]:
            tg_id = _cid("TargetGroup", f"{lb.name}-{listener['listener_port']}")
            resources[tg_id] = {
                "Type": "AWS::ElasticLoadBalancingV2::TargetGroup",
                "Properties": {
                    "Port": listener["target_port"],
                    "Protocol": listener["protocol"],
                    "VpcId": {"Ref": vpc_id},
                    "TargetType": "instance",
                    "HealthCheckPath": lb.attributes["health_check_path"],
                    "Targets": [{"Id": {"Ref": tid}, "Port": listener["target_port"]} for tid in target_ids],
                },
            }
            listener_id = _cid("Listener", f"{lb.name}-{listener['listener_port']}")
            resources[listener_id] = {
                "Type": "AWS::ElasticLoadBalancingV2::Listener",
                "Properties": {
                    "LoadBalancerArn": {"Ref": lb_id},
                    "Port": listener["listener_port"],
                    "Protocol": listener["protocol"],
                    "DefaultActions": [{"Type": "forward", "TargetGroupArn": {"Ref": tg_id}}],
                },
            }
            if listener["protocol"] == "HTTPS":
                # Only with a certificate: AWS refuses an HTTPS listener without
                # one, and none can be known when this is generated (ADR 0094).
                parameters[CERT_PARAM] = {
                    "Type": "String", "Default": "",
                    "Description": "ACM certificate ARN. Empty: no HTTPS listeners, HTTP only.",
                }
                conditions[HAS_CERT] = {"Fn::Not": [{"Fn::Equals": [{"Ref": CERT_PARAM}, ""]}]}
                resources[listener_id]["Condition"] = HAS_CERT
                resources[listener_id]["Properties"].update({
                    "SslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
                    "Certificates": [{"CertificateArn": {"Ref": CERT_PARAM}}],
                })
        outputs[f"{lb_id}DnsName"] = {"Value": {"Fn::GetAtt": [lb_id, "DNSName"]}}

    outputs["VpcId"] = {"Value": {"Ref": vpc_id}}
    for iid in instance_ids:
        outputs[f"{iid}Id"] = {"Value": {"Ref": iid}}

    template: Dict[str, object] = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": (
            f"IaCTranslate-generated migration of {plan.project_name} to AWS "
            "(rendered from the Infrastructure Graph)."
        ),
    }
    if parameters:
        template["Parameters"] = parameters
    if conditions:
        template["Conditions"] = conditions
    template["Resources"] = resources
    template["Outputs"] = outputs
    return template


def _readme(plan: MigrationPlan, image_keys: List[str]) -> str:
    needs_param = [k for k in image_keys if _ami_dynamic_ref(k) is None]
    param_note = (
        (
            "\nSome images have no public AWS SSM alias, so their AMI ids are template "
            f"parameters you must supply: {', '.join(_ami_parameter_name(k) for k in needs_param)}.\n"
        )
        if needs_param
        else ""
    )
    https_lbs = [
        lb.name for lb in plan.network.load_balancers
        if any(listener.protocol == "HTTPS" for listener in lb.listeners)
    ]
    https_note = (
        "\n**HTTPS needs a certificate.** "
        f"{', '.join(https_lbs)} {'has' if len(https_lbs) == 1 else 'have'} an HTTPS listener, "
        f"created only when the `{CERT_PARAM}` parameter holds an ACM certificate ARN "
        "(none is knowable at generation time). Left empty, the load balancers serve HTTP only.\n"
        if https_lbs else ""
    )
    return (
        f"# {plan.project_name} — CloudFormation (AWS)\n\n"
        "Generated by IaCTranslate, rendered from the Infrastructure Graph "
        "(see docs/adr/0010-infrastructure-graph.md) rather than the plan directly — "
        "the same topology the architecture diagram renders from.\n\n"
        "## Deploy\n\n"
        "```bash\n"
        f"aws cloudformation deploy --template-file template.json "
        f"--stack-name {stack_name(plan.project_name)} --capabilities CAPABILITY_NAMED_IAM"
        + (" --parameter-overrides " + " ".join(f"{_ami_parameter_name(k)}=<ami-id>" for k in needs_param)
           if needs_param else "")
        + "\n```\n"
        + param_note
        + https_note
        + KEPT_DISKS_NOTE
    )


def build_cloudformation_files(plan: MigrationPlan, target: Target) -> Dict[str, str]:
    """Render the plan as a CloudFormation template, walking the Infrastructure Graph."""
    if target.name != "aws":
        raise RendererNotSupportedError(
            f"the CloudFormation renderer only supports 'aws' (got '{target.name}')"
        )
    graph = build_graph(plan)
    template = _build_template(graph, plan)
    image_keys = sorted({n.attributes["image_key"] for n in graph.nodes_of(NodeKind.INSTANCE)})
    return {
        "template.json": json.dumps(template, indent=2) + "\n",
        "README.md": _readme(plan, image_keys),
    }
