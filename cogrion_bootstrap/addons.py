from dataclasses import dataclass, field


@dataclass
class HelmAddon:
    release_name: str
    namespace: str
    chart: str
    version: str = ""
    repo_name: str | None = None
    repo_url: str | None = None
    set_args: dict = field(default_factory=dict)
    detect: tuple[str, str] | None = None


@dataclass
class KubectlAddon:
    release_name: str
    namespace: str
    manifest_url: str
    detect: tuple[str, str] | None = None


# Shared addons with no cloud-specific config — providers import and use these directly
METRICS_SERVER = HelmAddon(
    release_name="metrics-server",
    namespace="kube-system",
    chart="metrics-server/metrics-server",
    repo_name="metrics-server",
    repo_url="https://kubernetes-sigs.github.io/metrics-server",
    detect=("deployment", "metrics-server"),
)

EXTERNAL_SECRETS = HelmAddon(
    release_name="external-secrets",
    namespace="external-secrets",
    chart="external-secrets/external-secrets",
    repo_name="external-secrets",
    repo_url="https://charts.external-secrets.io",
    detect=("deployment", "external-secrets"),
)

# Scales the coredns Deployment with cluster size (nodes/cores) so DNS lookups
# don't start timing out once a workload-heavy node group (e.g. observability)
# is added on top of the EKS addon's static 2-replica coredns default.
CLUSTER_PROPORTIONAL_AUTOSCALER = HelmAddon(
    release_name="cluster-proportional-autoscaler",
    namespace="kube-system",
    chart="cluster-proportional-autoscaler/cluster-proportional-autoscaler",
    repo_name="cluster-proportional-autoscaler",
    repo_url="https://kubernetes-sigs.github.io/cluster-proportional-autoscaler",
    set_args={
        "options.target": "deployment/coredns",
        # linear mode: replicas = clamp(nodes / nodesPerReplica, min, max).
        # nodesPerReplica=2 so a burst like the observability node group
        # (4 new nodes) roughly doubles coredns capacity ahead of the new
        # pods' DNS lookups, instead of staying fixed at 2 replicas.
        "config.linear.nodesPerReplica": "2",
        "config.linear.min": "2",
        "config.linear.max": "10",
    },
    detect=("deployment", "cluster-proportional-autoscaler"),
)


def helm_repos_for(addons: list) -> dict[str, str]:
    return {
        a.repo_name: a.repo_url
        for a in addons
        if isinstance(a, HelmAddon) and a.repo_name and a.repo_url
    }
