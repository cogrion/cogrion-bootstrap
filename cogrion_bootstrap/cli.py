import argparse
import subprocess
import sys

from .constants import (
    CPLANE_AGENT_CHART,
    CPLANE_AGENT_DEFAULT_VERSION,
    CPLANE_API_URL,
    ECR_PUBLIC_REGISTRY,
)
from .register import register_agent
from .helm import helm_apply, ensure_helm_repos, is_externally_managed
from .addons import HelmAddon, KubectlAddon, helm_repos_for


def _ecr_login(region: str, dry_run: bool = False) -> None:
    registry = ECR_PUBLIC_REGISTRY
    print(f"[ecr] logging in to {registry}")
    if dry_run:
        print(f"[ecr] dry-run: skipping login")
        return
    token = subprocess.run(
        ["aws", "ecr-public", "get-login-password", "--region", "us-east-1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = subprocess.run(
        ["helm", "registry", "login", registry, "--username", "AWS", "--password-stdin"],
        input=token,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"[ecr] helm registry login failed:\n{result.stderr.strip()}")
    print(f"[ecr] login successful")


def _kubectl_apply(manifest_url: str, dry_run: bool = False) -> None:
    cmd = ["kubectl", "apply", "-f", manifest_url]
    if dry_run:
        print(f"[kubectl] dry-run: {' '.join(cmd)}")
        return
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"[kubectl] apply failed:\n{result.stderr.strip()}")
    print(result.stdout.strip())


def _install_addons(addons: list, node_selector_set: dict, dry_run: bool) -> None:
    ensure_helm_repos(helm_repos_for(addons), dry_run=dry_run)
    for addon in addons:
        if addon.detect and not dry_run:
            kind, name = addon.detect
            if is_externally_managed(kind, name, addon.namespace):
                print(
                    f"[cogrion-bootstrap] {addon.release_name} already installed outside Helm — skipping"
                )
                continue
        if isinstance(addon, HelmAddon):
            helm_apply(
                release=addon.release_name,
                namespace=addon.namespace,
                chart=addon.chart,
                version=addon.version,
                set_args={**addon.set_args, **node_selector_set},
                dry_run=dry_run,
            )
        elif isinstance(addon, KubectlAddon):
            _kubectl_apply(addon.manifest_url, dry_run=dry_run)


def _parse_set_args(pairs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"invalid --agent-set value {pair!r}, expected KEY=VALUE")
        result[key] = value
    return result


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
        default=CPLANE_API_URL,
        help="Override the control plane API URL (default: https://cplane.api.cogrion.com)",
    )
    parser.add_argument(
        "--namespace",
        default="cogrion-system",
        help="Kubernetes namespace for the agent (default: cogrion-system)",
    )
    parser.add_argument(
        "--agent-version",
        default=CPLANE_AGENT_DEFAULT_VERSION,
        help="cplane-agent Helm chart version (composite tag)",
    )
    parser.add_argument(
        "--node-group-label",
        default="",
        help="Value for nodeSelector.nodegroup on all Helm releases",
    )
    parser.add_argument(
        "--agent-set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra --set override for the cplane-agent Helm release (repeatable), "
        "e.g. --agent-set autoscaling.enabled=false",
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
        "--no-create-irsa",
        action="store_true",
        default=False,
        help="Skip IRSA role creation (use when roles are pre-provisioned by Terraform)",
    )
    aws.add_argument(
        "--tofu-backend-bucket",
        default="",
        help="S3 bucket for OpenTofu remote state (required for stack provisioning)",
    )
    aws.add_argument(
        "--tofu-backend-region",
        default="",
        help="AWS region of the Tofu state bucket (defaults to --region if omitted)",
    )
    aws.add_argument(
        "--tofu-backend-key-prefix",
        default="",
        help="Key prefix within the Tofu state bucket (optional)",
    )

    # Addon toggles
    addon_group = parser.add_argument_group("Addons")
    addon_group.add_argument("--no-cluster-autoscaler", action="store_true", default=False)
    addon_group.add_argument("--no-efs-csi-driver", action="store_true", default=False)
    addon_group.add_argument("--no-metrics-server", action="store_true", default=False)
    addon_group.add_argument("--no-alb-controller", action="store_true", default=False)
    addon_group.add_argument("--no-external-secrets", action="store_true", default=False)

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

    node_selector_set = (
        {"nodeSelector.nodegroup": args.node_group_label} if args.node_group_label else {}
    )

    irsa_arns: dict = {}

    if args.provider == "aws":
        from .providers.aws import AWSProvider

        result = register_agent(
            control_plane_url=args.control_plane_url,
            token=args.token,
            namespace=args.namespace,
            dry_run=dry,
        )

        provider = AWSProvider(
            ext_account_id=result.ext_account_id,
            ext_workspace_id=result.ext_workspace_id,
            cluster_name=args.cluster_name,
            region=args.region,
            dry_run=dry,
        )

        if args.create_node_group:
            node_security_group_id = provider.ensure_cloud_resources(
                name=args.node_group_name,
                instance_type=args.node_group_instance_type,
                desired=args.node_group_desired,
                min_size=args.node_group_min,
                max_size=args.node_group_max,
                subnets=args.node_group_subnets,
                node_role_arn=args.node_role_arn,
            )
            if node_security_group_id:
                print(f"[aws] node_security_group_id: {node_security_group_id}")

        irsa_arns = {} if args.no_create_irsa else provider.ensure_iam()

        addons = provider.addons(
            irsa_arns=irsa_arns,
            vpc_id=args.vpc_id,
        )

        # Filter out disabled addons
        skip = set()
        if args.no_cluster_autoscaler:
            skip.add("cluster-autoscaler")
        if args.no_efs_csi_driver:
            skip.add("aws-efs-csi-driver")
        if args.no_metrics_server:
            skip.add("metrics-server")
        if not args.enable_alb_controller or args.no_alb_controller:
            skip.add("aws-load-balancer-controller")
        if args.no_external_secrets:
            skip.add("external-secrets")

        for name in skip:
            print(f"[cogrion-bootstrap] skipping {name} (disabled)")
        addons = [a for a in addons if a.release_name not in skip]

        _install_addons(addons, node_selector_set, dry)

    _ecr_login(region=getattr(args, "region", "us-east-1"), dry_run=dry)

    tofu_set: dict = {}
    if args.provider == "aws":
        tofu_backend_bucket = args.tofu_backend_bucket
        tofu_backend_region = args.tofu_backend_region or args.region
        if tofu_backend_bucket:
            tofu_set["tofu.backendBucket"] = tofu_backend_bucket
            tofu_set["tofu.backendRegion"] = tofu_backend_region
        if args.tofu_backend_key_prefix:
            tofu_set["tofu.backendKeyPrefix"] = args.tofu_backend_key_prefix

    agent_set = _parse_set_args(args.agent_set)

    helm_apply(
        release="cplane-agent",
        namespace=args.namespace,
        chart=CPLANE_AGENT_CHART,
        version=args.agent_version,
        set_args={
            "existingSecret": "cluster-agent-credentials",
            "serviceAccount.create": "false",
            "serviceAccount.name": "cplane-agent",
            **tofu_set,
            **node_selector_set,
            **agent_set,
        },
        dry_run=dry,
    )

    print("[cogrion-bootstrap] bootstrap complete")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
