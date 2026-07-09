# ---------------------------------------------------------------------------
# KubeBlocks IRSA
#
# KubeBlocks itself is installed by the cluster agent via KCL stacks after
# registration — not here. But the IRSA role must exist before the agent
# runs so that KubeBlocks' service accounts can assume it on first start.
# ---------------------------------------------------------------------------
resource "aws_iam_policy" "kubeblocks" {
  name   = "${local.platform_id}-kubeblocks-policy"
  policy = file("${path.module}/kubeblocks_policy.json")
}

module "kubeblocks_irsa" {
  source    = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version   = "~> 5.52.0"
  role_name = "${local.platform_id}-kubeblocks-role"

  role_policy_arns = {
    policy = aws_iam_policy.kubeblocks.arn
  }

  oidc_providers = {
    main = {
      provider_arn = module.eks.oidc_provider_arn
      namespace_service_accounts = [
        "kb-system:kubeblocks",
        "kb-system:kubeblocks-dataprotection-exec-worker",
        "kb-system:kubeblocks-dataprotection-worker",
      ]
    }
  }

  tags = local.tags
}

output "kubeblocks_irsa_role_arn" {
  value = module.kubeblocks_irsa.iam_role_arn
}
