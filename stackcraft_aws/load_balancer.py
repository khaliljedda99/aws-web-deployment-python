def create_alb(elbv2, name, vpc_id, subnets, alb_security_group_id, instance_id):
    target_name = f"{name}-Target-Group"
    groups = [group for group in elbv2.describe_target_groups()["TargetGroups"] if group["TargetGroupName"] == target_name]
    if groups:
        target = groups[0]["TargetGroupArn"]
    else:
        target = elbv2.create_target_group(Name=target_name, Protocol="HTTP", Port=80,
            VpcId=vpc_id, TargetType="instance", HealthCheckProtocol="HTTP", HealthCheckPath="/",
            Matcher={"HttpCode": "200-399"})["TargetGroups"][0]["TargetGroupArn"]
    elbv2.register_targets(TargetGroupArn=target, Targets=[{"Id": instance_id, "Port": 80}])

    alb_name = f"{name}-ALB"
    existing = [item for item in elbv2.describe_load_balancers()["LoadBalancers"] if item["LoadBalancerName"] == alb_name]
    if existing:
        load_balancer = existing[0]
        alb_arn = load_balancer["LoadBalancerArn"]
        dns_name = load_balancer["DNSName"]
    else:
        load_balancer = elbv2.create_load_balancer(Name=alb_name, Subnets=subnets,
            SecurityGroups=[alb_security_group_id], Scheme="internet-facing", Type="application", IpAddressType="ipv4")["LoadBalancers"][0]
        alb_arn = load_balancer["LoadBalancerArn"]
        dns_name = load_balancer["DNSName"]
        elbv2.add_tags(ResourceArns=[alb_arn], Tags=[{"Key": "Name", "Value": alb_name}])

    listeners = elbv2.describe_listeners(LoadBalancerArn=alb_arn)["Listeners"]
    if not any(listener["Port"] == 80 and listener["Protocol"] == "HTTP" for listener in listeners):
        elbv2.create_listener(LoadBalancerArn=alb_arn, Protocol="HTTP", Port=80,
            DefaultActions=[{"Type": "forward", "TargetGroupArn": target}])
    return dns_name
