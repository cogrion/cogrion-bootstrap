import boto3

from .base import BaseProvider


class AWSProvider(BaseProvider):
    def __init__(self, cluster_name: str, region: str, dry_run: bool):
        super().__init__(cluster_name=cluster_name, dry_run=dry_run)
        self.region = region
        self.eks = boto3.client("eks", region_name=region)
        self.ec2 = boto3.client("ec2", region_name=region)
        self.iam = boto3.client("iam")

    def ensure_node_group(
        self,
        name: str,
        instance_type: str,
        desired: int,
        min_size: int,
        max_size: int,
        subnets: str,
        node_role_arn: str,
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
                waiter = self.eks.get_waiter("nodegroup_deleted")
                waiter.wait(clusterName=self.cluster_name, nodegroupName=name)

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
        waiter = self.eks.get_waiter("nodegroup_active")
        waiter.wait(clusterName=self.cluster_name, nodegroupName=name)
        print(f"[aws] node group '{name}' is active")

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

        # Fall back to subnets with a NAT gateway route
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

    def _ensure_node_role(self, role_name: str) -> str:
        try:
            return self.iam.get_role(RoleName=role_name)["Role"]["Arn"]
        except self.iam.exceptions.NoSuchEntityException:
            pass

        print(f"[aws] creating IAM role '{role_name}'")
        if self.dry_run:
            print(f"[aws] dry-run: skipping role creation")
            return f"arn:aws:iam::000000000000:role/{role_name}"

        import json

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
