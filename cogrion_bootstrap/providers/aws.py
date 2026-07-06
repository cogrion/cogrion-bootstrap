import json
import os
import re
import subprocess

import boto3
from botocore.exceptions import ClientError

from ..addons import HelmAddon, METRICS_SERVER, EXTERNAL_SECRETS, CLUSTER_PROPORTIONAL_AUTOSCALER
from .base import BaseProvider

_POLICY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "iam", "aws")

# Maps role key -> (policy file, format vars, trusted service accounts)
_IRSA_ROLES = {
    "bootstrap": (
        "bootstrap.json",
        ["ext_workspace_id", "aws_account_id"],
        ["cogrion-system:bootstrap-sa"],
    ),
    "cluster-agent": (
        "cluster-agent.json",
        ["ext_workspace_id", "aws_account_id", "ext_account_id"],
        [
            "cogrion-system:cplane-agent",
        ],
    ),
    "kubeblocks": (
        "kubeblocks.json",
        [],
        [
            "kb-system:kubeblocks",
            "kb-system:kubeblocks-dataprotection-exec-worker",
            "kb-system:kubeblocks-dataprotection-worker",
        ],
    ),
    "cluster-autoscaler": (
        "cluster-autoscaler.json",
        [],
        ["kube-system:cluster-autoscaler"],
    ),
    "efs-csi-driver": (
        "efs-csi-driver.json",
        [],
        ["kube-system:efs-csi-controller-sa"],
    ),
    "alb-controller": (
        "alb-controller.json",
        [],
        ["kube-system:aws-load-balancer-controller"],
    ),
    "external-secrets": (
        "external-secrets.json",
        [],
        ["external-secrets:external-secrets"],
    ),
}


