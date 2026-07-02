# AWS Infrastructure Requirements for EKS Clusters

This document describes the AWS networking, IAM, and security group configuration that an existing EKS cluster must have in place for the Cogrion platform bootstrap to succeed. It is derived from three Terraform modules:

- `account-networking/vpc` — VPC, subnets, NAT Gateway
- `workspace-cluster` — EKS cluster, managed node groups, EKS add-ons
- `workspace-cluster-bootstrap` — IRSA roles, namespaces, storage classes, bootstrap Job

**Sections 1–9** cover infrastructure that must exist before any bootstrap runs. **Sections 10–13** describe what the `workspace-cluster-bootstrap` Terraform module provisions on top of the cluster.

---

## 1. VPC

| Attribute | Requirement |
|---|---|
| CIDR | RFC1918 range, e.g. `10.42.0.0/19` |
| Secondary CIDR | RFC6598 range `100.64.0.0/16` attached to the VPC |
| DNS hostnames | Enabled (`enableDnsHostnames = true`) |
| DNS support | Enabled (`enableDnsSupport = true`) |
| NAT Gateway | At least one NAT Gateway (single or per-AZ) |
| Internet Gateway | Required if public subnets are used (PoC/Dev/Test); optional for fully private |
| Availability zones | Minimum 2 AZs |

---

## 2. Subnets

Four subnet tiers are required. All must exist before the cluster is created.

### 2a. EKS Data Plane Subnets (nodes + pods)

These are carved from the secondary RFC6598 CIDR and are where EKS worker nodes and pods run. VPC-CNI prefix delegation is enabled so each node can serve a large number of pod IPs.

| AZ | CIDR | Notes |
|---|---|---|
| AZ 1 | `100.64.0.0/17` | 32,766 usable IPs |
| AZ 2 | `100.64.128.0/17` | 32,766 usable IPs |

Required subnet tags:

```
kubernetes.io/role/internal-elb = 1
karpenter.sh/discovery           = <vpc-name>
kubernetes.io/cluster/<vpc-name> = shared
```

> These are the subnets passed as `private_subnets` to the cluster module. The module filters by `100.*` CIDR prefix to identify them as EKS data plane subnets.

### 2b. Private Subnets (NAT / NLB / Jumphost)

Routable RFC1918 subnets. Used for internal load balancers, private NAT, and any EC2 instances.

| AZ | Example CIDR | Size |
|---|---|---|
| AZ 1 | `10.42.1.0/24` | 252 IPs |
| AZ 2 | `10.42.2.0/24` | 252 IPs |

Required subnet tags — same as EKS data plane subnets above.

### 2c. Public Subnets (NAT Gateway egress / external load balancers)

Used for NAT Gateway and public-facing load balancers. Can be omitted in fully private deployments.

| AZ | Example CIDR | Size |
|---|---|---|
| AZ 1 | `10.42.0.0/25` | 124 IPs |
| AZ 2 | `10.42.0.128/25` | 124 IPs |

Required subnet tags:

```
kubernetes.io/role/elb                   = 1
kubernetes.io/cluster/<vpc-name>         = shared
```

### 2d. Database Private Subnets

Isolated subnets for RDS and other managed database services. A subnet group named `<vpc-name>-db-subnet` must be created from these.

| AZ | Example CIDR | Size |
|---|---|---|
| AZ 1 | `10.42.4.0/24` | 252 IPs |
| AZ 2 | `10.42.5.0/24` | 252 IPs |

---

## 3. Security Groups

### 3a. Cluster Security Group (EKS-managed)

Created automatically by EKS. The following **additional** inbound rule must exist:

| Rule | Protocol | Port range | Source |
|---|---|---|---|
| Nodes on ephemeral ports | TCP | 1025–65535 | Node security group |

### 3b. Node Security Group

Created automatically by EKS for managed node groups. The following **additional** inbound rules must exist:

