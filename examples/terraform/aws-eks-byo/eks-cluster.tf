# Merged from terraform-workspace-infra-aws/modules/workspace-cluster's
# eks.tf + irsa.tf + addons.tf. Node group definitions live separately in
# eks-nodegroups.tf (local.eks_managed_node_groups, referenced below).
#
# Adapted for standalone/customer use:
# - subnet_ids come directly from this example's own module.vpc (vpc.tf),
#   not a separate "existing subnet IDs" variable — the original module
#   assumed VPC provisioning was a prior, separate stage.
# - kubernetes/helm provider auth uses the portable `aws eks get-token`
#   (AWS CLI) instead of Cogrion's internal get-aws-eks-token.sh script,
#   which does a two-hop core/client role assumption specific to Cogrion's
#   own automation and wouldn't exist on a customer's machine.

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_iam_session_context" "current" {
  arn = data.aws_caller_identity.current.arn
}
data "aws_partition" "current" {}

data "aws_subnet" "private" {
  for_each = toset(module.vpc.private_subnets)
  id       = each.value
}

locals {
  # Sort by AZ name so [0] is always az1, ensuring stateful node groups
  # and gp3-az1 EBS volumes land in the same AZ.
  eks_secondary_subnet_ids = [
    for entry in sort([
      for subnet_id in module.vpc.private_subnets :
      "${data.aws_subnet.private[subnet_id].availability_zone}:${subnet_id}"
      if startswith(data.aws_subnet.private[subnet_id].cidr_block, "100.")
    ]) :
    split(":", entry)[1]
  ]
}

locals {
  eks_managed_node_groups = {
    for key, ng in var.eks_managed_node_groups : key => {
      name = coalesce(ng.name, format("%s-%s", local.cogrion_workspace_prefix, key))

      iam_role_name = coalesce(ng.name, format("%s-%s", local.cogrion_workspace_prefix, key))

      # stateful=true -> pin to the first (az1) subnet only.
      # stateful=false -> spread across all EKS data-plane subnets.
      subnet_ids = ng.stateful ? [local.eks_secondary_subnet_ids[0]] : local.eks_secondary_subnet_ids

      min_size     = ng.min_size
      max_size     = ng.max_size
      desired_size = ng.desired_size

      instance_types = ng.instance_types

      use_latest_ami_release_version = ng.use_latest_ami_release_version
      ami_release_version            = try(ng.ami_release_version, null)
      ami_type                       = ng.ami_type

      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 2
      }

      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size = ng.disk_size
            volume_type = ng.disk_type
          }
        }
      }

      labels = ng.labels
      taints = ng.taints

      tags = merge({
        Name = format("%s-ng-%s", key, local.cogrion_workspace_prefix)

        "karpenter.sh/discovery" = local.cogrion_workspace_prefix

        # Required for cluster-autoscaler ASG auto-discovery.
        "k8s.io/cluster-autoscaler/enabled"                           = "true"
        "k8s.io/cluster-autoscaler/${local.cogrion_workspace_prefix}" = "owned"
      }, ng.tags)
    }
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.1.5"

  name               = local.cogrion_workspace_prefix
  kubernetes_version = var.eks_kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = local.eks_secondary_subnet_ids

  # WARNING: avoid in preprod/prod — fine for a sandbox. Flip to false once
  # the cluster is up and you've confirmed private access works.
  endpoint_public_access = var.eks_cluster_endpoint_public_access

  authentication_mode                      = "API_AND_CONFIG_MAP"
  enable_cluster_creator_admin_permissions = true

  #---------------------------------------
  # Amazon EKS Managed Add-ons
  #---------------------------------------
  addons = {
    coredns = {
      addon_version = var.eks_addon_versions.coredns
    }
    eks-pod-identity-agent = {
      before_compute = true
      addon_version  = var.eks_addon_versions.eks_pod_identity_agent
    }
    kube-proxy = {
      addon_version = var.eks_addon_versions.kube_proxy
    }
    vpc-cni = {
      before_compute = true
      preserve       = true
      addon_version  = var.eks_addon_versions.vpc_cni
      configuration_values = jsonencode({
        env = {
          # https://docs.aws.amazon.com/eks/latest/userguide/cni-increase-ip-addresses.html
          ENABLE_PREFIX_DELEGATION = "true"
          WARM_PREFIX_TARGET       = "1"
        }
      })
    }
    aws-ebs-csi-driver = {
      service_account_role_arn = module.ebs_csi_driver_irsa.iam_role_arn
      addon_version            = var.eks_addon_versions.aws_ebs_csi_driver
    }
  }

  # Root account, current caller, and any additional admin roles get access
  # to the cluster's KMS key.
  kms_key_administrators = distinct(concat(
    ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"],
    var.kms_key_admin_roles,
    [data.aws_iam_session_context.current.issuer_arn]
  ))

  security_group_additional_rules = {
    ingress_nodes_ephemeral_ports_tcp = {
      description                = "Nodes on ephemeral ports"
      protocol                   = "tcp"
      from_port                  = 1025
      to_port                    = 65535
      type                       = "ingress"
      source_node_security_group = true
    }
  }

  node_security_group_additional_rules = {
    ingress_self_all = {
      description = "Node to node all ports/protocols"
      protocol    = "-1"
      from_port   = 0
      to_port     = 0
      type        = "ingress"
      self        = true
    }
    ingress_cluster_to_node_all_traffic = {
      description                   = "Cluster API to nodegroup all traffic"
      protocol                      = "-1"
      from_port                     = 0
      to_port                       = 0
      type                          = "ingress"
      source_cluster_security_group = true
    }
  }

  # See eks-nodegroups.tf for how this map is built.
  eks_managed_node_groups = local.eks_managed_node_groups

  security_group_tags = {
    "karpenter.sh/discovery" = local.cogrion_workspace_prefix
  }
  node_security_group_tags = {
    "karpenter.sh/discovery" = local.cogrion_workspace_prefix
  }

  tags = local.tags
}

