"""Coordinate the AWS deployment.

Run this module from the repository root with:
    python -m stackcraft_aws.main
"""

import boto3
from botocore.exceptions import ClientError

from .autoscaling import DESIRED_CAPACITY, ensure_auto_scaling_group
from .cleanup import new_resource_registry, rollback_deployment
from .compute import create_key_pair, create_launch_template
from .config import Config
from .load_balancer import create_alb, wait_for_healthy_targets
from .network import create_security_groups, get_default_vpc, get_public_subnets


def main():
    """Provision networking, the launch template, ALB, and two-instance ASG."""
    config = Config.from_env()
    session = boto3.Session(region_name=config.region)
    ec2 = session.client("ec2")
    elbv2 = session.client("elbv2")
    autoscaling = session.client("autoscaling")
    resources = new_resource_registry()

    try:
        # Resolve shared networking before creating dependent resources.
        vpc_id = get_default_vpc(ec2)
        subnets = get_public_subnets(ec2, vpc_id)
        instance_sg, alb_sg = create_security_groups(
            ec2, vpc_id, config.stack_name, config.my_ip_cidr, resources)
        key_name = create_key_pair(ec2, config.stack_name, config.key_pair_file, resources)
        launch_template_id = create_launch_template(
            ec2, config.stack_name, instance_sg, key_name, config.region, resources)
        dns_name, target_group_arn = create_alb(
            elbv2, config.stack_name, vpc_id, subnets, alb_sg, resources)
        group_name = ensure_auto_scaling_group(
            autoscaling, config.stack_name, launch_template_id, subnets,
            target_group_arn, resources)

        print(f"Auto Scaling Group: {group_name} (minimum and desired capacity: {DESIRED_CAPACITY})")
        wait_for_healthy_targets(
            elbv2, autoscaling, group_name, target_group_arn,
            expected_count=DESIRED_CAPACITY)
    except Exception as exc:
        cleanup_errors = rollback_deployment(ec2, elbv2, autoscaling, resources)
        error_type = "AWS API error" if isinstance(exc, ClientError) else "Deployment failed"
        message = f"{error_type}: {exc}"
        if cleanup_errors:
            message += "\nRollback issues:\n- " + "\n- ".join(cleanup_errors)
        else:
            message += "\nRollback completed; previously existing resources were preserved."
        raise SystemExit(message) from exc

    print(f"ALB URL: http://{dns_name}")
    print(f"Private key saved to: {config.key_pair_file}")


if __name__ == "__main__":
    main()
