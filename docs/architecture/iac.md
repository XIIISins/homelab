<!-- docs/architecture/iac.md -->

# IaC

| Tool | Responsibility |
|------|---------------|
| Terraform | Everything with an API or provider. Modules under `terraform/`: `proxmox/` (K3s VMs, LXCs, standalone VMs, pools, scoped access), `netbox`, `vault`, `authentik`, `cloudflare`, `adguard`, `tailscale`, `garage`, `semaphore`, `github`, `aws`, `digitalocean` and `digitalocean-burst` |
| Ansible | OS/node-level config, drift correction and the audit trail: baseline, hardening, K3s install and the Calico addon files, LXC/VM service roles. Run by hand or by Semaphore against a NetBox dynamic inventory ([`ansible-orchestration.md`](ansible-orchestration.md)) |
| Flux CD | K3s workload lifecycle: everything in `k8s/asgard/`. Merging to `main` is the deploy |
| Dependabot, chart-bump agent | Dependency PRs: Dependabot for GitHub Actions; the `chart-bump` agent for Helm charts and platform components (K3s, Calico, Flux, pinned images). Pin policy: [`ci.md`](../procedures/ci.md). Renovate is deferred, and the question reopened when Phase 7 was dropped ("Renovate — stay manual through stabilization" in [`decisions.md`](../operations/decisions.md)) |

**Layering:**
- **Terraform** owns anything with an API: Proxmox guests, DNS records and rewrites, Vault config, Authentik applications, NetBox records, AWS KMS and backup buckets. Every new LXC/VM also gets its NetBox declarations in the same change.
- **Ansible** owns the OS: baseline, hardening (security sysctls, SSH, SELinux, module blocklist), K3s *install*, prerequisites and network plumbing (sysctls, VLAN 20 policy routing), and the Calico addon files. `asgard-k3s.yml` runs against the `k3s` group (CPs then workers): `baseline`, `k3s`, the worker disk and iSCSI-cleanup roles, `vlagent`, `zabbix-agent`, `hardening`.
- **Flux** owns in-cluster workloads. No `kubectl apply` against production.
- **Docs** (this directory and [`teardown-rebuild.md`](../procedures/teardown-rebuild.md)) cover what has no useful API: the KPN Experia Box config, the rebuild runbook, DSM settings.

State and applies: `terraform apply` only from the main checkout; one `ansible-playbook` at a time across agents (Semaphore serializes its own runs). Git is the spec; NetBox is the queryable view of it.
