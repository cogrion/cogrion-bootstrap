import argparse
import sys

from .register import register_agent
from .helm import helm_apply
from .addons import ADDONS


def main():
    parser = argparse.ArgumentParser(
        prog="cogrion-bootstrap",
        description="Bootstrap a tenant cluster for the Cogrion platform.",
    )
    parser.add_argument(
        "--token", required=True, help="One-time bootstrap token from the control plane"
    )
    parser.add_argument(
        "--provider",
        choices=["aws", "alicloud", "gcp", "azure"],
        required=True,
        help="Cloud provider (alicloud, gcp, azure: coming soon)",
    )
    parser.add_argument(
        "--control-plane-url",
        default="https://cplane.api.cogrion.com",
        help="Override the control plane API URL (default: https://cplane.api.cogrion.com)",
    )
    parser.add_argument(
        "--namespace",
        default="cogrion-system",
        help="Kubernetes namespace for the agent (default: cogrion-system)",
    )
    parser.add_argument(
        "--agent-version",
        default="0.1.5-0.1.11",
        help="cplane-agent Helm chart version (composite tag)",
    )
    parser.add_argument(
        "--node-group-label",
        default="",
        help="Value for nodeSelector.nodegroup on all Helm releases",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print actions without executing anything"
    )

    # AWS
    aws = parser.add_argument_group("AWS")
    aws.add_argument("--cluster-name", help="EKS cluster name")
    aws.add_argument("--region", help="AWS region")
    aws.add_argument(
        "--create-node-group",
        action="store_true",
        default=True,
        help="Create a managed node group (default: true)",
    )
    aws.add_argument("--no-create-node-group", dest="create_node_group", action="store_false")
    aws.add_argument("--node-group-name", default="system")
    aws.add_argument("--node-group-instance-type", default="t3.medium")
    aws.add_argument("--node-group-desired", type=int, default=2)
    aws.add_argument("--node-group-min", type=int, default=1)
    aws.add_argument("--node-group-max", type=int, default=4)
    aws.add_argument(
        "--node-group-subnets",
        default="",
        help="Comma-separated subnet IDs (auto-discovered if omitted)",
    )
    aws.add_argument(
        "--node-role-arn",
        default="",
        help="IAM role ARN for the node group (auto-created if omitted)",
    )
    aws.add_argument("--enable-alb-controller", action="store_true", default=False)
    aws.add_argument("--vpc-id", default="", help="VPC ID (required when --enable-alb-controller)")
    aws.add_argument(
        "--cluster-autoscaler-role-arn", default="", help="IRSA role ARN for cluster-autoscaler"
    )
    aws.add_argument(
        "--efs-csi-driver-role-arn", default="", help="IRSA role ARN for aws-efs-csi-driver"
    )
    aws.add_argument(
        "--external-secrets-role-arn", default="", help="IRSA role ARN for external-secrets"
    )
    aws.add_argument(
        "--alb-controller-role-arn",
        default="",
        help="IRSA role ARN for aws-load-balancer-controller",
    )

    # Addon toggles (provider-agnostic names where possible)
    addons = parser.add_argument_group("Addons")
    addons.add_argument("--enable-cluster-autoscaler", action="store_true", default=True)
    addons.add_argument(
        "--no-cluster-autoscaler", dest="enable_cluster_autoscaler", action="store_false"
    )
    addons.add_argument("--enable-efs-csi-driver", action="store_true", default=True)
    addons.add_argument("--no-efs-csi-driver", dest="enable_efs_csi_driver", action="store_false")
    addons.add_argument("--enable-external-secrets", action="store_true", default=True)
    addons.add_argument(
        "--no-external-secrets", dest="enable_external_secrets", action="store_false"
    )
    addons.add_argument("--enable-metrics-server", action="store_true", default=True)
    addons.add_argument("--no-metrics-server", dest="enable_metrics_server", action="store_false")

    args = parser.parse_args()

    if args.provider in ("alicloud", "gcp", "azure"):
        parser.error(f"--provider {args.provider} is not yet supported — coming soon")

    if args.provider == "aws":
        if not args.cluster_name:
            parser.error("--cluster-name is required for --provider aws")
        if not args.region:
            parser.error("--region is required for --provider aws")
        if args.enable_alb_controller and not args.vpc_id:
            parser.error("--vpc-id is required when --enable-alb-controller is set")

    dry = args.dry_run
    print(
        f"[cogrion-bootstrap] provider={args.provider} cluster={getattr(args, 'cluster_name', '')} dry_run={dry}"
    )

    if args.provider == "aws":
        from .providers.aws import AWSProvider

        provider = AWSProvider(cluster_name=args.cluster_name, region=args.region, dry_run=dry)
        if args.create_node_group:
            provider.ensure_node_group(
                name=args.node_group_name,
                instance_type=args.node_group_instance_type,
                desired=args.node_group_desired,
                min_size=args.node_group_min,
                max_size=args.node_group_max,
                subnets=args.node_group_subnets,
                node_role_arn=args.node_role_arn,
            )

    register_agent(
        control_plane_url=args.control_plane_url,
        token=args.token,
        namespace=args.namespace,
        dry_run=dry,
    )

    addon_flags = {
        "cluster-autoscaler": args.enable_cluster_autoscaler,
        "aws-efs-csi-driver": args.enable_efs_csi_driver,
        "external-secrets": args.enable_external_secrets,
        "metrics-server": args.enable_metrics_server,
        "aws-load-balancer-controller": getattr(args, "enable_alb_controller", False),
    }
    irsa_arns = {
        "cluster-autoscaler": getattr(args, "cluster_autoscaler_role_arn", ""),
        "aws-efs-csi-driver": getattr(args, "efs_csi_driver_role_arn", ""),
        "external-secrets": getattr(args, "external_secrets_role_arn", ""),
        "aws-load-balancer-controller": getattr(args, "alb_controller_role_arn", ""),
    }

    node_selector_set = (
        {"nodeSelector.nodegroup": args.node_group_label} if args.node_group_label else {}
    )

    for addon in ADDONS:
        if not addon_flags.get(addon.release_name, False):
            print(f"[cogrion-bootstrap] skipping {addon.release_name}")
            continue
        extra = addon.extra_set_args(
            cluster_name=getattr(args, "cluster_name", ""),
            region=getattr(args, "region", ""),
            vpc_id=getattr(args, "vpc_id", ""),
            irsa_arn=irsa_arns.get(addon.release_name, ""),
        )
        helm_apply(
            release=addon.release_name,
            namespace=addon.namespace,
            chart=addon.chart,
            version=addon.version,
            set_args={**addon.default_set_args, **extra, **node_selector_set},
            dry_run=dry,
        )

    helm_apply(
        release="cplane-agent",
        namespace=args.namespace,
        chart="oci://public.ecr.aws/quantdata/charts/cplane-agent",
        version=args.agent_version,
        set_args={"existingSecret": "cluster-agent-credentials", **node_selector_set},
        dry_run=dry,
    )

    print("[cogrion-bootstrap] bootstrap complete")


if __name__ == "__main__":
    sys.exit(main())