| Rule | Protocol | Port range | Source |
|---|---|---|---|
| Node to node (all traffic) | All | 0–65535 | Self (same SG) |
| Cluster API to nodegroup | All | 0–65535 | Cluster security group |

Required security group tags (applied to both cluster and node SGs):

```
karpenter.sh/discovery = <workspace-id>
```

where `<workspace-id>` = `qd-platform-<workspace-id>`.

---

## 4. IAM Roles

### 4a. Cross-account Automation Role (Cogrion control plane)

This role lives in the **Cogrion AWS account** and is assumed by the Tofu/Terraform runner.

| Attribute | Value |
|---|---|
| Name | `cross-account-automation-role` |
| Account | Cogrion core platform account |
| Purpose | Chains to the customer-account automation role |

### 4b. Customer Account Automation Role

This role lives in the **customer AWS account** and is assumed by the cross-account role above.

| Attribute | Value |
|---|---|
| Name pattern | `quant-data-automation-<account-id>` |
| Trust | Cogrion cross-account role |
| External ID | `<account-id>` (prevents confused deputy) |
| Purpose | Provisions the VPC and EKS cluster in the customer account |

### 4c. EKS Cluster IAM Role

Created by the EKS module. Standard AWS-managed policies required:

- `AmazonEKSClusterPolicy`

### 4d. EKS Node IAM Role

Created by the EKS module per managed node group. Standard AWS-managed policies required:

- `AmazonEKSWorkerNodePolicy`
- `AmazonEC2ContainerRegistryReadOnly`
- `AmazonEKS_CNI_Policy`

### 4e. EBS CSI Driver IRSA Role

| Attribute | Value |
|---|---|
| Name pattern | `qd-platform-<workspace-id>-ebs-csi-driver` |
| Attached policy | `AmazonEBSCSIDriverPolicy` |
| Trusted service account | `kube-system:ebs-csi-controller-sa` |
| Trust mechanism | OIDC (IRSA) |

The cluster's OIDC provider must be registered with IAM before this role can be created.

### 4f. KMS Key Administrators

The KMS key used for EKS envelope encryption must grant `kms:*` to:

- `arn:aws:iam::<account-id>:root`
- Any additional roles passed via `kms_key_admin_roles`
- The IAM identity used to run Terraform (auto-discovered via `aws_iam_session_context`)

---

## 5. EKS Cluster Configuration

| Attribute | Value |
|---|---|
| Kubernetes version | `1.36` (default; configurable via `eks_kubernetes_version`) |
| Cluster name | `qd-platform-<workspace-id>` |
| Authentication mode | `API_AND_CONFIG_MAP` |
| Endpoint public access | `true` (sandbox/dev); should be `false` for prod |
| Subnets | EKS data plane subnets only (filtered to `100.*` CIDR) |
| Envelope encryption | KMS — key admins as listed in §4f |

### Managed Add-ons (installed by EKS)

| Add-on | Version | Notes |
|---|---|---|
| `coredns` | latest | |
| `eks-pod-identity-agent` | latest | installed before compute |
| `kube-proxy` | latest | |
| `vpc-cni` | `v1.20.4-eksbuild.3` | prefix delegation enabled; installed before compute |
| `aws-ebs-csi-driver` | `v1.48.0-eksbuild.2` | requires EBS CSI IRSA role (§4e) |

### VPC-CNI Prefix Delegation

The `vpc-cni` add-on must be configured with:

```
ENABLE_PREFIX_DELEGATION = true
WARM_PREFIX_TARGET       = 1
```

---

## 6. Node Groups (Default)

The default node group is named `system` and is used for platform components (autoscaler, load balancer controller, etc.).

| Attribute | Default |
|---|---|
| Instance types | `m5.xlarge` |
| Min / max / desired | 1 / 3 / 1 |
| Disk | 100 GB gp3 |
| Multi-AZ | Yes (all EKS data plane subnets) |
| IMDSv2 | Required (`http_tokens = required`) |
| Hop limit | 2 (required for pods to reach IMDS) |

