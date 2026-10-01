<!-- docs/procedures/canary-pool.md -->

# Canary pool (Phase 10b1)

*Three disposable 512 MB LXCs on **Urd only** that the AIOps loop (10f heal, 10g rebuild) may fault-inject. Roadmap: [`aiops-roadmap.md`](../operations/aiops-roadmap.md) 10b1 + "Blast-radius tiers" T1. Decision: [`decisions.md`](../operations/decisions.md) "Canary pool on Urd, not Skuld/Verd".*

## What they are

| Host | VMID | IP (VLAN 11) | Node |
|------|------|--------------|------|
| `canary-1` | 1190 | `10.0.11.190` | Urd |
| `canary-2` | 1191 | `10.0.11.191` | Urd |
| `canary-3` | 1192 | `10.0.11.192` | Urd |

- 1 vCPU / 512 MB (+256 MB swap) / 4 GB rootfs on `local-lvm`, unprivileged, Debian 13. Terraform: `canary_nodes` in `terraform/proxmox/asgard-lxcs/lxcs.tf` (API-token module, not `-root`).
- Run **nothing** but the fleet baseline: `baseline`, `vlagent`, `zabbix-agent`, `hardening` (`playbooks/asgard-canary.yml`, imported by `site.yml`, so the 30-min reconcile and drift-check cover them). Zabbix host group `Asgard/LXCs/Canary`. No data, no VIP, no secrets, no inbound consumers: destroying one loses nothing.
- NetBox: VMs `canary-1..3` (role `canary`, tags `ansible:canary`, `aiops:t1`, `aiops:canary`, VMID custom field), declared in `terraform/netbox/vms.tf`. The `aiops:*` tags are not `ansible:*`, so they never become Ansible groups.
- Ansible: group `canary` (`hosts.yml`, also via NetBox), `group_vars/canary.yml` sets `aiops_tier: T1`, `aiops_canary: true`.
- AIOps registry: `canary-1..3` are listed individually in `host_tiers.T1` of `aiops/actions.yml`, with `restart-unit` allow-list `[vlagent.service, zabbix-agent2.service]`. `replay-role` / `replay-role-check` accept them through `host_tiers.T1` with the existing tag allow-list (`baseline`, `hardening`, `vlagent`, `zabbix-agent`). Nothing was widened for any non-canary host; all mutators remain `max_autonomy: approval`.

## RAM headroom (Urd, read-only `free -m` + `pct list`, 2026-10-01)

Urd total 31.9 GB; 7.6 GB **available** with PBS (1 GB) already on it (7 LXCs + gondul 4 GB + einherjar-urd 16 GB running; swap 1.4 GB used). Canary caps total 1.5 GB, so worst case ~6.1 GB available afterwards (real canary RSS is ~60-100 MB each, so ~7.3 GB expected). CPU and thin-pool disk are not constraints. Do not add more tenants to Urd without re-checking.

## Build (operator gates, from the main checkout)

1. `terraform/proxmox/asgard-lxcs`: `terraform plan` then `apply` (expect +6: 3 `random_password`, 3 containers).
2. `terraform/netbox`: `terraform plan -parallelism=2` then `apply -parallelism=2` (default parallelism OOMKills NetBox). Expect +1 `netbox_device_role` (`canary`), +3 `netbox_tag`, +3 VMs, +3 interfaces, +3 IPs, +3 primary IPs (+16, no changes/destroys to existing).
3. Day-1 (root only, no `ansible` user yet), one at a time with the Ansible playbook lock rule:
   `ansible-playbook -i inventory/hosts.yml -e 'ansible_user=root' --tags baseline --limit canary playbooks/asgard-canary.yml`
4. Full converge as `ansible` (locks root out at the end):
   `ansible-playbook -i inventory/hosts.yml --limit canary playbooks/asgard-canary.yml`
5. Refresh the NetBox inventory cache (Semaphore `refresh-netbox-inventory`) so the reconcile loop sees them; run `playbooks/zabbix-agent.yml --limit canary` if the host record did not register.
6. Reboot-test one canary (`pct reboot 1190` on Urd) and re-check the agents.

## Destroy / recreate (lossless)

From the **main** checkout (never a worktree):

1. `terraform -chdir=terraform/proxmox/asgard-lxcs apply -replace='proxmox_virtual_environment_container.canary["canary-2"]'` (plan must show exactly one replace, name + vmid + IP unchanged, so NetBox needs no change).
2. Drop the stale SSH host key (`ssh-keygen -R 10.0.11.191`).
3. Repeat Build steps 3-4 with `--limit canary-2`. The Zabbix host record is re-registered idempotently; the NetBox record is untouched.

To retire a canary permanently: remove it from `canary_nodes` (Terraform), the `vms.tf` entries, `hosts.yml` and the `aiops/actions.yml` T1 list in one PR.

## Fault injection: stay scoped to `canary-*`

Urd also hosts **PBS (1101), Hugin (1102), Saga (1110), Bifrost (1113), Factorio (1120), Vör (1131), Hlin (1133), the asgard K3s CP gondul (2001) and worker einherjar-urd (2101)**. LXCs share Urd's kernel and its network/storage. Rules for any 10f/10g test:

- Act only through registry actions (Semaphore) or a test harness whose target is validated against `^canary-[123]$` **and** VMID 1190-1192. Never a group, a glob, or "all on Urd".
- Inject inside the guest: stop/kill a unit, fill the 4 GB rootfs, hog memory inside the 512 MB cgroup (the cap keeps the OOM in the container), spin its one vCPU. `pct stop|reboot|destroy` is allowed only for those three VMIDs.
- Never touch the PVE host or anything host-global: no `sysctl`, `modprobe`, `iptables`/`tc` on Urd or `vmbr0`, no `sysrq`/kernel-panic tests (shared kernel), no `systemctl` on Urd, no thin-pool-wide fills, no NFS (Munin) or `local-lvm` stress outside the guest rootfs. Network faults only inside the container's netns or via its own `net0` link.
- Do not use shared dependencies as the fault target (AdGuard VIP, Vault, NetBox, Zabbix): the canaries only exercise their own baseline.
- Whole-node scenarios (freeze, reboot of Urd) are out of scope for the canary pool: Urd is production.

## Interaction with the reconcile loop and alerting

- The 30-min `asgard-apply` and 6h drift-check include `canary` (via `site.yml`): a baseline-config fault gets re-converged, and a stopped or destroyed canary makes the run report a failed/unreachable host (Hermod `critical`). Until the 10f1 maintenance flag exists, schedule tests knowing this; tracked in [`open-questions.md`](../operations/open-questions.md).
- Canary Zabbix triggers will fire to Hermod like any other host. Zabbix host group `Asgard/LXCs/Canary` exists so a maintenance window or action filter can target it.
