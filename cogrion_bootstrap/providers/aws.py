import json
import os
import subprocess

import boto3

from ..addons import HelmAddon, METRICS_SERVER, EXTERNAL_SECRETS
from .base import BaseProvider

_POLICY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "iam", "aws")

# Maps role key -> (policy file, format vars, trusted service accounts)
_IRSA_ROLES = {
    "bootstrap": (
        "bootstrap.json",
        ["platform_id"],
        ["cogrion-system:bootstrap-sa"],
    ),
    "cluster-agent": (
        "cluster-agent.json",
        ["platform_id", "account_id"],
        [
            "cogrion-system:cluster-agent-python-supervisor",
            "cogrion-system:cluster-agent-python-worker",
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
    def __init__(self, cluster_name: str, region: str, dry_run: bool):
        super().__init__(cluster_name=cluster_name, dry_run=dry_run)
        self.region = region
        self.eks = boto3.client("eks", region_name=region)
        self.ec2 = boto3.client("ec2", region_name=region)
        self.iam = boto3.client("iam")
        self.sts = boto3.client("sts")
        self._cached_oidc_arn: str = ""
        self._cached_oidc_url: str = ""
        self._cached_account_id: str = ""

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
        ]

    def ensure_iam(self) -> dict[str, str]:
        """Create all platform IRSA roles and their k8s ServiceAccounts.

        Returns a map of role-key -> IAM role ARN.
        """
        platform_id = self.cluster_name
        account_id = self._get_account_id()

        format_vars = {"platform_id": platform_id, "account_id": account_id}

        arns: dict[str, str] = {}
        for role_key, (policy_file, var_keys, service_accounts) in _IRSA_ROLES.items():
            role_name = f"{platform_id}-{role_key}-role"
            policy_name = f"{platform_id}-{role_key}-policy"
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
    ) -> None:
        status = self._node_group_status(name)
        print(f"[aws] node group '{name}' status: {status or 'not found'}")

        if status == "ACTIVE":
            print(f"[aws] node group '{name}' already active — skipping")
            return

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
            return

        lt_id, lt_version = self._ensure_launch_template(
            name=f"{self.cluster_name}-{name}",
            disk_size=disk_size,
            disk_type=disk_type,
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
    ):
        self.ensure_cloud_resources(
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
            print(f"[aws] ServiceAccount {namespace}/{name} already exists — skipping")
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

        account_id = self._get_account_id()
        region = self.region
        oidc_id = oidc_url.split("/")[-1]
        arn = (
            f"arn:aws:iam::{account_id}:oidc-provider/oidc.eks.{region}.amazonaws.com/id/{oidc_id}"
        )

        self._cached_oidc_arn = arn
        self._cached_oidc_url = oidc_url
        return arn, oidc_url

    def _ensure_irsa_role(
        self,
        role_name: str,
        policy_name: str,
        policy_doc: str,
        service_accounts: list[str],
    ) -> str:
        """Idempotently create an IRSA role and return its ARN."""
        try:
            arn = self.iam.get_role(RoleName=role_name)["Role"]["Arn"]
            print(f"[aws] IRSA role '{role_name}' already exists")
            return arn
        except self.iam.exceptions.NoSuchEntityException:
            pass

        print(f"[aws] creating IRSA role '{role_name}'")
        if self.dry_run:
            print(f"[aws] dry-run: skipping IRSA role creation")
            return f"arn:aws:iam::000000000000:role/{role_name}"

        policy_arn = self._ensure_policy(policy_name, policy_doc)
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

        arn = self.iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
        )[
            "Role"
        ]["Arn"]

        self.iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        print(f"[aws] IRSA role created: {arn}")
        return arn

    def _ensure_policy(self, policy_name: str, policy_doc: str) -> str:
        """Idempotently create a customer-managed policy and return its ARN."""
        account_id = self._get_account_id()
        policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
        try:
            self.iam.get_policy(PolicyArn=policy_arn)
            print(f"[aws] IAM policy '{policy_name}' already exists")
            return policy_arn
        except self.iam.exceptions.NoSuchEntityException:
            pass

        print(f"[aws] creating IAM policy '{policy_name}'")
        self.iam.create_policy(PolicyName=policy_name, PolicyDocument=policy_doc)
        return policy_arn

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

    def _ensure_launch_template(self, name: str, disk_size: int, disk_type: str) -> tuple[str, str]:
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
    return content


def _irsa_set(key: str, arn: str) -> dict:
    return {key: arn} if arn else {}
