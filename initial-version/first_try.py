"""Historical, single-file version of the Tooplate Hosting deployment.

This is kept to document the project's first implementation. It defaults to
Frankfurt (eu-central-1) and uses direct create calls; the current deployment
is modular and runs with: python -m stackcraft_aws.main
"""


import boto3
import urllib.request
import os
import time
from botocore.exceptions import ClientError

# =====================================================
# CONFIGURATION
# =====================================================

REGION = "eu-central-1"  # Historical default: Frankfurt; the modular app defaults to Virginia.
TEMPLATE_NAME = "Hosting"

KEY_NAME = f"{TEMPLATE_NAME}-KeyPair"
EC2_SG_NAME = f"{TEMPLATE_NAME}-EC2-SG"
ALB_SG_NAME = f"{TEMPLATE_NAME}-ALB-SG"
INSTANCE_NAME = f"{TEMPLATE_NAME}-EC2-Instance"
ALB_NAME = f"{TEMPLATE_NAME}-ALB"
TARGET_GROUP_NAME = f"{TEMPLATE_NAME}-Target-Group"

KEY_FILE = f"{KEY_NAME}.pem"

ec2 = boto3.client("ec2", region_name=REGION)
elbv2 = boto3.client("elbv2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)


# =====================================================
# 1. GET MY PUBLIC IP
# =====================================================

def get_my_ip():
    with urllib.request.urlopen(
        "https://checkip.amazonaws.com", timeout=10
    ) as response:
        return response.read().decode().strip()


MY_IP = get_my_ip()  # Captured once and used for the SSH /32 ingress rule.

print(f"My public IP: {MY_IP}")


# =====================================================
# 2. GET DEFAULT VPC AND ALL ITS SUBNETS
# =====================================================

def get_default_vpc_and_subnets():
    "Find the default VPC and choose one subnet per AZ for the load balancer."

    response = ec2.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )

    if not response["Vpcs"]:
        raise Exception("No default VPC found in this region.")

    vpc_id = response["Vpcs"][0]["VpcId"]

    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )["Subnets"]

    # Keep one subnet per AZ, preferring default subnets.
    selected = {}

    for subnet in sorted(
        subnets,
        key=lambda s: not s["DefaultForAz"]
    ):
        az = subnet["AvailabilityZone"]
        selected.setdefault(az, subnet)

    selected_subnets = list(selected.values())

    if len(selected_subnets) < 2:
        raise Exception(
            "ALB requires subnets in at least two Availability Zones."
        )

    print(f"Default VPC: {vpc_id}")
    print("Selected ALB Availability Zones:")

    for subnet in selected_subnets:
        print(
            f"  {subnet['AvailabilityZone']}: "
            f"{subnet['SubnetId']}"
        )

    return vpc_id, selected_subnets


VPC_ID, SUBNETS = get_default_vpc_and_subnets()


# =====================================================
# 3. CREATE KEY PAIR
# =====================================================

def create_key_pair():
    "Create the EC2 key pair and save its private PEM material locally."

    response = ec2.create_key_pair(
        KeyName=KEY_NAME,
        KeyType="rsa",
        KeyFormat="pem",
        TagSpecifications=[
            {
                "ResourceType": "key-pair",
                "Tags": [
                    {"Key": "Name", "Value": KEY_NAME},
                    {"Key": "Template", "Value": TEMPLATE_NAME}
                ]
            }
        ]
    )

    with open(KEY_FILE, "w") as file:
        file.write(response["KeyMaterial"])

    os.chmod(KEY_FILE, 0o400)

    print(f"Key pair created: {KEY_NAME}")
    print(f"Private key saved: {KEY_FILE}")


# =====================================================
# 4. CREATE EC2 SECURITY GROUP
# =====================================================

def create_ec2_security_group():
    "Create the instance security group with SSH and HTTP ingress."

    response = ec2.create_security_group(
        GroupName=EC2_SG_NAME,
        Description=f"{TEMPLATE_NAME} EC2 Security Group",
        VpcId=VPC_ID,
        TagSpecifications=[
            {
                "ResourceType": "security-group",
                "Tags": [
                    {"Key": "Name", "Value": EC2_SG_NAME},
                    {"Key": "Template", "Value": TEMPLATE_NAME}
                ]
            }
        ]
    )

    sg_id = response["GroupId"]

    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [
                    {
                        "CidrIp": f"{MY_IP}/32",
                        "Description": "SSH from my IP"
                    }
                ]
            },
            {
                "IpProtocol": "tcp",
                "FromPort": 80,
                "ToPort": 80,
                "IpRanges": [
                    {
                        "CidrIp": "0.0.0.0/0",
                        "Description": "HTTP from anywhere"
                    }
                ]
            }
        ]
    )

    print(f"EC2 Security Group created: {sg_id}")

    return sg_id


