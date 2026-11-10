# Tooplate Hosting on AWS

A modular Python and Boto3 project that deploys Tooplate's [Hosting template](https://www.tooplate.com/view/2017-hosting) on Amazon Linux 2023 behind an internet-facing Application Load Balancer (ALB). An Auto Scaling Group maintains two EC2 web servers across the available public subnets.

## 1. AWS architecture

    Internet / users
           |
           | HTTP :80
           v
    Application Load Balancer (public; public subnets in multiple AZs)
           |
           | HTTP :80
           v
    Target group (health check: /)
           |
           +----------------------+
           |                      |
           v                      v
    EC2 t2.micro              EC2 t2.micro
    Auto Scaling Group maintains desired capacity of 2

The ALB accepts public HTTP traffic and forwards it to the target group. The Auto Scaling Group (ASG) launches the EC2 instances in the selected public subnets and registers them with that target group. The EC2 security group accepts HTTP only from the ALB security group. SSH access is limited to the detected or configured MY_IP_CIDR.

## 2. Resources created

| Resource | Name | Configuration |
| --- | --- | --- |
| EC2 key pair | Hosting-KeyPair | Private key saved locally as Hosting-KeyPair.pem |
| EC2 security group | Hosting-EC2-SG | SSH (22) from MY_IP_CIDR; HTTP (80) from Hosting-ALB-SG only |
| ALB security group | Hosting-ALB-SG | HTTP (80) from anywhere |
| Launch template | Hosting-Launch-Template | Amazon Linux 2023, t2.micro, Hosting-KeyPair, EC2 security group, and website user data |
| Auto Scaling Group | Hosting-ASG | Minimum 2, desired 2, maximum 2; spans selected public subnets and registers targets with Hosting-Target-Group |
| EC2 instances | Hosting-EC2-Instance | Two Amazon Linux 2023 t2.micro instances maintained by the ASG; Apache serves the Hosting template |
| Application Load Balancer | Hosting-ALB | Internet-facing; uses selected default-VPC public subnets |
| Target group | Hosting-Target-Group | HTTP (80), health check path /; receives instances from the ASG |
| Listener | HTTP :80 | Forwards requests to Hosting-Target-Group |

Resources are named after Tooplate's Hosting template. This deployment uses a fixed ASG capacity of two instances; change the capacity settings in stackcraft_aws/autoscaling.py to scale it.

## Requirements

- Python 3.10 or newer
- Boto3 (install with pip)
- AWS credentials available through Boto3's standard credential chain
- IAM permissions for EC2, Auto Scaling, ELBv2, and Systems Manager Parameter Store
- A default VPC with public subnets in at least two Availability Zones

## Configure and deploy (Git Bash)

    python -m venv .venv
    source .venv/Scripts/activate
    pip install -r requirements.txt
    cp .env.example .env

For Virginia, set AWS_REGION=us-east-1. Leave MY_IP_CIDR blank to have the program detect your public IPv4 address at startup and restrict SSH to that address (/32). Set MY_IP_CIDR manually only if automatic detection is unavailable or you need to override it. Then load the values and run:

    set -a
    source .env
    set +a
    python -m stackcraft_aws.main

The command prints the Auto Scaling Group name, then waits up to 10 minutes for both `InService` instances in that group to become healthy targets. Healthy targets outside the group, such as an older standalone instance, do not count. It prints the ALB endpoint only after both ASG instances pass the health checks. While waiting, it reports each target's state; if the wait times out, it exits with the target IDs and health-check details to help diagnose the deployment.

If deployment fails, the program attempts to roll back resources created during that run in dependency order. It preserves resources it reused, restores the previous settings of an existing Auto Scaling Group, and reports any cleanup failures alongside the deployment error.

If you previously ran the single-instance version, the ASG does not adopt or terminate that instance. The old instance may also remain registered in the reused target group. After both ASG instances are healthy, deregister the old instance from the target group and terminate it in EC2 to avoid an extra target and ongoing instance charges.

## Project modules

- config.py — environment settings and validation
- network.py — default VPC, public subnet selection, and security groups
- compute.py — key pair, launch template, AMI lookup, and website user data
- autoscaling.py — maintains two EC2 instances and attaches them to the ALB target group
- load_balancer.py — ALB, target group, listener, and wait for healthy targets
- cleanup.py — tracks newly created resources and rolls them back after deployment failure
- main.py — deployment entry point; coordinates resource creation, readiness, and rollback

## Original first attempt

The project's original single-file Boto3 implementation is preserved in [initial-version/first_try.py](initial-version/first_try.py). It shows the initial deployment approach before the code was split into modules. This historical example defaults to eu-central-1 and uses direct create calls; use the modular entry point above for the current deployment.

## Security and costs

SSH (22) is restricted to MY_IP_CIDR. When the variable is blank, the program discovers the current public IPv4 address using checkip.amazonaws.com and adds /32. The ALB security group allows HTTP (80) from anywhere, while the EC2 security group allows HTTP only from the ALB security group. This example serves plain HTTP. AWS charges for resources such as EC2 and the ALB while they are running.

The private key is written to Hosting-KeyPair.pem with restrictive read-only permissions. Keep it private. The .env file, PEM keys, Python virtual environment, and bytecode cache are excluded by .gitignore.

## Recovering a lost key pair file

AWS does not let you download the original private key again. If Hosting-KeyPair.pem is missing while the Hosting-KeyPair still exists in AWS, restore the file from a secure backup; the script cannot recreate that key material.

If the AWS key-pair resource no longer exists, the script creates a replacement. If Hosting-KeyPair.pem already exists locally, the replacement key material overwrites it. The replacement key works for new instances only: existing instances still trust the old public key. To access or replace those instances, use another configured access method such as EC2 Instance Connect, or replace them through the Auto Scaling Group after confirming it can launch two instances.

## Cleanup

Delete resources in dependency order: delete the Auto Scaling Group with desired capacity set to zero (or delete it with instance termination enabled), then delete the HTTP listener, ALB, and target group. Delete any remaining project EC2 instances, then the launch template, Hosting-EC2-SG, Hosting-ALB-SG, and Hosting-KeyPair. Confirm each resource belongs to this project before removing it.

