# Cogrion Platform — Customer-Provisioned AWS Infrastructure Checklist

**Revision date:** 2026-07-06

## Purpose

This checklist is for customers whose security policy requires that all cloud infrastructure — networking, IAM, storage, and Kubernetes cluster-level objects — be created exclusively by their own personnel, with no exceptions for third-party tooling or automation.

Under this model, Cogrion does not run Terraform or apply Kubernetes manifests against your account. Instead, your team provisions everything listed below, and Cogrion's installation is limited to applying Helm chart values against resources that already exist. This document tells you exactly what to create and what information to hand back to Cogrion once each piece is in place.

Work through the sections in order — later sections (IAM roles, storage classes) depend on identifiers produced by earlier ones (VPC, cluster).

---

## 1. Networking

### 1.1 VPC

| Requirement | Value |
|---|---|
| Primary CIDR | RFC1918 range, e.g. `10.42.0.0/19` |
| Secondary CIDR | RFC6598 range `100.64.0.0/16`, attached to the VPC |
| DNS hostnames | Enabled |
| DNS support | Enabled |
| Availability zones | Minimum 2 |
| NAT Gateway | At least one (single or per-AZ) for outbound internet access from private subnets |
| Internet Gateway | Required only if public subnets are used |

### 1.2 Subnets

Four subnet tiers, each spanning all AZs in use:

| Tier | Purpose | Example CIDR (AZ1 / AZ2) | Required tags |
|---|---|---|---|
| EKS data plane | Where EKS nodes and pods run; carved from the secondary `100.64.0.0/16` CIDR | `100.64.0.0/17` / `100.64.128.0/17` | `kubernetes.io/role/internal-elb=1`, `karpenter.sh/discovery=<vpc-name>`, `kubernetes.io/cluster/<vpc-name>=shared` |
| Private | Internal load balancers, private NAT, jump hosts | `10.42.1.0/24` / `10.42.2.0/24` | same as above |
| Public | NAT Gateway egress, public-facing load balancers (omit for fully private deployments) | `10.42.0.0/25` / `10.42.0.128/25` | `kubernetes.io/role/elb=1`, `kubernetes.io/cluster/<vpc-name>=shared` |
| Database | Isolated subnets for managed database services; group into a subnet group named `<vpc-name>-db-subnet` | `10.42.4.0/24` / `10.42.5.0/24` | — |

