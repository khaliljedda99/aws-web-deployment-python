"""Roll back only AWS resources created during a failed deployment."""

from pathlib import Path
import time

from botocore.exceptions import ClientError


def new_resource_registry():
    """Return a registry for resources and changes made by one deployment run."""
    return {
        "key_pair": None,
        "security_groups": [],
        "added_ingress": [],
        "launch_templates": [],
        "launch_template_versions": [],
        "target_groups": [],
        "load_balancers": [],
        "listeners": [],
        "updated_listeners": [],
        "created_auto_scaling_groups": [],
        "updated_auto_scaling_groups": [],
    }


def rollback_deployment(ec2, elbv2, autoscaling, resources):
    """Best-effort cleanup in dependency order; preserve resources reused before this run."""
    errors = []

    def attempt(label, action):
        try:
            action()
        except Exception as exc:  # Continue cleanup so one failure does not block later steps.
            errors.append(f"{label}: {exc}")

    # Restore existing groups before removing resources that their old settings may use.
    for snapshot in reversed(resources["updated_auto_scaling_groups"]):
        def restore_group(snapshot=snapshot):
            autoscaling.update_auto_scaling_group(**snapshot["Settings"])
            group_name = snapshot["Settings"]["AutoScalingGroupName"]
            groups = autoscaling.describe_auto_scaling_groups(
                AutoScalingGroupNames=[group_name]
            )["AutoScalingGroups"]
            if not groups:
                return
            current_targets = set(groups[0].get("TargetGroupARNs", []))
            previous_targets = set(snapshot["TargetGroupARNs"])
            added_targets = current_targets - previous_targets
            removed_targets = previous_targets - current_targets
            if added_targets:
                autoscaling.detach_load_balancer_target_groups(
                    AutoScalingGroupName=group_name,
                    TargetGroupARNs=list(added_targets),
                )
            if removed_targets:
                autoscaling.attach_load_balancer_target_groups(
                    AutoScalingGroupName=group_name,
                    TargetGroupARNs=list(removed_targets),
                )

        attempt(
            f"restore Auto Scaling Group {snapshot['Settings']['AutoScalingGroupName']}",
            restore_group,
        )

    # Force-delete only groups created by this run; retain previously existing groups.
    for group_name in reversed(resources["created_auto_scaling_groups"]):
        def delete_group(group_name=group_name):
            groups = autoscaling.describe_auto_scaling_groups(
                AutoScalingGroupNames=[group_name]
            )["AutoScalingGroups"]
            instance_ids = [
                instance["InstanceId"]
                for group in groups
                for instance in group.get("Instances", [])
            ]
            if not groups:
                return
            autoscaling.delete_auto_scaling_group(
                AutoScalingGroupName=group_name, ForceDelete=True
            )
            if instance_ids:
                ec2.get_waiter("instance_terminated").wait(
                    InstanceIds=instance_ids,
                    WaiterConfig={"Delay": 10, "MaxAttempts": 30},
                )

        attempt(f"delete Auto Scaling Group {group_name}", delete_group)

    created_sg_ids = set(resources["security_groups"])
    for item in reversed(resources["added_ingress"]):
        if item["GroupId"] in created_sg_ids:
            continue  # Deleting the newly created group removes these rules too.
        attempt(
            f"revoke added ingress from {item['GroupId']}",
            lambda item=item: ec2.revoke_security_group_ingress(
                GroupId=item["GroupId"], IpPermissions=[item["Permission"]]
            ),
        )

    # Restore reused listeners before deleting any target groups from this run.
    for snapshot in reversed(resources["updated_listeners"]):
        attempt(
            f"restore listener {snapshot['ListenerArn']}",
            lambda snapshot=snapshot: elbv2.modify_listener(
                ListenerArn=snapshot["ListenerArn"],
                DefaultActions=snapshot["DefaultActions"],
            ),
        )

    for listener_arn in reversed(resources["listeners"]):
        attempt(
            f"delete listener {listener_arn}",
            lambda listener_arn=listener_arn: elbv2.delete_listener(
                ListenerArn=listener_arn
            ),
        )

    for load_balancer_arn in reversed(resources["load_balancers"]):
        def delete_load_balancer(load_balancer_arn=load_balancer_arn):
            elbv2.delete_load_balancer(LoadBalancerArn=load_balancer_arn)
            elbv2.get_waiter("load_balancers_deleted").wait(
                LoadBalancerArns=[load_balancer_arn],
                WaiterConfig={"Delay": 10, "MaxAttempts": 30},
            )

        attempt(f"delete load balancer {load_balancer_arn}", delete_load_balancer)

    for target_group_arn in reversed(resources["target_groups"]):
        attempt(
            f"delete target group {target_group_arn}",
            lambda target_group_arn=target_group_arn: elbv2.delete_target_group(
                TargetGroupArn=target_group_arn
            ),
        )

    for item in reversed(resources["launch_template_versions"]):
        attempt(
            f"delete launch template version {item['VersionNumber']}",
            lambda item=item: ec2.delete_launch_template_versions(
                LaunchTemplateId=item["LaunchTemplateId"],
                Versions=[str(item["VersionNumber"])],
            ),
        )

    for launch_template_id in reversed(resources["launch_templates"]):
        attempt(
            f"delete launch template {launch_template_id}",
            lambda launch_template_id=launch_template_id: ec2.delete_launch_template(
                LaunchTemplateId=launch_template_id
            ),
        )

    # Delete the EC2 group before the ALB group it references in its ingress rule.
    for group_id in resources["security_groups"]:
        def delete_security_group(group_id=group_id):
            for attempt_number in range(12):
                try:
                    ec2.delete_security_group(GroupId=group_id)
                    return
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code")
                    if code != "DependencyViolation" or attempt_number == 11:
                        raise
                    time.sleep(5)

        attempt(f"delete security group {group_id}", delete_security_group)

    key_pair = resources["key_pair"]
    if key_pair:
        try:
            ec2.delete_key_pair(KeyName=key_pair["Name"])
        except Exception as exc:
            errors.append(f"delete key pair {key_pair['Name']}: {exc}")
        else:
            if key_pair.get("FileCreated", False):
                key_path = Path(key_pair["Path"])
                if key_path.is_file():
                    def remove_key_file(key_path=key_path):
                        # Windows maps a read-only key mode to a read-only file flag.
                        key_path.chmod(0o600)
                        key_path.unlink()

                    attempt(f"remove private key file {key_path}", remove_key_file)

    return errors
