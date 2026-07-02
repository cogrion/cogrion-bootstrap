from dataclasses import dataclass, field


@dataclass
class Addon:
    release_name: str
    namespace: str
    chart: str
    version: str
    default_set_args: dict = field(default_factory=dict)

    def extra_set_args(self, cluster_name: str, region: str, vpc_id: str, irsa_arn: str) -> dict:
        return {}


@dataclass
class ClusterAutoscaler(Addon):
    def extra_set_args(self, cluster_name, region, vpc_id, irsa_arn):
        args = {
            "autoDiscovery.clusterName": cluster_name,
            "awsRegion": region,
        }
        if irsa_arn:
            args["serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"] = irsa_arn
        return args


@dataclass
class EfsCsiDriver(Addon):
    def extra_set_args(self, cluster_name, region, vpc_id, irsa_arn):
        if irsa_arn:
            return {
                "controller.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn": irsa_arn
            }
        return {}


@dataclass
class ExternalSecrets(Addon):
    def extra_set_args(self, cluster_name, region, vpc_id, irsa_arn):
        if irsa_arn:
            return {"serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn": irsa_arn}
        return {}


@dataclass
class AlbController(Addon):
    def extra_set_args(self, cluster_name, region, vpc_id, irsa_arn):
        args = {
            "clusterName": cluster_name,
            "vpcId": vpc_id,
            "podDisruptionBudget.maxUnavailable": "1",
            "enableServiceMutatorWebhook": "false",
        }
        if irsa_arn:
            args["serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"] = irsa_arn
        return args


ADDONS: list[Addon] = [
    ClusterAutoscaler(
        release_name="cluster-autoscaler",
        namespace="kube-system",
        chart="autoscaler/cluster-autoscaler",
        version="9.57.0",
    ),
    EfsCsiDriver(
        release_name="aws-efs-csi-driver",
        namespace="kube-system",
        chart="efs-csi-driver/aws-efs-csi-driver",
        version="",
    ),
    Addon(
        release_name="metrics-server",
        namespace="kube-system",
        chart="metrics-server/metrics-server",
        version="",
    ),
    AlbController(
        release_name="aws-load-balancer-controller",
        namespace="kube-system",
        chart="eks/aws-load-balancer-controller",
        version="",
    ),
    ExternalSecrets(
        release_name="external-secrets",
        namespace="external-secrets",
        chart="external-secrets/external-secrets",
        version="",
    ),
]

# Helm repo → URL mapping for the charts above
HELM_REPOS: dict[str, str] = {
    "autoscaler": "https://kubernetes.github.io/autoscaler",
    "efs-csi-driver": "https://kubernetes-sigs.github.io/aws-efs-csi-driver",
    "metrics-server": "https://kubernetes-sigs.github.io/metrics-server",
    "eks": "https://aws.github.io/eks-charts",
    "external-secrets": "https://charts.external-secrets.io",
}
