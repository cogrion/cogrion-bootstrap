# ---------------------------------------------------------------------------
# Cogrion bootstrap Job
#
# Clones cogrion-bootstrap and runs the CLI with --register-only: registers
# the cluster with the control plane, writes cluster-agent-credentials, and
# copies it into the external-dns namespace. Nothing else — mirrors
# production (terraform-workspace-infra-aws/modules/workspace-cluster-bootstrap),
# where Terraform owns every addon (see eks-cluster.tf/helm-addons.tf) and
# cplane-agent/external-dns are installed later via KCL stacks, not this Job.
#
# Gated on var.bootstrap_token being non-empty.
# Recreated automatically when bootstrap_token changes.
# ---------------------------------------------------------------------------

locals {
  bootstrap_enabled         = var.bootstrap_token != ""
  bootstrap_script_checksum = substr(filesha256("${path.module}/bootstrap.sh"), 0, 8)
}

# ---------------------------------------------------------------------------
# IAM policy — everything cogrion_bootstrap.providers.aws.AWSProvider needs to
# call from inside the Job itself: S3 (tofu state/KubeBlocks backups), EKS
# (cluster/nodegroup describe + node group lifecycle), EC2 (node security
# group + launch template provisioning), IAM (IRSA role/policy provisioning
# via ensure_iam()), and STS (caller identity).
# ---------------------------------------------------------------------------
resource "aws_iam_policy" "bootstrap" {
  count       = local.bootstrap_enabled ? 1 : 0
  name        = "${local.cogrion_workspace_prefix}-bootstrap-policy"
  description = "Cogrion bootstrap job policy"
  policy = templatefile("${path.module}/bootstrap_policy.json", {
    cogrion_workspace_id = local.cogrion_workspace_prefix
    aws_account_id       = data.aws_caller_identity.current.account_id
    aws_region           = var.region
    cluster_name         = local.cogrion_workspace_prefix
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

  role_name = "${local.cogrion_workspace_prefix}-bootstrap-role"

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
    name = "${local.cogrion_workspace_prefix}-bootstrap-crb"
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
# Trigger — recreates the Job when any of its env inputs change. The Job's
# pod template is immutable at the k8s API level, so Terraform can't just
# patch it in place; this forces a replace via replace_triggered_by below.
# ---------------------------------------------------------------------------
resource "terraform_data" "bootstrap_trigger" {
  count = local.bootstrap_enabled ? 1 : 0
  input = {
    token                    = var.bootstrap_token
    checksum                 = local.bootstrap_script_checksum
    enable_external_dns      = var.enable_external_dns
    control_plane_url        = var.control_plane_url
    cluster_name             = local.cogrion_workspace_prefix
    region                   = var.region
    terraform_backend_bucket = var.terraform_backend_bucket
    traefik_subnets          = join(",", module.vpc.public_subnets)
    agent_version            = var.agent_version
    node_group_label         = var.system_nodegroup_label
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
            value = local.cogrion_workspace_prefix
          }
          env {
            name  = "REGION"
            value = var.region
          }
          env {
            name  = "TOFU_BACKEND_BUCKET"
            value = var.terraform_backend_bucket
          }
          env {
            name  = "TRAEFIK_SUBNETS"
            value = join(",", module.vpc.public_subnets)
          }
          env {
            name  = "AGENT_VERSION"
            value = var.agent_version
          }
          env {
            name  = "NODE_GROUP_LABEL"
            value = var.system_nodegroup_label
          }
          env {
            name  = "ENABLE_EXTERNAL_DNS"
            value = var.enable_external_dns ? "true" : "false"
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

  # register-only doesn't touch any addon — just needs the cluster to exist.
  depends_on = [
    module.bootstrap_irsa,
    kubernetes_cluster_role_binding_v1.bootstrap,
    kubernetes_secret_v1.bootstrap_token,
    kubernetes_config_map_v1.bootstrap_script,
    module.eks,
  ]
}
