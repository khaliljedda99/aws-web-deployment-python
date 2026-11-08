"""Discover the default VPC and configure deployment network access.

This demo expects default-VPC public subnets and creates separate security
groups for the EC2 host and the internet-facing load balancer.
"""

def get_default_vpc(ec2):
    "Return the default VPC ID or raise an explanatory error."
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise RuntimeError("No default VPC found. This demo expects a default VPC with public subnets.")
    return vpcs[0]["VpcId"]


def get_public_subnets(ec2, vpc_id):
    "Return one default public subnet per AZ, requiring two for the ALB."
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    # In a default VPC, the default subnet in each AZ has a route to the Internet Gateway.
    default_subnets = [s for s in subnets if s.get("DefaultForAz") and s.get("MapPublicIpOnLaunch")]
    by_az = {}
    for subnet in sorted(default_subnets, key=lambda s: s["AvailabilityZone"]):
        by_az.setdefault(subnet["AvailabilityZone"], subnet["SubnetId"])
    selected = list(by_az.values())
    if len(selected) < 2:
        raise RuntimeError("ALB requires public subnets in at least two Availability Zones.")
    return selected


def create_security_groups(ec2, vpc_id, name, my_ip_cidr, resources=None):
    "Create or reuse the EC2 and ALB security groups and add the required ingress rules."
    def get_or_create(group_name, description):
        """Find a matching security group in this VPC or create it."""
        groups = ec2.describe_security_groups(Filters=[
            {"Name": "group-name", "Values": [group_name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ])["SecurityGroups"]
        if groups:
            return groups[0]["GroupId"]
        group_id = ec2.create_security_group(
            GroupName=group_name, Description=description, VpcId=vpc_id
        )["GroupId"]
        if resources is not None:
            resources["security_groups"].append(group_id)
        return group_id

    instance_sg = get_or_create(f"{name}-EC2-SG", f"{name} EC2 ingress")
    alb_sg = get_or_create(f"{name}-ALB-SG", f"{name} ALB HTTP ingress")
    ec2.create_tags(Resources=[instance_sg, alb_sg], Tags=[{"Key": "Name", "Value": name}])
    # Allow SSH from the operator, and allow HTTP to EC2 only from the ALB.
    ingress_rules = [
        (instance_sg, {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": my_ip_cidr, "Description": "SSH from operator"}]}),
        (instance_sg, {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "UserIdGroupPairs": [{"GroupId": alb_sg, "Description": "HTTP from ALB"}]}),
        (alb_sg, {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTP"}]}),
    ]
    for group_id, permission in ingress_rules:
        current = ec2.describe_security_groups(GroupIds=[group_id])["SecurityGroups"][0].get("IpPermissions", [])
        # Remove legacy public HTTP ingress from EC2 so existing deployments
        # also become reachable only through the ALB.
        if group_id == instance_sg:
            for public_cidr in ("0.0.0.0/0", "::/0"):
                public_range_key, public_cidr_key = (
                    ("IpRanges", "CidrIp") if ":" not in public_cidr
                    else ("Ipv6Ranges", "CidrIpv6")
                )
                has_public_http = any(
                    rule.get("IpProtocol") == "tcp"
                    and rule.get("FromPort") == 80
                    and rule.get("ToPort") == 80
                    and any(source.get(public_cidr_key) == public_cidr
                            for source in rule.get(public_range_key, []))
                    for rule in current
                )
                if has_public_http:
                    ec2.revoke_security_group_ingress(
                        GroupId=group_id,
                        IpPermissions=[{
                            "IpProtocol": "tcp",
                            "FromPort": 80,
                            "ToPort": 80,
                            public_range_key: [{public_cidr_key: public_cidr}],
                        }],
                    )
            current = ec2.describe_security_groups(GroupIds=[group_id])["SecurityGroups"][0].get("IpPermissions", [])

        source_field = "IpRanges" if "IpRanges" in permission else "UserIdGroupPairs"
        source_key = "CidrIp" if source_field == "IpRanges" else "GroupId"
        expected_source = permission[source_field][0][source_key]
        already_present = any(
            rule.get("IpProtocol") == permission["IpProtocol"]
            and rule.get("FromPort") == permission["FromPort"]
            and rule.get("ToPort") == permission["ToPort"]
            and any(source.get(source_key) == expected_source for source in rule.get(source_field, []))
            for rule in current
        )
        if not already_present:
            ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=[permission])
            if resources is not None:
                resources["added_ingress"].append({
                    "GroupId": group_id,
                    "Permission": permission,
                })
    return instance_sg, alb_sg
