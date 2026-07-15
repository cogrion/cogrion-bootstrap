import json
import subprocess
import tempfile
import os


def is_externally_managed(kind: str, name: str, namespace: str) -> bool:
    """Return True if the resource exists but is not managed by Helm."""
    result = subprocess.run(
        [
            "kubectl",
            "get",
            kind,
            name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.metadata.labels.app\\.kubernetes\\.io/managed-by}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False  # resource does not exist
    managed_by = result.stdout.strip()
    return managed_by != "Helm"


def _helm_status(release: str, namespace: str) -> str:
    result = subprocess.run(
        ["helm", "status", release, "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    try:
        return json.loads(result.stdout).get("info", {}).get("status", "")
    except Exception:
        return ""


def _helm_description(release: str, namespace: str) -> str:
    """The reason for the release's current status, e.g. why it failed."""
    result = subprocess.run(
        ["helm", "status", release, "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    try:
        return json.loads(result.stdout).get("info", {}).get("description", "")
    except Exception:
        return ""


def ensure_helm_repos(repos: dict[str, str], dry_run: bool = False) -> None:
    for name, url in repos.items():
        result = subprocess.run(
            ["helm", "repo", "list", "-o", "json"],
            capture_output=True,
            text=True,
        )
        existing = []
        try:
            existing = [r["name"] for r in json.loads(result.stdout or "[]")]
        except Exception:
            pass

        if name in existing:
            print(f"[helm] repo '{name}' already added — skipping")
            continue

        print(f"[helm] adding repo '{name}' ({url})")
        if not dry_run:
            subprocess.run(["helm", "repo", "add", name, url], check=True)

    if not dry_run:
        subprocess.run(["helm", "repo", "update"], check=True)


def helm_apply(
    release: str,
    namespace: str,
    chart: str,
    version: str | None = None,
    set_args: dict | None = None,
    values_yaml: str = "",
    dry_run: bool = False,
) -> None:
    status = _helm_status(release, namespace)
    print(f"[helm] {release} current status: {status or 'not found'}")

    if status in ("pending-install", "pending-upgrade", "pending-rollback", "failed"):
        # A "failed" release has no guarantee of a prior successfully-deployed
        # revision to roll back to — often the failure IS revision 1, in which
        # case `helm rollback` (no target) errors with "release has no 0
        # version" and leaves the broken history in place. Delete-and-reinstall
        # is the only recovery that works unconditionally.
        reason = _helm_description(release, namespace)
        suffix = f": {reason}" if reason else ""
        print(f"[helm] {release} stuck in '{status}'{suffix} — deleting before reinstall")
        if not dry_run:
            subprocess.run(["helm", "delete", release, "-n", namespace], check=False)

    cmd = [
        "helm",
        "upgrade",
        "--install",
        release,
        chart,
        "--namespace",
        namespace,
        "--create-namespace",
        "--timeout",
        "600s",
        "--wait",
    ]

    if version:
        cmd += ["--version", version]

    for key, value in (set_args or {}).items():
        if value:
            escaped = str(value).replace(",", "\\,")
            cmd += ["--set", f"{key}={escaped}"]

    tmp_values = None
    if values_yaml:
        tmp_values = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        tmp_values.write(values_yaml)
        tmp_values.flush()
        cmd += ["--values", tmp_values.name]

    print(f"[helm] running: {' '.join(cmd)}")

    if dry_run:
        if tmp_values:
            os.unlink(tmp_values.name)
        print(f"[helm] dry-run: skipping execution")
        return

    result = subprocess.run(cmd, capture_output=True, text=True)
    if tmp_values:
        os.unlink(tmp_values.name)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if (
            "cannot be imported into the current release" in stderr
            and "invalid ownership metadata" in stderr
        ):
            print(f"[helm] {release} already managed outside Helm — skipping")
            return
        error_detail = stderr or result.stdout.strip()
        raise RuntimeError(
            f"[helm] '{release}' install failed (exit {result.returncode}):\n{error_detail}"
        )

    print(f"[helm] {release} ready")
