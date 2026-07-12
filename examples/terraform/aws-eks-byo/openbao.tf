# ---------------------------------------------------------------------------
# OpenBao BYO infra
#
# Provisions the namespace/StorageClass/S3 bucket/IRSA role/PV-reattach RBAC
# that openbao's KCL module (kcl/applications/openbao/main.k) would
# otherwise create itself. Set `openbao.byoInfra: true` (or the workspace-
# wide `byoInfra: true`) in the workspace's KCL values once this is applied,
# so the KCL module skips creating this infra and just uses it. See
# platform-stacks/kcl/applications/openbao/README.md for the full naming
# contract this file has to satisfy.
# ---------------------------------------------------------------------------

resource "kubernetes_namespace_v1" "openbao" {
  count = var.create_required_byo_infra ? 1 : 0
  metadata {
    name = "openbao"
  }
}

resource "kubernetes_storage_class_v1" "openbao_gp3_retain" {
  count                  = var.create_required_byo_infra ? 1 : 0
  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Retain"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  metadata {
    name = "gp3-retain"
  }
  parameters = {
    type      = "gp3"
    fsType    = "xfs"
    encrypted = "true"
  }
}

module "s3_bucket" {
  count   = var.create_required_byo_infra ? 1 : 0
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "~> 4.0"

  bucket = "${local.cogrion_workspace_prefix}-openbao-snapshots"
  acl    = "private"

  control_object_ownership = true
  object_ownership         = "ObjectWriter"

  versioning = {
    enabled = true
  }
  tags = local.tags
}

resource "aws_iam_policy" "openbao" {
  count  = var.create_required_byo_infra ? 1 : 0
  name   = "${local.cogrion_workspace_prefix}-openbao-policy"
  policy = file("${path.module}/openbao_policy.json")
  tags   = local.tags
}

module "openbao_irsa" {
  count     = var.create_required_byo_infra ? 1 : 0
  source    = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version   = "~> 5.52.0"
  role_name = "${local.cogrion_workspace_prefix}-openbao-role"

  role_policy_arns = {
    policy = aws_iam_policy.openbao[0].arn
  }

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["openbao:openbao"]
    }
  }

  tags = local.tags
}

resource "kubernetes_service_account_v1" "openbao" {
  count = var.create_required_byo_infra ? 1 : 0
  metadata {
    name      = "openbao"
    namespace = kubernetes_namespace_v1.openbao[0].metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = module.openbao_irsa[0].iam_role_arn
    }
  }
}

resource "kubernetes_cluster_role_v1" "openbao_pv_reattach" {
  count = var.create_required_byo_infra ? 1 : 0
  metadata {
    name = "openbao-pv-reattach"
    labels = {
      "app.kubernetes.io/name"       = "openbao-pv-reattach"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
  rule {
    api_groups = [""]
    resources  = ["persistentvolumes"]
    verbs      = ["get", "list", "patch"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "openbao_pv_reattach" {
  count = var.create_required_byo_infra ? 1 : 0
  metadata {
    name = "openbao-pv-reattach"
    labels = {
      "app.kubernetes.io/name"       = "openbao-pv-reattach"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.openbao_pv_reattach[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.openbao[0].metadata[0].name
    namespace = kubernetes_namespace_v1.openbao[0].metadata[0].name
  }
}

output "openbao_irsa_role_arn" {
  value = var.create_required_byo_infra ? module.openbao_irsa[0].iam_role_arn : null
}

output "openbao_namespace" {
  value = var.create_required_byo_infra ? kubernetes_namespace_v1.openbao[0].metadata[0].name : null
}

output "openbao_snapshot_bucket_name" {
  value = var.create_required_byo_infra ? module.s3_bucket[0].s3_bucket_id : null
}

output "openbao_storage_class_name" {
  value = var.create_required_byo_infra ? kubernetes_storage_class_v1.openbao_gp3_retain[0].metadata[0].name : null
}
