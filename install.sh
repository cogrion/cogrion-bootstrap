#!/usr/bin/env bash
# Cogrion cluster bootstrap installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/cogrion/cluster-bootstrap/main/install.sh | bash -s -- \
#     --control-plane-url https://dev.cplane.api.cogrion.com \
#     --token <bootstrap-token> \
#     --cluster-name <eks-cluster-name> \
#     --region ap-southeast-1
#
# All additional flags are passed through to cogrion-bootstrap. Run with --help to see all options.
set -euo pipefail

REPO_URL="https://raw.githubusercontent.com/cogrion/cluster-bootstrap/main"
INSTALL_DIR="${TMPDIR:-/tmp}/cogrion-bootstrap-$$"

cleanup() { rm -rf "$INSTALL_DIR"; }
trap cleanup EXIT

# Ensure uv is available
if ! command -v uv &>/dev/null; then
  echo "[cogrion] installing uv..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "[cogrion] downloading bootstrap package..."
mkdir -p "$INSTALL_DIR"
curl -fsSL "${REPO_URL}/pyproject.toml"                           -o "$INSTALL_DIR/pyproject.toml"
curl -fsSL "${REPO_URL}/uv.lock"                                  -o "$INSTALL_DIR/uv.lock"

mkdir -p "$INSTALL_DIR/cogrion_bootstrap/providers"
for f in __init__.py cli.py register.py helm.py addons.py; do
  curl -fsSL "${REPO_URL}/cogrion_bootstrap/${f}" -o "$INSTALL_DIR/cogrion_bootstrap/${f}"
done
for f in __init__.py base.py aws.py; do
  curl -fsSL "${REPO_URL}/cogrion_bootstrap/providers/${f}" \
    -o "$INSTALL_DIR/cogrion_bootstrap/providers/${f}"
done

echo "[cogrion] running bootstrap..."
cd "$INSTALL_DIR"
uv run --project . python -m cogrion_bootstrap.cli "$@"