Node group labels:

```
WorkerType    = ON_DEMAND
NodeGroupType = system
```

Node group tags (required for cluster-autoscaler ASG discovery):

```
karpenter.sh/discovery                        = qd-platform-<workspace-id>
k8s.io/cluster-autoscaler/enabled             = true
k8s.io/cluster-autoscaler/qd-platform-<id>   = owned
```

Stateful node groups (e.g. for EBS-backed PVCs) must be pinned to a single AZ by passing only `eks_secondary_subnet_ids[0]`.

---

## 7. Optional Helm Addons (Day-2, via EKS Blueprints)

These are not required for bootstrap but are wired via `eks_blueprints_addons` in the cluster module. All are deployed to the `system` node group (`nodeSelector.nodegroup = system`).

| Addon | Flag | Notes |
|---|---|---|
| Cluster Autoscaler | `enable_cluster_autoscaler` | chart `9.57.0` |
| AWS Load Balancer Controller | `enable_aws_load_balancer_controller` | `vpcId` injected automatically |
| AWS EFS CSI Driver | `enable_aws_efs_csi_driver` | |
| External Secrets | `enable_external_secrets` | chart `0.18.2` |
| Metrics Server | `enable_metrics_server` | |
| Cluster Proportional Autoscaler | `enable_cluster_proportional_autoscaler` | |
| Karpenter | `enable_karpenter` | disabled by default; managed via KCL stacks |
| External DNS | managed via KCL stacks | not wired here |
| cert-manager | managed via KCL stacks | not wired here |

---

## 8. Resource Tagging

All resources must carry these tags (applied via `default_tags`):

| Tag | Value |
|---|---|
| `PlatformVersion` | `0.0.1` |
| `GithubRepo` | `terraform-workspace-infra-aws` |
| `GithubOrg` | `sparqd` |
| `AccountId` | `<account-id>` |
| `WorkspaceId` | `<workspace-id>` |

---

## 9. Summary: What Must Exist Before Bootstrap

The `cogrion-bootstrap` installer assumes the following are already in place:

1. VPC with secondary `100.64.0.0/16` CIDR block
2. Four subnet tiers (EKS data plane, private, public, database) with correct tags
3. NAT Gateway for outbound internet access from private subnets
4. EKS cluster created and `ACTIVE`, pointed at EKS data plane subnets
5. `api_and_config_map` authentication mode enabled on the cluster
6. EBS CSI IRSA role created and associated with the `aws-ebs-csi-driver` addon
7. Node security group with node-to-node and cluster-to-node ingress rules
8. `karpenter.sh/discovery` tag on both cluster and node security groups
9. Subnet tags (`kubernetes.io/role/internal-elb`, `karpenter.sh/discovery`) on EKS data plane subnets
10. Customer-account automation role with correct trust policy and external ID

---

## 10. Kubernetes Namespaces

The bootstrap module creates three namespaces. They are never deleted by automation — decommissioning requires a manual step.

| Namespace | Purpose |
|---|---|
| `cogrion-system` | Platform agent and all system-level resources |
| `kb-system` | KubeBlocks operator and data-protection workers |

---

## 11. Storage Classes

The bootstrap module reconfigures the default storage class and adds per-AZ classes.

### gp2 (existing)

The default annotation is removed from `gp2` so it no longer competes with `gp3`.

### gp3 (new default)

| Attribute | Value |
|---|---|
| Name | `gp3` |
| Provisioner | `ebs.csi.aws.com` |
| Volume type | `gp3` |
| Filesystem | `xfs` |
| Encryption | `true` |
| Reclaim policy | `Delete` |
| Volume expansion | Enabled |
| Binding mode | `WaitForFirstConsumer` |
| Default class | Yes |

### gp3-az1 … gp3-azN (per-AZ)

