def get_default_vpc(ec2):
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise RuntimeError("No default VPC found. This demo expects a default VPC with public subnets.")
    return vpcs[0]["VpcId"]


def get_public_subnets(ec2, vpc_id):
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


def create_security_groups(ec2, vpc_id, name, my_ip_cidr):
    def get_or_create(group_name, description):
        groups = ec2.describe_security_groups(Filters=[
            {"Name": "group-name", "Values": [group_name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ])["SecurityGroups"]
        if groups:
            return groups[0]["GroupId"]
        return ec2.create_security_group(GroupName=group_name, Description=description, VpcId=vpc_id)["GroupId"]

    instance_sg = get_or_create(f"{name}-EC2-SG", f"{name} EC2 ingress")
    alb_sg = get_or_create(f"{name}-ALB-SG", f"{name} ALB HTTP ingress")
    ec2.create_tags(Resources=[instance_sg, alb_sg], Tags=[{"Key": "Name", "Value": name}])
    ingress_rules = [
        (instance_sg, {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": my_ip_cidr, "Description": "SSH from operator"}]}),
        (instance_sg, {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTP"}]}),
        (alb_sg, {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTP"}]}),
    ]
    for group_id, permission in ingress_rules:
        current = ec2.describe_security_groups(GroupIds=[group_id])["SecurityGroups"][0].get("IpPermissions", [])
        already_present = any(p.get("IpProtocol") == permission["IpProtocol"] and p.get("FromPort") == permission["FromPort"] and p.get("ToPort") == permission["ToPort"] and any(r.get("CidrIp") == permission["IpRanges"][0]["CidrIp"] for r in p.get("IpRanges", [])) for p in current)
        if not already_present:
            ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=[permission])
    # Restrict origin HTTP to the ALB; the explicit public port 80 rule above is retained per request.
    return instance_sg, alb_sg
