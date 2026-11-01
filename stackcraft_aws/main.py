"""Coordinate the AWS deployment.

Run this module from the repository root with:
    python -m stackcraft_aws.main
"""

import boto3
from botocore.exceptions import ClientError

from .config import Config
from .network import get_default_vpc, get_public_subnets, create_security_groups
from .compute import create_key_pair, launch_instance
from .load_balancer import create_alb


def main():
    "Load configuration, provision dependencies in order, and print the site endpoint."
    config = Config.from_env()
    session = boto3.Session(region_name=config.region)
    ec2 = session.client("ec2")
    ec2_resource = session.resource("ec2")
    elbv2 = session.client("elbv2")
    # Resolve shared networking before creating resources that depend on it.
    vpc_id = get_default_vpc(ec2)
    subnets = get_public_subnets(ec2, vpc_id)
    # The EC2 and ALB each receive the security group designed for their traffic.
    instance_sg, alb_sg = create_security_groups(ec2, vpc_id, config.stack_name, config.my_ip_cidr)
    key_name = create_key_pair(ec2, config.stack_name, config.key_pair_file)
    instance_id = launch_instance(ec2_resource, config.stack_name, subnets[0], instance_sg, key_name, config.region)
    ec2_resource.Instance(instance_id).wait_until_running()
    # The target group and listener attach the running instance to the public ALB.
    dns = create_alb(elbv2, config.stack_name, vpc_id, subnets, alb_sg, instance_id)
    print(f"Instance: {instance_id}")
    print(f"ALB URL: http://{dns}")
    print(f"Private key saved to: {config.key_pair_file}")


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        raise SystemExit(f"AWS API error: {exc}")