#---------------------------------------------------------------
# IRSA for EBS CSI Driver
#---------------------------------------------------------------
module "ebs_csi_driver_irsa" {
  source                = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version               = "~> 5.52"
  role_name             = format("%s-%s", local.cogrion_workspace_prefix, "ebs-csi-driver")
  attach_ebs_csi_policy = true
  oidc_providers = {
    main = {
      provider_arn = module.eks.oidc_provider_arn
      namespace_service_accounts = [
        "kube-system:ebs-csi-controller-sa",
      ]
    }
  }
}

#---------------------------------------------------------------
# Cluster access for the kubernetes/helm providers below, and for the
# eks_blueprints_addons module (Helm releases onto the cluster we just made).
#---------------------------------------------------------------
provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
  }
}

provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
    }
  }
}

# ---------------------------------------------------------
# EKS Blueprints Addons
# ---------------------------------------------------------
locals {
  _blueprints_config = merge(
    {},
    var.eks_blueprints_addons
  )
}

module "eks_blueprints_addons" {
  source  = "aws-ia/eks-blueprints-addons/aws"
  version = "1.23.0"

  providers = {
    kubernetes = kubernetes
    helm       = helm
  }

  cluster_name      = module.eks.cluster_name
  cluster_endpoint  = module.eks.cluster_endpoint
  cluster_version   = module.eks.cluster_version
  oidc_provider_arn = module.eks.oidc_provider_arn

  # chart 9.37.0+ adds RBAC for resource.k8s.io (deviceclasses, resourceclaims)
  # required by the autoscaler's DRA watch loops — without it the autoscaler
  # logs "Failed to watch" errors. Override via eks_blueprints_addons.cluster_autoscaler.
  enable_cluster_autoscaler = try(local._blueprints_config.enable_cluster_autoscaler, false)
  cluster_autoscaler = merge(
    try(var.eks_blueprints_addons.cluster_autoscaler, {}),
    {
      chart_version = var.eks_blueprints_addon_versions.cluster_autoscaler
    }
  )

