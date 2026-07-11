# aws-eks-byo

A standalone, self-contained Terraform/OpenTofu example for provisioning the
AWS infrastructure a Cogrion workspace runs on — VPC, EKS cluster, node
groups, and cluster bootstrap (storage classes, namespaces, cluster agent).

## Why this exists

Cogrion tenants fall into two groups:

- **Cogrion-provisioned** — Cogrion's own automation creates and manages your
  cloud infrastructure end to end.
- **Self-managed** — you provision and own the infrastructure yourself, using
  this example as the reference implementation, then hand Cogrion the
  resulting identifiers (bucket names, IAM role ARNs, node group, ...) via
  the `byo*` fields on each KCL module's config (e.g.
  `ObservabilityConfig.byoThanosBucket`, `byoPrometheusRoleArn`,
  `byoNodegroup`). Cogrion adopts what you built instead of provisioning a
  duplicate.

This example is intentionally a single, hand-merged, standalone
directory — not a set of composed modules — so you can read every resource
it creates top to bottom before running it. It is the intended eventual
replacement for `terraform-workspace-infra-aws`'s modular structure; that
repo predates this simplification and will be deprecated once this example
covers everything it does today. **One implementation serves both tenant
groups** — this is the same code Cogrion's own automation runs, not a
parallel copy that could drift.

### Dual-mode provider auth

The `aws` provider supports both consumption modes from the same config:

- **Self-deploy** (default) — leave `assume_role_arn` unset. Terraform uses
  your default credentials (`aws configure`, environment variables, an
  already-assumed role — your choice) directly, in your own account.
- **Assume-role** — set `assume_role_arn` (plus `assume_role_session_name`
  and, if your trust policy requires it, `assume_role_external_id`). This is
  how Cogrion's own automation runs this same example: assuming into your
  account from Cogrion's side, rather than holding your credentials.

The `assume_role` block is generated with a `dynamic` block gated on whether
`assume_role_arn` is set, so it's omitted entirely in self-deploy mode —
not just left with empty values.

## Prerequisites

- Terraform >= 1.11.0, or OpenTofu >= 1.10.0 (native S3 state locking —
  version tracks don't line up between the two tools)
- AWS credentials — either your own default credentials (self-deploy mode)
  or an IAM role Cogrion's automation can assume (assume-role mode, set via
  `assume_role_arn`)
- An AWS region and a set of non-overlapping CIDR ranges for your VPC (see
  `variables.tf` for the defaults this example expects)
