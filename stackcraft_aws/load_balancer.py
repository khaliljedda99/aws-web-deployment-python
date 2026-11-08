"""Create the ALB, HTTP listener, and target group used by the Auto Scaling Group."""

import time


def create_alb(elbv2, name, vpc_id, subnets, alb_security_group_id, resources=None):
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
        if resources is not None:
            resources["target_groups"].append(target_group_arn)

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
        if resources is not None:
            resources["load_balancers"].append(alb_arn)
        elbv2.add_tags(ResourceArns=[alb_arn], Tags=[{"Key": "Name", "Value": alb_name}])

    # The ASG registers its instances with this target group after launch.
    listeners = elbv2.describe_listeners(LoadBalancerArn=alb_arn)["Listeners"]
    if not any(x["Port"] == 80 and x["Protocol"] == "HTTP" for x in listeners):
        listener = elbv2.create_listener(LoadBalancerArn=alb_arn, Protocol="HTTP", Port=80,
            DefaultActions=[{"Type": "forward", "TargetGroupArn": target_group_arn}])
        if resources is not None:
            resources["listeners"].append(listener["Listeners"][0]["ListenerArn"])
    return dns_name, target_group_arn


def wait_for_healthy_targets(elbv2, target_group_arn, expected_count=2,
                             timeout_seconds=600, poll_interval=15):
    """Wait until the target group has enough healthy targets or explain the failures."""
    deadline = time.monotonic() + timeout_seconds
    last_report = None

    while True:
        targets = elbv2.describe_target_health(
            TargetGroupArn=target_group_arn
        )["TargetHealthDescriptions"]
        healthy_count = sum(
            target["TargetHealth"]["State"] == "healthy" for target in targets
        )
        if healthy_count >= expected_count:
            return

        target_summary = "; ".join(
            f"{target['Target']['Id']}: {target['TargetHealth']['State']}"
            + (f" ({target['TargetHealth'].get('Reason')}: "
               f"{target['TargetHealth'].get('Description')})"
               if target["TargetHealth"].get("Reason") else "")
            for target in targets
        ) or "no targets registered yet"
        report = (healthy_count, target_summary)
        if report != last_report:
            print(f"Waiting for healthy targets ({healthy_count}/{expected_count}). {target_summary}")
            last_report = report

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Timed out waiting for {expected_count} healthy targets. {target_summary}"
            )
        time.sleep(min(poll_interval, remaining))