  enable_aws_efs_csi_driver = try(local._blueprints_config.enable_aws_efs_csi_driver, false)
  aws_efs_csi_driver = merge(
    try(var.eks_blueprints_addons.aws_efs_csi_driver, {}),
    {
      chart_version = var.eks_blueprints_addon_versions.aws_efs_csi_driver
      set = concat(
        try(var.eks_blueprints_addons.aws_efs_csi_driver.set, []),
        [{ name = "nodeSelector.nodegroup", value = "system" }]
      )
    }
  )

  enable_cluster_proportional_autoscaler = try(local._blueprints_config.enable_cluster_proportional_autoscaler, false)
  cluster_proportional_autoscaler = merge(
    try(var.eks_blueprints_addons.cluster_proportional_autoscaler, {}),
    {
      chart_version = var.eks_blueprints_addon_versions.cluster_proportional_autoscaler
      set = concat(
        try(var.eks_blueprints_addons.cluster_proportional_autoscaler.set, []),
        [
          { name = "nodeSelector.nodegroup", value = "system" },
          # Chart requires options.target to be one of
          # deployment/replicationcontroller/replicaset — CoreDNS is the
          # standard thing this addon scales. Override via
          # eks_blueprints_addons.cluster_proportional_autoscaler.set if
          # you're autoscaling something else.
          { name = "options.target", value = "deployment/coredns" },
        ]
      )
      # Chart also requires a scaling mode config (ladder or linear) — these
      # are the standard linear-mode defaults for CoreDNS: 1 replica per 256
      # cores or 16 nodes, whichever is larger, capped between 1 and 100.
      values = concat(
        try(var.eks_blueprints_addons.cluster_proportional_autoscaler.values, []),
        [yamlencode({
          config = {
            linear = {
              coresPerReplica           = 256
              nodesPerReplica           = 16
              min                       = 1
              max                       = 100
              preventSinglePointFailure = true
            }
          }
        })]
      )
    }
  )

  enable_metrics_server = try(local._blueprints_config.enable_metrics_server, false)
  metrics_server = merge(
    try(var.eks_blueprints_addons.metrics_server, {}),
    {
      chart_version = var.eks_blueprints_addon_versions.metrics_server
      set = concat(
        try(var.eks_blueprints_addons.metrics_server.set, []),
        [{ name = "nodeSelector.nodegroup", value = "system" }]
      )
    }
  )

  enable_aws_load_balancer_controller = try(local._blueprints_config.enable_aws_load_balancer_controller, false)
  aws_load_balancer_controller = merge(
    try(var.eks_blueprints_addons.aws_load_balancer_controller, {}),
    {
      chart_version = var.eks_blueprints_addon_versions.aws_load_balancer_controller
      set = concat(
        try(var.eks_blueprints_addons.aws_load_balancer_controller.set, []),
        [
          { name = "vpcId", value = module.vpc.vpc_id },
          { name = "podDisruptionBudget.maxUnavailable", value = "1" },
          { name = "enableServiceMutatorWebhook", value = "false" },
          { name = "nodeSelector.nodegroup", value = "system" },
        ]
      )
    }
  )

  # Traefik and external-dns are managed in helm-addons.tf (standalone helm_release),
  # not here. cert-manager is not used — see kubeblocks-irsa.tf comment.

  enable_karpenter = false

  enable_external_secrets = try(local._blueprints_config.enable_external_secrets, false)
  external_secrets = merge(
    try(var.eks_blueprints_addons.external_secrets, {}),
    {
      chart_version = var.eks_blueprints_addon_versions.external_secrets
      set = concat(
        try(var.eks_blueprints_addons.external_secrets.set, []),
        [{ name = "nodeSelector.nodegroup", value = "system" }]
      )
    }
  )

  tags = local.tags

  depends_on = [module.vpc]
}
