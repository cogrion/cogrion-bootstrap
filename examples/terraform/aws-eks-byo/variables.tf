# --- Shared ------------------------------------------------------------
variable "region" {
  description = "AWS region to provision into"
  type        = string
}

variable "tags" {
  description = "Additional freeform tags, merged with the required Cogrion identifiers below into local.tags"
  type        = map(string)
  default     = {}
}

# Required so Cogrion can match this infrastructure back to the right
# workspace when adopted via byo* fields — first-class variables instead of
# freeform tags map entries so they can't be typo'd or forgotten.
variable "cogrion_account_id" {
  description = "Cogrion account identifier this infrastructure belongs to"
  type        = string
}

variable "cogrion_workspace_id" {
  description = "Cogrion workspace identifier this infrastructure belongs to"
  type        = string
}

# --- Tagging — organizational / operational / compliance / finops ---------
# All have industry-standard defaults so `terraform plan` works out of the
# box for a self-deploy customer — override per your own org's conventions.
# See locals.tf for how these are assembled into tags_account/tags.
variable "extra_tags" {
  description = "Additional freeform tags, purely additive on top of var.tags and the mandatory Cogrion/org/compliance tags below"
  type        = map(string)
  default     = {}
}

variable "cost_center" {
  description = "Finance cost center code for chargeback (e.g. CC-1024)"
  type        = string
  default     = "unassigned"
}

variable "owner" {
  description = "Individual or team accountable for this infrastructure"
  type        = string
  default     = "platform-eng"
}

variable "project_name" {
  description = "Logical project/repo grouping (e.g. cogrion-terraform)"
  type        = string
  default     = "cogrion-workspace"
}

