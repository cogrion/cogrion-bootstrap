# Merged from terraform-workspace-infra-aws/modules/account-networking/vpc.
# Simplified for direct customer use: no cross-account assume-role (Cogrion's
# own automation runs this via a dual core/client provider setup — a
# self-managed customer just runs it in their own account with the single
# default `aws` provider declared in versions.tf) and no remote-state backend
# forced on you — configure your own in versions.tf if you want one.

data "aws_availability_zones" "available" {
  # Exclude local zones
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = var.vpc_name
  cidr = var.vpc_cidr
  azs  = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Secondary CIDR block attached to VPC for EKS Control Plane ENI + Nodes + Pods
  secondary_cidr_blocks = var.secondary_cidr_blocks

  # 1/ RFC6598 range 100.64.0.0/16 for EKS Data Plane for two subnets (32766 IPs
  #    per subnet) across two AZs for EKS Control Plane ENI + Nodes + Pods
  # 2/ Two private subnets with RFC1918 private IPv4 address range for
  #    Private NAT + NLB + Airflow + EC2 jumphost etc.
  private_subnets = concat(var.private_subnets, var.eks_data_plane_subnet_secondary_cidr)

  # Optional public subnets for NAT and IGW — fine for a sandbox/PoC; disable
  # for production and use Private NAT + TGW instead.
  public_subnets = var.public_subnets

  # Private subnets for databases
  database_subnet_group_name         = "${var.vpc_name}-db-subnet"
  database_subnets                   = var.db_private_subnets
  create_database_subnet_group       = true
  create_database_subnet_route_table = true

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  enable_dns_support   = true

  public_subnet_tags = {
    "kubernetes.io/role/elb"                = 1
    "kubernetes.io/cluster/${var.vpc_name}" = "shared"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb"       = 1
    "karpenter.sh/discovery"                = var.vpc_name
    "kubernetes.io/cluster/${var.vpc_name}" = "shared"
  }

  enable_flow_log = false

  tags = local.tags_account
}