**Hand back to Cogrion:** VPC ID, EKS data-plane subnet IDs, database service CIDR (see [3.1](#31-cluster)), public subnet IDs (if using ALB ingress, see [7](#7-load-balancer-dns-and-sso-if-using-ingress-fronted-services-eg-grafana)).

---

## 2. S3 Buckets

Every bucket a component would otherwise create for itself must be pre-created by your team, with the exact name below. `{platform_id}` is the workspace identifier your Cogrion contact confirms at kickoff.

| Bucket Name Pattern | Used By | On Decommission | Purpose |
|---|---|---|---|
| `quant-data-tfstate-{account_id}` | Platform/cluster-agent operations | Retain | Terraform/OpenTofu remote state backend |
| `{platform_id}-default-wh` | Trino, Hive Metastore, JupyterHub | Retain | Default data warehouse |
| `{platform_id}-workspace` | JupyterHub, Workspace File Management, QuantData BFF, Superset, Delta-Spark, ML Platform | Retain | Shared user workspace storage |
| `{platform_id}-log-archive` | Fluent Bit | Retain | Long-term audit log archival |
| `{platform_id}-airflow-logs` | Airflow | Retain | Task and scheduler remote logging |
| `qd-platform-{ext_workspace_id}-cubestore` | Cube | Retain | CubeStore pre-aggregation cache |
| `{platform_id}-openbao-snapshots` | OpenBao | Retain | Raft snapshot backups |
| `{platform_id}-spark-event-logs` | Spark Operator, Spark History Server | Retain | Spark driver/executor event logs (shared by both) |
| `{platform_id}-superset-async-results` | Superset | Retain | Async query result cache |
| `{platform_id}-trino-exchange-bucket` | Trino | Retain | Fault-tolerant execution data exchange |
| `{platform_id}-mlflow-artifact-bucket` | MLflow | Retain | Model artifacts and experiment data |
| `{platform_id}-thanos` | Observability (Prometheus/Thanos) | Retain | Long-term Prometheus metrics storage |
| `{platform_id}-loki-chunks` | Observability (Loki) | Retain | Log chunks (log content) |
| `{platform_id}-loki-ruler` | Observability (Loki) | Retain | Ruler state (alerting rules) |
| `{platform_id}-velero-backup` | Velero | Safe to delete | Cluster backup storage — the only bucket Cogrion's own automation would otherwise delete on stack removal |
| `qd-platform-{workspace_id}-kb-backup` | KubeBlocks | Retain | Database backup storage |

**Hand back to Cogrion:** confirmation that each bucket relevant to your deployment exists.

---

## 3. EKS Cluster

### 3.1 Cluster

| Requirement | Value |
|---|---|
| Kubernetes version | 1.32 or later |
| Authentication mode | `API_AND_CONFIG_MAP` |
| Subnets | EKS data-plane subnets only ([1.2](#12-subnets)) |
| Envelope encryption | KMS key, with your account's key administrators granted `kms:*` |
| Endpoint access | Private preferred for production; public access acceptable for non-production |

**Managed add-ons to install:**

| Add-on | Notes |
|---|---|
| `coredns` | |
| `eks-pod-identity-agent` | Install before compute nodes are added |
| `kube-proxy` | |
| `vpc-cni` | Enable prefix delegation: `ENABLE_PREFIX_DELEGATION=true`, `WARM_PREFIX_TARGET=1`. Install before compute nodes are added |
| `aws-ebs-csi-driver` | Requires the EBS CSI IRSA role ([4.3](#43-ebs-csi-driver-role)) |

**Hand back to Cogrion:** Cluster name, cluster API endpoint, the cluster's Kubernetes service CIDR (`kubectl cluster-info dump | grep -i service-cluster-ip-range`, or from your VPC-CNI/cluster config), OIDC provider ARN (needed for [4.3](#43-ebs-csi-driver-role)–[4.4](#44-platform-level-irsa-roles)).

### 3.2 Security Groups

The cluster and node security groups are typically created automatically by EKS. Confirm these additional rules exist:

| Security group | Rule | Protocol | Port range | Source |
|---|---|---|---|---|
| Cluster SG | Nodes on ephemeral ports | TCP | 1025–65535 | Node security group |
| Node SG | Node to node (all traffic) | All | 0–65535 | Self (same SG) |
| Node SG | Cluster API to node group | All | 0–65535 | Cluster security group |

**Hand back to Cogrion:** Cluster security group ID, node security group ID.

### 3.3 Node Groups

At least one managed node group for general platform components:

| Attribute | Recommended default |
|---|---|
| Instance types | `m5.xlarge` or equivalent |
| Min / max / desired | 1 / 3 / 1 |
| Disk | 100 GB, `gp3` |
| AMI | Standard EKS-optimized AMI |
| IMDSv2 | Required (`http_tokens=required`), hop limit 2 |
| Multi-AZ | Spread across all EKS data-plane subnets |

Additional dedicated node groups (e.g. for observability, Karpenter-managed compute) may be requested per-component during onboarding — Cogrion will specify sizing and taints/labels at that time.

---

## 4. IAM Roles

All IRSA (IAM Roles for Service Accounts) roles below require your cluster's OIDC provider ([3.1](#31-cluster)) to be registered with IAM before they can be created.

### 4.1 EKS Cluster Role

Standard AWS-managed policy: `AmazonEKSClusterPolicy`.

### 4.2 EKS Node Role

Standard AWS-managed policies: `AmazonEKSWorkerNodePolicy`, `AmazonEC2ContainerRegistryReadOnly`, `AmazonEKS_CNI_Policy`.

### 4.3 EBS CSI Driver Role

| Attribute | Value |
|---|---|
| Trusted service account | `kube-system:ebs-csi-controller-sa` |
| Attached policy | `AmazonEBSCSIDriverPolicy` |

### 4.4 Platform-Level IRSA Roles

These support the Cogrion platform agent and cluster lifecycle itself, not an individual data/analytics component. Trust-scoped to your cluster's OIDC provider and the listed service account(s).

| Role Name Pattern | Key AWS Permissions | Purpose |
|---|---|---|
| `{platform_id}-bootstrap-role` | S3: CreateBucket, DeleteBucket, PutBucketEncryption, PutBucketPublicAccessBlock on `{platform_id}*` buckets | One-time bootstrap Job; creates and configures platform S3 buckets |
| `{platform_id}-kubeblocks-role` | S3: full CRUD on backup and tfstate buckets; EC2: volume create/attach/detach/delete; KMS: key create and manage | KubeBlocks operator; manages EBS volumes for database PVCs and writes encrypted backups to S3 |
| `{platform_id}-cluster-agent-role` | S3: full CRUD; EKS: full cluster/nodegroup/addon/access-entry lifecycle; EC2: launch template and instance management; KMS: full key lifecycle; ECR: image pull; SQS/Events: Karpenter interruption handling | Cluster Agent service account; owns the full EKS cluster lifecycle, KMS-based OpenBao unseal, and Karpenter node provisioning on behalf of the control plane |
| `{platform_id}-ebs-csi-driver` | AWS managed `AmazonEBSCSIDriverPolicy` | EBS CSI driver; provisions and attaches EBS volumes for PersistentVolumeClaims (same role as [4.3](#43-ebs-csi-driver-role)) |

> Under the restricted model, these roles cannot be assumed cross-account by Cogrion automation — your team creates them directly and hands Cogrion the resulting ARNs, the same as every other IRSA role in this document.

### 4.5 Per-Component IRSA Roles

Each data/analytics component that reads/writes S3 (or other AWS services) needs its own IAM role, trust-scoped to a specific namespace + service account, created via the shared `infra/aws/eks-irsa` pattern and bound to a Kubernetes service account via the `eks.amazonaws.com/role-arn` annotation. Only provision the ones for components you're actually deploying — confirm the exact list with your Cogrion contact during onboarding.

| Role Name Pattern | Component | Namespace | Service Account | Key AWS Permissions | Purpose |
|---|---|---|---|---|---|
| `{cluster_name}-airflow` | Airflow | `airflow` | `airflow-sa` | S3: List/Get/Put/Delete on `{platform_id}-airflow-logs`; SecretsManager: GetSecretValue, DescribeSecret on `airflow/*` | Remote task/scheduler logging; SecretsManager backend for connections/variables |
| `{cluster_name}-jupyterhub` | JupyterHub | `jupyterhub` | `jupyterhub` | S3: full CRUD on warehouse, workspace, and spark-event-logs buckets | User notebook pods; read/write workspace bucket, read warehouse and Spark event log data |
| `{cluster_name}-spark-history-server` | Spark History Server | `spark-history-server` | `spark-history-server-sa` | S3: Get/List on `{platform_id}-spark-event-logs` | Reads completed Spark event logs to render the history UI |
| `{cluster_name}-workspace-file-management` | Workspace File Management | `cogrion-system` | `workspace-file-management` | S3: Get/Put/Delete/List, multipart upload on `{platform_id}-workspace` | User file upload/download/delete |
| `{cluster_name}-hive_metastore` | Hive Metastore | `hive-metastore` | `hive-metastore` | S3: Get/Put/List/Delete on `{platform_id}-default-wh` | Reads/writes table data files in the default warehouse bucket |
| `{cluster_name}-cubestore` | Cube | `cube` | `cubestore-sa` | S3: Get/Put/Delete/List, GetBucketLocation on `{platform_id}-cubestore` | CubeStore router/workers persist pre-aggregated query results |
| `{cluster_name}-velero` | Velero | `velero` | `velero` | S3: Get/Put/Delete/List, AbortMultipartUpload, object tagging, lifecycle config on backup bucket; EC2: DescribeVolumes, CreateSnapshot, DeleteSnapshot, CreateTags | Cluster backups to S3; EBS volume snapshots for stateful workloads |
| `{cluster_name}-mlflow` | MLflow | `mlflow` | `mlflow-sa` | S3: Get/Put/List/Delete on `{platform_id}-mlflow-artifact-bucket` | Stores model artifacts, parameters, experiment data |
| `{cluster_name}-openbao` | OpenBao | `openbao` | `openbao` | KMS: Encrypt/Decrypt/DescribeKey/CreateKey/CreateAlias/EnableKeyRotation; SecretsManager: CreateSecret/PutSecretValue/GetSecretValue; S3: full CRUD on `{platform_id}-openbao-snapshots` | KMS auto-unseal, bootstrap init secret, Raft snapshots to S3 |
| `{cluster_name}-observability-prometheus` | Prometheus/Thanos | `observability` | `prometheus` | S3: Get/Put/List/Delete on `{platform_id}-thanos` | Thanos sidecar uploads metric blocks for long-term retention |
| `{cluster_name}-observability-loki` | Loki | `observability` | `loki` | S3: Get/Put/List/Delete on `{platform_id}-loki-chunks` and `{platform_id}-loki-ruler` | Writes log chunks and alerting ruler state to S3 |
| `{cluster_name}-observability-fluent-bit` | Fluent Bit | `observability` | `fluent-bit` | S3: Get/Put/List/Delete on `{platform_id}-log-archive` | Ships collected audit logs to the S3 log archive bucket |
| `{cluster_name}-datahub-seed` | DataHub (one-time seed Job) | `datahub` | `datahub-seed-sa` | SecretsManager: CreateSecret, PutSecretValue, DescribeSecret on `airflow-*` | Creates the Airflow connection secret during DataHub initial setup |
| `{cluster_name}-quantdata-bff` | QuantData BFF | `cogrion-system` | `sparqd-bff-api` | S3: Get/Put/List/Delete on `{platform_id}-workspace` | Reads/writes workspace data on behalf of the frontend |
| `{cluster_name}-{team_name}` | Spark Team (per team) | `<team-name>` | `<team-name>` | S3: full CRUD (`*`); CloudWatch Logs: CreateLogGroup/CreateLogStream/PutLogEvents; S3 Tables: full namespace/table lifecycle | Per-team Spark workload identity; broad S3 access, log emission, Iceberg/Delta Lake S3 Table operations |
| `{platform_id}-karpenter-role` | Karpenter (controller) | `karpenter` | *(controller service account — confirm exact name with Cogrion contact)* | EC2: RunInstances/TerminateInstances/CreateFleet/CreateLaunchTemplate; SQS: ReceiveMessage/DeleteMessage on interruption queue; IAM: PassRole to node role | Provisions and terminates EC2 nodes on demand |
| `{platform_id}-karpenter-node-role` | Karpenter (node instance profile) | `karpenter` | *(EC2 instance profile — not service-account-scoped)* | AWS managed: AmazonEKSWorkerNodePolicy, AmazonEC2ContainerRegistryReadOnly, AmazonEKS_CNI_Policy, AmazonSSMManagedInstanceCore | Instance profile for Karpenter-provisioned nodes to join the cluster and pull images |

`{platform_id}` / `{cluster_name}` is the workspace identifier your Cogrion contact will confirm at kickoff (they are the same value unless you've overridden the cluster name).

**Hand back to Cogrion:** the IAM role ARN for each role above that applies to your deployment.

---

## 5. Storage Classes

| Storage class | Provisioner | Type | Filesystem | Encrypted | Reclaim policy | Notes |
|---|---|---|---|---|---|---|
| `gp3` (default) | `ebs.csi.aws.com` | `gp3` | `xfs` | Yes | `Delete` | Volume expansion enabled, `WaitForFirstConsumer` binding mode; set as cluster default |
| `gp3-az1`, `gp3-az2`, … (one per AZ) | `ebs.csi.aws.com` | `gp3` | `xfs` | Yes | `Delete` | Same as above, plus an `allowedTopologies` constraint pinning volumes to a single AZ — used by stateful workloads (Prometheus, Loki, Grafana) that require AZ-local EBS volumes |

**Hand back to Cogrion:** confirmation of storage class names, if different from the above.

---

## 6. Kubernetes Namespaces

Create the two platform-system namespaces, plus the exact namespace for every component you plan to deploy:

| Namespace | Created For |
|---|---|
| `cogrion-system` | Platform agent and system-level resources |
| `kb-system` | Database operator (KubeBlocks) and data-protection workers |
| `observability` | Prometheus, Loki, Grafana, log shippers |
| `karpenter` | Cluster autoscaling |
| `airflow` | Airflow |
| `celeborn` | Celeborn |
| `cube` | Cube |
| `datahub` | DataHub |
| `hive-metastore` | Hive Metastore |
| `jupyterhub` | JupyterHub |
| `kafka` | Kafka |
| `mlflow` | MLflow |
| `openbao` | OpenBao |
| `ranger` | Ranger |
| `spark-history-server` | Spark History Server |
| `spark-operator` | Spark Operator |
| `superset` | Superset |
| `trino` | Trino |
| `velero` | Velero |
| `cogrion-system` | Agent-Based ML Platform, Chatbot Backend, Dashboard Access Management, Delta-Spark Product Capabilities, Ontology Backend, Pipeline Backend, QuantData BFF, Text-to-SQL, Workflow Backend, Workspace File Management — these components share a single namespace |
| `<team-name>` | Spark Team — one namespace per Spark team, named after the team |

**Hand back to Cogrion:** confirmation that each namespace exists, and any deviation from the names above.

---

## 7. Load Balancer, DNS, and SSO (if using ingress-fronted services, e.g. Grafana)

| Requirement | Notes |
|---|---|
| Application Load Balancer (or your ingress equivalent) | Provisioned in the public subnets from [1.2](#12-subnets) |
| ACM certificate | Attached to the ALB's HTTPS listener |
| DNS base domain | A domain/subdomain we can create records under (e.g. `<component>.<workspace>.yourdomain.com`) |
| OIDC provider (Keycloak or equivalent) | Reachable from inside the cluster, if single sign-on is required for dashboards |

**Hand back to Cogrion:** ALB group/tag name, subnet IDs the ALB uses, ACM certificate ARN, DNS base domain, OIDC realm/base URL, your account identifier for the OIDC realm.

---

## 8. Summary — Information to Return to Cogrion

Once provisioning is complete, please send us:

1. VPC ID, EKS data-plane subnet IDs, public subnet IDs, cluster service CIDR
2. Confirmation of S3 buckets created ([2](#2-s3-buckets))
3. EKS cluster name, OIDC provider ARN, cluster security group ID, node security group ID
4. IAM role ARN for each platform-level role ([4.4](#44-platform-level-irsa-roles)) and per-component role ([4.5](#45-per-component-irsa-roles)) that applies to your deployment
5. Confirmation of storage class names ([5](#5-storage-classes))
6. Confirmation of namespaces created ([6](#6-kubernetes-namespaces))
7. ALB group/tag name, ALB subnet IDs, ACM certificate ARN, DNS base domain, OIDC base URL and account identifier ([7](#7-load-balancer-dns-and-sso-if-using-ingress-fronted-services-eg-grafana), if applicable)

Cogrion will use these values to configure your deployment. No infrastructure creation is performed on our side under this model — every value above must correspond to a resource your team has already created.
