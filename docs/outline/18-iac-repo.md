# IaC — repo structure

Public GitHub repo. Everything is code. Nothing sensitive is committed.

## Directory layout

```
homelab/
├── CLAUDE.md                     ← Claude Code context — read before touching anything
├── renovate.json                 ← Renovate configuration
│
├── terraform/
│   ├── proxmox/                  ← VM and LXC definitions
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   ├── lxcs.tf               ← Must-run LXC definitions
│   │   └── vms.tf                ← K3s VM definitions
│   ├── k3s/                      ← K3s bootstrap (cloud-init, templates)
│   ├── dns/                      ← Cloudflare DNS records
│   ├── aws/                      ← KMS key + IAM user
│   ├── terraform.tfvars.example  ← Committed — shows required variables
│   └── backend.tf                ← S3 state backend config
│
├── ansible/
│   ├── inventory/
│   │   ├── hosts.yml             ← Static inventory (supplemented by AWX dynamic)
│   │   └── group_vars/
│   │       ├── all.yml           ← Common vars (non-sensitive)
│   │       └── all.vault.yml     ← Ansible Vault encrypted secrets
│   ├── roles/
│   │   ├── common/               ← Applied to every host
│   │   ├── proxmox/              ← Proxmox-specific config
│   │   ├── k3s-prereqs/          ← K3s VM prerequisites
│   │   ├── lxc-base/             ← Base LXC config
│   │   ├── sssd/                 ← SSSD + Authentik LDAP
│   │   └── ...
│   └── playbooks/
│       ├── proxmox.yml           ← Configure Proxmox nodes
│       ├── must-run.yml          ← Configure must-run LXCs
│       ├── k3s-vms.yml           ← Configure K3s VMs
│       └── bootstrap.yml         ← Full bootstrap playbook
│
├── k8s/                          ← Flux-managed manifests
│   ├── flux-system/              ← Flux bootstrap output (generated)
│   ├── infrastructure/
│   │   ├── sources/              ← HelmRepository definitions
│   │   ├── metallb/
│   │   ├── traefik/
│   │   ├── cert-manager/
│   │   ├── authentik/
│   │   ├── vault/
│   │   ├── external-secrets/
│   │   ├── sealed-secrets/
│   │   ├── awx/
│   │   ├── prometheus/
│   │   └── grafana/
│   └── apps/
│       ├── outline/
│       ├── immich/
│       ├── jellyfin/
│       ├── n8n/
│       ├── komga/
│       ├── phpipam/
│       ├── privatebin/
│       ├── homepage/
│       └── startpage/
│
└── docs/
    ├── homelab-design.md         ← Single reference document (complete design)
    └── outline/                  ← This directory — Outline knowledge base
        ├── 01-overview.md
        ├── 02-network-topology.md
        └── ...
```

## Conventions

| Convention | Rule |
|-----------|------|
| Terraform provider | `bpg/proxmox` — not `telmate` |
| Ansible structure | Roles for reusable logic, playbooks orchestrate roles |
| K8s manifests | Helm charts via `HelmRelease` where available, Kustomize otherwise |
| Secrets | Never committed — Ansible Vault or ESO + Vault |
| Naming | Norse mythology theme for hosts/LXCs |
| IPs | All static — no DHCP for infrastructure |
| Image versions | Always pinned — Renovate handles updates |
| Commit messages | Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`) |

## Gitignore essentials

```gitignore
# Terraform
.terraform/
terraform.tfvars
*.tfstate
*.tfstate.backup
.terraform.lock.hcl   # commit this — pins provider versions

# Ansible
*.retry
.vault-pass

# Local
.DS_Store
```
