"""Create the EC2 key pair and launch template for the web servers."""

import base64
from pathlib import Path
from botocore.exceptions import ClientError

TEMPLATE_URL = "https://www.tooplate.com/download/2017_hosting"


def create_key_pair(ec2, name, key_path):
    """Create or reuse the named EC2 key pair and save its private PEM file."""
    key_name = f"{name}-KeyPair"
    existing = ec2.describe_key_pairs(Filters=[{"Name": "key-name", "Values": [key_name]}])["KeyPairs"]
    if existing:
        if not Path(key_path).is_file():
            raise RuntimeError(f"Key pair {key_name!r} already exists, but its private key file {key_path!r} is missing. Delete the existing key pair in AWS before rerunning.")
        return key_name
    response = ec2.create_key_pair(KeyName=key_name, KeyType="rsa", KeyFormat="pem")
    path = Path(key_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response["KeyMaterial"], encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key_name


def user_data(template_url=TEMPLATE_URL):
    """Return the EC2 startup script that installs Apache and the Tooplate site."""
    return f'''#!/bin/bash
set -euxo pipefail
# Amazon Linux 2023 ships curl-minimal; installing full curl conflicts with it.
dnf install -y httpd curl-minimal unzip
systemctl enable --now httpd
workdir=$(mktemp -d)
curl -fL --retry 5 --retry-delay 3 "{template_url}" -o "$workdir/template.zip"
unzip -q "$workdir/template.zip" -d "$workdir/site"
site_root=$(find "$workdir/site" -type f -name index.html -printf '%h\\n' | head -n 1)
test -n "$site_root"
cp -a "$site_root"/. /var/www/html/
chown -R apache:apache /var/www/html/
'''


def latest_amazon_linux_2023(region_name):
    """Return the current Amazon Linux 2023 x86_64 AMI ID for a region."""
    import boto3
    ssm = boto3.client("ssm", region_name=region_name)
    return ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]


def create_launch_template(ec2, name, security_group_id, key_name, region_name):
    """Create a launch template or add a new version for the Auto Scaling Group."""
    image_id = latest_amazon_linux_2023(region_name)
    template_name = f"{name}-Launch-Template"
    template_data = {
        "ImageId": image_id,
        "InstanceType": "t2.micro",
        "KeyName": key_name,
        "SecurityGroupIds": [security_group_id],
        "UserData": base64.b64encode(user_data().encode("utf-8")).decode("ascii"),
        "TagSpecifications": [
            {"ResourceType": "instance", "Tags": [
                {"Key": "Name", "Value": f"{name}-EC2-Instance"},
                {"Key": "Project", "Value": name}]},
            {"ResourceType": "volume", "Tags": [{"Key": "Project", "Value": name}]},
        ],
    }
    try:
        existing = ec2.describe_launch_templates(
            LaunchTemplateNames=[template_name]
        )["LaunchTemplates"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "InvalidLaunchTemplateName.NotFoundException":
            raise
        existing = []
    if existing:
        template_id = existing[0]["LaunchTemplateId"]
        ec2.create_launch_template_version(
            LaunchTemplateId=template_id, SourceVersion="$Latest",
            LaunchTemplateData=template_data,
            VersionDescription="Updated web server configuration")
        return template_id
    response = ec2.create_launch_template(
        LaunchTemplateName=template_name,
        VersionDescription="Initial web server configuration",
        LaunchTemplateData=template_data,
        TagSpecifications=[{"ResourceType": "launch-template", "Tags": [
            {"Key": "Name", "Value": template_name}, {"Key": "Project", "Value": name}]}])
    return response["LaunchTemplate"]["LaunchTemplateId"]