# =====================================================
# 5. CREATE ALB SECURITY GROUP
# =====================================================

def create_alb_security_group():
    "Create the ALB security group with public HTTP ingress."

    response = ec2.create_security_group(
        GroupName=ALB_SG_NAME,
        Description=f"{TEMPLATE_NAME} ALB Security Group",
        VpcId=VPC_ID,
        TagSpecifications=[
            {
                "ResourceType": "security-group",
                "Tags": [
                    {"Key": "Name", "Value": ALB_SG_NAME},
                    {"Key": "Template", "Value": TEMPLATE_NAME}
                ]
            }
        ]
    )

    sg_id = response["GroupId"]

    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 80,
                "ToPort": 80,
                "IpRanges": [
                    {
                        "CidrIp": "0.0.0.0/0",
                        "Description": "HTTP from anywhere"
                    }
                ]
            }
        ]
    )

    print(f"ALB Security Group created: {sg_id}")

    return sg_id


# =====================================================
# 6. GET LATEST AMAZON LINUX 2023 AMI
# =====================================================

def get_amazon_linux_ami():
    "Read the current Amazon Linux 2023 AMI ID from Systems Manager."

    response = ssm.get_parameter(
        Name=(
            "/aws/service/ami-amazon-linux-latest/"
            "al2023-ami-kernel-default-x86_64"
        )
    )

    ami_id = response["Parameter"]["Value"]

    print(f"Amazon Linux 2023 AMI: {ami_id}")

    return ami_id


# =====================================================
# 7. EC2 USER DATA - INSTALL TOOPLATE WEBSITE
# =====================================================

# EC2 executes this once on first boot to install Apache and copy the template.
USER_DATA = """#!/bin/bash
set -eux

dnf update -y
dnf install -y httpd curl unzip

systemctl enable httpd

mkdir -p /var/www/html
mkdir -p /tmp/tooplate

# Download Tooplate Hosting template
curl -fL \
  https://www.tooplate.com/download/2017_hosting \
  -o /tmp/hosting.zip

# Extract website files
unzip -o /tmp/hosting.zip -d /tmp/tooplate

# Locate index.html
INDEX_FILE=$(find /tmp/tooplate -type f \
  -name index.html | head -n 1)

if [ -n "$INDEX_FILE" ]; then
    cp -a "$(dirname "$INDEX_FILE")"/. /var/www/html/
else
    echo "Tooplate template extraction failed" \
      > /var/www/html/index.html
fi

chown -R apache:apache /var/www/html
chmod -R 755 /var/www/html

systemctl start httpd
"""


# =====================================================
# 8. CREATE EC2 INSTANCE
# =====================================================

def create_ec2_instance(ec2_sg_id):
    "Launch a t2.micro and wait for its EC2 status checks."

    ami_id = get_amazon_linux_ami()

    subnet_id = SUBNETS[0]["SubnetId"]

    response = ec2.run_instances(
        ImageId=ami_id,
        InstanceType="t2.micro",
        MinCount=1,
        MaxCount=1,
        KeyName=KEY_NAME,

        NetworkInterfaces=[
            {
                "DeviceIndex": 0,
                "SubnetId": subnet_id,
                "Groups": [ec2_sg_id],
                "AssociatePublicIpAddress": True
            }
        ],

        UserData=USER_DATA,

        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": INSTANCE_NAME},
                    {"Key": "Template", "Value": TEMPLATE_NAME}
                ]
            }
        ]
    )

    instance_id = response["Instances"][0]["InstanceId"]

    print(f"EC2 instance launched: {instance_id}")

    print("Waiting for EC2 instance to become running...")

    ec2.get_waiter("instance_running").wait(
        InstanceIds=[instance_id]
    )

    print("Waiting for EC2 status checks...")

    ec2.get_waiter("instance_status_ok").wait(
        InstanceIds=[instance_id]
    )

    instance = ec2.describe_instances(
        InstanceIds=[instance_id]
    )["Reservations"][0]["Instances"][0]

    print(f"EC2 Public IP: {instance.get('PublicIpAddress')}")

    return instance_id


