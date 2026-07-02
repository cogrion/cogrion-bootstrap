import json
import pytest
from unittest.mock import MagicMock, call
from cogrion_bootstrap.providers.aws import AWSProvider, _IRSA_ROLES


def _make_provider(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    return AWSProvider(cluster_name="my-cluster", region="ap-southeast-1", dry_run=False)


def test_skips_node_group_if_already_active(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="ACTIVE")
    provider._discover_eks_subnets = MagicMock()

    provider.ensure_node_group("system", "m5.xlarge", 1, 1, 3, "", "")

    provider.eks.create_nodegroup.assert_not_called()


def test_dry_run_skips_node_group_creation(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    provider = AWSProvider(cluster_name="my-cluster", region="ap-southeast-1", dry_run=True)
    provider._node_group_status = MagicMock(return_value="")
    provider._discover_eks_subnets = MagicMock(return_value=["subnet-aaa"])
    provider._ensure_node_role = MagicMock(return_value="arn:aws:iam::000:role/r")

    provider.ensure_node_group("system", "m5.xlarge", 1, 1, 3, "", "")

    provider.eks.create_nodegroup.assert_not_called()


def test_creates_node_group_with_correct_params(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="")
    provider._discover_eks_subnets = MagicMock(return_value=["subnet-aaa", "subnet-bbb"])
    provider._ensure_node_role = MagicMock(return_value="arn:aws:iam::123:role/node-role")
    provider._ensure_launch_template = MagicMock(return_value=("lt-abc123", "1"))
    provider.eks.get_waiter = MagicMock(return_value=MagicMock())

    provider.ensure_node_group("system", "m5.xlarge", 1, 1, 3, "", "")

    provider.eks.create_nodegroup.assert_called_once()
    call_kwargs = provider.eks.create_nodegroup.call_args.kwargs
    assert call_kwargs["clusterName"] == "my-cluster"
    assert call_kwargs["nodegroupName"] == "system"
    assert call_kwargs["instanceTypes"] == ["m5.xlarge"]
    assert call_kwargs["scalingConfig"] == {"minSize": 1, "maxSize": 3, "desiredSize": 1}
    assert call_kwargs["labels"] == {"WorkerType": "ON_DEMAND", "nodegroup": "system"}
    assert call_kwargs["tags"]["karpenter.sh/discovery"] == "my-cluster"
    assert call_kwargs["tags"]["k8s.io/cluster-autoscaler/enabled"] == "true"
    assert call_kwargs["launchTemplate"] == {"id": "lt-abc123", "version": "1"}


def test_uses_provided_subnets_without_discovery(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="")
    provider._discover_eks_subnets = MagicMock()
    provider._ensure_node_role = MagicMock(return_value="arn:aws:iam::123:role/node-role")
    provider._ensure_launch_template = MagicMock(return_value=("lt-abc123", "1"))
    provider.eks.get_waiter = MagicMock(return_value=MagicMock())

    provider.ensure_node_group("system", "m5.xlarge", 1, 1, 3, "subnet-111,subnet-222", "")

    provider._discover_eks_subnets.assert_not_called()
    call_kwargs = provider.eks.create_nodegroup.call_args.kwargs
    assert call_kwargs["subnets"] == ["subnet-111", "subnet-222"]


def test_stateful_node_group_pins_to_first_subnet(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="")
    provider._discover_eks_subnets = MagicMock(return_value=["subnet-az1", "subnet-az2"])
    provider._ensure_node_role = MagicMock(return_value="arn:aws:iam::123:role/node-role")
    provider._ensure_launch_template = MagicMock(return_value=("lt-abc123", "1"))
    provider.eks.get_waiter = MagicMock(return_value=MagicMock())

    provider.ensure_node_group("stateful", "m5.xlarge", 1, 1, 3, "", "", stateful=True)

    call_kwargs = provider.eks.create_nodegroup.call_args.kwargs
    assert call_kwargs["subnets"] == ["subnet-az1"]


def test_discover_eks_subnets_prefers_rfc6598(mocker):
    provider = _make_provider(mocker)
    provider.eks.describe_cluster = MagicMock(
        return_value={
            "cluster": {
                "resourcesVpcConfig": {"subnetIds": ["subnet-priv", "subnet-eks1", "subnet-eks2"]}
            }
        }
    )
    provider.ec2.describe_subnets = MagicMock(
        return_value={
            "Subnets": [
                {
                    "SubnetId": "subnet-priv",
                    "CidrBlock": "10.42.1.0/24",
                    "AvailabilityZone": "ap-southeast-1a",
                },
                {
                    "SubnetId": "subnet-eks1",
                    "CidrBlock": "100.64.0.0/17",
                    "AvailabilityZone": "ap-southeast-1a",
                },
                {
                    "SubnetId": "subnet-eks2",
                    "CidrBlock": "100.64.128.0/17",
                    "AvailabilityZone": "ap-southeast-1b",
                },
            ]
        }
    )

    result = provider._discover_eks_subnets()

    assert result == ["subnet-eks1", "subnet-eks2"]
    provider.ec2.describe_route_tables.assert_not_called()


def test_discover_eks_subnets_falls_back_to_nat(mocker):
    provider = _make_provider(mocker)
    provider.eks.describe_cluster = MagicMock(
        return_value={"cluster": {"resourcesVpcConfig": {"subnetIds": ["subnet-priv"]}}}
    )
    provider.ec2.describe_subnets = MagicMock(
        return_value={
            "Subnets": [
                {
                    "SubnetId": "subnet-priv",
                    "CidrBlock": "10.42.1.0/24",
                    "AvailabilityZone": "ap-southeast-1a",
                    "VpcId": "vpc-123",
                },
            ]
        }
    )
    provider.ec2.describe_route_tables = MagicMock(
        return_value={"RouteTables": [{"Routes": [{"NatGatewayId": "nat-abc"}]}]}
    )

    result = provider._discover_eks_subnets()

    assert result == ["subnet-priv"]


def test_discover_eks_subnets_raises_when_none_found(mocker):
    provider = _make_provider(mocker)
    provider.eks.describe_cluster = MagicMock(
        return_value={"cluster": {"resourcesVpcConfig": {"subnetIds": ["subnet-pub"]}}}
    )
    provider.ec2.describe_subnets = MagicMock(
        return_value={
            "Subnets": [
                {
                    "SubnetId": "subnet-pub",
                    "CidrBlock": "10.42.0.0/25",
                    "AvailabilityZone": "ap-southeast-1a",
                    "VpcId": "vpc-123",
                },
            ]
        }
    )
    provider.ec2.describe_route_tables = MagicMock(return_value={"RouteTables": [{"Routes": []}]})

    with pytest.raises(RuntimeError, match="Pass --node-group-subnets"):
        provider._discover_eks_subnets()


def test_ensure_irsa_role_skips_if_exists(mocker):
    provider = _make_provider(mocker)
    provider.iam.get_role = MagicMock(
        return_value={"Role": {"Arn": "arn:aws:iam::123:role/existing"}}
    )

    arn = provider._ensure_irsa_role("existing-role", "existing-policy", "{}", ["ns:sa"])

    assert arn == "arn:aws:iam::123:role/existing"
    provider.iam.create_role.assert_not_called()


def test_ensure_irsa_role_creates_role_and_policy(mocker):
    provider = _make_provider(mocker)
    not_found = Exception("NoSuchEntity")
    provider.iam.exceptions.NoSuchEntityException = type(not_found)
    provider.iam.get_role = MagicMock(side_effect=not_found)
    provider.iam.get_policy = MagicMock(side_effect=not_found)
    provider._get_account_id = MagicMock(return_value="123456789012")
    provider._get_oidc = MagicMock(
        return_value=(
            "arn:aws:iam::123456789012:oidc-provider/oidc.eks.ap-southeast-1.amazonaws.com/id/ABCD",
            "oidc.eks.ap-southeast-1.amazonaws.com/id/ABCD",
        )
    )
    provider.iam.create_role = MagicMock(
        return_value={"Role": {"Arn": "arn:aws:iam::123:role/new-role"}}
    )

    arn = provider._ensure_irsa_role(
        role_name="new-role",
        policy_name="new-policy",
        policy_doc='{"Version": "2012-10-17", "Statement": []}',
        service_accounts=["cogrion-system:bootstrap-sa"],
    )

    assert arn == "arn:aws:iam::123:role/new-role"
    provider.iam.create_policy.assert_called_once()
    provider.iam.create_role.assert_called_once()
    trust = json.loads(provider.iam.create_role.call_args.kwargs["AssumeRolePolicyDocument"])
    assert trust["Statement"][0]["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert "system:serviceaccount:cogrion-system:bootstrap-sa" in (
        trust["Statement"][0]["Condition"]["StringEquals"][
            "oidc.eks.ap-southeast-1.amazonaws.com/id/ABCD:sub"
        ]
    )
    provider.iam.attach_role_policy.assert_called_once()


def test_ensure_irsa_role_dry_run_skips_creation(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    provider = AWSProvider(cluster_name="my-cluster", region="ap-southeast-1", dry_run=True)
    not_found = Exception("NoSuchEntity")
    provider.iam.exceptions.NoSuchEntityException = type(not_found)
    provider.iam.get_role = MagicMock(side_effect=not_found)

    arn = provider._ensure_irsa_role("r", "p", "{}", ["ns:sa"])

    assert "000000000000" in arn
    provider.iam.create_role.assert_not_called()


def test_ensure_iam_creates_all_roles(mocker):
    provider = _make_provider(mocker)
    provider._get_account_id = MagicMock(return_value="123456789012")
    provider._ensure_irsa_role = MagicMock(side_effect=lambda role_name, **kw: f"arn::{role_name}")
    provider._ensure_service_account = MagicMock()

    arns = provider.ensure_iam()

    assert set(arns.keys()) == set(_IRSA_ROLES.keys())
    assert provider._ensure_irsa_role.call_count == len(_IRSA_ROLES)
    assert provider._ensure_service_account.call_count == sum(
        len(sas) for _, _, sas in _IRSA_ROLES.values()
    )


def test_ensure_iam_passes_correct_service_accounts(mocker):
    provider = _make_provider(mocker)
    provider._get_account_id = MagicMock(return_value="123456789012")
    provider._ensure_service_account = MagicMock()
    calls_made = {}

    def capture(role_name, policy_name, policy_doc, service_accounts):
        calls_made[role_name] = service_accounts
        return f"arn::{role_name}"

    provider._ensure_irsa_role = MagicMock(side_effect=capture)
    provider.ensure_iam()

    assert "cogrion-system:bootstrap-sa" in calls_made["my-cluster-bootstrap-role"]
    assert (
        "cogrion-system:cluster-agent-python-supervisor"
        in calls_made["my-cluster-cluster-agent-role"]
    )
    assert (
        "cogrion-system:cluster-agent-python-worker" in calls_made["my-cluster-cluster-agent-role"]
    )
    assert "kb-system:kubeblocks" in calls_made["my-cluster-kubeblocks-role"]
