# Tooplate Hosting on AWS

A modular Python/boto3 project that deploys Tooplate's [Hosting template](https://www.tooplate.com/view/2017-hosting) on Amazon Linux 2023 behind an internet-facing Application Load Balancer.

## 1. AWS architecture

    Internet / users
           |
           | HTTP :80
           v
    Application Load Balancer (public; all available default-VPC AZ subnets)
           |
           | HTTP :80
           v
    Target group (health check: /)
           |
           | HTTP :80
           v
    EC2 t2.micro (Amazon Linux 2023, Apache, Tooplate Hosting)

The ALB uses one default public subnet from each available Availability Zone. The single EC2 instance launches in one of those subnets. SSH access to the instance uses the Hosting key pair and is limited to MY_IP_CIDR.

## 2. Resources created

| Resource | Name | Configuration |
| --- | --- | --- |
| EC2 key pair | Hosting-KeyPair | Private key saved locally as Hosting-KeyPair.pem |
| EC2 security group | Hosting-EC2-SG | SSH (22) from MY_IP_CIDR; HTTP (80) from anywhere |
| EC2 instance | Hosting-EC2-Instance | Amazon Linux 2023, t2.micro; Apache serves the Hosting template |
| ALB security group | Hosting-ALB-SG | HTTP (80) from anywhere |
| Application Load Balancer | Hosting-ALB | Internet-facing; uses available default-VPC public subnet AZs |
| Target group | Hosting-Target-Group | HTTP (80), health check path /; registers the EC2 instance |
| Listener | HTTP :80 | Forwards requests to Hosting-Target-Group |

Resources are named after the Tooplate Hosting template. This is a single-instance deployment; it does not create autoscaling or blue/green target groups.

## Requirements

- Python 3.10 or newer
- Boto3 (install with pip)
- AWS credentials available through boto3's standard credential chain
- IAM permissions for EC2, VPC, ELBv2, and Systems Manager Parameter Store
- A default VPC with public subnets in at least two Availability Zones

## Configure and deploy (Git Bash)

    python -m venv .venv
    source .venv/Scripts/activate
    pip install -r requirements.txt
    cp .env.example .env

For Virginia, use AWS_REGION=us-east-1. Leave MY_IP_CIDR blank to have the script detect your public IPv4 address at startup and restrict SSH to that address (/32). Set MY_IP_CIDR manually only if automatic detection is unavailable or you need to override it. Then load the values and run:

    set -a
    source .env
    set +a
    python -m stackcraft_aws.main

The command prints the EC2 instance ID and ALB endpoint. Allow a few minutes for EC2 user data to download the site files and finish configuring Apache.

## Project modules

- config.py — environment settings and validation
- network.py — default VPC, public subnet selection, and security groups
- compute.py — key pair, EC2 instance, AMI lookup, and website user data
- load_balancer.py — ALB, target group, and listener
- main.py — deployment entry point

## Original single-file attempt

The initial monolithic script is preserved in [examples/first_try.py](examples/first_try.py) for reference. It defaults to eu-central-1 and uses direct create calls; use the modular entry point above for the current deployment.

## Security and costs

SSH (22) is restricted to MY_IP_CIDR. When the variable is blank, the script discovers the current public IPv4 address using checkip.amazonaws.com and adds /32. HTTP (80) is open to the internet on the EC2 and ALB security groups, as requested. This example serves plain HTTP and uses one EC2 target. AWS charges for resources such as EC2 and the ALB while they are running.

The private key is written to Hosting-KeyPair.pem. Keep it private. The .env file, PEM keys, Python virtual environment, and bytecode cache are excluded by .gitignore.

## Cleanup

In the AWS Console, switch to the deployment region and delete the HTTP listener, ALB, and target group; terminate Hosting-EC2-Instance; then delete Hosting-EC2-SG, Hosting-ALB-SG, and Hosting-KeyPair. Confirm each resource belongs to this project before removing it.

