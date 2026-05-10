# IaC — Terraform

Terraform provisions everything that needs to exist before Ansible can configure it. It defines *what* resources exist. Ansible defines *how* they are configured.

## Responsibilities

| Resource | Terraform manages |
|----------|-----------------|
| Proxmox VMs | Create, resize, destroy |
| Proxmox LXCs | Create, configure, destroy |
| K3s VM bootstrap | Cloud-init, initial OS setup |
| Cloudflare DNS records | A, CNAME, SRV, TXT records |
| AWS KMS key | Vault unseal key |
| AWS IAM user + policy | `vault-unseal` user, least-privilege policy |

## Providers

```hcl
terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"    # NOT telmate — bpg is actively maintained
      version = "~> 0.x"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.x"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.x"
    }
  }
}
```

## State management

Terraform state should not live on a local filesystem — it gets lost and contains sensitive data. Options:

- **Terraform Cloud** free tier — simplest, zero ops
- **S3 backend** — use the existing AWS account, state in an S3 bucket with DynamoDB locking
- **Local with encryption** — acceptable for a single operator, use `terraform.tfstate` gitignored + manual backup

Recommended: S3 backend in the existing AWS account. Costs pennies per month, state is versioned, locking prevents concurrent runs.

## Secrets in Terraform

Secrets are never in Terraform files or state where avoidable. Patterns used:

```bash
# Inject via environment variables — never in .tf files
export TF_VAR_proxmox_api_token="$(ansible-vault decrypt --output - secrets/proxmox_token.vault)"
export TF_VAR_cloudflare_api_token="$(ansible-vault decrypt --output - secrets/cf_token.vault)"

terraform apply
```

`terraform.tfvars` is gitignored. `terraform.tfvars.example` is committed with placeholder values showing the required variables.

## VM/LXC provisioning pattern

All VMs and LXCs are provisioned from cloud-init templates. Templates are created once per OS version (Ubuntu 24.04 LTS, Debian 12) and reused.

```hcl
resource "proxmox_virtual_environment_vm" "k3s_cp" {
  count     = 3
  name      = "k3s-cp-${count.index + 1}"
  node_name = local.nodes[count.index]

  clone {
    vm_id = var.ubuntu_template_id
  }

  initialization {
    ip_config {
      ipv4 {
        address = "10.0.20.${150 + count.index}/24"
        gateway = "10.0.254.1"
      }
    }
    user_account {
      username = var.admin_user
      keys     = [var.ssh_public_key]
    }
  }

  memory { dedicated = 2048 }
  cpu    { cores = 2 }
}
```
