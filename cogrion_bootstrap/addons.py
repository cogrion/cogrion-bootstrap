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


def helm_repos_for(addons: list) -> dict[str, str]:
    return {
        a.repo_name: a.repo_url
        for a in addons
        if isinstance(a, HelmAddon) and a.repo_name and a.repo_url
    }
