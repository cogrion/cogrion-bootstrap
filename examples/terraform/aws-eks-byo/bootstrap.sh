#!/usr/bin/env bash
# Runs as a Kubernetes Job (alpine/k8s image) via the workspace-cluster-bootstrap
# Terraform module. Clones cogrion-bootstrap and runs the CLI with the full
# set of args for this example cluster.
#
# Required env vars (injected by Terraform):
#   BOOTSTRAP_TOKEN         — one-time token from the control plane
#   CONTROL_PLANE_URL       — base URL of the control plane API
#   CLUSTER_NAME            — EKS cluster name
#   REGION                  — AWS region
#   TOFU_BACKEND_BUCKET     — S3 bucket for OpenTofu remote state
#   TRAEFIK_SUBNETS         — comma-separated public subnet IDs for the NLB
#   AGENT_VERSION           — cplane-agent Helm chart version (optional)
#   NODE_GROUP_LABEL        — nodegroup node label value (default: system)
#   ENABLE_EXTERNAL_DNS     — "true"/"false", install external-dns + dns-webhook (default: true)
#   DNS_WEBHOOK_TAG         — dns-webhook sidecar image tag (optional)
set -euo pipefail

REPO_URL="https://github.com/cogrion/cogrion-bootstrap.git"
CLONE_DIR="/tmp/cogrion-bootstrap"

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
for var in BOOTSTRAP_TOKEN CONTROL_PLANE_URL CLUSTER_NAME REGION TOFU_BACKEND_BUCKET TRAEFIK_SUBNETS; do
  [[ -z "${!var:-}" ]] && { echo "ERROR: $var is required"; exit 1; }
done

# ---------------------------------------------------------------------------
# Install uv if not present (alpine/k8s image may not have it)
# ---------------------------------------------------------------------------
if ! command -v uv &>/dev/null; then
  echo "[bootstrap] installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# ---------------------------------------------------------------------------
# Clone / update
# ---------------------------------------------------------------------------
if [[ -d "$CLONE_DIR/.git" ]]; then
  echo "[bootstrap] updating existing clone"
  git -C "$CLONE_DIR" pull --ff-only
else
  echo "[bootstrap] cloning cogrion-bootstrap"
  git clone "$REPO_URL" "$CLONE_DIR"
fi

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
args=(
  --token "$BOOTSTRAP_TOKEN"
  --provider aws
  --cluster-name "$CLUSTER_NAME"
  --region "$REGION"
  --control-plane-url "$CONTROL_PLANE_URL"
  --tofu-backend-bucket "$TOFU_BACKEND_BUCKET"
  --no-create-node-group
  --node-group-label "${NODE_GROUP_LABEL:-system}"
  --agent-version "${AGENT_VERSION:-}"
  --traefik-subnets "$TRAEFIK_SUBNETS"
  # The Job has no stdin to read the interactive 'yes' confirmation from.
  --auto-approve
)

if [[ "${ENABLE_EXTERNAL_DNS:-true}" == "false" ]]; then
  args+=(--no-external-dns)
fi
if [[ -n "${DNS_WEBHOOK_TAG:-}" ]]; then
  args+=(--dns-webhook-tag "$DNS_WEBHOOK_TAG")
fi

uv run --project "$CLONE_DIR" python -m cogrion_bootstrap.cli "${args[@]}"
