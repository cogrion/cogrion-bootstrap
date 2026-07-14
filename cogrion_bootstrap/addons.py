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
    values_yaml: str = ""
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
    version="3.13.1",
    repo_name="metrics-server",
    repo_url="https://kubernetes-sigs.github.io/metrics-server",
    detect=("deployment", "metrics-server"),
)

EXTERNAL_SECRETS = HelmAddon(
    release_name="external-secrets",
    namespace="external-secrets",
    chart="external-secrets/external-secrets",
    version="2.7.0",
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
    version="1.1.0",
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

TRAEFIK_VERSION = "41.0.2"
TRAEFIK_NAMESPACE = "traefik"


# public_subnets: comma-separated subnet IDs tagged kubernetes.io/role/elb=1.
# AWS ELB/NLB subnet discovery requires either (a) the subnets carry the
# kubernetes.io/cluster/<name>=shared tag, or (b) they are explicitly listed
# via this annotation. We use the annotation because the tag key depends on the
# cluster name, which callers of this function may not carry.
def make_traefik(public_subnets: str) -> HelmAddon:
    return HelmAddon(
        release_name="traefik",
        namespace=TRAEFIK_NAMESPACE,
        chart="traefik/traefik",
        version=TRAEFIK_VERSION,
        repo_name="traefik",
        repo_url="https://traefik.github.io/charts",
        set_args={
            "service.annotations.service\\.beta\\.kubernetes\\.io/aws-load-balancer-subnets": public_subnets,
        },
        detect=("deployment", "traefik"),
    )


DNS_WEBHOOK_IMAGE = "public.ecr.aws/quantdata/cogrion/dns-webhook"
DNS_WEBHOOK_VERSION = "0.1.1"


# external-dns with the dns-webhook sidecar. The sidecar proxies the
# external-dns webhook provider protocol to the control-plane, which holds the
# Cloudflare token — it never reaches the tenant cluster.
_CREDENTIALS_SECRET = "cluster-agent-credentials"

_EXTERNAL_DNS_VALUES_TEMPLATE = """\
provider:
  name: webhook
  webhook:
    image:
      repository: {image}
      tag: "{tag}"
    env:
      - name: CONTROL_PLANE_URL
        value: "{control_plane_url}"
      - name: PORT
        value: "8888"
      - name: MTLS_CLIENT_CERT
        valueFrom:
          secretKeyRef:
            name: {secret}
            key: CPLANE_AGENT_MTLS_CLIENT_CERT
      - name: MTLS_CLIENT_KEY
        valueFrom:
          secretKeyRef:
            name: {secret}
            key: CPLANE_AGENT_MTLS_CLIENT_KEY
      - name: MTLS_CA_CERT
        valueFrom:
          secretKeyRef:
            name: {secret}
            key: CPLANE_AGENT_MTLS_CA_CERT
policy: sync
sources:
  - service
  - ingress
"""


def make_external_dns(control_plane_url: str, webhook_tag: str = DNS_WEBHOOK_VERSION) -> HelmAddon:
    return HelmAddon(
        release_name="external-dns",
        namespace="external-dns",
        chart="external-dns/external-dns",
        version="1.21.1",
        repo_name="external-dns",
        repo_url="https://kubernetes-sigs.github.io/external-dns",
        values_yaml=_EXTERNAL_DNS_VALUES_TEMPLATE.format(
            image=DNS_WEBHOOK_IMAGE,
            tag=webhook_tag,
            control_plane_url=control_plane_url,
            secret=_CREDENTIALS_SECRET,
        ),
        detect=("deployment", "external-dns"),
    )


def helm_repos_for(addons: list) -> dict[str, str]:
    return {
        a.repo_name: a.repo_url
        for a in addons
        if isinstance(a, HelmAddon) and a.repo_name and a.repo_url
    }
