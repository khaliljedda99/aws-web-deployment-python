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
    http_listener = next(
        (
            listener
            for listener in listeners
            if listener["Port"] == 80 and listener["Protocol"] == "HTTP"
        ),
        None,
    )
    desired_action = {"Type": "forward", "TargetGroupArn": target_group_arn}

    if http_listener is None:
        # No HTTP listener exists: create it.
        response = elbv2.create_listener(
            LoadBalancerArn=alb_arn,
            Protocol="HTTP",
            Port=80,
            DefaultActions=[desired_action],
        )
        if resources is not None:
            resources["listeners"].append(
                response["Listeners"][0]["ListenerArn"]
            )
    else:
        # Check both simple and weighted forward actions before modifying it.
        forwards_to_target_group = False
        current_actions = http_listener.get("DefaultActions", [])
        for action in current_actions:
            if action.get("Type") != "forward":
                continue
            if action.get("TargetGroupArn") == target_group_arn:
                forwards_to_target_group = True
                break
            forward_config = action.get("ForwardConfig", {})
            target_groups = forward_config.get("TargetGroups", [])
            if any(
                group.get("TargetGroupArn") == target_group_arn
                for group in target_groups
            ):
                forwards_to_target_group = True
                break

        if not forwards_to_target_group:
            if resources is not None:
                resources["updated_listeners"].append(
                    {
                        "ListenerArn": http_listener["ListenerArn"],
                        "DefaultActions": current_actions,
                    }
                )
            elbv2.modify_listener(
                ListenerArn=http_listener["ListenerArn"],
                DefaultActions=[desired_action],
            )
    return dns_name, target_group_arn


def wait_for_healthy_targets(elbv2, autoscaling, group_name, target_group_arn,
                             expected_count=2, timeout_seconds=600,
                             poll_interval=15):
    """Wait until enough InService instances in the ASG are healthy ALB targets."""
    deadline = time.monotonic() + timeout_seconds
    last_report = None

    while True:
        groups = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[group_name]
        )["AutoScalingGroups"]
        asg_instances = {
            instance["InstanceId"]
            for group in groups
            for instance in group["Instances"]
            if instance["LifecycleState"] == "InService"
        }
        targets = elbv2.describe_target_health(
            TargetGroupArn=target_group_arn
        )["TargetHealthDescriptions"]
        healthy_targets = {
            target["Target"]["Id"]
            for target in targets
            if target["TargetHealth"]["State"] == "healthy"
        }
        healthy_asg_targets = asg_instances & healthy_targets
        healthy_count = len(healthy_asg_targets)
        if healthy_count >= expected_count:
            return

        target_summary = "; ".join(
            f"{target['Target']['Id']}: {target['TargetHealth']['State']}"
            + (f" ({target['TargetHealth'].get('Reason')}: "
               f"{target['TargetHealth'].get('Description')})"
               if target["TargetHealth"].get("Reason") else "")
            for target in targets
        ) or "no targets registered yet"
        report = (healthy_count, len(asg_instances), target_summary)
        if report != last_report:
            print(
                f"Waiting for healthy ASG targets ({healthy_count}/{expected_count}; "
                f"{len(asg_instances)} ASG instances InService). {target_summary}"
            )
            last_report = report

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Timed out waiting for {expected_count} healthy targets from Auto "
                f"Scaling Group {group_name}; {healthy_count} InService ASG instances "
                f"are healthy. {target_summary}"
            )
        time.sleep(min(poll_interval, remaining))
