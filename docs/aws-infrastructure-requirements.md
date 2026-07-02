# AWS Infrastructure Requirements for EKS Clusters

This document describes the AWS networking, IAM, and security group configuration that an existing EKS cluster must have in place for the Cogrion platform bootstrap to succeed. It is derived from the `account-networking/vpc` and `workspace-cluster` Terraform modules.

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
