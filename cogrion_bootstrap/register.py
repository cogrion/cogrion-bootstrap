import json
import subprocess
import tempfile
import os
import urllib.request
import urllib.error


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"POST {url} returned HTTP {e.code}: {body}") from e


def register_agent(control_plane_url: str, token: str, namespace: str, dry_run: bool) -> None:
    secret_name = "cluster-agent-credentials"

    # Idempotency — skip if secret already exists
    check = subprocess.run(
        ["kubectl", "get", "secret", secret_name, "-n", namespace],
        capture_output=True,
    )
    if check.returncode == 0:
        print(f"[register] {secret_name} already exists — skipping registration")
        return

    print(f"[register] registering with {control_plane_url}")

    if dry_run:
        print(f"[register] dry-run: would POST {control_plane_url}/api/v1/agent/register")
        return

    result = _post_json(f"{control_plane_url}/api/v1/agent/register", {"token": token})

    mtls = result.get("mtls", {})
    client_cert = mtls.get("clientCert", "")
    client_key = mtls.get("clientKey", "")
    ca_cert = mtls.get("caCert", "")

    if not client_cert or not client_key:
        raise RuntimeError("Registration response missing mTLS credentials")

    # Write certs to temp files so kubectl can read them as binary-safe --from-file
    with tempfile.TemporaryDirectory() as tmp:
        cert_path = os.path.join(tmp, "client.crt")
        key_path = os.path.join(tmp, "client.key")
        ca_path = os.path.join(tmp, "ca.crt")

        with open(cert_path, "w") as f:
            f.write(client_cert)
        with open(key_path, "w") as f:
            f.write(client_key)

        cmd = [
            "kubectl",
            "create",
            "secret",
            "generic",
            secret_name,
            f"-n={namespace}",
            f"--from-literal=CPLANE_AGENT_UID={result.get('agentUid', '')}",
            f"--from-literal=CPLANE_AGENT_WORKSPACE_UID={result.get('workspaceUid', '')}",
            f"--from-literal=CPLANE_AGENT_EXT_ACCOUNT_ID={result.get('extAccountId', '')}",
            f"--from-literal=CPLANE_AGENT_EXT_WORKSPACE_ID={result.get('extWorkspaceId', '')}",
            f"--from-literal=CPLANE_AGENT_GLOBAL_SERVICE_BASE_URL={result.get('globalServiceBaseUrl', '')}",
            f"--from-literal=CPLANE_AGENT_URL={control_plane_url}",
            f"--from-literal=CPLANE_AGENT_MTLS_EXPIRES_AT={mtls.get('expiresAt', '')}",
            f"--from-file=CPLANE_AGENT_MTLS_CLIENT_CERT={cert_path}",
            f"--from-file=CPLANE_AGENT_MTLS_CLIENT_KEY={key_path}",
            f"--from-literal=GIT_USERNAME={result.get('gitPATConfig', {}).get('username', '')}",
            f"--from-literal=GIT_TOKEN={result.get('gitPATConfig', {}).get('token', '')}",
            f"--from-literal=GITHUB_APP_ID={result.get('githubAppConfig', {}).get('githubAppId', '')}",
            f"--from-literal=GITHUB_INSTALLATION_ID={result.get('githubAppConfig', {}).get('githubAppInstallationId', '')}",
            f"--from-literal=GITHUB_PRIVATE_KEY={result.get('githubAppConfig', {}).get('githubAppPrivateKey', '')}",
            "--dry-run=client",
            "-o",
            "yaml",
        ]

        if ca_cert:
            with open(ca_path, "w") as f:
                f.write(ca_cert)
            cmd.append(f"--from-file=CPLANE_AGENT_MTLS_CA_CERT={ca_path}")

        # Ensure namespace exists
        subprocess.run(
            ["kubectl", "create", "namespace", namespace, "--dry-run=client", "-o", "yaml"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=subprocess.run(cmd, capture_output=True, check=True).stdout,
            check=True,
        )

    print(f"[register] {secret_name} written (agentUid={result.get('agentUid')})")
