"""Maintain two EC2 web servers and attach them to the ALB target group."""

MIN_CAPACITY = 2
MAX_CAPACITY = 2
DESIRED_CAPACITY = 2


def ensure_auto_scaling_group(autoscaling, name, launch_template_id, subnets,
                             target_group_arn, resources=None):
    """Create or update the group so two instances stay registered with the ALB."""
    group_name = f"{name}-ASG"
    settings = {
        "AutoScalingGroupName": group_name,
        "MinSize": MIN_CAPACITY,
        "MaxSize": MAX_CAPACITY,
        "DesiredCapacity": DESIRED_CAPACITY,
        "VPCZoneIdentifier": ",".join(subnets),
        "LaunchTemplate": {"LaunchTemplateId": launch_template_id, "Version": "$Latest"},
        "HealthCheckType": "ELB",
        "HealthCheckGracePeriod": 300,
    }
    existing = autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[group_name])["AutoScalingGroups"]
    if existing:
        if resources is not None:
            current = existing[0]
            resources["updated_auto_scaling_groups"].append({
                "Settings": {
                    "AutoScalingGroupName": group_name,
                    "MinSize": current["MinSize"],
                    "MaxSize": current["MaxSize"],
                    "DesiredCapacity": current["DesiredCapacity"],
                    "VPCZoneIdentifier": current.get("VPCZoneIdentifier", ""),
                    "HealthCheckType": current.get("HealthCheckType", "EC2"),
                    "HealthCheckGracePeriod": current.get("HealthCheckGracePeriod", 0),
                    "LaunchTemplate": current.get("LaunchTemplate"),
                },
                "TargetGroupARNs": current.get("TargetGroupARNs", []),
            })
        autoscaling.update_auto_scaling_group(**settings)
        current_targets = set(existing[0].get("TargetGroupARNs", []))
        if target_group_arn not in current_targets:
            autoscaling.attach_load_balancer_target_groups(
                AutoScalingGroupName=group_name,
                TargetGroupARNs=[target_group_arn],
            )
    else:
        tags = [
            {"Key": "Name", "Value": f"{name}-EC2-Instance", "PropagateAtLaunch": True,
             "ResourceId": group_name, "ResourceType": "auto-scaling-group"},
            {"Key": "Project", "Value": name, "PropagateAtLaunch": True,
             "ResourceId": group_name, "ResourceType": "auto-scaling-group"},
        ]
        autoscaling.create_auto_scaling_group(
            **settings, TargetGroupARNs=[target_group_arn], Tags=tags)
        if resources is not None:
            resources["created_auto_scaling_groups"].append(group_name)
    return group_name
