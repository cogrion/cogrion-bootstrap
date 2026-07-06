import pytest
from unittest.mock import MagicMock, patch
from cogrion_bootstrap.cli import _ecr_login, _kubectl_apply, _install_addons

# --- _ecr_login ---


def test_ecr_login_dry_run_skips_subprocess(mocker):
    run = mocker.patch("cogrion_bootstrap.cli.subprocess.run")
    _ecr_login("us-east-1", dry_run=True)
    run.assert_not_called()


def test_ecr_login_calls_aws_and_helm(mocker):
    run = mocker.patch(
        "cogrion_bootstrap.cli.subprocess.run",
        side_effect=[
            MagicMock(returncode=0, stdout="my-token"),  # aws ecr-public get-login-password
            MagicMock(returncode=0),  # helm registry login
        ],
    )

    _ecr_login("us-east-1", dry_run=False)

    aws_call = run.call_args_list[0].args[0]
    helm_call = run.call_args_list[1].args[0]
    assert "aws" in aws_call
    assert "ecr-public" in aws_call
    assert "helm" in helm_call
    assert "registry" in helm_call
    assert "login" in helm_call


def test_ecr_login_raises_on_helm_failure(mocker):
    mocker.patch(
        "cogrion_bootstrap.cli.subprocess.run",
        side_effect=[
            MagicMock(returncode=0, stdout="my-token"),
            MagicMock(returncode=1, stderr="auth failed"),
        ],
    )

    with pytest.raises(RuntimeError, match="auth failed"):
        _ecr_login("us-east-1", dry_run=False)


# --- _kubectl_apply ---


def test_kubectl_apply_dry_run_skips_execution(mocker):
    run = mocker.patch("cogrion_bootstrap.cli.subprocess.run")
    _kubectl_apply("https://example.com/manifest.yaml", dry_run=True)
    run.assert_not_called()


def test_kubectl_apply_runs_kubectl(mocker):
    run = mocker.patch(
        "cogrion_bootstrap.cli.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="applied", stderr=""),
    )

    _kubectl_apply("https://example.com/manifest.yaml", dry_run=False)

    cmd = run.call_args.args[0]
    assert cmd == ["kubectl", "apply", "-f", "https://example.com/manifest.yaml"]


def test_kubectl_apply_raises_on_failure(mocker):
    mocker.patch(
        "cogrion_bootstrap.cli.subprocess.run",
        return_value=MagicMock(returncode=1, stderr="not found", stdout=""),
    )

    with pytest.raises(RuntimeError, match="not found"):
        _kubectl_apply("https://example.com/manifest.yaml", dry_run=False)


# --- _install_addons ---


def test_install_addons_calls_helm_apply_for_helm_addon(mocker):
    from cogrion_bootstrap.addons import HelmAddon

    addon = HelmAddon(
        release_name="my-chart",
        namespace="default",
        chart="repo/my-chart",
        repo_name="repo",
        repo_url="https://repo.example.com",
        set_args={"key": "val"},
        detect=None,
    )

    mocker.patch("cogrion_bootstrap.cli.ensure_helm_repos")
    helm_apply = mocker.patch("cogrion_bootstrap.cli.helm_apply")

    _install_addons([addon], node_selector_set={}, dry_run=False)

    helm_apply.assert_called_once_with(
        release="my-chart",
        namespace="default",
        chart="repo/my-chart",
        version="",
        set_args={"key": "val"},
        dry_run=False,
    )


def test_install_addons_calls_kubectl_apply_for_kubectl_addon(mocker):
    from cogrion_bootstrap.addons import KubectlAddon

    addon = KubectlAddon(
        release_name="crds",
        namespace="default",
        manifest_url="https://example.com/crds.yaml",
        detect=None,
    )

    mocker.patch("cogrion_bootstrap.cli.ensure_helm_repos")
    kubectl_apply = mocker.patch("cogrion_bootstrap.cli._kubectl_apply")

    _install_addons([addon], node_selector_set={}, dry_run=False)

    kubectl_apply.assert_called_once_with("https://example.com/crds.yaml", dry_run=False)


def test_install_addons_skips_externally_managed(mocker):
    from cogrion_bootstrap.addons import HelmAddon

    addon = HelmAddon(
        release_name="metrics-server",
        namespace="kube-system",
        chart="metrics-server/metrics-server",
        repo_name="metrics-server",
        repo_url="https://kubernetes-sigs.github.io/metrics-server",
        set_args={},
        detect=("deployment", "metrics-server"),
    )

    mocker.patch("cogrion_bootstrap.cli.ensure_helm_repos")
    mocker.patch("cogrion_bootstrap.cli.is_externally_managed", return_value=True)
    helm_apply = mocker.patch("cogrion_bootstrap.cli.helm_apply")

    _install_addons([addon], node_selector_set={}, dry_run=False)

    helm_apply.assert_not_called()


def test_install_addons_merges_node_selector(mocker):
    from cogrion_bootstrap.addons import HelmAddon

    addon = HelmAddon(
        release_name="my-chart",
        namespace="default",
        chart="repo/my-chart",
        repo_name="repo",
        repo_url="https://repo.example.com",
        set_args={"existing": "value"},
        detect=None,
    )

    mocker.patch("cogrion_bootstrap.cli.ensure_helm_repos")
    helm_apply = mocker.patch("cogrion_bootstrap.cli.helm_apply")

    _install_addons([addon], node_selector_set={"nodeSelector.nodegroup": "system"}, dry_run=False)

    call_set_args = helm_apply.call_args.kwargs["set_args"]
    assert call_set_args["existing"] == "value"
    assert call_set_args["nodeSelector.nodegroup"] == "system"


# --- main() argument validation ---


