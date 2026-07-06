# Cogrion Bootstrap

One-command bootstrap for a Cogrion tenant cluster. Registers the cluster with the control plane, installs required Kubernetes addons, and deploys the `cplane-agent`.

The bootstrap token is provided by Cogrion when a new workspace is provisioned.

## What it does

1. Creates a managed node group on the target cluster (skippable with `--no-create-node-group`)
2. Registers the cluster with the control plane (`POST /api/v1/agent/register`)
3. Writes a `cluster-agent-credentials` Kubernetes secret with the returned mTLS credentials
4. Installs Kubernetes addons (see [Addons](#addons))
5. Installs the `cplane-agent` Helm chart

All steps are idempotent — safe to re-run.

## Cloud providers

### AWS (EKS)

**Prerequisites:** `kubectl` configured against the target cluster, `helm` v3, AWS credentials with permissions to create EKS node groups and IAM roles (or pass `--node-role-arn`).

```bash
curl -fsSL https://raw.githubusercontent.com/cogrion/cluster-bootstrap/main/install.sh | bash -s -- \
  --provider aws \
  --token <bootstrap-token> \
  --cluster-name <eks-cluster-name> \
  --region ap-southeast-1 \
  --tofu-backend-bucket <s3-state-bucket>
```

To target a different environment (e.g. staging):
```bash
curl -fsSL https://raw.githubusercontent.com/cogrion/cluster-bootstrap/main/install.sh | bash -s -- \
  --provider aws \
  --token <bootstrap-token> \
  --cluster-name <eks-cluster-name> \
  --region ap-southeast-1 \
  --control-plane-url https://staging.cplane.api.cogrion.com
```

**AWS-specific options:**

```
--cluster-name               EKS cluster name (required)
--region                     AWS region (required)
--no-create-node-group       Skip node group creation
--node-group-name            (default: system)
--node-group-instance-type   (default: t3.medium)
--node-group-desired         (default: 2)
--node-group-min             (default: 1)
--node-group-max             (default: 4)
--node-group-subnets         Comma-separated subnet IDs (auto-discovered if omitted)
--node-role-arn              IAM role ARN for the node group (auto-created if omitted)
--enable-alb-controller      Install AWS Load Balancer Controller (requires --vpc-id)
--vpc-id                     VPC ID (required when --enable-alb-controller)
--tofu-backend-bucket        S3 bucket for OpenTofu remote state (required for stack provisioning)
--tofu-backend-region        AWS region of the state bucket (defaults to --region)
--tofu-backend-key-prefix    Key prefix within the state bucket (optional)

IRSA role ARNs (optional — addon service accounts use instance profile if omitted):
  --cluster-autoscaler-role-arn
  --efs-csi-driver-role-arn
  --external-secrets-role-arn
  --alb-controller-role-arn
```

**IAM policies:**

The policies used by each addon are in [`iam/aws/`](iam/aws/). Review them before granting access.

| Addon | Policy file |
|---|---|
| Cluster Autoscaler | [iam/aws/cluster-autoscaler.json](iam/aws/cluster-autoscaler.json) |
| AWS EFS CSI Driver | [iam/aws/efs-csi-driver.json](iam/aws/efs-csi-driver.json) |
| External Secrets | [iam/aws/external-secrets.json](iam/aws/external-secrets.json) |
| AWS Load Balancer Controller | [iam/aws/alb-controller.json](iam/aws/alb-controller.json) |

### Alibaba Cloud (ACK) — coming soon

### Google Cloud (GKE) — coming soon

### Azure (AKS) — coming soon

## Addons

| Addon | Enabled by default | Flag to disable |
|---|---|---|
| Cluster Autoscaler | yes | `--no-cluster-autoscaler` |
| AWS EFS CSI Driver | yes | `--no-efs-csi-driver` |
| Metrics Server | yes | `--no-metrics-server` |
| External Secrets | yes | `--no-external-secrets` |
| Cluster Proportional Autoscaler (scales `coredns` with cluster size) | yes | `--no-cluster-proportional-autoscaler` |
| AWS Load Balancer Controller | no | `--enable-alb-controller` |

### Known issue: cplane-agent HPA and duplicate/aborted commands

The `cplane-agent` chart's HPA (`autoscaling.enabled`, 1-3 replicas on CPU) is currently unsafe: every replica join force-fails in-flight commands claimed by *live sibling* replicas, not just ones orphaned by a real crash (`project-management#186`). Until that's fixed, disable it at bootstrap time:

```bash
--agent-set autoscaling.enabled=false
```

## All options

```
--token                One-time bootstrap token (required)
--provider             Cloud provider: aws (required)
--control-plane-url    Override the control plane URL (default: https://cplane.api.cogrion.com)
--namespace            Kubernetes namespace for the agent (default: cogrion-system)
--agent-version        cplane-agent Helm chart version
--node-group-label     Value for nodeSelector.nodegroup on all Helm releases
--agent-set            Extra --set override for the cplane-agent Helm release, KEY=VALUE (repeatable)
--dry-run              Print actions without executing anything
```

## Running locally

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/cogrion/cluster-bootstrap
cd cluster-bootstrap
uv run python -m cogrion_bootstrap.cli --help
```
