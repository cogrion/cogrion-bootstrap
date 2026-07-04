import json
import pytest
from unittest.mock import MagicMock, call
from cogrion_bootstrap.providers.aws import AWSProvider, _IRSA_ROLES, _load_policy


def _make_provider(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    return AWSProvider(
        ext_account_id="111122223333",
        ext_workspace_id="w-test01",
        cluster_name="my-cluster",
        region="ap-southeast-1",
        dry_run=False,
    )


def test_skips_node_group_if_already_active(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="ACTIVE")
    provider._discover_eks_subnets = MagicMock()

    provider.ensure_node_group("system", "m5.xlarge", 1, 1, 3, "", "")

    provider.eks.create_nodegroup.assert_not_called()


def test_dry_run_skips_node_group_creation(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    provider = AWSProvider(
        ext_account_id="111122223333",
        ext_workspace_id="w-test01",
        cluster_name="my-cluster",
        region="ap-southeast-1",
        dry_run=True,
    )
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
    provider._ensure_policy = MagicMock(return_value="arn:aws:iam::123:policy/existing-policy")

    arn = provider._ensure_irsa_role("existing-role", "existing-policy", "{}", ["ns:sa"])

    assert arn == "arn:aws:iam::123:role/existing"
    provider.iam.create_role.assert_not_called()


def test_ensure_irsa_role_updates_policy_even_if_role_already_exists(mocker):
    """Reproduces the reported bug: when an IRSA role already exists,
    _ensure_irsa_role updates only the trust policy and returns early — it
    never calls _ensure_policy, so IAM policy content changes (e.g. edited
    iam/aws/*.json, or a changed {ext_account_id} placeholder) never reach
    AWS on any bootstrap run after the very first one that created the role."""
    provider = _make_provider(mocker)
    provider.iam.get_role = MagicMock(
        return_value={"Role": {"Arn": "arn:aws:iam::123:role/existing"}}
    )
    provider._ensure_policy = MagicMock(return_value="arn:aws:iam::123:policy/existing-policy")

    arn = provider._ensure_irsa_role(
        "existing-role", "existing-policy", '{"Statement": []}', ["ns:sa"]
    )

    assert arn == "arn:aws:iam::123:role/existing"
    provider._ensure_policy.assert_called_once_with("existing-policy", '{"Statement": []}')
    provider.iam.attach_role_policy.assert_called_once_with(
        RoleName="existing-role", PolicyArn="arn:aws:iam::123:policy/existing-policy"
    )


def test_ensure_irsa_role_dry_run_skips_policy_update_when_role_exists(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    provider = AWSProvider(
        ext_account_id="111122223333",
        ext_workspace_id="w-test01",
        cluster_name="my-cluster",
        region="ap-southeast-1",
        dry_run=True,
    )
    provider.iam.get_role = MagicMock(
        return_value={"Role": {"Arn": "arn:aws:iam::123:role/existing"}}
    )
    provider._ensure_policy = MagicMock()

    arn = provider._ensure_irsa_role("existing-role", "existing-policy", "{}", ["ns:sa"])

    assert arn == "arn:aws:iam::123:role/existing"
    provider.iam.update_assume_role_policy.assert_not_called()
    provider._ensure_policy.assert_not_called()
    provider.iam.attach_role_policy.assert_not_called()


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
    provider = AWSProvider(
        ext_account_id="111122223333",
        ext_workspace_id="w-test01",
        cluster_name="my-cluster",
        region="ap-southeast-1",
        dry_run=True,
    )
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

    assert "cogrion-system:bootstrap-sa" in calls_made["w-test01-bootstrap-role"]
    assert "cogrion-system:cplane-agent" in calls_made["w-test01-cluster-agent-role"]
    assert "kb-system:kubeblocks" in calls_made["w-test01-kubeblocks-role"]


def test_load_policy_substitutes_all_vars(mocker):
    doc = _load_policy("bootstrap.json", ext_workspace_id="w-test01")

    assert "{ext_workspace_id}" not in doc
    assert "w-test01" in doc
    json.loads(doc)  # must remain valid JSON


def test_load_policy_raises_on_unresolved_placeholder(mocker):
    with pytest.raises(ValueError, match="Unresolved placeholders"):
        _load_policy("bootstrap.json")  # ext_workspace_id not passed


def test_load_policy_cluster_agent_substitutes_all_vars():
    doc = _load_policy(
        "cluster-agent.json",
        ext_workspace_id="w-test01",
        ext_account_id="devlocalaws",
    )

    assert "{ext_workspace_id}" not in doc
    assert "{ext_account_id}" not in doc
    json.loads(doc)


def test_addons_returns_helm_addon_list(mocker):
    provider = _make_provider(mocker)

    addons = provider.addons(irsa_arns={}, vpc_id="vpc-123")

    assert isinstance(addons, list)
    assert len(addons) > 0
    release_names = [a.release_name for a in addons]
    assert "aws-efs-csi-driver" in release_names
    assert "aws-load-balancer-controller" in release_names


def test_addons_sets_alb_cluster_and_vpc(mocker):
    provider = _make_provider(mocker)

    addons = provider.addons(irsa_arns={}, vpc_id="vpc-abc")

    alb = next(a for a in addons if a.release_name == "aws-load-balancer-controller")
    assert alb.set_args["clusterName"] == "my-cluster"
    assert alb.set_args["vpcId"] == "vpc-abc"


def test_addons_injects_irsa_arn_for_efs(mocker):
    provider = _make_provider(mocker)
    irsa_arns = {"efs-csi-driver": "arn:aws:iam::123:role/efs-role"}

    addons = provider.addons(irsa_arns=irsa_arns, vpc_id="vpc-123")

    efs = next(a for a in addons if a.release_name == "aws-efs-csi-driver")
    key = "controller.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    assert efs.set_args[key] == "arn:aws:iam::123:role/efs-role"


def test_ensure_service_account_patches_annotation_if_exists(mocker):
    provider = _make_provider(mocker)
    mocker.patch(
        "cogrion_bootstrap.providers.aws.subprocess.run",
        return_value=MagicMock(returncode=0),
    )

    provider._ensure_service_account("cogrion-system", "bootstrap-sa", "arn:aws:iam::123:role/r")

    # check call + annotate call
    import cogrion_bootstrap.providers.aws as aws_mod

    assert aws_mod.subprocess.run.call_count == 2


def test_ensure_service_account_dry_run_skips_creation(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    provider = AWSProvider(
        ext_account_id="111122223333",
        ext_workspace_id="w-test01",
        cluster_name="my-cluster",
        region="ap-southeast-1",
        dry_run=True,
    )
    mocker.patch(
        "cogrion_bootstrap.providers.aws.subprocess.run",
        return_value=MagicMock(returncode=1),
    )

    provider._ensure_service_account("cogrion-system", "bootstrap-sa", "arn:aws:iam::123:role/r")

    import cogrion_bootstrap.providers.aws as aws_mod

    # only the existence check ran — no kubectl apply
    assert aws_mod.subprocess.run.call_count == 1


def test_ensure_launch_template_returns_existing(mocker):
    provider = _make_provider(mocker)
    provider.ec2.describe_launch_templates = MagicMock(
        return_value={
            "LaunchTemplates": [{"LaunchTemplateId": "lt-existing", "DefaultVersionNumber": 3}]
        }
    )

    lt_id, lt_ver = provider._ensure_launch_template("my-lt", 100, "gp3")

    assert lt_id == "lt-existing"
    assert lt_ver == "3"
    provider.ec2.create_launch_template.assert_not_called()


def test_ensure_launch_template_dry_run_returns_placeholder(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    provider = AWSProvider(
        ext_account_id="111122223333",
        ext_workspace_id="w-test01",
        cluster_name="my-cluster",
        region="ap-southeast-1",
        dry_run=True,
    )
    provider.ec2.describe_launch_templates = MagicMock(return_value={"LaunchTemplates": []})

    lt_id, lt_ver = provider._ensure_launch_template("my-lt", 100, "gp3")

    assert lt_id == "lt-00000000000000000"
    assert lt_ver == "1"
    provider.ec2.create_launch_template.assert_not_called()


def test_ensure_launch_template_creates_with_imdsv2(mocker):
    provider = _make_provider(mocker)
    provider.ec2.describe_launch_templates = MagicMock(return_value={"LaunchTemplates": []})
    provider.ec2.create_launch_template = MagicMock(
        return_value={"LaunchTemplate": {"LaunchTemplateId": "lt-new", "DefaultVersionNumber": 1}}
    )

    lt_id, lt_ver = provider._ensure_launch_template("my-lt", 50, "gp3")

    assert lt_id == "lt-new"
    call_data = provider.ec2.create_launch_template.call_args.kwargs["LaunchTemplateData"]
    assert call_data["MetadataOptions"]["HttpTokens"] == "required"
    assert call_data["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == 50
    assert call_data["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] is True


def test_ensure_node_role_returns_existing(mocker):
    provider = _make_provider(mocker)
    provider.iam.get_role = MagicMock(return_value={"Role": {"Arn": "arn:aws:iam::123:role/node"}})

    arn = provider._ensure_node_role("node-role")

    assert arn == "arn:aws:iam::123:role/node"
    provider.iam.create_role.assert_not_called()


def test_ensure_node_role_dry_run_returns_placeholder(mocker):
    mocker.patch("cogrion_bootstrap.providers.aws.boto3.client", return_value=MagicMock())
    provider = AWSProvider(
        ext_account_id="111122223333",
        ext_workspace_id="w-test01",
        cluster_name="my-cluster",
        region="ap-southeast-1",
        dry_run=True,
    )
    not_found = Exception("NoSuchEntity")
    provider.iam.exceptions.NoSuchEntityException = type(not_found)
    provider.iam.get_role = MagicMock(side_effect=not_found)

    arn = provider._ensure_node_role("node-role")

    assert "000000000000" in arn
    provider.iam.create_role.assert_not_called()


def test_ensure_node_role_creates_and_attaches_policies(mocker):
    provider = _make_provider(mocker)
    not_found = Exception("NoSuchEntity")
    provider.iam.exceptions.NoSuchEntityException = type(not_found)
    provider.iam.get_role = MagicMock(side_effect=not_found)
    provider.iam.create_role = MagicMock(
        return_value={"Role": {"Arn": "arn:aws:iam::123:role/node-role"}}
    )

    arn = provider._ensure_node_role("node-role")

    assert arn == "arn:aws:iam::123:role/node-role"
    trust = json.loads(provider.iam.create_role.call_args.kwargs["AssumeRolePolicyDocument"])
    assert trust["Statement"][0]["Principal"]["Service"] == "ec2.amazonaws.com"
    assert provider.iam.attach_role_policy.call_count == 3


def test_ensure_policy_skips_if_exists(mocker):
    provider = _make_provider(mocker)
    provider._get_account_id = MagicMock(return_value="123456789012")
    provider.iam.get_policy = MagicMock(return_value={"Policy": {"DefaultVersionId": "v1"}})
    provider.iam.get_policy_version = MagicMock(return_value={"PolicyVersion": {"Document": {}}})

    arn = provider._ensure_policy("my-policy", "{}")

    assert "my-policy" in arn
    provider.iam.create_policy.assert_not_called()
    provider.iam.create_policy_version.assert_not_called()


def test_ensure_policy_updates_when_ext_account_id_placeholder_changes(mocker):
    """Reproduces the reported bug: re-running bootstrap with a different
    {ext_account_id} (e.g. tfstate bucket suffix corrected from a wrong value)
    resolves to a different policy document via _load_policy, but has no
    effect on an already-created IAM policy — _ensure_policy currently
    returns early on "already exists" without ever comparing/pushing the new
    document."""
    provider = _make_provider(mocker)
    provider._get_account_id = MagicMock(return_value="123456789012")
    policy_arn = "arn:aws:iam::123456789012:policy/w-test01-cluster-agent-policy"

    old_doc = json.loads(
        _load_policy(
            "cluster-agent.json", ext_workspace_id="w-test01", ext_account_id="wrong-account"
        )
    )
    new_doc = json.loads(
        _load_policy(
            "cluster-agent.json", ext_workspace_id="w-test01", ext_account_id="devlocalaws"
        )
    )
    assert old_doc != new_doc  # sanity check the two placeholders actually resolve differently

    provider.iam.get_policy = MagicMock(
        return_value={"Policy": {"Arn": policy_arn, "DefaultVersionId": "v1"}}
    )
    provider.iam.get_policy_version = MagicMock(
        return_value={"PolicyVersion": {"Document": old_doc}}
    )
    provider.iam.list_policy_versions = MagicMock(
        return_value={"Versions": [{"VersionId": "v1", "IsDefaultVersion": True}]}
    )

    arn = provider._ensure_policy("w-test01-cluster-agent-policy", json.dumps(new_doc))

    assert arn == policy_arn
    provider.iam.create_policy_version.assert_called_once()
    call_kwargs = provider.iam.create_policy_version.call_args.kwargs
    assert call_kwargs["PolicyArn"] == policy_arn
    assert json.loads(call_kwargs["PolicyDocument"]) == new_doc
    assert call_kwargs["SetAsDefault"] is True


def test_ensure_policy_updates_when_aws_account_id_placeholder_changes(mocker):
    """Same scenario as above but for {aws_account_id} (the numeric AWS
    account, distinct from {ext_account_id}) — not currently used by any
    shipped policy file, but must still trigger an update through
    _ensure_policy's generic document-diff, since a future policy (or a
    workspace moved to a different AWS account) can rely on it."""
    provider = _make_provider(mocker)
    provider._get_account_id = MagicMock(return_value="123456789012")
    policy_arn = "arn:aws:iam::123456789012:policy/my-policy"

    template = '{{"Version": "2012-10-17", "Statement": [{{"Sid": "AccountScoped", "Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::acct-{aws_account_id}-bucket*"}}]}}'
    old_doc = json.loads(template.format(aws_account_id="111111111111"))
    new_doc = json.loads(template.format(aws_account_id="222222222222"))
    assert old_doc != new_doc

    provider.iam.get_policy = MagicMock(
        return_value={"Policy": {"Arn": policy_arn, "DefaultVersionId": "v1"}}
    )
    provider.iam.get_policy_version = MagicMock(
        return_value={"PolicyVersion": {"Document": old_doc}}
    )
    provider.iam.list_policy_versions = MagicMock(
        return_value={"Versions": [{"VersionId": "v1", "IsDefaultVersion": True}]}
    )

    arn = provider._ensure_policy("my-policy", json.dumps(new_doc))

    assert arn == policy_arn
    provider.iam.create_policy_version.assert_called_once()
    call_kwargs = provider.iam.create_policy_version.call_args.kwargs
    assert call_kwargs["PolicyArn"] == policy_arn
    assert json.loads(call_kwargs["PolicyDocument"]) == new_doc
    assert call_kwargs["SetAsDefault"] is True


def test_ensure_policy_skips_update_when_document_unchanged(mocker):
    """Same content (just re-ordered/re-serialized) must not trigger a spurious
    policy version — IAM caps policies at 5 versions, so a no-op diff must
    stay a no-op."""
    provider = _make_provider(mocker)
    provider._get_account_id = MagicMock(return_value="123456789012")
    policy_arn = "arn:aws:iam::123456789012:policy/my-policy"
    doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Same",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": "arn:aws:s3:::same-bucket*",
            }
        ],
    }

    provider.iam.get_policy = MagicMock(
        return_value={"Policy": {"Arn": policy_arn, "DefaultVersionId": "v1"}}
    )
    provider.iam.get_policy_version = MagicMock(return_value={"PolicyVersion": {"Document": doc}})

    # Re-serialized (different key order / whitespace) but semantically identical.
    arn = provider._ensure_policy("my-policy", json.dumps(doc, indent=2))

    assert arn == policy_arn
    provider.iam.create_policy_version.assert_not_called()


def test_ensure_policy_creates_if_missing(mocker):
    provider = _make_provider(mocker)
    provider._get_account_id = MagicMock(return_value="123456789012")
    not_found = Exception("NoSuchEntity")
    provider.iam.exceptions.NoSuchEntityException = type(not_found)
    provider.iam.get_policy = MagicMock(side_effect=not_found)

    arn = provider._ensure_policy("my-policy", "{}")

    provider.iam.create_policy.assert_called_once_with(PolicyName="my-policy", PolicyDocument="{}")
    assert "my-policy" in arn


def test_get_oidc_caches_result(mocker):
    provider = _make_provider(mocker)
    provider._get_account_id = MagicMock(return_value="123456789012")
    provider.eks.describe_cluster = MagicMock(
        return_value={
            "cluster": {
                "identity": {
                    "oidc": {"issuer": "https://oidc.eks.ap-southeast-1.amazonaws.com/id/ABCD1234"}
                }
            }
        }
    )

    arn1, url1 = provider._get_oidc()
    arn2, url2 = provider._get_oidc()

    assert arn1 == arn2
    assert url1 == url2
    provider.eks.describe_cluster.assert_called_once()  # cached on second call


def test_node_group_delete_and_retry_on_create_failed(mocker):
    provider = _make_provider(mocker)
    provider._node_group_status = MagicMock(return_value="CREATE_FAILED")
    provider._discover_eks_subnets = MagicMock(return_value=["subnet-aaa"])
    provider._ensure_node_role = MagicMock(return_value="arn:aws:iam::123:role/r")
    provider._ensure_launch_template = MagicMock(return_value=("lt-abc", "1"))
    provider.eks.get_waiter = MagicMock(return_value=MagicMock())

    provider.ensure_node_group("system", "m5.xlarge", 1, 1, 3, "", "")

    provider.eks.delete_nodegroup.assert_called_once_with(
        clusterName="my-cluster", nodegroupName="system"
    )
    provider.eks.create_nodegroup.assert_called_once()


def test_ensure_service_account_creates_when_missing(mocker):
    provider = _make_provider(mocker)
    run = mocker.patch(
        "cogrion_bootstrap.providers.aws.subprocess.run",
        side_effect=[
            MagicMock(returncode=1),  # existence check — not found
            MagicMock(returncode=0, stdout=b"ns-yaml"),  # kubectl create namespace --dry-run
            MagicMock(returncode=0),  # kubectl apply namespace
            MagicMock(returncode=0, stdout=b"sa-yaml"),  # kubectl create serviceaccount --dry-run
            MagicMock(returncode=0),  # kubectl apply serviceaccount
            MagicMock(returncode=0),  # kubectl annotate
        ],
    )

    provider._ensure_service_account("cogrion-system", "bootstrap-sa", "arn:aws:iam::123:role/r")

    assert run.call_count == 6
    annotate_cmd = run.call_args_list[5].args[0]
    assert "annotate" in annotate_cmd
    assert "eks.amazonaws.com/role-arn=arn:aws:iam::123:role/r" in annotate_cmd


def test_get_account_id_fetches_and_caches(mocker):
    provider = _make_provider(mocker)
    provider.sts.get_caller_identity = MagicMock(return_value={"Account": "999988887777"})

    result1 = provider._get_account_id()
    result2 = provider._get_account_id()

    assert result1 == "999988887777"
    provider.sts.get_caller_identity.assert_called_once()  # cached on second call
    assert result2 == result1


def test_node_group_status_returns_status(mocker):
    provider = _make_provider(mocker)
    provider.eks.describe_nodegroup = MagicMock(return_value={"nodegroup": {"status": "ACTIVE"}})

    assert provider._node_group_status("my-group") == "ACTIVE"


def test_node_group_status_returns_empty_on_not_found(mocker):
    provider = _make_provider(mocker)
    not_found = Exception("ResourceNotFound")
    provider.eks.exceptions.ResourceNotFoundException = type(not_found)
    provider.eks.describe_nodegroup = MagicMock(side_effect=not_found)

    assert provider._node_group_status("missing-group") == ""


def test_discover_eks_subnets_uses_main_route_table_when_no_subnet_association(mocker):
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
    # First call (subnet association) returns empty — falls back to main route table
    provider.ec2.describe_route_tables = MagicMock(
        side_effect=[
            {"RouteTables": []},  # no subnet-specific route table
            {"RouteTables": [{"Routes": [{"NatGatewayId": "nat-abc"}]}]},  # main route table
        ]
    )

    result = provider._discover_eks_subnets()

    assert result == ["subnet-priv"]
    assert provider.ec2.describe_route_tables.call_count == 2


def test_prune_oldest_policy_version_noop_under_limit(mocker):
    provider = _make_provider(mocker)
    policy_arn = "arn:aws:iam::123456789012:policy/my-policy"
    provider.iam.list_policy_versions = MagicMock(
        return_value={
            "Versions": [
                {"VersionId": "v1", "IsDefaultVersion": True},
                {"VersionId": "v2", "IsDefaultVersion": False},
            ]
        }
    )

    provider._prune_oldest_policy_version_if_at_limit(policy_arn)

    provider.iam.delete_policy_version.assert_not_called()


def test_prune_oldest_policy_version_deletes_oldest_non_default_at_limit(mocker):
    provider = _make_provider(mocker)
    policy_arn = "arn:aws:iam::123456789012:policy/my-policy"
    provider.iam.list_policy_versions = MagicMock(
        return_value={
            "Versions": [
                {"VersionId": "v5", "IsDefaultVersion": True, "CreateDate": "2026-07-05"},
                {"VersionId": "v1", "IsDefaultVersion": False, "CreateDate": "2026-07-01"},
                {"VersionId": "v3", "IsDefaultVersion": False, "CreateDate": "2026-07-03"},
                {"VersionId": "v2", "IsDefaultVersion": False, "CreateDate": "2026-07-02"},
                {"VersionId": "v4", "IsDefaultVersion": False, "CreateDate": "2026-07-04"},
            ]
        }
    )

    provider._prune_oldest_policy_version_if_at_limit(policy_arn)

    provider.iam.delete_policy_version.assert_called_once_with(PolicyArn=policy_arn, VersionId="v1")
