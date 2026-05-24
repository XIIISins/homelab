<!-- docs/architecture/iac.md -->

# IaC

| Tool | Responsibility |
|------|---------------|
| Terraform (`bpg/proxmox`) | VMs, LXCs, DNS, AWS KMS |
| Terraform (`hashicorp/vault`) | Vault config: auth method, policies, roles, KV engine |
| Ansible + AWX | OS config, drift correction, audit trail. Also: K3s install + the Calico addon manifest |
| Flux CD | K3s workload lifecycle |
| Renovate | Dependency version PRs (planned — activated at "2.0" state) |

**IaC layering — explicit model:**
- **Terraform** — anything with an API/provider: Proxmox VMs/LXCs, Cloudflare DNS, AWS KMS, Vault config.
- **Ansible** — OS/node-level: baseline, hardening (security sysctls, SSH, SELinux, module blocklist), K3s *install* + prerequisites + network plumbing (sysctls + VLAN 20 policy routing) + the Calico addon manifest. Playbook `asgard-k3s.yml` runs roles `baseline → k3s → hardening` against the `asgard_k3s` group.
- **Flux** — in-cluster workloads: everything in `k8s/`.
- **Docs (this file + [`docs/procedures/teardown-rebuild.md`](../procedures/teardown-rebuild.md))** — KPN Experia Box config, rebuild runbook, anything else without a useful API.
