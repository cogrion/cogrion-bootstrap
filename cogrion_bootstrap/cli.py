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
from .addons import (
    HelmAddon,
    KubectlAddon,
    METRICS_SERVER,
    EXTERNAL_SECRETS,
    CLUSTER_PROPORTIONAL_AUTOSCALER,
    TRAEFIK_VERSION,
    TRAEFIK_NAMESPACE,
    DNS_WEBHOOK_VERSION,
    make_traefik,
    make_external_dns,
    helm_repos_for,
)


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
                values_yaml=addon.values_yaml,
                dry_run=dry_run,
            )
        elif isinstance(addon, KubectlAddon):
            _kubectl_apply(addon.manifest_url, dry_run=dry_run)


def _print_plan(
    args: any, node_group_label: str, addons_to_install: list[tuple[str, str, str]]
) -> None:
    print()
    print("=" * 60)
    print("  Cogrion Bootstrap Plan")
    print("=" * 60)
    print(f"  Provider        : {args.provider}")
    if args.provider == "aws":
        print(f"  Cluster         : {args.cluster_name}  ({args.region})")
        print(f"  Control plane   : {args.control_plane_url}")
        print()
        print("  Node group")
        if args.create_node_group:
            print(f"    Create        : {args.node_group_name}")
            print(f"    Instance type : {args.node_group_instance_type}")
            print(
                f"    Desired / min / max : {args.node_group_desired} / {args.node_group_min} / {args.node_group_max}"
            )
        else:
            print(f"    Use existing  : {args.node_group_name}")
        print(f"    nodegroup label (nodeSelector.nodegroup): {node_group_label}")
        print()
        print("  IRSA roles      :", "skip (--no-create-irsa)" if args.no_create_irsa else "create")
        print()
        # Collect all namespaces that will be created (deduplicated, ordered)
        namespaces = dict.fromkeys(
            [args.namespace] + [ns for _, _, ns in addons_to_install if ns != "kube-system"]
        )
        print("  Namespaces to ensure:")
        for ns in namespaces:
            print(f"    - {ns}")
        print()
        if addons_to_install:
            print("  Addons to install:")
            for name, version, namespace in addons_to_install:
                ver_str = f"  ({version})" if version else ""
                print(f"    - {name}{ver_str}  [{namespace}]")
        else:
            print("  Addons to install : (none)")
        print()
        print(f"  cplane-agent Helm chart  : {args.agent_version}")
        print(f"  Agent namespace          : {args.namespace}")
        if args.tofu_backend_bucket:
            print(f"  Tofu state bucket        : {args.tofu_backend_bucket}")
    print("=" * 60)
    print()


