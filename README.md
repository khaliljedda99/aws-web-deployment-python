# StackCraft on AWS

A small, modular Python project that provisions a Tooplate StackCraft portfolio site on AWS with boto3.

It creates an EC2 key pair, network security groups, one Amazon Linux 2023 **t2.micro** instance, an internet-facing Application Load Balancer, an HTTP listener, and a target group. EC2 user data downloads the [StackCraft template](https://www.tooplate.com/view/2162-stackcraft) and serves it with Apache.

## Architecture

    Internet --HTTP:80--> Application Load Balancer --HTTP:80--> EC2 (Apache)
                               |                                    |
                        stackcraft-alb-sg                    stackcraft-instance-sg
                        TCP 80 from anywhere                  TCP 80 from anywhere
                                                              TCP 22 from MY_IP_CIDR

The ALB uses public subnets in two Availability Zones from the default VPC. The single EC2 target launches in one of those subnets.

## Requirements

- Python 3.10 or newer
- AWS credentials available to boto3 (for example, an AWS CLI profile)
- AWS IAM permissions to manage EC2 instances, key pairs, security groups, load balancers, target groups, and listeners, and to read the public Amazon Linux AMI parameter from Systems Manager Parameter Store
- A default VPC with public subnets in at least two Availability Zones in the selected region

## Configure and launch (Git Bash)

    python -m venv .venv
    source .venv/Scripts/activate
    pip install -r requirements.txt
    cp .env.example .env

Edit .env and set MY_IP_CIDR to your public IPv4 address with a /32 suffix. Keep AWS_REGION=us-east-1 for Virginia, or set it to the region that contains your default VPC. Then load the values and launch:

    set -a
    source .env
    set +a
    python -m stackcraft_aws.main

The command prints the EC2 instance ID and the ALB URL. Allow a few minutes for cloud-init to download the template and complete setup before opening the URL.

## Modules

- config.py: environment configuration and input checks
- network.py: default VPC, public subnets, and security groups
- compute.py: key pair, Amazon Linux instance, and website user data
- load_balancer.py: target group, ALB, and HTTP listener
- main.py: provisioning entry point

Resource names use the stackcraft prefix. The setup can resume if a prior attempt created the security groups, key pair, instance, target group, or ALB before failing.

## Security and costs

SSH is limited to MY_IP_CIDR. HTTP is open to the internet on both security groups, as requested. The instance has one target and the ALB serves plain HTTP; add HTTPS and a certificate before using this as a production site. AWS charges for running resources such as EC2 and the load balancer.

The private key is saved to KEY_PAIR_FILE (default: stackcraft-key.pem). Keep it private. .env and *.pem files are ignored by Git.

## Cleanup

In the AWS Console, switch to the same region and remove the StackCraft ALB listener and ALB, then the target group. Terminate the stackcraft-ec2 instance, delete stackcraft-instance-sg and stackcraft-alb-sg, and delete the stackcraft-key key pair. Confirm each resource belongs to this project before deleting it.

## Template

The website design and source archive come from [Tooplate's StackCraft template](https://www.tooplate.com/view/2162-stackcraft). Follow Tooplate's current template usage terms for redistribution and attribution.
