# Backend is intentionally empty here — fill it in via `-backend-config=backend.hcl`
# (copy backend.hcl.example) or -backend-config= flags at init time, not hardcoded,
# so the same config works across environments/accounts. See README for the
# one-time bucket setup this backend expects to already exist. Locking uses
# native S3 conditional writes (use_lockfile in backend.hcl) — no DynamoDB
# table; that pattern is deprecated in favor of this. Terraform added it in
# 1.11; OpenTofu added it in its own 1.10 (version tracks don't line up
# between the two) — 1.10.0 is the floor that works for both.
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.35.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.0"
    }
  }
}

# Dual-mode: self-deploy (default) or assume-role (Cogrion's own automation).
# Leave assume_role_arn empty to use your own default credentials directly —
# the assume_role block is omitted entirely. Set it and this provider assumes
# into the target account instead, same as Cogrion's internal automation does.
# Terraform's `dynamic` block is allowed in a `provider` block as long as it's
# driven by variables, not resource/data output — provider config must be
# resolvable before anything else is read.
provider "aws" {
  region = var.region

  dynamic "assume_role" {
    for_each = var.assume_role_arn != "" ? [1] : []
    content {
      role_arn     = var.assume_role_arn
      session_name = var.assume_role_session_name
      external_id  = var.assume_role_external_id != "" ? var.assume_role_external_id : null
    }
  }

  # Applies to every resource this provider creates, including inside child
  # modules — so new resources (EKS, node groups, IAM roles, ...) get tagged
  # automatically as this example grows, without threading `tags = local.tags`
  # into each one by hand.
  default_tags {
    tags = local.tags_account
  }
}
