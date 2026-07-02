import base64
import json
import pytest
from unittest.mock import MagicMock
from cogrion_bootstrap.register import register_agent, RegistrationResult


def _response():
    return {
        "agentUid": "uid-123",
        "workspaceUid": "ws-456",
        "extAccountId": "acc-789",
        "extWorkspaceId": "ws-ext-001",
        "globalServiceBaseUrl": "global.cogrion.com",
        "mtls": {
            "clientCert": "-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----\n",
            "clientKey": "-----BEGIN RSA PRIVATE KEY-----\nDEF\n-----END RSA PRIVATE KEY-----\n",
            "expiresAt": "2027-01-01T00:00:00Z",
        },
        "gitPATConfig": {"username": "git-user", "token": "git-token"},
        "githubAppConfig": {
            "githubAppId": "",
            "githubAppInstallationId": "",
            "githubAppPrivateKey": "",
        },
    }


def _encoded_secret_json():
    def enc(v):
        return base64.b64encode(v.encode()).decode()

    data = {
        "CPLANE_AGENT_UID": enc("uid-existing"),
        "CPLANE_AGENT_WORKSPACE_UID": enc("ws-existing"),
        "CPLANE_AGENT_EXT_ACCOUNT_ID": enc("acc-existing"),
        "CPLANE_AGENT_EXT_WORKSPACE_ID": enc("ws-ext-existing"),
        "CPLANE_AGENT_GLOBAL_SERVICE_BASE_URL": enc("global.existing.com"),
        "CPLANE_AGENT_URL": enc("https://cp.existing.com"),
        "CPLANE_AGENT_MTLS_EXPIRES_AT": enc("2028-01-01T00:00:00Z"),
        "CPLANE_AGENT_MTLS_CLIENT_CERT": enc("cert-data"),
        "CPLANE_AGENT_MTLS_CLIENT_KEY": enc("key-data"),
        "CPLANE_AGENT_MTLS_CA_CERT": enc("ca-data"),
        "GIT_USERNAME": enc("git-existing"),
        "GIT_TOKEN": enc("tok-existing"),
        "GITHUB_APP_ID": enc(""),
        "GITHUB_INSTALLATION_ID": enc(""),
        "GITHUB_PRIVATE_KEY": enc(""),
    }
    return json.dumps({"data": data}).encode()


def test_skips_registration_if_secret_already_exists(mocker):
    mocker.patch(
        "cogrion_bootstrap.register.subprocess.run",
        side_effect=[
            MagicMock(returncode=0),  # secret exists
            MagicMock(returncode=0, stdout=_encoded_secret_json()),  # kubectl get secret -o json
        ],
    )
    post = mocker.patch("cogrion_bootstrap.register._post_json")

    result = register_agent(
        "https://cp.example.com", token="tok", namespace="cogrion-system", dry_run=False
    )

    post.assert_not_called()
    assert isinstance(result, RegistrationResult)
    assert result.agent_uid == "uid-existing"
    assert result.mtls_client_cert == "cert-data"
    assert result.git_username == "git-existing"


def test_dry_run_returns_skipped_result(mocker):
    mocker.patch("cogrion_bootstrap.register.subprocess.run", return_value=MagicMock(returncode=1))
    mocker.patch("cogrion_bootstrap.register._post_json")

    result = register_agent(
        "https://cp.example.com", token="tok", namespace="cogrion-system", dry_run=True
    )

    assert result.skipped is True
    assert result.agent_uid == ""


def test_posts_to_correct_register_endpoint(mocker):
    mocker.patch(
        "cogrion_bootstrap.register.subprocess.run",
        side_effect=[
            MagicMock(returncode=1),  # secret does not exist
            MagicMock(returncode=0, stdout=b""),  # namespace apply
            MagicMock(returncode=0, stdout=b"apiVersion: v1"),  # kubectl create secret --dry-run
            MagicMock(returncode=0),  # kubectl apply
        ],
    )
    post = mocker.patch("cogrion_bootstrap.register._post_json", return_value=_response())

    register_agent(
        "https://cp.example.com", token="my-token", namespace="cogrion-system", dry_run=False
    )

    post.assert_called_once_with(
        "https://cp.example.com/api/v1/agent/register", {"token": "my-token"}
    )


def test_registration_returns_populated_result(mocker):
    mocker.patch(
        "cogrion_bootstrap.register.subprocess.run",
        side_effect=[
            MagicMock(returncode=1),
            MagicMock(returncode=0, stdout=b""),
            MagicMock(returncode=0, stdout=b"apiVersion: v1"),
            MagicMock(returncode=0),
        ],
    )
    mocker.patch("cogrion_bootstrap.register._post_json", return_value=_response())

    result = register_agent(
        "https://cp.example.com", token="tok", namespace="cogrion-system", dry_run=False
    )

    assert isinstance(result, RegistrationResult)
    assert result.skipped is False
    assert result.agent_uid == "uid-123"
    assert result.workspace_uid == "ws-456"
    assert result.control_plane_url == "https://cp.example.com"
    assert result.mtls_expires_at == "2027-01-01T00:00:00Z"
    assert "BEGIN CERTIFICATE" in result.mtls_client_cert
    assert result.git_username == "git-user"
    assert result.git_token == "git-token"


def test_registration_includes_ca_cert_when_present(mocker):
    resp = _response()
    resp["mtls"]["caCert"] = "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n"
    mocker.patch(
        "cogrion_bootstrap.register.subprocess.run",
        side_effect=[
            MagicMock(returncode=1),
            MagicMock(returncode=0, stdout=b""),
            MagicMock(returncode=0, stdout=b"apiVersion: v1"),
            MagicMock(returncode=0),
        ],
    )
    mocker.patch("cogrion_bootstrap.register._post_json", return_value=resp)

    result = register_agent(
        "https://cp.example.com", token="tok", namespace="cogrion-system", dry_run=False
    )

    assert "BEGIN CERTIFICATE" in result.mtls_ca_cert


def test_post_json_returns_parsed_response(mocker):
    import json as _json
    from cogrion_bootstrap.register import _post_json

    mock_resp = MagicMock()
    mock_resp.read.return_value = _json.dumps({"ok": True}).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mocker.patch("cogrion_bootstrap.register.urllib.request.urlopen", return_value=mock_resp)

    result = _post_json("https://cp.example.com/api/v1/agent/register", {"token": "tok"})

    assert result == {"ok": True}


def test_post_json_raises_on_http_error(mocker):
    import urllib.error
    from cogrion_bootstrap.register import _post_json

    error = urllib.error.HTTPError(
        url="https://cp.example.com",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=None,
    )
    error.read = lambda: b"invalid token"
    mocker.patch("cogrion_bootstrap.register.urllib.request.urlopen", side_effect=error)

    with pytest.raises(RuntimeError, match="HTTP 401"):
        _post_json("https://cp.example.com/api/v1/agent/register", {"token": "bad"})


def test_raises_if_mtls_certs_missing(mocker):
    mocker.patch("cogrion_bootstrap.register.subprocess.run", return_value=MagicMock(returncode=1))
    mocker.patch(
        "cogrion_bootstrap.register._post_json",
        return_value={
            "agentUid": "uid-123",
            "mtls": {},
        },
    )

    try:
        register_agent(
            "https://cp.example.com", token="tok", namespace="cogrion-system", dry_run=False
        )
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "mTLS" in str(e)
