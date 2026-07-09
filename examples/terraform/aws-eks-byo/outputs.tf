output "vpc_id" {
  value = module.vpc.vpc_id
}

output "private_subnets" {
  value = module.vpc.private_subnets
}

output "public_subnets" {
  value = module.vpc.public_subnets
}

# --- EKS cluster -----------------------------------------------------------
# Feed these into your KCL flavor's byo* fields (ObservabilityConfig etc.) so
# Cogrion adopts this cluster/node-group instead of provisioning its own.

output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "cluster_certificate_authority_data" {
  value     = module.eks.cluster_certificate_authority_data
  sensitive = true
}

output "cluster_version" {
  value = module.eks.cluster_version
}

output "cluster_oidc_provider_arn" {
  value     = module.eks.oidc_provider_arn
  sensitive = true
}

output "cluster_primary_security_group_id" {
  value = module.eks.cluster_primary_security_group_id
}

output "cluster_service_cidr" {
  value = module.eks.cluster_service_cidr
}

output "node_security_group_id" {
  value = module.eks.node_security_group_id
}

output "cluster_secondary_subnet_ids" {
  value = local.eks_secondary_subnet_ids
}

output "eks_managed_node_groups" {
  description = "Map of attribute maps for all EKS managed node groups created"
  value       = module.eks.eks_managed_node_groups
}

output "cluster_status" {
  value = module.eks.cluster_status
}

output "ebs_csi_driver_irsa_role_arn" {
  value = module.ebs_csi_driver_irsa.iam_role_arn
}
