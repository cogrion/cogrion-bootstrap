locals {
  # Account-level tags — everything that describes the Cogrion account/org
  # this infrastructure belongs to, not any one workspace. Applied via the
  # aws provider's default_tags (versions.tf) so it lands on every resource
  # that provider creates, plus explicitly on vpc.tf (a module, so provider
  # default_tags alone isn't enough for its own tags argument).
  tags_account = merge(

    # ---------------------------------------------------------------------
    # COGRION
    # ---------------------------------------------------------------------
    {
      CogrionAccountId = var.cogrion_account_id
    },

    # ---------------------------------------------------------------------
    # ORGANIZATIONAL — ownership, cost allocation, chargeback mapping
    # ---------------------------------------------------------------------
    {
      BusinessUnit = "platform-eng"   # org/dept this resource rolls up to, for chargeback
      CostCenter   = var.cost_center  # finance cost center code (e.g. CC-1024)
      Owner        = var.owner        # individual or team accountable for this resource
      Project      = var.project_name # logical project/repo grouping (e.g. cogrion-terraform)
      Environment  = var.environment  # dev / staging / prod — blast-radius boundary
    },

    # ---------------------------------------------------------------------
    # OPERATIONAL — traceability, drift detection, ownership of change
    # ---------------------------------------------------------------------
    {
      ManagedBy  = "terraform"         # flags IaC-managed resources; anything untagged = manual/drift
      Region     = var.region          # deployment region (e.g. ap-southeast-1)
      Service    = var.service_name    # component this resource belongs to (e.g. keycloak, argocd)
      Repository = "cogrion-terraform" # source repo that owns this resource's IaC
      GitCommit  = var.git_commit_sha  # commit SHA that last applied this resource, for traceability
    },

    # ---------------------------------------------------------------------
    # SECURITY / COMPLIANCE — SOC2 & ISO 27001 audit evidence
    # ---------------------------------------------------------------------
    {
      ComplianceScope    = "soc2 iso27001"            # which audit frameworks this resource is in scope for — space-separated: AWS tag values disallow commas
      DataClassification = var.data_classification    # public / internal / confidential / restricted — SOC2 CC6.1, ISO A.8.2
      ContainsPII        = tostring(var.contains_pii) # required for data mapping / GDPR evidence
      EncryptionRequired = "true"                     # evidences encryption-at-rest control for audit
      BackupPolicy       = var.backup_policy          # e.g. daily-30d — SOC2 availability criteria evidence
      RetentionPolicy    = var.retention_policy       # data retention window, for retention control evidence
    },

    # ---------------------------------------------------------------------
    # FINOPS — cost hygiene, lifecycle management (not audit-required)
    # ---------------------------------------------------------------------
    {
      AutoShutdown = tostring(var.auto_shutdown) # non-prod cost control
      # ExpirationDate = var.expiration_date # for temp/sandbox resources, drives cleanup automation
      # CreatedDate    = timestamp()         # resource creation timestamp, for lifecycle audits
    },

    # ---------------------------------------------------------------------
    # CALLER-SUPPLIED OVERRIDES / EXTRAS — merged last so they win. var.tags
    # is the original freeform-tags input (kept for backward compatibility
    # with existing tfvars); var.extra_tags is purely additive on top of it.
    # ---------------------------------------------------------------------
    var.tags,
    var.extra_tags
  )

  # Workspace-level tags — tags_account plus the one workspace-specific
  # identifier. Used explicitly on every resource that belongs to this one
  # workspace (IAM policies/roles, EKS cluster/node groups, etc.) rather
  # than relying on default_tags, since those resources' names are already
  # workspace-prefixed and their tags should reflect that same scope.
  tags = merge(local.tags_account, {
    CogrionWorkspaceId = var.cogrion_workspace_id
  })

  cogrion_workspace_prefix = format("cogrion-%s", var.cogrion_workspace_id)
}