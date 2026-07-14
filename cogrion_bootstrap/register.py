import base64
import json
import ssl
import subprocess
import tempfile
import os
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"


def _discover_oidc_issuer() -> Optional[str]:
    """Read this pod's own projected ServiceAccount token and return its
    `iss` claim — the cluster's own OIDC issuer URL. Mirrors
    control-plane/agent's discoverOidcIssuer() (src/lifecycle/oidcIssuer.ts);
    without this, createClusterAuthMount never runs and ESO can never
    authenticate to OpenBao for the wildcard TLS cert."""
    try:
        with open(SA_TOKEN_PATH) as f:
            token = f.read().strip()
    except OSError:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None

    iss = payload.get("iss")
    return iss if isinstance(iss, str) else None


@dataclass
class RegistrationResult:
    skipped: bool = False
    agent_uid: str = ""
    workspace_uid: str = ""
    ext_account_id: str = ""
    ext_workspace_id: str = ""
    global_service_base_url: str = ""
    control_plane_url: str = ""
    mtls_expires_at: str = ""
    mtls_client_cert: str = ""
    mtls_client_key: str = ""
    mtls_ca_cert: str = ""
    git_username: str = ""
    git_token: str = ""
    github_app_id: str = ""
    github_installation_id: str = ""
    github_private_key: str = ""


def _post_json(url: str, payload: dict, skip_tls_verify: bool = False) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    ctx = ssl.create_default_context()
    if skip_tls_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"POST {url} returned HTTP {e.code}: {body}") from e


def register_agent(
    control_plane_url: str, token: str, namespace: str, dry_run: bool, skip_tls_verify: bool = False
) -> RegistrationResult:
    secret_name = "cluster-agent-credentials"

    # Idempotency — skip if secret already exists
    check = subprocess.run(
        ["kubectl", "get", "secret", secret_name, "-n", namespace],
        capture_output=True,
    )
    if check.returncode == 0:
        print(f"[register] {secret_name} already exists — skipping registration")
        return _read_existing_secret(secret_name, namespace)

    print(f"[register] registering with {control_plane_url}")

    if dry_run:
        print(f"[register] dry-run: would POST {control_plane_url}/api/v1/agent/register")
        return RegistrationResult(skipped=True)

    oidc_issuer_url = _discover_oidc_issuer()
    if not oidc_issuer_url:
        raise RuntimeError(
            "Could not discover this cluster's OIDC issuer from the bootstrap "
            "Job's own ServiceAccount token — registering without it would "
            "leave OpenBao's cluster auth mount unprovisioned, so ESO could "
            "never authenticate to pull the wildcard TLS cert. Aborting "
            "installation; please contact Cogrion support."
        )

    result = _post_json(
        f"{control_plane_url}/api/v1/agent/register",
        {"token": token, "oidcIssuerUrl": oidc_issuer_url},
    )

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
    return RegistrationResult(
        agent_uid=result.get("agentUid", ""),
        workspace_uid=result.get("workspaceUid", ""),
        ext_account_id=result.get("extAccountId", ""),
        ext_workspace_id=result.get("extWorkspaceId", ""),
        global_service_base_url=result.get("globalServiceBaseUrl", ""),
        control_plane_url=control_plane_url,
        mtls_expires_at=mtls.get("expiresAt", ""),
        mtls_client_cert=client_cert,
        mtls_client_key=client_key,
        mtls_ca_cert=ca_cert,
        git_username=result.get("gitPATConfig", {}).get("username", ""),
        git_token=result.get("gitPATConfig", {}).get("token", ""),
        github_app_id=result.get("githubAppConfig", {}).get("githubAppId", ""),
        github_installation_id=result.get("githubAppConfig", {}).get("githubAppInstallationId", ""),
        github_private_key=result.get("githubAppConfig", {}).get("githubAppPrivateKey", ""),
    )


def _read_existing_secret(secret_name: str, namespace: str) -> RegistrationResult:
    out = subprocess.run(
        ["kubectl", "get", "secret", secret_name, "-n", namespace, "-o", "json"],
        capture_output=True,
        check=True,
    )
    data = json.loads(out.stdout).get("data", {})

    def _decode(key: str) -> str:
        raw = data.get(key, "")
        return base64.b64decode(raw).decode() if raw else ""

    return RegistrationResult(
        agent_uid=_decode("CPLANE_AGENT_UID"),
        workspace_uid=_decode("CPLANE_AGENT_WORKSPACE_UID"),
        ext_account_id=_decode("CPLANE_AGENT_EXT_ACCOUNT_ID"),
        ext_workspace_id=_decode("CPLANE_AGENT_EXT_WORKSPACE_ID"),
        global_service_base_url=_decode("CPLANE_AGENT_GLOBAL_SERVICE_BASE_URL"),
        control_plane_url=_decode("CPLANE_AGENT_URL"),
        mtls_expires_at=_decode("CPLANE_AGENT_MTLS_EXPIRES_AT"),
        mtls_client_cert=_decode("CPLANE_AGENT_MTLS_CLIENT_CERT"),
        mtls_client_key=_decode("CPLANE_AGENT_MTLS_CLIENT_KEY"),
        mtls_ca_cert=_decode("CPLANE_AGENT_MTLS_CA_CERT"),
        git_username=_decode("GIT_USERNAME"),
        git_token=_decode("GIT_TOKEN"),
        github_app_id=_decode("GITHUB_APP_ID"),
        github_installation_id=_decode("GITHUB_INSTALLATION_ID"),
        github_private_key=_decode("GITHUB_PRIVATE_KEY"),
    )
