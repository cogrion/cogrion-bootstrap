from abc import ABC, abstractmethod


class BaseProvider(ABC):
    def __init__(self, cluster_name: str, dry_run: bool):
        self.cluster_name = cluster_name
        self.dry_run = dry_run

    @abstractmethod
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
        pass
