from unittest.mock import MagicMock
from cogrion_bootstrap.register import register_agent


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


def test_skips_registration_if_secret_already_exists(mocker):
    mocker.patch("cogrion_bootstrap.register.subprocess.run", return_value=MagicMock(returncode=0))
    post = mocker.patch("cogrion_bootstrap.register._post_json")

    register_agent("https://cp.example.com", token="tok", namespace="cogrion-system", dry_run=False)

    post.assert_not_called()


def test_dry_run_does_not_call_post(mocker):
    mocker.patch("cogrion_bootstrap.register.subprocess.run", return_value=MagicMock(returncode=1))
    post = mocker.patch("cogrion_bootstrap.register._post_json")

    register_agent("https://cp.example.com", token="tok", namespace="cogrion-system", dry_run=True)

    post.assert_not_called()


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
