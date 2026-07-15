import json
import pytest
from unittest.mock import MagicMock
from cogrion_bootstrap.helm import (
    helm_apply,
    ensure_helm_repos,
    is_externally_managed,
    _helm_status,
    _helm_description,
)


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


def test_helm_apply_deletes_failed_release(mocker):
    # A "failed" release has no successfully-deployed revision to roll back
    # to (the failure IS the only/latest revision) — `helm rollback` with no
    # target errors out ("release has no 0 version"), so recovery must be
    # delete-and-reinstall, same as the pending-* states.
    mocker.patch("cogrion_bootstrap.helm._helm_status", return_value="failed")
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
    rollback_calls = [c for c in run.call_args_list if "rollback" in c.args[0]]
    assert rollback_calls == []


def test_helm_apply_prints_failure_reason_before_deleting(mocker, capsys):
    # The recovery log line previously said only "stuck in 'failed'" with no
    # indication of *why* — hiding real causes like a client rate-limiter
    # timeout behind a generic message on every retry.
    mocker.patch("cogrion_bootstrap.helm._helm_status", return_value="failed")
    mocker.patch(
        "cogrion_bootstrap.helm._helm_description",
        return_value='Release "cplane-agent" failed: client rate limiter Wait '
        "returned an error: context deadline exceeded",
    )
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run", return_value=mocker.MagicMock(returncode=0)
    )

    helm_apply(
        release="cplane-agent",
        namespace="cogrion-system",
        chart="oci://example/chart",
        dry_run=False,
    )

    out = capsys.readouterr().out
    assert "context deadline exceeded" in out


def test_helm_description_returns_description(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=MagicMock(
            returncode=0,
            stdout=json.dumps({"info": {"status": "failed", "description": "boom"}}),
        ),
    )
    assert _helm_description("my-release", "default") == "boom"


def test_helm_description_returns_empty_on_missing_release(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=MagicMock(returncode=1, stdout=""),
    )
    assert _helm_description("my-release", "default") == ""


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


def test_helm_status_returns_deployed(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=MagicMock(returncode=0, stdout=json.dumps({"info": {"status": "deployed"}})),
    )
    assert _helm_status("my-release", "default") == "deployed"


def test_helm_status_returns_empty_on_missing_release(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=MagicMock(returncode=1, stdout=""),
    )
    assert _helm_status("my-release", "default") == ""


def test_helm_status_returns_empty_on_invalid_json(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="not-json"),
    )
    assert _helm_status("my-release", "default") == ""


def test_ensure_helm_repos_skips_existing_repo(mocker):
    run = mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        side_effect=[
            MagicMock(returncode=0, stdout=json.dumps([{"name": "eks"}])),
            MagicMock(returncode=0),  # repo update
        ],
    )

    ensure_helm_repos({"eks": "https://aws.github.io/eks-charts"}, dry_run=False)

    calls = [c.args[0] for c in run.call_args_list]
    assert not any("add" in c for c in calls)


def test_ensure_helm_repos_adds_missing_repo(mocker):
    run = mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        side_effect=[
            MagicMock(returncode=0, stdout="[]"),
            MagicMock(returncode=0),  # repo add
            MagicMock(returncode=0),  # repo update
        ],
    )

    ensure_helm_repos({"eks": "https://aws.github.io/eks-charts"}, dry_run=False)

    add_call = run.call_args_list[1].args[0]
    assert add_call == ["helm", "repo", "add", "eks", "https://aws.github.io/eks-charts"]


def test_ensure_helm_repos_dry_run_skips_add_and_update(mocker):
    run = mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="[]"),
    )

    ensure_helm_repos({"eks": "https://aws.github.io/eks-charts"}, dry_run=True)

    assert run.call_count == 1


def test_ensure_helm_repos_handles_invalid_json_in_list(mocker):
    mocker.patch(
        "cogrion_bootstrap.helm.subprocess.run",
        side_effect=[
            MagicMock(returncode=0, stdout="bad-json"),
            MagicMock(returncode=0),  # repo add
            MagicMock(returncode=0),  # repo update
        ],
    )

    ensure_helm_repos({"eks": "https://aws.github.io/eks-charts"}, dry_run=False)
