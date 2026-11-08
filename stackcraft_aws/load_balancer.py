"""Create the ALB, HTTP listener, and target group used by the Auto Scaling Group."""


def create_alb(elbv2, name, vpc_id, subnets, alb_security_group_id):
    """Create or reuse the ALB path and return its DNS name and target group ARN."""
    target_name = f"{name}-Target-Group"
    groups = [g for g in elbv2.describe_target_groups()["TargetGroups"] if g["TargetGroupName"] == target_name]
    if groups:
        target_group_arn = groups[0]["TargetGroupArn"]
    else:
        target_group_arn = elbv2.create_target_group(
            Name=target_name, Protocol="HTTP", Port=80, VpcId=vpc_id,
            TargetType="instance", HealthCheckProtocol="HTTP", HealthCheckPath="/",
            Matcher={"HttpCode": "200-399"})["TargetGroups"][0]["TargetGroupArn"]

    alb_name = f"{name}-ALB"
    existing = [x for x in elbv2.describe_load_balancers()["LoadBalancers"] if x["LoadBalancerName"] == alb_name]
    if existing:
        load_balancer = existing[0]
        alb_arn, dns_name = load_balancer["LoadBalancerArn"], load_balancer["DNSName"]
    else:
        load_balancer = elbv2.create_load_balancer(
            Name=alb_name, Subnets=subnets, SecurityGroups=[alb_security_group_id],
            Scheme="internet-facing", Type="application", IpAddressType="ipv4")["LoadBalancers"][0]
        alb_arn, dns_name = load_balancer["LoadBalancerArn"], load_balancer["DNSName"]
        elbv2.add_tags(ResourceArns=[alb_arn], Tags=[{"Key": "Name", "Value": alb_name}])

    # The ASG registers its instances with this target group after launch.
    listeners = elbv2.describe_listeners(LoadBalancerArn=alb_arn)["Listeners"]
    if not any(x["Port"] == 80 and x["Protocol"] == "HTTP" for x in listeners):
        elbv2.create_listener(LoadBalancerArn=alb_arn, Protocol="HTTP", Port=80,
            DefaultActions=[{"Type": "forward", "TargetGroupArn": target_group_arn}])
    return dns_name, target_group_arn
