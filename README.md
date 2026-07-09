# Cogrion Bootstrap

Registers a tenant cluster with the Cogrion control plane, installs required Kubernetes addons, and deploys the `cplane-agent`. The bootstrap token is provided by Cogrion when a new workspace is provisioned.

There are two supported paths:

- **CLI** — one command, handles everything end-to-end including node group and IRSA role creation.
- **Terraform (AWS)** — use [`examples/terraform/aws-eks-byo/`](examples/terraform/aws-eks-byo/) to provision the cluster and addons as code, then run the CLI once to register and install the agent.

Both paths install the same set of addons and produce the same result.

---

## AWS (EKS) — CLI runbook

**Prerequisites:** `kubectl` configured against the target cluster, `helm` v3, AWS credentials with EKS and IAM permissions.

> **Network access required.** Both the CLI and `tofu apply` must run from a machine with direct connectivity to the EKS API server and OIDC endpoint. Options: a machine on the same VPC (EC2, Cloud9, CloudShell), a VPN or Direct Connect session into the VPC, or a public-endpoint cluster (`eks_cluster_endpoint_public_access = true`). Without this, Terraform's OIDC thumbprint fetch and `kubectl` will fail with "network is unreachable".

### 1. Install

```bash
curl -fsSL https://raw.githubusercontent.com/cogrion/cogrion-bootstrap/main/install.sh | bash
```

Or with `uv` from source:

```bash
git clone https://github.com/cogrion/cogrion-bootstrap
cd cogrion-bootstrap
uv tool install --editable src/  # if applicable, or:
uv run python -m cogrion_bootstrap.cli --help
```

### 2. Run

```bash
cogrion-bootstrap \
  --provider aws \
  --token <bootstrap-token> \
  --cluster-name <eks-cluster-name> \
  --region ap-southeast-1 \
  --node-group-label system \
  --traefik-subnets subnet-aaa111,subnet-bbb222 \
  --control-plane-url https://cplane.api.cogrion.com \
  --tofu-backend-bucket <s3-state-bucket>
```

The CLI will print a full plan of everything it will create — node group, IRSA roles, namespaces, addons, agent chart version — and require you to type `yes` before proceeding.

### Find the public subnets for `--traefik-subnets`

Traefik creates an NLB — AWS needs to know which subnets to place it in. Pass the public subnet IDs:

```bash
aws ec2 describe-subnets \
  --filters Name=tag:kubernetes.io/role/elb,Values=1 \
  --query 'Subnets[].SubnetId' \
  --output text
```

If you provisioned the cluster with the `examples/terraform/aws-eks-byo/` Terraform example, run `tofu output public_subnets` instead.

### 3. Find the node group label

`--node-group-label` must match the actual `nodegroup` k8s label on the nodes. Check it with:

```bash
kubectl get nodes --show-labels | grep nodegroup
```

If you created the node group with this CLI, the label is set to `--node-group-name` (default: `system`). If you brought your own cluster (e.g. created via Terraform), check the EKS console → the node group → **Kubernetes labels** tab.

### Adopting an existing node group

If the node group already exists (e.g. provisioned by Terraform):

```bash
cogrion-bootstrap \
  --provider aws \
  --token <bootstrap-token> \
  --cluster-name <eks-cluster-name> \
  --region ap-southeast-1 \
  --no-create-node-group \
  --node-group-name <existing-nodegroup-name> \
  --node-group-label <nodegroup-label-value> \
  --traefik-subnets subnet-aaa111,subnet-bbb222 \
  --no-create-irsa \
  --control-plane-url https://cplane.api.cogrion.com \
  --tofu-backend-bucket <s3-state-bucket>
```

Use `--no-create-irsa` when IRSA roles were already created by Terraform.

### AWS CLI options

```
--cluster-name               EKS cluster name (required)
--region                     AWS region (required)
--node-group-label           Value of the 'nodegroup' k8s node label (required)
--no-create-node-group       Skip node group creation (use existing)
--node-group-name            EKS node group name to create or adopt (default: system)
--node-group-instance-type   (default: t3.medium)
--node-group-desired         (default: 2)
--node-group-min             (default: 1)
--node-group-max             (default: 4)
--node-group-subnets         Comma-separated subnet IDs (auto-discovered if omitted)
--node-role-arn              IAM role ARN for the node group (auto-created if omitted)
--no-create-irsa             Skip IRSA role creation (use when roles are pre-provisioned)
--traefik-subnets            Comma-separated public subnet IDs for the Traefik NLB (required unless --no-traefik)
--enable-alb-controller      Install AWS Load Balancer Controller (requires --vpc-id)
--vpc-id                     VPC ID (required when --enable-alb-controller)
--tofu-backend-bucket        S3 bucket for OpenTofu remote state
--tofu-backend-region        AWS region of the state bucket (defaults to --region)
--tofu-backend-key-prefix    Key prefix within the state bucket (optional)
```

### IAM policies

The policies used by each addon are in [`iam/aws/`](iam/aws/). Review them before granting access.

