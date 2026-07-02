import pytest
from unittest.mock import MagicMock, patch
from cogrion_bootstrap.providers.aws import AWSProvider


def _make_provider(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    return AWSProvider(cluster_name="my-cluster", region="ap-southeast-1", dry_run=False)


def test_skips_node_group_if_already_active(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="ACTIVE")
    provider._discover_subnets = MagicMock()

    provider.ensure_node_group("system", "t3.medium", 2, 1, 4, "", "")

    provider.eks.create_nodegroup.assert_not_called()


def test_dry_run_skips_node_group_creation(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    provider = AWSProvider(cluster_name="my-cluster", region="ap-southeast-1", dry_run=True)
    provider._node_group_status = MagicMock(return_value="")
    provider._discover_subnets = MagicMock(return_value=["subnet-aaa"])
    provider._ensure_node_role = MagicMock(return_value="arn:aws:iam::000:role/r")

    provider.ensure_node_group("system", "t3.medium", 2, 1, 4, "", "")

    provider.eks.create_nodegroup.assert_not_called()


def test_creates_node_group_with_correct_params(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="")
    provider._discover_subnets = MagicMock(return_value=["subnet-aaa", "subnet-bbb"])
    provider._ensure_node_role = MagicMock(return_value="arn:aws:iam::123:role/node-role")
    provider.eks.get_waiter = MagicMock(return_value=MagicMock())

    provider.ensure_node_group("system", "t3.medium", 2, 1, 4, "", "")

    provider.eks.create_nodegroup.assert_called_once()
    call_kwargs = provider.eks.create_nodegroup.call_args.kwargs
    assert call_kwargs["clusterName"] == "my-cluster"
    assert call_kwargs["nodegroupName"] == "system"
    assert call_kwargs["instanceTypes"] == ["t3.medium"]
    assert call_kwargs["scalingConfig"] == {"minSize": 1, "maxSize": 4, "desiredSize": 2}


def test_uses_provided_subnets_without_discovery(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="")
    provider._discover_subnets = MagicMock()
    provider._ensure_node_role = MagicMock(return_value="arn:aws:iam::123:role/node-role")
    provider.eks.get_waiter = MagicMock(return_value=MagicMock())

    provider.ensure_node_group("system", "t3.medium", 2, 1, 4, "subnet-111,subnet-222", "")

    provider._discover_subnets.assert_not_called()
    call_kwargs = provider.eks.create_nodegroup.call_args.kwargs
    assert call_kwargs["subnets"] == ["subnet-111", "subnet-222"]
