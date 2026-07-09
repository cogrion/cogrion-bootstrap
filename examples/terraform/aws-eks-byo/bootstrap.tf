# ---------------------------------------------------------------------------
# Cogrion bootstrap Job
#
# Clones cogrion-bootstrap and runs the CLI to register the cluster with the
# control plane, install all addons (Traefik, external-dns, etc.), and install
# cplane-agent.
#
# Gated on var.bootstrap_token being non-empty.
# Recreated automatically when bootstrap_token or agent_version changes.
# ---------------------------------------------------------------------------

locals {
  bootstrap_enabled        = var.bootstrap_token != ""
  bootstrap_script_checksum = substr(filesha256("${path.module}/bootstrap.sh"), 0, 8)
}

# ---------------------------------------------------------------------------
# IAM policy — S3 permissions for OpenTofu remote state and KubeBlocks backups
# ---------------------------------------------------------------------------
resource "aws_iam_policy" "bootstrap" {
  count       = local.bootstrap_enabled ? 1 : 0
  name        = "${local.platform_id}-bootstrap-policy"
  description = "Cogrion bootstrap job policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:CreateBucket", "s3:DeleteBucket", "s3:ListBucket",
          "s3:PutBucketEncryption", "s3:GetBucketEncryption",
          "s3:PutBucketPublicAccessBlock", "s3:GetBucketPublicAccessBlock",
        ]
        Resource = [
          "arn:aws:s3:::${local.platform_id}*",
          "arn:aws:s3:::${local.platform_id}*/*",
        ]
      }
    ]
  })
  tags = local.tags
}

# ---------------------------------------------------------------------------
# IRSA — lets the Job pod call AWS APIs without static credentials
# ---------------------------------------------------------------------------
module "bootstrap_irsa" {
  count   = local.bootstrap_enabled ? 1 : 0
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.52.0"

  role_name = "${local.platform_id}-bootstrap-role"

  role_policy_arns = {
    policy = aws_iam_policy.bootstrap[0].arn
  }

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["cogrion-system:bootstrap-sa"]
    }
  }

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Kubernetes resources
# ---------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "cogrion_system" {
  count = local.bootstrap_enabled ? 1 : 0
  metadata {
    name = "cogrion-system"
  }
}

resource "kubernetes_service_account_v1" "bootstrap" {
  count = local.bootstrap_enabled ? 1 : 0
  metadata {
    name      = "bootstrap-sa"
    namespace = kubernetes_namespace_v1.cogrion_system[0].metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = module.bootstrap_irsa[0].iam_role_arn
    }
  }
  depends_on = [kubernetes_namespace_v1.cogrion_system]
}

resource "kubernetes_cluster_role_binding_v1" "bootstrap" {
  count = local.bootstrap_enabled ? 1 : 0
  metadata {
    name = "${local.platform_id}-bootstrap-crb"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "cluster-admin"
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.bootstrap[0].metadata[0].name
    namespace = kubernetes_namespace_v1.cogrion_system[0].metadata[0].name
  }
  depends_on = [kubernetes_service_account_v1.bootstrap]
}

resource "kubernetes_secret_v1" "bootstrap_token" {
  count = local.bootstrap_enabled ? 1 : 0
  metadata {
    name      = "bootstrap-token"
    namespace = kubernetes_namespace_v1.cogrion_system[0].metadata[0].name
  }
  data = {
    token = var.bootstrap_token
  }
  type       = "Opaque"
  depends_on = [kubernetes_namespace_v1.cogrion_system]
}

resource "kubernetes_config_map_v1" "bootstrap_script" {
  count = local.bootstrap_enabled ? 1 : 0
  metadata {
    name      = "bootstrap-script-${local.bootstrap_script_checksum}"
    namespace = kubernetes_namespace_v1.cogrion_system[0].metadata[0].name
  }
  data = {
    "bootstrap.sh" = file("${path.module}/bootstrap.sh")
  }
  lifecycle {
    create_before_destroy = true
  }
  depends_on = [kubernetes_namespace_v1.cogrion_system]
}

# ---------------------------------------------------------------------------
# Trigger — recreates the Job when token or agent version changes
# ---------------------------------------------------------------------------
resource "terraform_data" "bootstrap_trigger" {
  count = local.bootstrap_enabled ? 1 : 0
  input = {
    token         = var.bootstrap_token
    agent_version = var.agent_version
    checksum      = local.bootstrap_script_checksum
  }
}

# ---------------------------------------------------------------------------
# Bootstrap Job
# ---------------------------------------------------------------------------
resource "kubernetes_job_v1" "bootstrap" {
  count = local.bootstrap_enabled ? 1 : 0

  metadata {
    generate_name = "cogrion-bootstrap-"
    namespace     = kubernetes_namespace_v1.cogrion_system[0].metadata[0].name
  }

  spec {
    ttl_seconds_after_finished = 3600
    backoff_limit              = 2

    template {
      metadata {}
      spec {
        restart_policy       = "OnFailure"
        service_account_name = kubernetes_service_account_v1.bootstrap[0].metadata[0].name

        container {
          name    = "bootstrap"
          image   = "alpine/k8s:1.29.2"
          command = ["bash", "/scripts/bootstrap.sh"]

          env {
            name = "BOOTSTRAP_TOKEN"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.bootstrap_token[0].metadata[0].name
                key  = "token"
              }
            }
          }
          env {
            name  = "CONTROL_PLANE_URL"
            value = var.control_plane_url
          }
          env {
            name  = "CLUSTER_NAME"
            value = module.eks.cluster_name
          }
          env {
            name  = "REGION"
            value = var.region
          }
          env {
            name  = "TOFU_BACKEND_BUCKET"
            value = var.tofu_backend_bucket
          }
          env {
            name  = "TRAEFIK_SUBNETS"
            value = join(",", module.vpc.public_subnets)
          }
          env {
            name  = "NODE_GROUP_LABEL"
            value = var.system_nodegroup_label
          }
          env {
            name  = "AGENT_VERSION"
            value = var.agent_version
          }
          env {
            name  = "ENABLE_EXTERNAL_DNS"
            value = var.enable_external_dns ? "true" : "false"
          }
          env {
            name  = "DNS_WEBHOOK_TAG"
            value = var.dns_webhook_image_tag
          }

          volume_mount {
            name       = "bootstrap-script"
            mount_path = "/scripts"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "200m"
              memory = "256Mi"
            }
          }
        }

        volume {
          name = "bootstrap-script"
          config_map {
            name         = kubernetes_config_map_v1.bootstrap_script[0].metadata[0].name
            default_mode = "0755"
          }
        }
      }
    }
  }

  wait_for_completion = true

  timeouts {
    create = "30m"
  }

  lifecycle {
    replace_triggered_by = [terraform_data.bootstrap_trigger[0]]
  }

  # Runs last: the CLI also installs/configures Traefik (see bootstrap.sh's
  # --traefik-subnets flag), which would otherwise race against the
  # Terraform-managed helm_release.traefik in helm-addons.tf. external-dns is
  # installed by the CLI itself (not Terraform) — see bootstrap.sh.
  depends_on = [
    module.bootstrap_irsa,
    kubernetes_cluster_role_binding_v1.bootstrap,
    kubernetes_secret_v1.bootstrap_token,
    kubernetes_config_map_v1.bootstrap_script,
    module.eks,
    module.eks_blueprints_addons,
    helm_release.traefik,
  ]
}