class AWSProvider(BaseProvider):
    def __init__(
        self,
        ext_account_id: str,
        ext_workspace_id: str,
        cluster_name: str,
        region: str,
        dry_run: bool,
    ):
        super().__init__(
            ext_account_id=ext_account_id,
            ext_workspace_id=ext_workspace_id,
            cluster_name=cluster_name,
            dry_run=dry_run,
        )
        self.region = region
        self.eks = boto3.client("eks", region_name=region)
        self.ec2 = boto3.client("ec2", region_name=region)
        self.iam = boto3.client("iam")
        self.sts = boto3.client("sts")
        self._cached_oidc_arn: str = ""
        self._cached_oidc_url: str = ""
        self._cached_account_id: str = ""
        self._cached_vpc_id: str = ""
        self._cached_cluster_sg_id: str = ""

    def addons(self, irsa_arns: dict[str, str], vpc_id: str = "") -> list:
        def _arn(key: str) -> str:
            return irsa_arns.get(key, "")

        cluster_autoscaler = HelmAddon(
            release_name="cluster-autoscaler",
            namespace="kube-system",
            chart="autoscaler/cluster-autoscaler",
            repo_name="autoscaler",
            repo_url="https://kubernetes.github.io/autoscaler",
            version="9.57.0",
            set_args={
                "autoDiscovery.clusterName": self.cluster_name,
                "awsRegion": self.region,
                **(
                    _irsa_set(
                        "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn",
                        _arn("cluster-autoscaler"),
                    )
                ),
            },
            detect=("deployment", "cluster-autoscaler"),
        )

        efs_csi_driver = HelmAddon(
            release_name="aws-efs-csi-driver",
            namespace="kube-system",
            chart="efs-csi-driver/aws-efs-csi-driver",
            repo_name="efs-csi-driver",
            repo_url="https://kubernetes-sigs.github.io/aws-efs-csi-driver",
            set_args={
                **(
                    _irsa_set(
                        "controller.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn",
                        _arn("efs-csi-driver"),
                    )
                ),
            },
            detect=("deployment", "efs-csi-controller"),
        )

        alb_controller = HelmAddon(
            release_name="aws-load-balancer-controller",
            namespace="kube-system",
            chart="eks/aws-load-balancer-controller",
            repo_name="eks",
            repo_url="https://aws.github.io/eks-charts",
            set_args={
                "clusterName": self.cluster_name,
                "vpcId": vpc_id,
                "podDisruptionBudget.maxUnavailable": "1",
                "enableServiceMutatorWebhook": "false",
                **(
                    _irsa_set(
                        "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn",
                        _arn("alb-controller"),
                    )
                ),
            },
            detect=("deployment", "aws-load-balancer-controller"),
        )

        external_secrets = HelmAddon(
            release_name=EXTERNAL_SECRETS.release_name,
            namespace=EXTERNAL_SECRETS.namespace,
            chart=EXTERNAL_SECRETS.chart,
            repo_name=EXTERNAL_SECRETS.repo_name,
            repo_url=EXTERNAL_SECRETS.repo_url,
            detect=EXTERNAL_SECRETS.detect,
            set_args={
                **(
                    _irsa_set(
                        "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn",
                        _arn("external-secrets"),
                    )
                ),
            },
        )

        return [
            # cluster_autoscaler,
            efs_csi_driver,
            METRICS_SERVER,
            alb_controller,
            external_secrets,
            CLUSTER_PROPORTIONAL_AUTOSCALER,
        ]

    def ensure_iam(self) -> dict[str, str]:
        """Create all platform IRSA roles and their k8s ServiceAccounts.

        Returns a map of role-key -> IAM role ARN.
        """
        ext_workspace_id = self.ext_workspace_id
        aws_account_id = self._get_account_id()
        format_vars = {
            "ext_workspace_id": ext_workspace_id,
            "aws_account_id": aws_account_id,
            "ext_account_id": self.ext_account_id,
        }

        arns: dict[str, str] = {}
        for role_key, (policy_file, var_keys, service_accounts) in _IRSA_ROLES.items():
            role_name = f"{ext_workspace_id}-{role_key}-role"
            policy_name = f"{ext_workspace_id}-{role_key}-policy"
            vars_for_policy = {k: format_vars[k] for k in var_keys}
            policy_doc = _load_policy(policy_file, **vars_for_policy)
            arn = self._ensure_irsa_role(
                role_name=role_name,
                policy_name=policy_name,
                policy_doc=policy_doc,
                service_accounts=service_accounts,
            )
            arns[role_key] = arn
            for ns_sa in service_accounts:
                namespace, sa_name = ns_sa.split(":", 1)
                self._ensure_service_account(namespace=namespace, name=sa_name, role_arn=arn)

        return arns

    def ensure_cloud_resources(
        self,
        name: str = "system",
        instance_type: str = "m5.xlarge",
        desired: int = 1,
        min_size: int = 1,
        max_size: int = 3,
        subnets: str = "",
        node_role_arn: str = "",
        stateful: bool = False,
        disk_size: int = 100,
        disk_type: str = "gp3",
    ) -> str | None:
        """Returns the shared node security group ID, or None in dry-run mode."""
        vpc_id, cluster_sg_id = self._get_cluster_network_info()
        node_sg_id = self._ensure_node_security_group(vpc_id, cluster_sg_id)

        status = self._node_group_status(name)
        print(f"[aws] node group '{name}' status: {status or 'not found'}")

        if status == "ACTIVE":
            print(f"[aws] node group '{name}' already active — skipping")
            return node_sg_id

        if status == "CREATE_FAILED":
            print(f"[aws] node group '{name}' in CREATE_FAILED — deleting before retry")
            if not self.dry_run:
                self.eks.delete_nodegroup(clusterName=self.cluster_name, nodegroupName=name)
                self.eks.get_waiter("nodegroup_deleted").wait(
                    clusterName=self.cluster_name, nodegroupName=name
                )

        subnet_list = subnets.split(",") if subnets else self._discover_eks_subnets()

        # Stateful node groups are pinned to a single AZ to prevent EBS volume
        # node affinity conflicts when pods are rescheduled across AZs.
        if stateful and len(subnet_list) > 1:
            subnet_list = [subnet_list[0]]
            print(f"[aws] stateful=True — pinning node group to single AZ subnet: {subnet_list[0]}")

        role_arn = node_role_arn or self._ensure_node_role(f"{self.cluster_name}-node-role")

        print(
            f"[aws] creating node group '{name}' ({instance_type}, desired={desired}) in subnets: {subnet_list}"
        )

        if self.dry_run:
            print(f"[aws] dry-run: skipping node group creation")
            return None

        lt_id, lt_version = self._ensure_launch_template(
            name=f"{self.cluster_name}-{name}",
            disk_size=disk_size,
            disk_type=disk_type,
            security_group_ids=[node_sg_id],
        )

        self.eks.create_nodegroup(
            clusterName=self.cluster_name,
            nodegroupName=name,
            nodeRole=role_arn,
            subnets=subnet_list,
            instanceTypes=[instance_type],
            scalingConfig={"minSize": min_size, "maxSize": max_size, "desiredSize": desired},
            labels={"WorkerType": "ON_DEMAND", "nodegroup": name},
            tags={
                "karpenter.sh/discovery": self.cluster_name,
                "k8s.io/cluster-autoscaler/enabled": "true",
                f"k8s.io/cluster-autoscaler/{self.cluster_name}": "owned",
            },
            launchTemplate={"id": lt_id, "version": lt_version},
        )

        print(f"[aws] waiting for node group '{name}' to become active...")
        self.eks.get_waiter("nodegroup_active").wait(
            clusterName=self.cluster_name, nodegroupName=name
        )
        print(f"[aws] node group '{name}' is active")
        return node_sg_id

    def ensure_node_group(
        self,
        name,
        instance_type,
        desired,
        min_size,
        max_size,
        subnets,
        node_role_arn,
        stateful: bool = False,
        disk_size: int = 100,
    ) -> str | None:
        return self.ensure_cloud_resources(
            name=name,
            instance_type=instance_type,
            desired=desired,
            min_size=min_size,
            max_size=max_size,
            subnets=subnets,
            node_role_arn=node_role_arn,
            stateful=stateful,
            disk_size=disk_size,
        )

    def _ensure_service_account(self, namespace: str, name: str, role_arn: str) -> None:
        """Idempotently create a k8s ServiceAccount annotated with the IRSA role ARN."""
        check = subprocess.run(
            ["kubectl", "get", "serviceaccount", name, "-n", namespace],
            capture_output=True,
        )
        if check.returncode == 0:
            print(f"[aws] ServiceAccount {namespace}/{name} exists — patching annotation")
            if not self.dry_run:
                subprocess.run(
                    [
                        "kubectl",
                        "annotate",
                        "serviceaccount",
                        name,
                        "-n",
                        namespace,
                        f"eks.amazonaws.com/role-arn={role_arn}",
                        "--overwrite",
                    ],
                    check=True,
                )
            return

        print(f"[aws] creating ServiceAccount {namespace}/{name}")
        if self.dry_run:
            print(f"[aws] dry-run: skipping ServiceAccount creation")
            return

        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=subprocess.run(
                ["kubectl", "create", "namespace", namespace, "--dry-run=client", "-o", "yaml"],
                capture_output=True,
                check=True,
            ).stdout,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=subprocess.run(
                [
                    "kubectl",
                    "create",
                    "serviceaccount",
                    name,
                    "-n",
                    namespace,
                    "--dry-run=client",
                    "-o",
                    "yaml",
                ],
                capture_output=True,
                check=True,
            ).stdout,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            [
                "kubectl",
                "annotate",
                "serviceaccount",
                name,
                "-n",
                namespace,
                f"eks.amazonaws.com/role-arn={role_arn}",
                "--overwrite",
            ],
            check=True,
        )
        print(f"[aws] ServiceAccount {namespace}/{name} created")

    def _get_account_id(self) -> str:
        if not self._cached_account_id:
            self._cached_account_id = self.sts.get_caller_identity()["Account"]
        return self._cached_account_id

    def _get_oidc(self) -> tuple[str, str]:
        """Return (oidc_provider_arn, oidc_url_without_scheme) for the cluster."""
        if self._cached_oidc_arn:
            return self._cached_oidc_arn, self._cached_oidc_url

        cluster = self.eks.describe_cluster(name=self.cluster_name)
        issuer = cluster["cluster"]["identity"]["oidc"]["issuer"]
        oidc_url = issuer.replace("https://", "")

        aws_account_id = self._get_account_id()
        region = self.region
        oidc_id = oidc_url.split("/")[-1]
        arn = f"arn:aws:iam::{aws_account_id}:oidc-provider/oidc.eks.{region}.amazonaws.com/id/{oidc_id}"

        self._cached_oidc_arn = arn
        self._cached_oidc_url = oidc_url
        return arn, oidc_url

    def _get_cluster_network_info(self) -> tuple[str, str]:
        """Return (vpc_id, cluster_security_group_id) for the cluster."""
        if self._cached_vpc_id:
            return self._cached_vpc_id, self._cached_cluster_sg_id

        cluster = self.eks.describe_cluster(name=self.cluster_name)["cluster"]
        vpc_config = cluster["resourcesVpcConfig"]

        self._cached_vpc_id = vpc_config["vpcId"]
        self._cached_cluster_sg_id = vpc_config["clusterSecurityGroupId"]
        return self._cached_vpc_id, self._cached_cluster_sg_id

    def _authorize_ingress_idempotent(self, group_id: str, ip_permissions: list[dict]) -> None:
        """authorize_security_group_ingress, treating an already-present rule as success."""
        try:
            self.ec2.authorize_security_group_ingress(
                GroupId=group_id, IpPermissions=ip_permissions
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise
            print(f"[aws] ingress rule already present on {group_id} — skipping")

    def _ensure_node_security_group(self, vpc_id: str, cluster_sg_id: str) -> str:
        """Idempotently create the shared node security group and the
        cross-SG rules required for EKS managed node groups that use a
        distinct security group from the cluster's own — mirrors
        terraform-workspace-infra-aws/modules/workspace-cluster/eks.tf's
        node_security_group_additional_rules (self all-traffic, cluster<->node
        all-traffic in both directions). Both cross-SG directions must be
        all-traffic, not just kubelet ports — a single shared node SG gets
        that symmetric trust for free via self-reference; splitting it into
        two SGs means both directions have to be granted explicitly.
        """
        name = f"{self.cluster_name}-node"
        resp = self.ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": [name]},
            ]
        )
        if resp["SecurityGroups"]:
            sg_id = resp["SecurityGroups"][0]["GroupId"]
            print(f"[aws] node security group '{name}' exists — reusing {sg_id}")
            return sg_id

        print(f"[aws] creating node security group '{name}'")
        if self.dry_run:
            print(f"[aws] dry-run: skipping node security group creation")
            return f"sg-00000000000000000"

        sg_id = self.ec2.create_security_group(
            GroupName=name,
            Description=f"Shared node security group for {self.cluster_name}",
            VpcId=vpc_id,
            TagSpecifications=[
                {
                    "ResourceType": "security-group",
                    "Tags": [
                        {"Key": "Name", "Value": name},
                        {"Key": f"kubernetes.io/cluster/{self.cluster_name}", "Value": "owned"},
                        {"Key": "karpenter.sh/discovery", "Value": self.cluster_name},
                    ],
                }
            ],
        )["GroupId"]

        # ingress_self_all — node to node, all ports/protocols
        self._authorize_ingress_idempotent(
            sg_id, [{"IpProtocol": "-1", "UserIdGroupPairs": [{"GroupId": sg_id}]}]
        )
        # ingress_cluster_to_node_all_traffic — cluster API to nodegroup, all ports/protocols
        self._authorize_ingress_idempotent(
            sg_id, [{"IpProtocol": "-1", "UserIdGroupPairs": [{"GroupId": cluster_sg_id}]}]
        )
        # node -> cluster SG, all traffic. A plain EKS-auto-created cluster SG only
        # self-references by default, unlike a terraform-aws-modules/eks-managed
        # cluster SG. Scoping this to just kubelet ports (443, 1025-65535) is not
        # enough: any workload on the cluster SG (e.g. coredns) needs to receive
        # traffic from nodes on this separate SG too (e.g. DNS on port 53), which
        # a single shared node SG would get for free via self-reference.
        self._authorize_ingress_idempotent(
            cluster_sg_id, [{"IpProtocol": "-1", "UserIdGroupPairs": [{"GroupId": sg_id}]}]
        )

        print(f"[aws] node security group '{name}' created: {sg_id}")
        return sg_id

    def _ensure_irsa_role(
        self,
        role_name: str,
        policy_name: str,
        policy_doc: str,
        service_accounts: list[str],
    ) -> str:
        """Idempotently create an IRSA role and return its ARN."""
        oidc_arn, oidc_url = self._get_oidc()
        subjects = [f"system:serviceaccount:{sa}" for sa in service_accounts]
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Federated": oidc_arn},
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            f"{oidc_url}:sub": subjects,
                            f"{oidc_url}:aud": "sts.amazonaws.com",
                        }
                    },
                }
            ],
        }

        try:
            arn = self.iam.get_role(RoleName=role_name)["Role"]["Arn"]
            role_exists = True
            print(f"[aws] IRSA role '{role_name}' exists — updating trust policy")
        except self.iam.exceptions.NoSuchEntityException:
            role_exists = False
            arn = f"arn:aws:iam::000000000000:role/{role_name}"

        if self.dry_run:
            print(f"[aws] dry-run: skipping IRSA role/policy changes for '{role_name}'")
            return arn

        # _ensure_policy runs regardless of whether the role already exists —
        # an existing role does not imply its attached policy document is
        # still up to date (e.g. after editing iam/aws/*.json).
        if role_exists:
            self.iam.update_assume_role_policy(
                RoleName=role_name,
                PolicyDocument=json.dumps(trust),
            )
        else:
            print(f"[aws] creating IRSA role '{role_name}'")

        policy_arn = self._ensure_policy(policy_name, policy_doc)

        if not role_exists:
            arn = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust),
            )["Role"]["Arn"]
            print(f"[aws] IRSA role created: {arn}")

        self.iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        return arn

    def _ensure_policy(self, policy_name: str, policy_doc: str) -> str:
        """Idempotently create a customer-managed policy and return its ARN.

        If the policy already exists, its current default version is compared
        against `policy_doc` and a new version is pushed only when they
        differ — a resolved-but-unchanged document (e.g. same placeholders,
        different key order) must not create a spurious version, since IAM
        caps managed policies at 5 versions.
        """
        aws_account_id = self._get_account_id()
        policy_arn = f"arn:aws:iam::{aws_account_id}:policy/{policy_name}"
        try:
            policy = self.iam.get_policy(PolicyArn=policy_arn)["Policy"]
        except self.iam.exceptions.NoSuchEntityException:
            print(f"[aws] creating IAM policy '{policy_name}'")
            self.iam.create_policy(PolicyName=policy_name, PolicyDocument=policy_doc)
            return policy_arn

        current_doc = self.iam.get_policy_version(
            PolicyArn=policy_arn, VersionId=policy["DefaultVersionId"]
        )["PolicyVersion"]["Document"]

        if current_doc == json.loads(policy_doc):
            print(f"[aws] IAM policy '{policy_name}' already up to date")
            return policy_arn

        print(f"[aws] IAM policy '{policy_name}' exists but changed — pushing new version")
        self._prune_oldest_policy_version_if_at_limit(policy_arn)
        self.iam.create_policy_version(
            PolicyArn=policy_arn, PolicyDocument=policy_doc, SetAsDefault=True
        )
        return policy_arn

    def _prune_oldest_policy_version_if_at_limit(self, policy_arn: str) -> None:
        """IAM allows at most 5 versions per managed policy. Delete the oldest
        non-default version before pushing a new one if already at the cap."""
        versions = self.iam.list_policy_versions(PolicyArn=policy_arn)["Versions"]
        if len(versions) < 5:
            return
        oldest = min(
            (v for v in versions if not v["IsDefaultVersion"]),
            key=lambda v: v["CreateDate"],
        )
        print(f"[aws] policy at 5-version limit — deleting oldest version {oldest['VersionId']}")
        self.iam.delete_policy_version(PolicyArn=policy_arn, VersionId=oldest["VersionId"])

    def _node_group_status(self, name: str) -> str:
        try:
            resp = self.eks.describe_nodegroup(clusterName=self.cluster_name, nodegroupName=name)
            return resp["nodegroup"]["status"]
        except self.eks.exceptions.ResourceNotFoundException:
            return ""

    def _discover_eks_subnets(self) -> list[str]:
        """Return EKS data-plane subnets (RFC6598 100.64.0.0/10) sorted by AZ.

        The platform carves worker nodes and pods out of the secondary 100.64.0.0/16
        CIDR attached to the VPC, not the routable RFC1918 private subnets or public
        subnets. Sorting by AZ name keeps subnet[0] stable so stateful node groups
        always land in az1 alongside their gp3-az1 EBS volumes.
        """
        cluster = self.eks.describe_cluster(name=self.cluster_name)
        all_subnet_ids = cluster["cluster"]["resourcesVpcConfig"]["subnetIds"]

        resp = self.ec2.describe_subnets(SubnetIds=all_subnet_ids)
        eks_subnets = [s for s in resp["Subnets"] if s["CidrBlock"].startswith("100.")]

        if eks_subnets:
            sorted_subnets = [
                s["SubnetId"] for s in sorted(eks_subnets, key=lambda s: s["AvailabilityZone"])
            ]
            print(f"[aws] discovered EKS data-plane subnets (100.x): {sorted_subnets}")
            return sorted_subnets

        # Fallback for clusters not using the RFC6598 secondary CIDR pattern:
        # prefer private subnets that have a NAT gateway route over public subnets.
        nat_subnets = []
        for subnet in resp["Subnets"]:
            sid = subnet["SubnetId"]
            rtbs = self.ec2.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": [sid]}]
            )["RouteTables"]
            if not rtbs:
                rtbs = self.ec2.describe_route_tables(
                    Filters=[
                        {"Name": "vpc-id", "Values": [subnet["VpcId"]]},
                        {"Name": "association.main", "Values": ["true"]},
                    ]
                )["RouteTables"]
            for rtb in rtbs:
                if any(r.get("NatGatewayId") for r in rtb.get("Routes", [])):
                    nat_subnets.append(sid)
                    break

        if nat_subnets:
            print(f"[aws] discovered private subnets with NAT (fallback): {nat_subnets}")
            return nat_subnets

        raise RuntimeError(
            "Could not discover EKS data-plane subnets — no 100.x secondary-CIDR subnets "
            "and no private subnets with a NAT gateway route. "
            "Pass --node-group-subnets explicitly."
        )

    def _ensure_launch_template(
        self, name: str, disk_size: int, disk_type: str, security_group_ids: list[str]
    ) -> tuple[str, str]:
        resp = self.ec2.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [name]}]
        )
        if resp["LaunchTemplates"]:
            lt = resp["LaunchTemplates"][0]
            lt_id = lt["LaunchTemplateId"]
            lt_version = str(lt["DefaultVersionNumber"])
            print(f"[aws] launch template '{name}' already exists ({lt_id} v{lt_version})")
            return lt_id, lt_version

        print(f"[aws] creating launch template '{name}'")
        if self.dry_run:
            print(f"[aws] dry-run: skipping launch template creation")
            return "lt-00000000000000000", "1"

        resp = self.ec2.create_launch_template(
            LaunchTemplateName=name,
            LaunchTemplateData={
                # IMDSv2 required; hop limit 2 allows pods to reach instance metadata.
                "MetadataOptions": {
                    "HttpEndpoint": "enabled",
                    "HttpTokens": "required",
                    "HttpPutResponseHopLimit": 2,
                },
                "BlockDeviceMappings": [
                    {
                        "DeviceName": "/dev/xvda",
                        "Ebs": {
                            "VolumeSize": disk_size,
                            "VolumeType": disk_type,
                            "Encrypted": True,
                            "DeleteOnTermination": True,
                        },
                    }
                ],
                "SecurityGroupIds": security_group_ids,
            },
        )
        lt = resp["LaunchTemplate"]
        lt_id = lt["LaunchTemplateId"]
        lt_version = str(lt["DefaultVersionNumber"])
        print(f"[aws] launch template created: {lt_id} v{lt_version}")
        return lt_id, lt_version

    def _ensure_node_role(self, role_name: str) -> str:
        try:
            return self.iam.get_role(RoleName=role_name)["Role"]["Arn"]
        except self.iam.exceptions.NoSuchEntityException:
            pass

        print(f"[aws] creating IAM role '{role_name}'")
        if self.dry_run:
            print(f"[aws] dry-run: skipping role creation")
            return f"arn:aws:iam::000000000000:role/{role_name}"

        arn = self.iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )["Role"]["Arn"]

        for policy in [
            "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
            "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
            "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
        ]:
            self.iam.attach_role_policy(RoleName=role_name, PolicyArn=policy)

        return arn


def _load_policy(filename: str, **vars) -> str:
    path = os.path.normpath(os.path.join(_POLICY_DIR, filename))
    with open(path) as f:
        content = f.read()
    for key, value in vars.items():
        content = content.replace("{" + key + "}", value)
    unresolved = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", content)
    if unresolved:
        raise ValueError(f"Unresolved placeholders in {filename}: {unresolved}")
    json.loads(content)  # validate structure
    print(f"[aws] Loaded policy {filename}:\n", content)
    return content


def _irsa_set(key: str, arn: str) -> dict:
    return {key: arn} if arn else {}
