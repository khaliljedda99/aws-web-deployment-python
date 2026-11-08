"""Maintain two EC2 web servers and attach them to the ALB target group."""

MIN_CAPACITY = 2
MAX_CAPACITY = 2
DESIRED_CAPACITY = 2


def ensure_auto_scaling_group(autoscaling, name, launch_template_id, subnets, target_group_arn):
    """Create or update the group so two instances stay registered with the ALB."""
    group_name = f"{name}-ASG"
    settings = {
        "AutoScalingGroupName": group_name,
        "MinSize": MIN_CAPACITY,
        "MaxSize": MAX_CAPACITY,
        "DesiredCapacity": DESIRED_CAPACITY,
        "VPCZoneIdentifier": ",".join(subnets),
        "TargetGroupARNs": [target_group_arn],
        "LaunchTemplate": {"LaunchTemplateId": launch_template_id, "Version": "$Latest"},
        "HealthCheckType": "ELB",
        "HealthCheckGracePeriod": 300,
    }
    existing = autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[group_name])["AutoScalingGroups"]
    if existing:
        autoscaling.update_auto_scaling_group(**settings)
    else:
        tags = [
            {"Key": "Name", "Value": f"{name}-EC2-Instance", "PropagateAtLaunch": True,
             "ResourceId": group_name, "ResourceType": "auto-scaling-group"},
            {"Key": "Project", "Value": name, "PropagateAtLaunch": True,
             "ResourceId": group_name, "ResourceType": "auto-scaling-group"},
        ]
        autoscaling.create_auto_scaling_group(**settings, Tags=tags)
    return group_name
