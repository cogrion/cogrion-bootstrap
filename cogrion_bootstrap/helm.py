import subprocess


def _helm_status(release: str, namespace: str) -> str:
    result = subprocess.run(
        ["helm", "status", release, "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    import json

    try:
        return json.loads(result.stdout).get("info", {}).get("status", "")
    except Exception:
        return ""


def helm_apply(
    release: str,
    namespace: str,
    chart: str,
    version: str | None = None,
    set_args: dict | None = None,
    dry_run: bool = False,
) -> None:
    status = _helm_status(release, namespace)
    print(f"[helm] {release} current status: {status or 'not found'}")

    if status in ("pending-install", "pending-upgrade", "pending-rollback"):
        print(f"[helm] {release} stuck in '{status}' — deleting before reinstall")
        if not dry_run:
            subprocess.run(["helm", "delete", release, "-n", namespace], check=False)
    elif status == "failed":
        print(f"[helm] {release} in failed state — rolling back")
        if not dry_run:
            subprocess.run(["helm", "rollback", release, "-n", namespace], check=False)

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
        "120s",
        "--wait",
    ]

    if version:
        cmd += ["--version", version]

    for key, value in (set_args or {}).items():
        if value:
            cmd += ["--set", f"{key}={value}"]

    print(f"[helm] running: {' '.join(cmd)}")

    if dry_run:
        print(f"[helm] dry-run: skipping execution")
        return

    subprocess.run(cmd, check=True)
    print(f"[helm] {release} ready")