One storage class per AZ (up to `az_count`, default 3), named `gp3-az1`, `gp3-az2`, etc.  
Same parameters as `gp3` but with an `allowedTopologies` constraint pinning volumes to a single AZ via `topology.ebs.csi.aws.com/zone`. Use these for stateful workloads that need EBS volume locality guarantees.

---

## 12. IRSA Roles

Three IRSA roles are created during bootstrap. All trust the cluster OIDC provider and require the OIDC provider ARN from the cluster Terraform remote state.

### 12a. Bootstrap Role

| Attribute | Value |
|---|---|
| Name pattern | `qd-platform-<workspace-id>-bootstrap-role` |
| Trusted service account | `cogrion-system:bootstrap-sa` |
| IAM policy | `qd-platform-<workspace-id>-bootstrap-policy` |

**Bootstrap IAM policy** (`bootstrap_policy.json`) — scoped to `arn:aws:s3:::qd-platform-<workspace-id>*`:

| Action | Purpose |
|---|---|
| `s3:CreateBucket`, `s3:DeleteBucket`, `s3:ListBucket` | Manage platform S3 buckets |
| `s3:PutBucketEncryption`, `s3:GetBucketEncryption` | Enforce encryption at rest |
| `s3:PutBucketPublicAccessBlock`, `s3:GetBucketPublicAccessBlock` | Block public access |

### 12b. Cluster Agent Role

| Attribute | Value |
|---|---|
| Name pattern | `qd-platform-<workspace-id>-cluster-agent-role` |
| Trusted service accounts | `cogrion-system:cluster-agent-python-supervisor`, `cogrion-system:cluster-agent-python-worker` |
| IAM policy | `qd-platform-<workspace-id>-cluster-agent-policy` |

**Cluster Agent IAM policy** (`cluster_agent_policy.json`) — broad operational permissions needed for Day-2 stack operations:

| Sid | Actions | Resource scope |
|---|---|---|
| S3 | Full S3 object + bucket lifecycle management | `quant-data-tfstate-<account-id>*`, `qd-platform-<workspace-id>*` |
| EKS | Describe, tag, update cluster/nodegroup/addons; create access entries; pod identity; OIDC; SSM parameter reads | `*` |
| AllowEKSClusterPassRole | `iam:PassRole` | `*` |
| AllowEC2SpotSLRCreation | `iam:CreateServiceLinkedRole` | EC2 Spot SLR only |
| ECRAccess | ECR auth token, layer upload/download; `ecr-public:GetAuthorizationToken` | `*` |
| IAMAccessForTerraform | Create/attach/delete IAM roles, policies, instance profiles | `*` |
| Karpenter | EventBridge rules, SQS queue lifecycle, `iam:GetInstanceProfile` | `*` |
| EC2Access | Launch templates, `RunInstances`, describe instances/subnets/SGs/VPCs, tag management | `*` |
| KMSForOpenBaoUnseal | Full KMS key lifecycle (create, describe, rotate, policy, schedule deletion) | `*` |

### 12c. KubeBlocks Role

| Attribute | Value |
|---|---|
| Name pattern | `qd-platform-<workspace-id>-kubeblocks-role` |
| Trusted service accounts | `kb-system:kubeblocks`, `kb-system:kubeblocks-dataprotection-exec-worker`, `kb-system:kubeblocks-dataprotection-worker` |
| IAM policy | `qd-platform-<workspace-id>-kubeblocks-policy` |

**KubeBlocks IAM policy** (`kubeblocks_policy.json`):

