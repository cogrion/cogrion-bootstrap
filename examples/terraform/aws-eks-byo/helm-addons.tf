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

  # Single Service:LoadBalancer for the whole cluster — every app routes
  # through this one Traefik Ingress, not a Service:LoadBalancer of its own.
  # "aws-load-balancer-type: nlb" is required explicitly: without it, the
  # legacy in-tree AWS cloud provider defaults to a Classic ELB, not NLB,
  # regardless of the subnets annotation below. This annotation is honored
  # by the legacy in-tree provider directly, so it works whether or not the
  # aws_load_balancer_controller addon is enabled for this environment.
  #
  # NLB subnet discovery: explicitly list the public subnets so AWS knows
  # where to create the load balancer. Without this the service-controller
  # errors "could not find any suitable subnets for creating the ELB".
  # Using values/yamlencode instead of a set block because Helm parses
  # comma-separated values in --set as multiple key=value pairs.
  #
  # aws-load-balancer-additional-resource-tags: local.tags_traefik_lb
  # (locals.tf) — same tags as the EKS cluster itself, plus an explicit Name
  # tag, since the in-tree service-controller has no annotation to rename
  # the resource itself (aws-load-balancer-name is AWS Load Balancer
  # Controller-only — confirmed unsupported/silently ignored here). The
  # annotation takes a plain "k1=v1,k2=v2" string, not a map, since k8s
  # annotation values are always strings.
  values = [yamlencode({
    service = {
      annotations = {
        "service.beta.kubernetes.io/aws-load-balancer-type"                     = "nlb"
        "service.beta.kubernetes.io/aws-load-balancer-subnets"                  = join(",", module.vpc.public_subnets)
        "service.beta.kubernetes.io/aws-load-balancer-additional-resource-tags" = join(",", [for k, v in local.tags_traefik_lb : "${k}=${v}"])
      }
    }
  })]

  depends_on = [module.eks]
}

# ---------------------------------------------------------------------------
# external-dns — with the dns-webhook sidecar, which proxies the external-dns
# webhook provider protocol to the control plane (holds the Cloudflare
# token — it never reaches the tenant cluster). Values mirror
# cogrion_bootstrap.addons.make_external_dns. The mTLS client cert/key/CA the
# sidecar reads come from the cluster-agent-credentials secret, which the
# bootstrap Job (--register-only) copies into this namespace before this
# release installs — see bootstrap.tf.
# ---------------------------------------------------------------------------
resource "helm_release" "external_dns" {
  count = var.enable_external_dns ? 1 : 0

  name       = "external-dns"
  repository = "https://kubernetes-sigs.github.io/external-dns"
  chart      = "external-dns"
  version    = "1.21.1"
  namespace  = "external-dns"
  timeout    = 120

  values = [yamlencode({
    provider = {
      name = "webhook"
      webhook = {
        image = {
          repository = "public.ecr.aws/quantdata/cogrion/dns-webhook"
          tag        = var.dns_webhook_tag
        }
        env = [
          { name = "CONTROL_PLANE_URL", value = var.control_plane_url },
          # 8888 matches external-dns's own --webhook-provider-url default
          # (dns-webhook >=0.1.2), so no extraArgs override is needed for
          # the client side.
          { name = "PORT", value = "8888" },
          { name = "LOG_LEVEL", value = "debug" },
          {
            name = "MTLS_CLIENT_CERT"
            valueFrom = {
              secretKeyRef = {
                name = "cluster-agent-credentials"
                key  = "CPLANE_AGENT_MTLS_CLIENT_CERT"
              }
            }
          },
          {
            name = "MTLS_CLIENT_KEY"
            valueFrom = {
              secretKeyRef = {
                name = "cluster-agent-credentials"
                key  = "CPLANE_AGENT_MTLS_CLIENT_KEY"
              }
            }
          },
          {
            name = "MTLS_CA_CERT"
            valueFrom = {
              secretKeyRef = {
                name = "cluster-agent-credentials"
                key  = "CPLANE_AGENT_MTLS_CA_CERT"
              }
            }
          },
        ]
        # The chart hardcodes containerPort 8080 for the webhook container's
        # `http-webhook` named port (templates/deployment.yaml, not
        # values-driven), but its probes default to targeting that name —
        # which resolves to 8080, not the 8888 we actually bind (PORT above).
        # Point the probes at the literal port instead of the named one.
        livenessProbe = {
          httpGet = {
            path = "/healthz"
            port = 8888
          }
        }
        readinessProbe = {
          httpGet = {
            path = "/healthz"
            port = 8888
          }
        }
      }
    }
    policy  = "sync"
    sources = ["service", "ingress"]

    # See cluster_agent.tf's matching annotation: ties this pod template to
    # the bootstrap Job's replace trigger so a credentials rotation (secret
    # rewritten out-of-band by the Job) actually forces a rollout here too —
    # the dns-webhook sidecar reads its mTLS cert/key via secretKeyRef, which
    # k8s does not live-reload.
    podAnnotations = local.bootstrap_enabled ? {
      "cogrion.io/credentials-checksum" = terraform_data.bootstrap_trigger[0].id
    } : {}
  })]

  depends_on = [kubernetes_job_v1.bootstrap]
}