- An S3 bucket for Terraform state (see below — this backend is standardized,
  not optional, so state is never left on a laptop and concurrent applies
  can't corrupt it)

## One-time backend setup

This example always uses the S3 backend, with native S3 state locking
(`use_lockfile`, Terraform >= 1.11 / OpenTofu >= 1.10 — no DynamoDB lock
table; that pattern is deprecated in favor of this). Create the bucket once,
before the first `terraform init`:

```bash
aws s3api create-bucket --bucket my-cogrion-tfstate --region us-east-1
aws s3api put-bucket-versioning --bucket my-cogrion-tfstate \
  --versioning-configuration Status=Enabled
```

## Usage

```bash
cd examples/aws-eks-byo

cp terraform.tfvars.example terraform.tfvars   # fill in your own values
cp backend.hcl.example backend.hcl             # fill in your bucket

terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

### Backend config via CLI flags instead of a file

If you'd rather not keep a `backend.hcl` file around (e.g. scripting this
from CI, or your bucket name is generated at runtime), pass the same values
as individual `-backend-config` flags instead:

```bash
terraform init \
  -backend-config="bucket=my-cogrion-tfstate" \
  -backend-config="key=aws-eks-byo/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="encrypt=true" \
  -backend-config="use_lockfile=true"
```

Both forms configure the same empty `backend "s3" {}` block in
`versions.tf` — use whichever fits your workflow.

### Targeted plan/apply

No staged runbook needed — just target the piece you want, then apply
everything else once you're satisfied:

```bash
terraform apply -target=module.vpc   # just the VPC
terraform apply                      # everything, including the rest
```

## After provisioning

Take the outputs this example produces (VPC ID, subnet IDs, cluster name,
OIDC provider ARN, node group name, bucket names, IAM role ARNs) and set the
matching `byo*` fields in your KCL flavor's values file so Cogrion adopts
these resources instead of creating its own.

## Reference

Auto-generated from this directory's `.tf` files via
[terraform-docs](https://terraform-docs.io) — do not hand-edit the block
below, run `terraform-docs .` (config in `.terraform-docs.yml`) to refresh it
after changing any variable, output, or provider requirement.

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.10.0 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | 6.35.0 |
| <a name="requirement_kubernetes"></a> [kubernetes](#requirement\_kubernetes) | ~> 2.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.35.0 |
| <a name="provider_helm"></a> [helm](#provider\_helm) | 2.17.0 |
| <a name="provider_kubernetes"></a> [kubernetes](#provider\_kubernetes) | 2.38.0 |
| <a name="provider_terraform"></a> [terraform](#provider\_terraform) | n/a |

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_bootstrap_irsa"></a> [bootstrap\_irsa](#module\_bootstrap\_irsa) | terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks | ~> 5.52.0 |
| <a name="module_cluster_agent_irsa"></a> [cluster\_agent\_irsa](#module\_cluster\_agent\_irsa) | terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks | ~> 5.52.0 |
| <a name="module_ebs_csi_driver_irsa"></a> [ebs\_csi\_driver\_irsa](#module\_ebs\_csi\_driver\_irsa) | terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks | ~> 5.52 |
| <a name="module_eks"></a> [eks](#module\_eks) | terraform-aws-modules/eks/aws | 21.1.5 |
| <a name="module_eks_blueprints_addons"></a> [eks\_blueprints\_addons](#module\_eks\_blueprints\_addons) | aws-ia/eks-blueprints-addons/aws | 1.23.0 |
| <a name="module_kubeblocks_irsa"></a> [kubeblocks\_irsa](#module\_kubeblocks\_irsa) | terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks | ~> 5.52.0 |
| <a name="module_vpc"></a> [vpc](#module\_vpc) | terraform-aws-modules/vpc/aws | ~> 5.0 |

## Resources

| Name | Type |
| ---- | ---- |
| [aws_iam_policy.bootstrap](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/resources/iam_policy) | resource |
| [aws_iam_policy.cluster_agent_policy](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/resources/iam_policy) | resource |
| [aws_iam_policy.kubeblocks](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/resources/iam_policy) | resource |
| [helm_release.cplane_agent](https://registry.terraform.io/providers/hashicorp/helm/latest/docs/resources/release) | resource |
| [helm_release.external_dns](https://registry.terraform.io/providers/hashicorp/helm/latest/docs/resources/release) | resource |
| [helm_release.traefik](https://registry.terraform.io/providers/hashicorp/helm/latest/docs/resources/release) | resource |
| [kubernetes_cluster_role_binding_v1.bootstrap](https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs/resources/cluster_role_binding_v1) | resource |
| [kubernetes_config_map_v1.bootstrap_script](https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs/resources/config_map_v1) | resource |
| [kubernetes_job_v1.bootstrap](https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs/resources/job_v1) | resource |
| [kubernetes_namespace_v1.cogrion_system](https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs/resources/namespace_v1) | resource |
| [kubernetes_secret_v1.bootstrap_token](https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs/resources/secret_v1) | resource |
| [kubernetes_service_account_v1.bootstrap](https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs/resources/service_account_v1) | resource |
| [kubernetes_service_account_v1.cplane_agent](https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs/resources/service_account_v1) | resource |
| [terraform_data.bootstrap_trigger](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |
| [aws_availability_zones.available](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/data-sources/availability_zones) | data source |
| [aws_caller_identity.current](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/data-sources/caller_identity) | data source |
| [aws_iam_session_context.current](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/data-sources/iam_session_context) | data source |
| [aws_partition.current](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/data-sources/partition) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/data-sources/region) | data source |
| [aws_subnet.private](https://registry.terraform.io/providers/hashicorp/aws/6.35.0/docs/data-sources/subnet) | data source |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_agent_version"></a> [agent\_version](#input\_agent\_version) | cplane-agent Helm chart version (composite tag, e.g. 0.1.13-0.1.30) | `string` | `"0.1.13-0.1.32"` | no |
| <a name="input_assume_role_arn"></a> [assume\_role\_arn](#input\_assume\_role\_arn) | IAM role ARN to assume. Empty = use default credentials directly (self-deploy mode). | `string` | `""` | no |
| <a name="input_assume_role_external_id"></a> [assume\_role\_external\_id](#input\_assume\_role\_external\_id) | External ID for the assumed role, if your trust policy requires one. Only used when assume\_role\_arn is set. | `string` | `""` | no |
| <a name="input_assume_role_session_name"></a> [assume\_role\_session\_name](#input\_assume\_role\_session\_name) | Session name for the assumed role. Only used when assume\_role\_arn is set. | `string` | `"terraform-access"` | no |
| <a name="input_auto_shutdown"></a> [auto\_shutdown](#input\_auto\_shutdown) | Whether non-prod resources auto-shut-down for cost control | `bool` | `false` | no |
| <a name="input_az_count"></a> [az\_count](#input\_az\_count) | Count of availability zones | `number` | `2` | no |
| <a name="input_backup_policy"></a> [backup\_policy](#input\_backup\_policy) | Backup cadence/retention evidence for SOC2 availability criteria (e.g. daily-30d) | `string` | `"daily-30d"` | no |
| <a name="input_bootstrap_token"></a> [bootstrap\_token](#input\_bootstrap\_token) | One-time bootstrap token from the control plane. Set to trigger the bootstrap Job; leave empty to skip. | `string` | `""` | no |
| <a name="input_cogrion_account_id"></a> [cogrion\_account\_id](#input\_cogrion\_account\_id) | Cogrion account identifier this infrastructure belongs to | `string` | n/a | yes |
| <a name="input_cogrion_workspace_id"></a> [cogrion\_workspace\_id](#input\_cogrion\_workspace\_id) | Cogrion workspace identifier this infrastructure belongs to | `string` | n/a | yes |
| <a name="input_contains_pii"></a> [contains\_pii](#input\_contains\_pii) | Whether this workspace's data includes PII — required for data mapping / GDPR evidence | `bool` | `false` | no |
| <a name="input_control_plane_url"></a> [control\_plane\_url](#input\_control\_plane\_url) | Cogrion control plane API URL — used as the external-dns webhook base URL | `string` | `"https://cplane.api.cogrion.com"` | no |
| <a name="input_cost_center"></a> [cost\_center](#input\_cost\_center) | Finance cost center code for chargeback (e.g. CC-1024) | `string` | `"unassigned"` | no |
| <a name="input_data_classification"></a> [data\_classification](#input\_data\_classification) | Data sensitivity of this workspace — public / internal / confidential / restricted (SOC2 CC6.1, ISO A.8.2) | `string` | `"internal"` | no |
| <a name="input_db_private_subnets"></a> [db\_private\_subnets](#input\_db\_private\_subnets) | Private subnet CIDRs for database services. ~252 IPs per subnet/AZ. | `list(string)` | n/a | yes |
| <a name="input_dns_webhook_tag"></a> [dns\_webhook\_tag](#input\_dns\_webhook\_tag) | Image tag for the dns-webhook external-dns sidecar | `string` | `"0.1.6"` | no |
| <a name="input_eks_addon_versions"></a> [eks\_addon\_versions](#input\_eks\_addon\_versions) | EKS managed add-on versions, keyed by add-on name. Defaults are the latest versions compatible with eks\_kubernetes\_version at the time this example was last updated — override per-key to pin something else. | <pre>object({<br/>    coredns                = optional(string, "v1.14.3-eksbuild.3")<br/>    kube_proxy             = optional(string, "v1.36.0-eksbuild.9")<br/>    vpc_cni                = optional(string, "v1.22.3-eksbuild.1")<br/>    eks_pod_identity_agent = optional(string, "v1.3.10-eksbuild.3")<br/>    aws_ebs_csi_driver     = optional(string, "v1.62.0-eksbuild.1")<br/>  })</pre> | `{}` | no |
| <a name="input_eks_blueprints_addon_versions"></a> [eks\_blueprints\_addon\_versions](#input\_eks\_blueprints\_addon\_versions) | Helm chart versions for the eks\_blueprints\_addons module's sub-addons, keyed by addon name. Defaults are the latest chart versions at the time this example was last updated — override per-key to pin something else. | <pre>object({<br/>    cluster_autoscaler              = optional(string, "9.58.0")<br/>    aws_efs_csi_driver              = optional(string, "4.3.0")<br/>    cluster_proportional_autoscaler = optional(string, "1.1.0")<br/>    metrics_server                  = optional(string, "3.13.1")<br/>    aws_load_balancer_controller    = optional(string, "3.4.1")<br/>    external_secrets                = optional(string, "2.7.0")<br/>  })</pre> | `{}` | no |
| <a name="input_eks_blueprints_addons"></a> [eks\_blueprints\_addons](#input\_eks\_blueprints\_addons) | Arbitrary config passed to module "eks\_blueprints\_addons" (aws-ia/eks-blueprints-addons/aws).<br/>Accepts any attribute that module supports. For aws\_load\_balancer\_controller,<br/>vpcId is injected automatically — add extra `set` entries under<br/>aws\_load\_balancer\_controller.set instead of overriding it wholesale.<br/>Chart versions are not set here — see eks\_blueprints\_addon\_versions.<br/>Traefik and external-dns are managed separately in helm-addons.tf, not here.<br/>cert-manager is not used — wildcard TLS certs are issued by the control-plane<br/>via ACME/Cloudflare and synced into the cluster via ESO. | `any` | `{}` | no |
| <a name="input_eks_cluster_endpoint_public_access"></a> [eks\_cluster\_endpoint\_public\_access](#input\_eks\_cluster\_endpoint\_public\_access) | Whether the EKS API server endpoint is publicly reachable. Fine for a sandbox; set false for preprod/prod once you've confirmed private access works. | `bool` | `true` | no |
| <a name="input_eks_data_plane_subnet_secondary_cidr"></a> [eks\_data\_plane\_subnet\_secondary\_cidr](#input\_eks\_data\_plane\_subnet\_secondary\_cidr) | Secondary CIDR blocks for EKS node/pod IPs, ~32766 IPs per subnet/AZ | `list(string)` | <pre>[<br/>  "100.64.0.0/17",<br/>  "100.64.128.0/17"<br/>]</pre> | no |
| <a name="input_eks_kubernetes_version"></a> [eks\_kubernetes\_version](#input\_eks\_kubernetes\_version) | EKS control plane Kubernetes version | `string` | `"1.36"` | no |
| <a name="input_eks_managed_node_groups"></a> [eks\_managed\_node\_groups](#input\_eks\_managed\_node\_groups) | Map of EKS managed node group configurations. Each key becomes the node group's logical name. | <pre>map(object({<br/>    name        = optional(string)<br/>    description = optional(string)<br/><br/>    # When true, pins the node group to a single AZ (first subnet only).<br/>    # Required for EBS-backed PVCs to avoid "volume node affinity conflict"<br/>    # when pods are rescheduled across AZs. Defaults to false (multi-AZ<br/>    # spread) for stateless workloads.<br/>    stateful = optional(bool, false)<br/><br/>    min_size     = optional(number, 1)<br/>    max_size     = optional(number, 3)<br/>    desired_size = optional(number, 1)<br/><br/>    instance_types = optional(list(string), ["m5.xlarge"])<br/><br/>    disk_size = optional(number, 50)<br/>    disk_type = optional(string, "gp3")<br/><br/>    ami_release_version            = optional(string)<br/>    use_latest_ami_release_version = optional(bool, false)<br/>    # https://docs.aws.amazon.com/eks/latest/APIReference/API_Nodegroup.html#AmazonEKS-Type-Nodegroup-amiType<br/>    ami_type = optional(string)<br/><br/>    labels = optional(map(string), {})<br/><br/>    taints = optional(map(object({<br/>      key    = string<br/>      value  = string<br/>      effect = string<br/>    })), {})<br/><br/>    tags = optional(map(string), {})<br/>  }))</pre> | <pre>{<br/>  "system": {<br/>    "description": "System EKS managed node group",<br/>    "desired_size": 1,<br/>    "disk_size": 100,<br/>    "instance_types": [<br/>      "m5.xlarge"<br/>    ],<br/>    "labels": {<br/>      "NodeGroupType": "system",<br/>      "WorkerType": "ON_DEMAND"<br/>    },<br/>    "max_size": 3,<br/>    "min_size": 1<br/>  }<br/>}</pre> | no |
| <a name="input_enable_external_dns"></a> [enable\_external\_dns](#input\_enable\_external\_dns) | Install external-dns with the dns-webhook sidecar (requires control\_plane\_url). The bootstrap Job copies the cluster-agent-credentials mTLS secret into the external-dns namespace before this installs. | `bool` | `true` | no |
| <a name="input_enable_traefik"></a> [enable\_traefik](#input\_enable\_traefik) | Install Traefik ingress controller | `bool` | `true` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Deployment environment — dev / sandbox / staging / prod, sets the blast-radius boundary | `string` | `"dev"` | no |
| <a name="input_extra_tags"></a> [extra\_tags](#input\_extra\_tags) | Additional freeform tags, purely additive on top of var.tags and the mandatory Cogrion/org/compliance tags below | `map(string)` | `{}` | no |
| <a name="input_git_commit_sha"></a> [git\_commit\_sha](#input\_git\_commit\_sha) | Commit SHA that last applied this resource, for traceability. Normally injected by CI | `string` | `"unknown"` | no |
| <a name="input_kms_key_admin_roles"></a> [kms\_key\_admin\_roles](#input\_kms\_key\_admin\_roles) | Additional IAM role ARNs to grant admin on the EKS cluster's KMS key, beyond the account root and current caller | `list(string)` | `[]` | no |
| <a name="input_owner"></a> [owner](#input\_owner) | Individual or team accountable for this infrastructure | `string` | `"platform-eng"` | no |
| <a name="input_private_subnets"></a> [private\_subnets](#input\_private\_subnets) | Private subnet CIDRs. ~252 IPs per subnet/AZ, for Private NAT + NLB + EKS nodes + EC2 jumphost etc. | `list(string)` | n/a | yes |
| <a name="input_project_name"></a> [project\_name](#input\_project\_name) | Logical project/repo grouping (e.g. cogrion-terraform) | `string` | `"cogrion-workspace"` | no |
| <a name="input_public_subnets"></a> [public\_subnets](#input\_public\_subnets) | Public subnet CIDRs. ~124 IPs per subnet/AZ. | `list(string)` | n/a | yes |
| <a name="input_region"></a> [region](#input\_region) | AWS region to provision into | `string` | n/a | yes |
| <a name="input_retention_policy"></a> [retention\_policy](#input\_retention\_policy) | Data retention window evidence (e.g. 30d) | `string` | `"30d"` | no |
| <a name="input_secondary_cidr_blocks"></a> [secondary\_cidr\_blocks](#input\_secondary\_cidr\_blocks) | Secondary CIDR blocks to attach to the VPC | `list(string)` | <pre>[<br/>  "100.64.0.0/16"<br/>]</pre> | no |
| <a name="input_service_name"></a> [service\_name](#input\_service\_name) | Component this infrastructure belongs to (e.g. keycloak, argocd) | `string` | `"workspace-cluster"` | no |
| <a name="input_system_nodegroup_label"></a> [system\_nodegroup\_label](#input\_system\_nodegroup\_label) | Value of the 'nodegroup' k8s node label on the system node group, used as nodeSelector.nodegroup on all addon Helm releases | `string` | `"system"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | Additional freeform tags, merged with the required Cogrion identifiers below into local.tags | `map(string)` | `{}` | no |
| <a name="input_terraform_backend_bucket"></a> [terraform\_backend\_bucket](#input\_terraform\_backend\_bucket) | S3 bucket for OpenTofu remote state (required for stack provisioning) | `string` | `""` | no |
| <a name="input_vpc_cidr"></a> [vpc\_cidr](#input\_vpc\_cidr) | VPC CIDR | `string` | n/a | yes |
| <a name="input_vpc_name"></a> [vpc\_name](#input\_vpc\_name) | VPC name | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_cluster_certificate_authority_data"></a> [cluster\_certificate\_authority\_data](#output\_cluster\_certificate\_authority\_data) | n/a |
| <a name="output_cluster_endpoint"></a> [cluster\_endpoint](#output\_cluster\_endpoint) | n/a |
| <a name="output_cluster_name"></a> [cluster\_name](#output\_cluster\_name) | n/a |
| <a name="output_cluster_oidc_provider_arn"></a> [cluster\_oidc\_provider\_arn](#output\_cluster\_oidc\_provider\_arn) | n/a |
| <a name="output_cluster_primary_security_group_id"></a> [cluster\_primary\_security\_group\_id](#output\_cluster\_primary\_security\_group\_id) | n/a |
| <a name="output_cluster_secondary_subnet_ids"></a> [cluster\_secondary\_subnet\_ids](#output\_cluster\_secondary\_subnet\_ids) | n/a |
| <a name="output_cluster_service_cidr"></a> [cluster\_service\_cidr](#output\_cluster\_service\_cidr) | n/a |
| <a name="output_cluster_status"></a> [cluster\_status](#output\_cluster\_status) | n/a |
| <a name="output_cluster_version"></a> [cluster\_version](#output\_cluster\_version) | n/a |
| <a name="output_ebs_csi_driver_irsa_role_arn"></a> [ebs\_csi\_driver\_irsa\_role\_arn](#output\_ebs\_csi\_driver\_irsa\_role\_arn) | n/a |
| <a name="output_eks_managed_node_groups"></a> [eks\_managed\_node\_groups](#output\_eks\_managed\_node\_groups) | Map of attribute maps for all EKS managed node groups created |
| <a name="output_kubeblocks_irsa_role_arn"></a> [kubeblocks\_irsa\_role\_arn](#output\_kubeblocks\_irsa\_role\_arn) | n/a |
| <a name="output_node_security_group_id"></a> [node\_security\_group\_id](#output\_node\_security\_group\_id) | n/a |
| <a name="output_private_subnets"></a> [private\_subnets](#output\_private\_subnets) | n/a |
| <a name="output_public_subnets"></a> [public\_subnets](#output\_public\_subnets) | n/a |
| <a name="output_vpc_id"></a> [vpc\_id](#output\_vpc\_id) | n/a |
<!-- END_TF_DOCS -->
