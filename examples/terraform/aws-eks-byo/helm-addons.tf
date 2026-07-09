# ---------------------------------------------------------------------------
# Traefik — ingress controller
# Replaces ingress-nginx (EOL March 2026). Still speaks Ingress resources
# so no KCL changes needed — just the ingressClassName changes to "traefik".
# ---------------------------------------------------------------------------
resource "helm_release" "traefik" {
  count = var.enable_traefik ? 1 : 0

  name             = "traefik"
  repository       = "https://traefik.github.io/charts"
  chart            = "traefik"
  version          = "41.0.2"
  namespace        = "traefik"
  create_namespace = true

  set {
    name  = "nodeSelector.nodegroup"
    value = var.system_nodegroup_label
  }

  # NLB subnet discovery: explicitly list the public subnets so AWS knows
  # where to create the load balancer. Without this the service-controller
  # errors "could not find any suitable subnets for creating the ELB".
  # Using values/yamlencode instead of a set block because Helm parses
  # comma-separated values in --set as multiple key=value pairs.
  values = [yamlencode({
    service = {
      annotations = {
        "service.beta.kubernetes.io/aws-load-balancer-subnets" = join(",", module.vpc.public_subnets)
      }
    }
  })]

  depends_on = [module.eks]
}