variable "environment" {
  description = "Deployment environment — dev / sandbox / staging / prod, sets the blast-radius boundary"
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "sandbox", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "service_name" {
  description = "Component this infrastructure belongs to (e.g. keycloak, argocd)"
  type        = string
  default     = "workspace-cluster"
}

variable "git_commit_sha" {
  description = "Commit SHA that last applied this resource, for traceability. Normally injected by CI"
  type        = string
  default     = "unknown"
}

variable "data_classification" {
  description = "Data sensitivity of this workspace — public / internal / confidential / restricted (SOC2 CC6.1, ISO A.8.2)"
  type        = string
  default     = "internal"
  validation {
    condition     = contains(["public", "internal", "confidential", "restricted"], var.data_classification)
    error_message = "data_classification must be one of: public, internal, confidential, restricted."
  }
}

variable "contains_pii" {
  description = "Whether this workspace's data includes PII — required for data mapping / GDPR evidence"
  type        = bool
  default     = false
}

variable "backup_policy" {
  description = "Backup cadence/retention evidence for SOC2 availability criteria (e.g. daily-30d)"
  type        = string
  default     = "daily-30d"
}

variable "retention_policy" {
  description = "Data retention window evidence (e.g. 30d)"
  type        = string
  default     = "30d"
}

variable "auto_shutdown" {
  description = "Whether non-prod resources auto-shut-down for cost control"
  type        = bool
  default     = false
}

# --- Dual-mode provider auth ---------------------------------------------
# Leave assume_role_arn empty for self-deploy mode (your own default
# credentials). Set it for assume-role mode (Cogrion's own automation
# assuming into your account) — session_name/external_id are only used then.
variable "assume_role_arn" {
  description = "IAM role ARN to assume. Empty = use default credentials directly (self-deploy mode)."
  type        = string
  default     = ""
}

variable "assume_role_session_name" {
  description = "Session name for the assumed role. Only used when assume_role_arn is set."
  type        = string
  default     = "terraform-access"
}

variable "assume_role_external_id" {
  description = "External ID for the assumed role, if your trust policy requires one. Only used when assume_role_arn is set."
  type        = string
  default     = ""
}

# --- VPC -----------------------------------------------------------------
variable "az_count" {
  description = "Count of availability zones"
  type        = number
  default     = 2
}

variable "vpc_name" {
  description = "VPC name"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR"
  type        = string
}

# Routable public subnets with NAT Gateway and Internet Gateway. Not required
# for fully private clusters.
variable "public_subnets" {
  description = "Public subnet CIDRs. ~124 IPs per subnet/AZ."
  type        = list(string)
}

# Routable private subnets, for Private NAT Gateway -> Transit Gateway ->
# second VPC for overlapping CIDRs.
variable "private_subnets" {
  description = "Private subnet CIDRs. ~252 IPs per subnet/AZ, for Private NAT + NLB + EKS nodes + EC2 jumphost etc."
  type        = list(string)
}

variable "db_private_subnets" {
  description = "Private subnet CIDRs for database services. ~252 IPs per subnet/AZ."
  type        = list(string)
}

# RFC6598 range 100.64.0.0/10 — not publicly routable. Only one /16 (or
# multiples of /16) can be attached to a VPC as a secondary CIDR block.
variable "secondary_cidr_blocks" {
  description = "Secondary CIDR blocks to attach to the VPC"
  type        = list(string)
  default     = ["100.64.0.0/16"]
}

# EKS worker nodes and pods are placed on these subnets. Each gets 32766 IPs.
variable "eks_data_plane_subnet_secondary_cidr" {
  description = "Secondary CIDR blocks for EKS node/pod IPs, ~32766 IPs per subnet/AZ"
  type        = list(string)
  default     = ["100.64.0.0/17", "100.64.128.0/17"]
}

# --- EKS cluster -----------------------------------------------------------
variable "eks_kubernetes_version" {
  description = "EKS control plane Kubernetes version"
  type        = string
  default     = "1.36"
}

variable "eks_cluster_endpoint_public_access" {
  description = "Whether the EKS API server endpoint is publicly reachable. Fine for a sandbox; set false for preprod/prod once you've confirmed private access works."
  type        = bool
  default     = true
}

variable "kms_key_admin_roles" {
  description = "Additional IAM role ARNs to grant admin on the EKS cluster's KMS key, beyond the account root and current caller"
  type        = list(string)
  default     = []
}

variable "control_plane_url" {
  description = "Cogrion control plane API URL — used as the external-dns webhook base URL"
  type        = string
  default     = "https://cplane.api.cogrion.com"
}

variable "system_nodegroup_label" {
  description = "Value of the 'nodegroup' k8s node label on the system node group, used as nodeSelector.nodegroup on all addon Helm releases"
  type        = string
  default     = "system"
}

variable "enable_traefik" {
  description = "Install Traefik ingress controller"
  type        = bool
  default     = true
}

variable "enable_external_dns" {
  description = "Install external-dns with the dns-webhook sidecar (requires control_plane_url). The bootstrap Job copies the cluster-agent-credentials mTLS secret into the external-dns namespace before this installs."
  type        = bool
  default     = true
}

variable "dns_webhook_tag" {
  description = "Image tag for the dns-webhook external-dns sidecar"
  type        = string
  default     = "0.1.6"
}

variable "eks_managed_node_groups" {
  description = "Map of EKS managed node group configurations. Each key becomes the node group's logical name."
  type = map(object({
    name        = optional(string)
    description = optional(string)

    # When true, pins the node group to a single AZ (first subnet only).
    # Required for EBS-backed PVCs to avoid "volume node affinity conflict"
    # when pods are rescheduled across AZs. Defaults to false (multi-AZ
    # spread) for stateless workloads.
    stateful = optional(bool, false)

    min_size     = optional(number, 1)
    max_size     = optional(number, 3)
    desired_size = optional(number, 1)

    instance_types = optional(list(string), ["m5.xlarge"])

    disk_size = optional(number, 50)
    disk_type = optional(string, "gp3")

    ami_release_version            = optional(string)
    use_latest_ami_release_version = optional(bool, false)
    # https://docs.aws.amazon.com/eks/latest/APIReference/API_Nodegroup.html#AmazonEKS-Type-Nodegroup-amiType
    ami_type = optional(string)

    labels = optional(map(string), {})

    taints = optional(map(object({
      key    = string
      value  = string
      effect = string
    })), {})

    tags = optional(map(string), {})
  }))

  default = {
    system = {
      description    = "System EKS managed node group"
      min_size       = 1
      max_size       = 3
      desired_size   = 1
      instance_types = ["m5.xlarge"]
      disk_size      = 100
      labels = {
        WorkerType    = "ON_DEMAND"
        NodeGroupType = "system"
      }
    }
  }
}

variable "eks_addon_versions" {
  description = "EKS managed add-on versions, keyed by add-on name. Defaults are the latest versions compatible with eks_kubernetes_version at the time this example was last updated — override per-key to pin something else."
  type = object({
    coredns                = optional(string, "v1.14.3-eksbuild.3")
    kube_proxy             = optional(string, "v1.36.0-eksbuild.9")
    vpc_cni                = optional(string, "v1.22.3-eksbuild.1")
    eks_pod_identity_agent = optional(string, "v1.3.10-eksbuild.3")
    aws_ebs_csi_driver     = optional(string, "v1.62.0-eksbuild.1")
  })
  default = {}
}

variable "eks_blueprints_addon_versions" {
  description = "Helm chart versions for the eks_blueprints_addons module's sub-addons, keyed by addon name. Defaults are the latest chart versions at the time this example was last updated — override per-key to pin something else."
  type = object({
    cluster_autoscaler              = optional(string, "9.58.0")
    aws_efs_csi_driver              = optional(string, "4.3.0")
    cluster_proportional_autoscaler = optional(string, "1.1.0")
    metrics_server                  = optional(string, "3.13.1")
    aws_load_balancer_controller    = optional(string, "3.4.1")
    external_secrets                = optional(string, "2.7.0")
  })
  default = {}
}

variable "eks_blueprints_addons" {
  description = <<-EOT
    Arbitrary config passed to module "eks_blueprints_addons" (aws-ia/eks-blueprints-addons/aws).
    Accepts any attribute that module supports. For aws_load_balancer_controller,
    vpcId is injected automatically — add extra `set` entries under
    aws_load_balancer_controller.set instead of overriding it wholesale.
    Chart versions are not set here — see eks_blueprints_addon_versions.
    Traefik and external-dns are managed separately in helm-addons.tf, not here.
    cert-manager is not used — wildcard TLS certs are issued by the control-plane
    via ACME/Cloudflare and synced into the cluster via ESO.
  EOT
  type        = any
  default     = {}
}

variable "bootstrap_token" {
  description = "One-time bootstrap token from the control plane. Set to trigger the bootstrap Job; leave empty to skip."
  type        = string
  default     = ""
  sensitive   = true
}

variable "agent_version" {
  description = "cplane-agent Helm chart version (composite tag, e.g. 0.1.13-0.1.30)"
  type        = string
  default     = "0.1.13-0.1.32"
}

variable "terraform_backend_bucket" {
  description = "S3 bucket for OpenTofu remote state (required for stack provisioning)"
  type        = string
  default     = ""
}

# --- OpenBao BYO infra (openbao.tf) ---------------------------
variable "create_required_byo_infra" {
  description = "Provision the namespace/StorageClass/S3 bucket/IRSA role/PV-reattach RBAC that openbao's KCL module would otherwise create itself. Set openbao.byoInfra: true in the workspace's KCL values once this is applied."
  type        = bool
  default     = false
}