| Addon | Policy file |
|---|---|
| Cluster Autoscaler | [iam/aws/cluster-autoscaler.json](iam/aws/cluster-autoscaler.json) |
| AWS EFS CSI Driver | [iam/aws/efs-csi-driver.json](iam/aws/efs-csi-driver.json) |
| External Secrets | [iam/aws/external-secrets.json](iam/aws/external-secrets.json) |
| AWS Load Balancer Controller | [iam/aws/alb-controller.json](iam/aws/alb-controller.json) |
| KubeBlocks | [iam/aws/kubeblocks.json](iam/aws/kubeblocks.json) |

---

## AWS (EKS) — Terraform runbook

Use this path if you manage cluster infrastructure with Terraform and want addons declared as code.

> **Network access required.** `tofu apply` must run from a machine with connectivity to the EKS OIDC endpoint (same VPC, VPN, Direct Connect, CloudShell, or a cluster with `eks_cluster_endpoint_public_access = true`). See the note in the CLI runbook above.

### 1. Apply the example

```bash
cd examples/terraform/aws-eks-byo
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — set region, cogrion_account_id, cogrion_workspace_id,
# control_plane_url, and at minimum the VPC/subnet settings.
cp backend.hcl.example backend.hcl
# Edit backend.hcl — set your S3 bucket and key.
tofu init -backend-config=backend.hcl
tofu apply
```

This provisions the EKS cluster, node groups, IRSA roles, KubeBlocks IRSA, and installs all addons (Traefik, external-dns, external-secrets, metrics-server, cluster-autoscaler, etc.).

### 2. Register with the CLI

After `tofu apply`, run the CLI to register the cluster and install the agent. Addons and IRSA roles are already in place, so skip those steps:

```bash
cogrion-bootstrap \
  --provider aws \
  --token <bootstrap-token> \
  --cluster-name <eks-cluster-name> \
  --region ap-southeast-1 \
  --no-create-node-group \
  --node-group-name <nodegroup-name> \
  --node-group-label <nodegroup-label-value> \
  --no-create-irsa \
  --no-cluster-autoscaler \
  --no-efs-csi-driver \
  --no-metrics-server \
  --no-external-secrets \
  --no-traefik \
  --no-external-dns \
  --no-cluster-proportional-autoscaler \
  --control-plane-url https://cplane.api.cogrion.com \
  --tofu-backend-bucket <s3-state-bucket>
```

This registers the cluster and installs only the `cplane-agent` — everything else was already applied by Terraform.

---

## Alibaba Cloud (ACK) — coming soon

## Google Cloud (GKE) — coming soon

## Azure (AKS) — coming soon

---

## Addons

Both CLI and Terraform paths install the same addons:

| Addon | Namespace | Version | CLI flag to disable |
|---|---|---|---|
| Cluster Autoscaler | kube-system | 9.57.0 | `--no-cluster-autoscaler` |
| AWS EFS CSI Driver | kube-system | 4.3.0 | `--no-efs-csi-driver` |
| Metrics Server | kube-system | 3.13.1 | `--no-metrics-server` |
| External Secrets | external-secrets | 2.7.0 | `--no-external-secrets` |
| Traefik | traefik | 41.0.2 | `--no-traefik` |
| external-dns | external-dns | 1.21.1 | `--no-external-dns` |
| Cluster Proportional Autoscaler | kube-system | 1.1.0 | `--no-cluster-proportional-autoscaler` |
| AWS Load Balancer Controller | kube-system | — | `--enable-alb-controller` (opt-in) |

**Traefik** replaces ingress-nginx (EOL March 2026). It still speaks `Ingress` resources — no application changes needed, only `ingressClassName: traefik`.

**external-dns** uses the Cloudflare webhook-proxy pattern — the Cloudflare token never leaves the control-plane. external-dns calls the control-plane webhook instead of Cloudflare directly.

**KubeBlocks** is not installed here. It is installed by the `cplane-agent` via KCL stacks after registration. The IRSA role is provisioned (CLI: `ensure_iam`, Terraform: `kubeblocks-irsa.tf`) so that KubeBlocks service accounts can assume it on first start.

### Known issue: cplane-agent HPA

The `cplane-agent` chart's HPA is currently unsafe — every replica force-fails in-flight commands claimed by live siblings (tracked in `project-management#186`). Disable it until fixed:

```bash
--agent-set autoscaling.enabled=false
```

---

## All CLI options

```
--token                One-time bootstrap token (required)
--provider             Cloud provider: aws (required)
--control-plane-url    Override the control plane URL (default: https://cplane.api.cogrion.com)
--namespace            Kubernetes namespace for the agent (default: cogrion-system)
--agent-version        cplane-agent Helm chart version
--node-group-label     Value of the 'nodegroup' k8s node label (nodeSelector.nodegroup on all Helm releases)
--traefik-subnets      Comma-separated public subnet IDs for the Traefik NLB (required unless --no-traefik)
--agent-set            Extra --set override for cplane-agent, KEY=VALUE (repeatable)
--dry-run              Print the plan without executing anything
```
