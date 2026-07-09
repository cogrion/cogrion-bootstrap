# Merged from terraform-workspace-infra-aws/modules/workspace-cluster's
# eks.tf (the eks_managed_node_groups block specifically) — split into its
# own file since node group sizing/composition is the thing you'll actually
# tune per-workspace, separate from the cluster-level config in eks-cluster.tf.
#
# Note: these are EKS managed node groups, created as part of module.eks
# itself (eks-cluster.tf references local.eks_managed_node_groups below) —
# not standalone aws_eks_node_group resources. AWS's EKS module manages the
# node group lifecycle as part of the cluster module for a reason: node
# groups need the cluster's OIDC provider/security groups to already exist,
# so they're inherently coupled, not independent resources.

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

locals {
  eks_managed_node_groups = {
    for key, ng in var.eks_managed_node_groups : key => {
      name = coalesce(ng.name, format("%s-%s", var.cogrion_workspace_id, key))

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
        Name = format("%s-ng-%s", key, var.cogrion_workspace_id)

        "karpenter.sh/discovery" = local.platform_id

        # Required for cluster-autoscaler ASG auto-discovery.
        "k8s.io/cluster-autoscaler/enabled"              = "true"
        "k8s.io/cluster-autoscaler/${local.platform_id}" = "owned"
      }, ng.tags)
    }
  }
}
