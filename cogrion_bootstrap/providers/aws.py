import json

import boto3

from ..addons import HelmAddon, METRICS_SERVER, EXTERNAL_SECRETS
from .base import BaseProvider


class AWSProvider(BaseProvider):
    def __init__(self, cluster_name: str, region: str, dry_run: bool):
        super().__init__(cluster_name=cluster_name, dry_run=dry_run)
        self.region = region
        self.eks = boto3.client("eks", region_name=region)
        self.ec2 = boto3.client("ec2", region_name=region)
        self.iam = boto3.client("iam")

    def addons(
        self,
        cluster_autoscaler_role_arn: str = "",
        efs_csi_driver_role_arn: str = "",
        external_secrets_role_arn: str = "",
        alb_controller_role_arn: str = "",
        vpc_id: str = "",
    ) -> list:
        if not cluster_autoscaler_role_arn:
            cluster_autoscaler_role_arn = self._ensure_irsa_role(
                role_name=f"{self.cluster_name}-cluster-autoscaler",
                namespace="kube-system",
                service_account="cluster-autoscaler-aws-cluster-autoscaler",
                policy_statements=[
                    {
                        "Effect": "Allow",
                        "Action": [
                            "autoscaling:DescribeAutoScalingGroups",
                            "autoscaling:DescribeAutoScalingInstances",
                            "autoscaling:DescribeLaunchConfigurations",
                            "autoscaling:DescribeScalingActivities",
                            "autoscaling:DescribeTags",
                            "autoscaling:SetDesiredCapacity",
                            "autoscaling:TerminateInstanceInAutoScalingGroup",
                            "ec2:DescribeLaunchTemplateVersions",
                            "ec2:DescribeInstanceTypes",
                            "eks:DescribeNodegroup",
                        ],
                        "Resource": "*",
                    }
                ],
            )

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
                    {
                        "rbac.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn": cluster_autoscaler_role_arn
                    }
                    if cluster_autoscaler_role_arn
                    else {}
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
                    {
                        "controller.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn": efs_csi_driver_role_arn
                    }
                    if efs_csi_driver_role_arn
                    else {}
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
                    {
                        "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn": alb_controller_role_arn
                    }
                    if alb_controller_role_arn
                    else {}
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
                    {
                        "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn": external_secrets_role_arn
                    }
                    if external_secrets_role_arn
                    else {}
                ),
            },
        )

        return [
            cluster_autoscaler,
            efs_csi_driver,
            METRICS_SERVER,
            alb_controller,
            external_secrets,
        ]

    def ensure_cloud_resources(
        self,
        name: str = "system",
        instance_type: str = "t3.medium",
        desired: int = 2,
        min_size: int = 1,
        max_size: int = 4,
        subnets: str = "",
        node_role_arn: str = "",
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

        subnet_list = subnets.split(",") if subnets else self._discover_subnets()
        role_arn = node_role_arn or self._ensure_node_role(f"{self.cluster_name}-node-role")

        print(
            f"[aws] creating node group '{name}' ({instance_type}, desired={desired}) in subnets: {subnet_list}"
        )

        if self.dry_run:
            print(f"[aws] dry-run: skipping node group creation")
            return

        self.eks.create_nodegroup(
            clusterName=self.cluster_name,
            nodegroupName=name,
            nodeRole=role_arn,
            subnets=subnet_list,
            instanceTypes=[instance_type],
            scalingConfig={"minSize": min_size, "maxSize": max_size, "desiredSize": desired},
            labels={"nodegroup": name},
        )

        print(f"[aws] waiting for node group '{name}' to become active...")
        self.eks.get_waiter("nodegroup_active").wait(
            clusterName=self.cluster_name, nodegroupName=name
        )
        print(f"[aws] node group '{name}' is active")

    def ensure_node_group(
        self, name, instance_type, desired, min_size, max_size, subnets, node_role_arn
    ):
        self.ensure_cloud_resources(
            name=name,
            instance_type=instance_type,
            desired=desired,
            min_size=min_size,
            max_size=max_size,
            subnets=subnets,
            node_role_arn=node_role_arn,
        )

    def ensure_iam(self, role_name: str = "") -> None:
        self._ensure_node_role(role_name or f"{self.cluster_name}-node-role")

    def _node_group_status(self, name: str) -> str:
        try:
            resp = self.eks.describe_nodegroup(clusterName=self.cluster_name, nodegroupName=name)
            return resp["nodegroup"]["status"]
        except self.eks.exceptions.ResourceNotFoundException:
            return ""

    def _discover_subnets(self) -> list[str]:
        cluster = self.eks.describe_cluster(name=self.cluster_name)
        all_subnets = cluster["cluster"]["resourcesVpcConfig"]["subnetIds"]

        resp = self.ec2.describe_subnets(SubnetIds=all_subnets)
        public = [s["SubnetId"] for s in resp["Subnets"] if s.get("MapPublicIpOnLaunch")]
        if public:
            print(f"[aws] discovered public subnets: {public}")
            return public

        nat_subnets = []
        for subnet in all_subnets:
            rtbs = self.ec2.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet]}]
            )["RouteTables"]
            if not rtbs:
                vpc_id = resp["Subnets"][0]["VpcId"]
                rtbs = self.ec2.describe_route_tables(
                    Filters=[
                        {"Name": "vpc-id", "Values": [vpc_id]},
                        {"Name": "association.main", "Values": ["true"]},
                    ]
                )["RouteTables"]
            for rtb in rtbs:
                if any(r.get("NatGatewayId") for r in rtb.get("Routes", [])):
                    nat_subnets.append(subnet)
                    break

        if nat_subnets:
            print(f"[aws] discovered private subnets with NAT: {nat_subnets}")
            return nat_subnets

        raise RuntimeError(
            "Could not discover usable subnets — no public subnets and no private subnets with a NAT gateway route. "
            "Pass --node-group-subnets explicitly."
        )

    def _ensure_oidc_provider(self) -> str:
        cluster = self.eks.describe_cluster(name=self.cluster_name)["cluster"]
        issuer_url = cluster["identity"]["oidc"]["issuer"]
        issuer_host = issuer_url.replace("https://", "")
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        provider_arn = f"arn:aws:iam::{account_id}:oidc-provider/{issuer_host}"

        try:
            self.iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider_arn)
            return provider_arn
        except self.iam.exceptions.NoSuchEntityException:
            pass

        print(f"[aws] no OIDC provider found for cluster — associating '{issuer_host}'")
        if self.dry_run:
            print(f"[aws] dry-run: skipping OIDC provider creation")
            return provider_arn

        import ssl
        import urllib.request

        cert = ssl.get_server_certificate((issuer_host.split("/")[0], 443))
        thumbprint = (
            __import__("hashlib")
            .sha1(ssl.PEM_cert_to_DER_cert(cert))
            .hexdigest()
        )
        self.iam.create_open_id_connect_provider(
            Url=issuer_url,
            ClientIDList=["sts.amazonaws.com"],
            ThumbprintList=[thumbprint],
        )
        return provider_arn

    def _ensure_irsa_role(
        self,
        role_name: str,
        namespace: str,
        service_account: str,
        policy_statements: list,
    ) -> str:
        try:
            existing = self.iam.get_role(RoleName=role_name)["Role"]["Arn"]
            return existing
        except self.iam.exceptions.NoSuchEntityException:
            pass

        if self.dry_run:
            print(f"[aws] dry-run: skipping IRSA role creation for '{role_name}'")
            return f"arn:aws:iam::000000000000:role/{role_name}"

        oidc_provider_arn = self._ensure_oidc_provider()
        issuer_host = oidc_provider_arn.split("/", 1)[1]

        print(f"[aws] creating IRSA role '{role_name}' for {namespace}/{service_account}")
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Federated": oidc_provider_arn},
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            f"{issuer_host}:sub": f"system:serviceaccount:{namespace}:{service_account}",
                            f"{issuer_host}:aud": "sts.amazonaws.com",
                        }
                    },
                }
            ],
        }

        role_arn = self.iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy),
        )["Role"]["Arn"]

        self.iam.put_role_policy(
            RoleName=role_name,
            PolicyName=f"{role_name}-policy",
            PolicyDocument=json.dumps(
                {"Version": "2012-10-17", "Statement": policy_statements}
            ),
        )

        return role_arn

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