def test_main_errors_on_unsupported_provider(mocker):
    mocker.patch(
        "sys.argv",
        [
            "cogrion-bootstrap",
            "--token",
            "tok",
            "--provider",
            "alicloud",
            "--cluster-name",
            "c",
            "--region",
            "r",
        ],
    )
    from cogrion_bootstrap.cli import main

    with pytest.raises(SystemExit):
        main()


def test_main_errors_when_cluster_name_missing_for_aws(mocker):
    mocker.patch(
        "sys.argv",
        ["cogrion-bootstrap", "--token", "tok", "--provider", "aws", "--region", "ap-southeast-1"],
    )
    from cogrion_bootstrap.cli import main

    with pytest.raises(SystemExit):
        main()


def test_main_errors_when_region_missing_for_aws(mocker):
    mocker.patch(
        "sys.argv",
        [
            "cogrion-bootstrap",
            "--token",
            "tok",
            "--provider",
            "aws",
            "--cluster-name",
            "my-cluster",
        ],
    )
    from cogrion_bootstrap.cli import main

    with pytest.raises(SystemExit):
        main()


def test_main_errors_when_vpc_id_missing_with_alb_controller(mocker):
    mocker.patch(
        "sys.argv",
        [
            "cogrion-bootstrap",
            "--token",
            "tok",
            "--provider",
            "aws",
            "--cluster-name",
            "my-cluster",
            "--region",
            "ap-southeast-1",
            "--enable-alb-controller",
        ],
    )
    from cogrion_bootstrap.cli import main

    with pytest.raises(SystemExit):
        main()


def test_main_aws_happy_path(mocker):
    mocker.patch(
        "sys.argv",
        [
            "cogrion-bootstrap",
            "--token",
            "tok",
            "--provider",
            "aws",
            "--cluster-name",
            "my-cluster",
            "--region",
            "ap-southeast-1",
            "--dry-run",
        ],
    )
    reg_result = MagicMock(
        ext_account_id="111122223333", ext_workspace_id="w-test01", skipped=False
    )
    mocker.patch("cogrion_bootstrap.cli.register_agent", return_value=reg_result)
    provider_mock = MagicMock()
    provider_mock.ensure_cloud_resources = MagicMock()
    provider_mock.ensure_iam = MagicMock(return_value={})
    mocker.patch("cogrion_bootstrap.providers.aws.AWSProvider", return_value=provider_mock)

    from cogrion_bootstrap.cli import main

    main()

    provider_mock.ensure_cloud_resources.assert_called_once()
    provider_mock.ensure_iam.assert_called_once()


def test_main_aws_no_create_irsa(mocker):
    mocker.patch(
        "sys.argv",
        [
            "cogrion-bootstrap",
            "--token",
            "tok",
            "--provider",
            "aws",
            "--cluster-name",
            "my-cluster",
            "--region",
            "ap-southeast-1",
            "--no-create-irsa",
            "--dry-run",
        ],
    )
    reg_result = MagicMock(ext_account_id="111122223333", ext_workspace_id="w-test01")
    mocker.patch("cogrion_bootstrap.cli.register_agent", return_value=reg_result)
    provider_mock = MagicMock()
    mocker.patch("cogrion_bootstrap.providers.aws.AWSProvider", return_value=provider_mock)

    from cogrion_bootstrap.cli import main

    main()

    provider_mock.ensure_iam.assert_not_called()


def test_main_aws_agent_set_overrides_reach_cplane_agent_helm_apply(mocker):
    mocker.patch(
        "sys.argv",
        [
            "cogrion-bootstrap",
            "--token",
            "tok",
            "--provider",
            "aws",
            "--cluster-name",
            "my-cluster",
            "--region",
            "ap-southeast-1",
            "--agent-set",
            "autoscaling.enabled=false",
            "--agent-set",
            "autoscaling.maxReplicas=1",
            "--dry-run",
        ],
    )
    reg_result = MagicMock(ext_account_id="111122223333", ext_workspace_id="w-test01")
    mocker.patch("cogrion_bootstrap.cli.register_agent", return_value=reg_result)
    provider_mock = MagicMock()
    provider_mock.ensure_iam = MagicMock(return_value={})
    mocker.patch("cogrion_bootstrap.providers.aws.AWSProvider", return_value=provider_mock)
    helm_apply = mocker.patch("cogrion_bootstrap.cli.helm_apply")

    from cogrion_bootstrap.cli import main

    main()

    agent_call = next(
        call for call in helm_apply.call_args_list if call.kwargs["release"] == "cplane-agent"
    )
    set_args = agent_call.kwargs["set_args"]
    assert set_args["autoscaling.enabled"] == "false"
    assert set_args["autoscaling.maxReplicas"] == "1"


def test_main_aws_addon_disable_flags(mocker):
    mocker.patch(
        "sys.argv",
        [
            "cogrion-bootstrap",
            "--token",
            "tok",
            "--provider",
            "aws",
            "--cluster-name",
            "my-cluster",
            "--region",
            "ap-southeast-1",
            "--no-cluster-autoscaler",
            "--no-efs-csi-driver",
            "--no-metrics-server",
            "--no-external-secrets",
            "--no-cluster-proportional-autoscaler",
            "--dry-run",
        ],
    )
    reg_result = MagicMock(ext_account_id="111122223333", ext_workspace_id="w-test01")
    mocker.patch("cogrion_bootstrap.cli.register_agent", return_value=reg_result)
    provider_mock = MagicMock()
    provider_mock.ensure_iam = MagicMock(return_value={})
    mocker.patch("cogrion_bootstrap.providers.aws.AWSProvider", return_value=provider_mock)

    from cogrion_bootstrap.cli import main

    main()
