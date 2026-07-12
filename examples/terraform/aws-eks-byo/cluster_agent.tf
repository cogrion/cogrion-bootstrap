# ---------------------------------------------------------------------------
# Cluster agent infrastructure — installs cplane-agent (the in-cluster agent
# that executes KCL stack ops), Terraform-owned so it doesn't fight the
# bootstrap Job (which only registers + writes cluster-agent-credentials,
# see bootstrap.tf). Gated on local.bootstrap_enabled since cplane-agent
# needs that secret and the cogrion-system namespace the Job creates.
# ---------------------------------------------------------------------------

resource "aws_iam_policy" "cluster_agent_policy" {
  name        = "${local.cogrion_workspace_prefix}-cluster-agent-policy"
  description = "Cogrion cplane-agent policy"
  policy = templatefile("${path.module}/cluster_agent_policy.json", {
    aws_account_id           = data.aws_caller_identity.current.account_id
    cogrion_account_id       = var.cogrion_account_id
    cogrion_workspace_id     = var.cogrion_workspace_id
    cogrion_workspace_prefix = local.cogrion_workspace_prefix
  })
  tags = local.tags
}

module "cluster_agent_irsa" {
  source    = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version   = "~> 5.52.0"
  role_name = "${local.cogrion_workspace_prefix}-cluster-agent-role"

  role_policy_arns = {
    policy = aws_iam_policy.cluster_agent_policy.arn
  }

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["cogrion-system:cplane-agent"]
    }
  }
  tags = local.tags
}

# cplane-agent's chart defaults to serviceAccount.create=true with a
# generated name — we pre-create it here (annotated for IRSA) and pass
# serviceAccount.create=false/serviceAccount.name below so the chart reuses
# it, same pattern as the cogrion-bootstrap CLI's --register-only handoff.
resource "kubernetes_service_account_v1" "cplane_agent" {
  count = local.bootstrap_enabled ? 1 : 0
  metadata {
    name      = "cplane-agent"
    namespace = kubernetes_namespace_v1.cogrion_system[0].metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = module.cluster_agent_irsa.iam_role_arn
    }
  }
  depends_on = [kubernetes_namespace_v1.cogrion_system]
}

resource "helm_release" "cplane_agent" {
  count = local.bootstrap_enabled ? 1 : 0

  name       = "cplane-agent"
  namespace  = kubernetes_namespace_v1.cogrion_system[0].metadata[0].name
  repository = "oci://public.ecr.aws/quantdata/charts"
  chart      = "cplane-agent"
  version    = var.agent_version

  set {
    name  = "existingSecret"
    value = "cluster-agent-credentials"
  }
  set {
    name  = "serviceAccount.create"
    value = "false"
  }
  set {
    name  = "serviceAccount.name"
    value = "cplane-agent"
  }
  set {
    name  = "aws.region"
    value = var.region
  }
  set {
    name  = "tofu.backendBucket"
    value = var.terraform_backend_bucket
  }
  set {
    name  = "nodeSelector.nodegroup"
    value = var.system_nodegroup_label
  }

  # Deleting/rewriting cluster-agent-credentials out-of-band (e.g. to force
  # re-registration) is invisible to Helm/Terraform — env-var secretKeyRefs
  # aren't live-reloaded by k8s, and depends_on only affects apply ordering,
  # not diffing. This annotation ties the pod template to
  # terraform_data.bootstrap_trigger's id, which changes exactly when the
  # bootstrap Job gets replaced (see bootstrap.tf), forcing a real rollout
  # instead of requiring a manual `kubectl rollout restart`.
  set {
    name  = "podAnnotations.cogrion\\.io/credentials-checksum"
    value = terraform_data.bootstrap_trigger[0].id
  }

  # Registration (the bootstrap Job) must have already written
  # cluster-agent-credentials before this release starts, and the SA must
  # exist with its IRSA annotation before the chart's pod starts.
  depends_on = [
    kubernetes_job_v1.bootstrap,
    kubernetes_service_account_v1.cplane_agent,
  ]
}