def _copy_secret_to_namespace(
    secret_name: str, src_namespace: str, dst_namespace: str, dry_run: bool = False
) -> None:
    if dry_run:
        print(
            f"[kubectl] dry-run: copy secret {secret_name} from {src_namespace} to {dst_namespace}"
        )
        return
    # ensure destination namespace exists
    ns_result = subprocess.run(
        ["kubectl", "create", "namespace", dst_namespace, "--dry-run=client", "-o", "yaml"],
        capture_output=True,
        check=True,
    )
    subprocess.run(["kubectl", "apply", "-f", "-"], input=ns_result.stdout, check=True)

    get = subprocess.run(
        ["kubectl", "get", "secret", secret_name, "-n", src_namespace, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    import json as _json

    secret = _json.loads(get.stdout)
    secret["metadata"] = {
        "name": secret_name,
        "namespace": dst_namespace,
    }
    apply = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=_json.dumps(secret),
        text=True,
        capture_output=True,
    )
    if apply.returncode != 0:
        raise RuntimeError(
            f"[kubectl] failed to copy secret {secret_name} to {dst_namespace}:\n{apply.stderr.strip()}"
        )
    print(f"[kubectl] secret {secret_name} copied to namespace {dst_namespace}")


def _ensure_cogrion_system_namespace(cogrion_system_namespace: str, dry_run: bool = False) -> None:
    if dry_run:
        print(
            f"[kubectl] dry-run: kubectl create namespace {cogrion_system_namespace} --dry-run=client"
        )
        return
    result = subprocess.run(
        [
            "kubectl",
            "create",
            "namespace",
            cogrion_system_namespace,
            "--dry-run=client",
            "-o",
            "yaml",
        ],
        capture_output=True,
        check=True,
    )
    subprocess.run(["kubectl", "apply", "-f", "-"], input=result.stdout, check=True)
    print(f"[kubectl] namespace {cogrion_system_namespace} ensured")


def _parse_set_args(pairs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"invalid --agent-set value {pair!r}, expected KEY=VALUE")
        result[key] = value
    return result


def main():
    # stdout is block-buffered (not line-buffered) whenever it isn't a TTY —
    # e.g. piped through `kubectl logs` from the bootstrap Job. Without this,
    # every print() in this package (including the "[helm] running: ..." line
    # right before a `--wait`-ing helm install) sits in an internal buffer
    # and never reaches the log stream until it fills up or the process
    # exits, making a multi-minute install look like dead silence.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

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
        default=None,
        help="Value of the 'nodegroup' k8s node label on the system node group "
        "(applied as nodeSelector.nodegroup to all Helm releases). "
        "Defaults to --node-group-name when --create-node-group is set (the label "
        "is set to that value on creation). Required when --no-create-node-group is "
        "used — check the actual label with: kubectl get nodes --show-labels | grep nodegroup",
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
    parser.add_argument(
        "--skip-tls-verify",
        action="store_true",
        help="Disable TLS certificate verification when calling the control plane (use when the server has a self-signed cert)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Skip the interactive 'yes' confirmation prompt (for unattended/CI runs, "
        "e.g. the bootstrap Kubernetes Job, which has no stdin to read from)",
    )
    parser.add_argument(
        "--register-only",
        action="store_true",
        help="Only register with the control plane and copy the mTLS secret to the "
        "external-dns namespace, then exit — skip node group/IRSA/addons/cplane-agent. "
        "For setups where Terraform (or another tool) owns everything else.",
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
    aws.add_argument(
        "--node-group-name",
        default="system",
        help="EKS managed node group name to create (default: system). "
        "Required when --no-create-node-group is set so the existing node group "
        "can be located.",
    )
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
    addon_group.add_argument("--no-traefik", action="store_true", default=False)
    addon_group.add_argument(
        "--traefik-subnets",
        default=None,
        help=(
            "Comma-separated public subnet IDs for the Traefik NLB "
            "(e.g. subnet-aaa,subnet-bbb). Required unless --no-traefik. "
            "Get them from: tofu output public_subnets, or aws ec2 describe-subnets."
        ),
    )
    addon_group.add_argument("--no-external-dns", action="store_true", default=False)
    addon_group.add_argument(
        "--dns-webhook-tag",
        default=DNS_WEBHOOK_VERSION,
        help="Image tag for the dns-webhook external-dns sidecar",
    )
    addon_group.add_argument(
        "--no-cluster-proportional-autoscaler", action="store_true", default=False
    )

    args = parser.parse_args()

    if args.provider in ("alicloud", "gcp", "azure"):
        parser.error(f"--provider {args.provider} is not yet supported — coming soon")

    if args.provider == "aws" and not args.register_only:
        if not args.cluster_name:
            parser.error("--cluster-name is required for --provider aws")
        if not args.region:
            parser.error("--region is required for --provider aws")
        if args.enable_alb_controller and not args.vpc_id:
            parser.error("--vpc-id is required when --enable-alb-controller is set")
        if not args.create_node_group and not args.node_group_label:
            parser.error(
                "--node-group-label is required when --no-create-node-group is set "
                "(the nodegroup label on an existing node group cannot be inferred — "
                "check with: kubectl get nodes --show-labels | grep nodegroup)"
            )
        if not args.no_traefik and not args.traefik_subnets:
            parser.error(
                "--traefik-subnets is required (comma-separated public subnet IDs for the Traefik NLB). "
                "Get them with: tofu output public_subnets  or  "
                "aws ec2 describe-subnets --filters Name=tag:kubernetes.io/role/elb,Values=1 "
                "--query 'Subnets[].SubnetId' --output text"
            )

    dry = args.dry_run

    if args.register_only:
        if args.provider != "aws":
            parser.error("--register-only currently only supports --provider aws")
        register_agent(
            control_plane_url=args.control_plane_url,
            token=args.token,
            namespace=args.namespace,
            dry_run=dry,
            skip_tls_verify=args.skip_tls_verify,
            cluster_name=args.cluster_name,
            region=args.region,
        )
        if not args.no_external_dns:
            _copy_secret_to_namespace(
                secret_name="cluster-agent-credentials",
                src_namespace=args.namespace,
                dst_namespace="external-dns",
                dry_run=dry,
            )
        print(
            "[cogrion-bootstrap] --register-only: registration complete — "
            "skipping node group/IRSA/addons/cplane-agent."
        )
        return

    # When we create the node group we set labels={"nodegroup": node_group_name},
    # so the label value safely defaults to the name. For existing node groups the
    # label is unknown — validated above that --node-group-label is explicit.
    node_group_label: str = args.node_group_label or args.node_group_name

    # (release_name, chart_version, namespace) — versions must stay in sync with providers/aws.py
    _KNOWN_ADDONS: list[tuple[str, str, str]] = [
        ("cluster-autoscaler", "9.57.0", "kube-system"),
        ("aws-efs-csi-driver", "4.3.0", "kube-system"),
        (METRICS_SERVER.release_name, METRICS_SERVER.version, METRICS_SERVER.namespace),
        ("aws-load-balancer-controller", "", "kube-system"),
        (EXTERNAL_SECRETS.release_name, EXTERNAL_SECRETS.version, EXTERNAL_SECRETS.namespace),
        ("traefik", TRAEFIK_VERSION, TRAEFIK_NAMESPACE),
        ("external-dns", make_external_dns("").version, make_external_dns("").namespace),
        (
            CLUSTER_PROPORTIONAL_AUTOSCALER.release_name,
            CLUSTER_PROPORTIONAL_AUTOSCALER.version,
            CLUSTER_PROPORTIONAL_AUTOSCALER.namespace,
        ),
    ]
    skip_for_plan: set[str] = set()
    if args.provider == "aws":
        if args.no_cluster_autoscaler:
            skip_for_plan.add("cluster-autoscaler")
        if args.no_efs_csi_driver:
            skip_for_plan.add("aws-efs-csi-driver")
        if args.no_metrics_server:
            skip_for_plan.add("metrics-server")
        if not args.enable_alb_controller or args.no_alb_controller:
            skip_for_plan.add("aws-load-balancer-controller")
        if args.no_external_secrets:
            skip_for_plan.add("external-secrets")
        if args.no_traefik:
            skip_for_plan.add("traefik")
        if args.no_external_dns:
            skip_for_plan.add("external-dns")
        if args.no_cluster_proportional_autoscaler:
            skip_for_plan.add("cluster-proportional-autoscaler")
    addons_to_install = [(n, v, ns) for n, v, ns in _KNOWN_ADDONS if n not in skip_for_plan]

    _print_plan(args, node_group_label, addons_to_install)

    if args.auto_approve:
        print("  --auto-approve set — skipping confirmation.")
    else:
        print("  Only 'yes' will be accepted to approve.")
        print()
        answer = input("  Enter a value: ").strip()
        if answer != "yes":
            print()
            print("Error: Bootstrap cancelled.")
            sys.exit(1)

    node_selector_set = {"nodeSelector.nodegroup": node_group_label}

    irsa_arns: dict = {}

    if args.provider == "aws":
        from .providers.aws import AWSProvider

        result = register_agent(
            control_plane_url=args.control_plane_url,
            token=args.token,
            namespace=args.namespace,
            dry_run=dry,
            skip_tls_verify=args.skip_tls_verify,
            cluster_name=args.cluster_name,
            region=args.region,
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
        irsa_arns = {} if (args.no_create_irsa or dry) else provider.ensure_iam()

        addons = provider.addons(
            irsa_arns=irsa_arns,
            vpc_id=args.vpc_id,
            traefik_subnets=args.traefik_subnets or "",
        )
        # external-dns is not cloud-specific — assembled here rather than in
        # the provider, unlike traefik (whose NLB subnet annotation is AWS-only).
        addons.append(make_external_dns(args.control_plane_url, webhook_tag=args.dns_webhook_tag))

        for name in skip_for_plan:
            print(f"[cogrion-bootstrap] skipping {name} (disabled)")
        addons = [a for a in addons if a.release_name not in skip_for_plan]

        # Copy the mTLS secret into external-dns's namespace (creating the
        # namespace if needed) before installing/detecting external-dns —
        # the webhook sidecar reads it at container start, so it must exist
        # first, whether this run or Terraform owns the Helm release.
        if "external-dns" not in skip_for_plan:
            _copy_secret_to_namespace(
                secret_name="cluster-agent-credentials",
                src_namespace=args.namespace,
                dst_namespace="external-dns",
                dry_run=dry,
            )

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

    _ensure_cogrion_system_namespace(args.namespace, dry_run=dry)

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
