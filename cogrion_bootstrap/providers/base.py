from abc import ABC, abstractmethod


class BaseProvider(ABC):
    def __init__(
        self, ext_account_id: str, ext_workspace_id: str, cluster_name: str, dry_run: bool
    ):
        self.ext_account_id = ext_account_id
        self.ext_workspace_id = ext_workspace_id
        self.cluster_name = cluster_name
        self.dry_run = dry_run

    @abstractmethod
    def addons(self) -> list:
        """Return the ordered list of HelmAddon/KubectlAddon for this provider."""

    def ensure_cloud_resources(self, **kwargs) -> None:
        """Provision cloud-level resources (node groups, VPCs, etc). Override per provider."""

    def ensure_iam(self, **kwargs) -> None:
        """Provision IAM roles and policies. Override per provider."""
