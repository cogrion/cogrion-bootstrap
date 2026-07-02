import subprocess
from cogrion_bootstrap.helm import helm_apply, is_externally_managed


def test_is_externally_managed_returns_true_when_not_helm(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=mocker.MagicMock(returncode=0, stdout="EKS"),
    )
    assert is_externally_managed("deployment", "metrics-server", "kube-system") is True


def test_is_externally_managed_returns_false_when_helm_owned(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=mocker.MagicMock(returncode=0, stdout="Helm"),
    )
    assert is_externally_managed("deployment", "metrics-server", "kube-system") is False


def test_is_externally_managed_returns_false_when_resource_missing(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=mocker.MagicMock(returncode=1, stdout=""),
    )
    assert is_externally_managed("deployment", "metrics-server", "kube-system") is False


def test_helm_apply_dry_run_does_not_call_upgrade(mocker):
    run = mocker.patch("cogrion_bootstrap.helm.subprocess.run")
    helm_apply(
        release="cplane-agent",
        namespace="cogrion-system",
        chart="oci://example/chart",
        dry_run=True,
    )
    upgrade_calls = [c for c in run.call_args_list if len(c.args) > 0 and "upgrade" in c.args[0]]
    assert upgrade_calls == []


def test_helm_apply_builds_correct_command(mocker):
    mocker.patch("cogrion_bootstrap.helm._helm_status", return_value="")
    run = mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run", return_value=mocker.MagicMock(returncode=0)
    )

    helm_apply(
        release="cplane-agent",
        namespace="cogrion-system",
        chart="oci://example/chart",
        version="1.0.0",
        set_args={"existingSecret": "my-secret"},
        dry_run=False,
    )

    upgrade_call = next(c for c in run.call_args_list if "upgrade" in c.args[0])
    cmd = upgrade_call.args[0]
    assert "cplane-agent" in cmd
    assert "oci://example/chart" in cmd
    assert "--version" in cmd
    assert "1.0.0" in cmd
    assert "existingSecret=my-secret" in cmd


def test_helm_apply_deletes_stuck_release(mocker):
    mocker.patch("cogrion_bootstrap.helm._helm_status", return_value="pending-install")
    run = mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run", return_value=mocker.MagicMock(returncode=0)
    )

    helm_apply(
        release="cplane-agent",
        namespace="cogrion-system",
        chart="oci://example/chart",
        dry_run=False,
    )

    delete_call = next(c for c in run.call_args_list if "delete" in c.args[0])
    assert "cplane-agent" in delete_call.args[0]


def test_helm_apply_skips_non_helm_owned_release(mocker):
    mocker.patch("cogrion_bootstrap.helm._helm_status", return_value="")
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=mocker.MagicMock(
            returncode=1,
            stdout="",
            stderr=(
                'ServiceAccount "metrics-server" in namespace "kube-system" exists and '
                "cannot be imported into the current release: invalid ownership metadata; "
                'label validation error: key "app.kubernetes.io/managed-by" must equal "Helm"'
            ),
        ),
    )
    # should not raise
    helm_apply(
        release="metrics-server",
        namespace="kube-system",
        chart="metrics-server/metrics-server",
        dry_run=False,
    )


def test_helm_apply_raises_on_other_failures(mocker):
    mocker.patch("cogrion_bootstrap.helm._helm_status", return_value="")
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=mocker.MagicMock(returncode=1, stdout="", stderr="chart not found"),
    )
    try:
        helm_apply(
            release="some-chart", namespace="kube-system", chart="repo/some-chart", dry_run=False
        )
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "chart not found" in str(e)


def test_helm_apply_skips_empty_set_values(mocker):
    mocker.patch("cogrion_bootstrap.helm._helm_status", return_value="")
    run = mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run", return_value=mocker.MagicMock(returncode=0)
    )

    helm_apply(
        release="cplane-agent",
        namespace="cogrion-system",
        chart="oci://example/chart",
        set_args={"someKey": ""},
        dry_run=False,
    )

    upgrade_call = next(c for c in run.call_args_list if "upgrade" in c.args[0])
    assert "someKey" not in " ".join(upgrade_call.args[0])
