"""Create the EC2 key pair and web server.

The instance uses the latest Amazon Linux 2023 image exposed by AWS Systems
Manager and configures Apache through EC2 user data at first boot.
"""

from pathlib import Path


TEMPLATE_URL = "https://www.tooplate.com/download/2017_hosting"


def create_key_pair(ec2, name, key_path):
    "Create or reuse the named EC2 key pair and save its private PEM file locally."
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
    "Return the shell script EC2 runs to install Apache and publish the Tooplate site."
    return f'''#!/bin/bash
set -euxo pipefail
dnf install -y httpd curl unzip
systemctl enable --now httpd
# Keep downloaded and extracted template files out of Apache's document root.
workdir=$(mktemp -d)
curl -fL --retry 5 --retry-delay 3 "{template_url}" -o "$workdir/template.zip"
unzip -q "$workdir/template.zip" -d "$workdir/site"
# Find the archive's directory containing the website entry point.
site_root=$(find "$workdir/site" -type f -name index.html -printf '%h\\n' | head -n 1)
test -n "$site_root"
cp -a "$site_root"/. /var/www/html/
chown -R apache:apache /var/www/html
'''


def latest_amazon_linux_2023(region_name):
    "Look up the current Amazon Linux 2023 x86_64 AMI ID for this region."
    # Resolve the official Amazon Linux 2023 x86_64 image ID through SSM Parameter Store.
    import boto3
    ssm = boto3.client("ssm", region_name=region_name)
    return ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")['Parameter']['Value']


def launch_instance(ec2, name, subnet_id, security_group_id, key_name, region_name):
    "Reuse a named active instance or launch a new t2.micro web server."
    client = ec2.meta.client
    existing = client.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [f"{name}-EC2-Instance"]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
    ])
    # Reuse prevents an ordinary rerun from launching another matching instance.
    for reservation in existing["Reservations"]:
        if reservation["Instances"]:
            return reservation["Instances"][0]["InstanceId"]

    image_id = latest_amazon_linux_2023(region_name)
    response = client.run_instances(ImageId=image_id, InstanceType="t2.micro", MinCount=1, MaxCount=1,
        KeyName=key_name, SecurityGroupIds=[security_group_id], SubnetId=subnet_id,
        UserData=user_data(), TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": f"{name}-EC2-Instance"}, {"Key": "Project", "Value": name}]}])
    return response["Instances"][0]["InstanceId"]