| Sid | Actions | Resource scope |
|---|---|---|
| S3AccessForBackups | `s3:ListBucket`, `s3:GetBucketLocation` | `*` |
| S3ObjectAccess | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts` | `*` |
| EBSVolumeManagement | Create/delete/attach/detach/modify volumes, describe volumes, instances, AZs | `*` |
| EBSCreateTags | `ec2:CreateTags` | `arn:aws:ec2:*:*:volume/*` (only on `CreateVolume`) |
| EBSDeleteTags | `ec2:DeleteTags` | `arn:aws:ec2:*:*:volume/*` |

---

## 13. Tenant Bootstrap Job

The bootstrap Job is a one-shot Kubernetes Job in `cogrion-system` that calls `POST /agent/register` on the Cogrion control plane and sets up the cluster. It is gated on `tenant_bootstrap.enabled` (default `true`) and is replaced whenever `bootstrap_token` changes.

### Kubernetes RBAC

| Resource | Kind | Permissions |
|---|---|---|
| `bootstrap-sa` | ServiceAccount | Annotated with bootstrap IRSA role ARN |
| `bootstrap-role` | Role (namespaced) | `secrets`: create/get/update/patch; `jobs`: get/list |
| `bootstrap-cluster-rb` | ClusterRoleBinding | Binds `bootstrap-sa` to `cluster-admin` for CRD/operator installation |
| `bootstrap-rb` | RoleBinding | Binds `bootstrap-sa` to `bootstrap-role` in `cogrion-system` |

> The `cluster-admin` binding is intentional. The bootstrap Job installs CRDs, operators (KubeBlocks, snapshot-controller), and Helm charts that require cluster-wide permissions. The Job self-destructs after `ttl_seconds_after_finished = 3600`.

### Job spec

| Attribute | Value |
|---|---|
| Image | `alpine/k8s:1.29.2` |
| Restart policy | `OnFailure` |
| Backoff limit | 2 |
| TTL after finished | 3600 s (1 hour) |
| Completion timeout | `bootstrap_job_timeout` (default `6m`) |
| CPU request / limit | `100m` / `200m` |
| Memory request / limit | `128Mi` / `256Mi` |

### Environment variables passed to the Job

| Variable | Source |
|---|---|
| `BOOTSTRAP_TOKEN` | `bootstrap-token` Secret in `cogrion-system` |
| `CONTROL_PLANE_URL` | `tenant_bootstrap.control_plane_url` |
| `CLUSTER_AGENT_ENABLED` | `tenant_bootstrap.cluster_agent_enabled` (default `true`) |
| `CLUSTER_AGENT_VERSION` | `tenant_bootstrap.cluster_agent_version` (default `0.1.6-0.1.61`) |
| `CLUSTER_AGENT_DEV_MODE` | `tenant_bootstrap.cluster_agent_dev_mode` (default `false`) |
| `CLUSTER_AGENT_TS_ENABLED` | `tenant_bootstrap.cluster_agent_ts_enabled` (default `false`) |
| `CLUSTER_AGENT_TS_VERSION` | `tenant_bootstrap.cluster_agent_ts_version` (default `0.1.4-0.1.6`) |
| `S3_REGION` | `client_region` |
| `AWS_ALB_SUBNETS` | Comma-separated list of `public_subnets` IDs |
| `AWS_ALB_GROUP_NAME` | `<workspace-id>-alb` |
| `KUBEBLOCK_BACKUP_S3_BUCKET` | `qd-platform-<workspace-id>-kb-backup` |
| `KUBEBLOCK_BACKUP_S3_REGION` | `client_region` |
| `TOFU_BACKEND_BUCKET` | `backend_tfstate_bucket` |

### Script selection

The Job runs one of two bootstrap scripts based on `cluster_agent_ts_enabled`:

| Flag | Script | Use case |
|---|---|---|
| `false` (default) | `bootstrap.sh` | Standard cluster agent |
| `true` | `bootstrap_v2.sh` | TypeScript cluster agent variant |

The ConfigMap name includes an 8-character SHA256 checksum of the script file, so any script change triggers automatic Job replacement.

### Pre-requisites specific to the bootstrap Job

- Public subnet IDs must be passed via `public_subnets` — the Job uses them to configure the ALB ingress annotation
- `backend_tfstate_bucket` must exist and be accessible by the cluster-agent IRSA role
- The control plane URL (`control_plane_url`) must be reachable from within the cluster
- A valid one-time `bootstrap_token` must be provided — the Job will fail with 401 if the token is expired or already used
