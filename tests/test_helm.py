import subprocess
from cogrion_bootstrap.helm import helm_apply


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
    run = mocker.patch("cogrion_bootstrap.helm.subprocess.run")

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
    run = mocker.patch("cogrion_bootstrap.helm.subprocess.run")

    helm_apply(
        release="cplane-agent",
        namespace="cogrion-system",
        chart="oci://example/chart",
        dry_run=False,
    )

    delete_call = next(c for c in run.call_args_list if "delete" in c.args[0])
    assert "cplane-agent" in delete_call.args[0]


def test_helm_apply_skips_empty_set_values(mocker):
    mocker.patch("cogrion_bootstrap.helm._helm_status", return_value="")
    run = mocker.patch("cogrion_bootstrap.helm.subprocess.run")

    helm_apply(
        release="cplane-agent",
        namespace="cogrion-system",
        chart="oci://example/chart",
        set_args={"someKey": ""},
        dry_run=False,
    )

    upgrade_call = next(c for c in run.call_args_list if "upgrade" in c.args[0])
    assert "someKey" not in " ".join(upgrade_call.args[0])