# =====================================================
# 9. CREATE APPLICATION LOAD BALANCER
# =====================================================

def create_alb(alb_sg_id):
    "Create the internet-facing ALB across the selected public subnets."

    subnet_ids = [
        subnet["SubnetId"] for subnet in SUBNETS
    ]

    response = elbv2.create_load_balancer(
        Name=ALB_NAME,
        Subnets=subnet_ids,
        SecurityGroups=[alb_sg_id],
        Scheme="internet-facing",
        Type="application",
        IpAddressType="ipv4",
        Tags=[
            {"Key": "Name", "Value": ALB_NAME},
            {"Key": "Template", "Value": TEMPLATE_NAME}
        ]
    )

    alb = response["LoadBalancers"][0]

    alb_arn = alb["LoadBalancerArn"]

    print(f"ALB created: {alb_arn}")

    print("Waiting for ALB to become active...")

    elbv2.get_waiter("load_balancer_available").wait(
        LoadBalancerArns=[alb_arn]
    )

    return alb_arn, alb["DNSName"]


# =====================================================
# 10. CREATE TARGET GROUP
# =====================================================

def create_target_group():
    "Create the HTTP target group with a root-path health check."

    response = elbv2.create_target_group(
        Name=TARGET_GROUP_NAME,
        Protocol="HTTP",
        Port=80,
        VpcId=VPC_ID,
        TargetType="instance",

        HealthCheckProtocol="HTTP",
        HealthCheckPort="traffic-port",
        HealthCheckPath="/",
        HealthCheckIntervalSeconds=15,
        HealthCheckTimeoutSeconds=5,
        HealthyThresholdCount=2,
        UnhealthyThresholdCount=3,

        Matcher={"HttpCode": "200-399"},

        Tags=[
            {"Key": "Name", "Value": TARGET_GROUP_NAME},
            {"Key": "Template", "Value": TEMPLATE_NAME}
        ]
    )

    tg_arn = response["TargetGroups"][0]["TargetGroupArn"]

    print(f"Target Group created: {tg_arn}")

    return tg_arn


# =====================================================
# 11. REGISTER EC2 INSTANCE
# =====================================================

def register_instance(tg_arn, instance_id):
    "Register the EC2 instance as a target on HTTP port 80."

    elbv2.register_targets(
        TargetGroupArn=tg_arn,
        Targets=[
            {
                "Id": instance_id,
                "Port": 80
            }
        ]
    )

    print(f"Instance registered: {instance_id}")


# =====================================================
# 12. CREATE HTTP LISTENER
# =====================================================

def create_listener(alb_arn, tg_arn):
    "Create an HTTP listener that forwards to the target group."

    elbv2.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol="HTTP",
        Port=80,

        DefaultActions=[
            {
                "Type": "forward",
                "TargetGroupArn": tg_arn
            }
        ]
    )

    print("HTTP listener created on port 80")


# =====================================================
# 13. MAIN
# =====================================================

def main():
    "Run the original single-file deployment sequence and print its outputs."

    try:

        print("\n========== STARTING DEPLOYMENT ==========\n")

        # 1. Key pair
        create_key_pair()

        # 2. EC2 security group
        ec2_sg_id = create_ec2_security_group()

        # 3. ALB security group
        alb_sg_id = create_alb_security_group()

        # 4. EC2 instance
        instance_id = create_ec2_instance(ec2_sg_id)

        # 5. Application Load Balancer
        alb_arn, alb_dns = create_alb(alb_sg_id)

        # 6. Target Group
        tg_arn = create_target_group()

        # 7. Register EC2
        register_instance(tg_arn, instance_id)

        # 8. Listener
        create_listener(alb_arn, tg_arn)

        # 9. Final output
        print("\n========== DEPLOYMENT COMPLETED ==========\n")

        print(f"Template       : {TEMPLATE_NAME}")
        print(f"Key Pair       : {KEY_NAME}")
        print(f"EC2 Instance   : {instance_id}")
        print(f"EC2 SG         : {ec2_sg_id}")
        print(f"ALB SG         : {alb_sg_id}")
        print(f"Target Group   : {tg_arn}")

        print("\nALB DNS ENDPOINT:")
        print(f"http://{alb_dns}")

        print("\nPrivate Key:")
        print(KEY_FILE)

    except Exception as error:

        print("\nDEPLOYMENT FAILED!")
        print(error)
        raise


if __name__ == "__main__":
    main()
