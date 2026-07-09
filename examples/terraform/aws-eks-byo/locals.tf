locals {
  # Every resource in this example is tagged with these — var.tags for
  # anything freeform the customer wants, merged with the Cogrion identifiers
  # that must always be present.
  tags_account = merge(var.tags, {
    CogrionAccountId   = var.cogrion_account_id
    CogrionWorkspaceId = var.cogrion_workspace_id
  })

  tags = merge(var.tags, {
    CogrionAccountId   = var.cogrion_account_id
    CogrionWorkspaceId = var.cogrion_workspace_id
  })

  # EKS cluster name / node group naming prefix — must match what KCL's
  # ObservabilityConfig.platformId (and every other module's) expects, since
  # that's the single naming identifier used everywhere on the Cogrion side.
  platform_id = format("cogrion-%s", var.cogrion_workspace_id)
}
