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
- Run **nothing** but the fleet baseline: `baseline`, `vlagent`, `zabbix-agent`, `hardening` (`playbooks/asgard-canary.yml`, imported by **`site-nonprod.yml`, NOT `site.yml`**: the prod reconcile and drift-check never touch them). Zabbix host group `Asgard/LXCs/Canary`. No data, no VIP, no secrets, no inbound consumers: destroying one loses nothing.
- NetBox: VMs `canary-1..3` (role `canary`, tags `ansible:canary`, `aiops:t1`, `aiops:canary`, VMID custom field), declared in `terraform/netbox/vms.tf`. The `aiops:*` tags are not `ansible:*`, so they never become Ansible groups.
- Ansible: group `canary` (`hosts.yml`, also via NetBox), `group_vars/canary.yml` sets `aiops_tier: T1`, `aiops_canary: true`.
- Alerting: canaries are non-prod and NEVER alert above the `info` tier (FYI channel, no mention). See "Alerting caps" below.
- AIOps registry: `canary-1..3` are listed individually in `host_tiers.T1` of `aiops/actions.yml`, with `restart-unit` allow-list `[vlagent.service, zabbix-agent2.service]`. `replay-role` / `replay-role-check` accept them through `host_tiers.T1` with the existing tag allow-list (`baseline`, `hardening`, `vlagent`, `zabbix-agent`); the wrappers import `site-nonprod.yml` after `site.yml` so the replay actually reaches a canary (guard unchanged: one host, one tag, check-mode match). Nothing was widened for any non-canary host; all mutators remain `max_autonomy: approval`.

## RAM headroom (Urd, read-only `free -m` + `pct list`, 2026-10-01)

Urd total 31.9 GB; 7.6 GB **available** with PBS (1 GB) already on it (7 LXCs + gondul 4 GB + einherjar-urd 16 GB running; swap 1.4 GB used). Canary caps total 1.5 GB, so worst case ~6.1 GB available afterwards (real canary RSS is ~60-100 MB each, so ~7.3 GB expected). CPU and thin-pool disk are not constraints. Do not add more tenants to Urd without re-checking.

## Build (operator gates, from the main checkout)

0. **Alerting prerequisites first** (so the first canary event cannot reach a high tier): (a) seed Vault `secret/ansible/hermod/discord/info` (field `url`, the operator-created FYI channel webhook; 1P mirror `Hermod - Discord webhook - info`); (b) live run `ansible-playbook playbooks/asgard-hermod.yml --tags hermod-api` (renders the `info` Apprise block; the role fails fast if the secret is missing, and must not be run before it exists); (c) `ansible-playbook playbooks/asgard-zabbix.yml --tags zabbix:hermod-mediatype` (pushes the webhook script with the canary cap); (d) `terraform apply` in `terraform/semaphore` (new `asgard-nonprod-*` templates + daily schedule; check with `plan` first).
1. `terraform/proxmox/asgard-lxcs`: `terraform plan` then `apply` (expect +6: 3 `random_password`, 3 containers).
2. `terraform/netbox`: `terraform plan -parallelism=2` then `apply -parallelism=2` (default parallelism OOMKills NetBox). Expect +1 `netbox_device_role` (`canary`), +3 `netbox_tag`, +3 VMs, +3 interfaces, +3 IPs, +3 primary IPs (+16, no changes/destroys to existing).
3. Day-1 (root only, no `ansible` user yet), one at a time with the Ansible playbook lock rule:
   `ansible-playbook -i inventory/hosts.yml -e 'ansible_user=root' --tags baseline --limit canary playbooks/site-nonprod.yml`
4. Full converge as `ansible` (locks root out at the end):
   `ansible-playbook -i inventory/hosts.yml --limit canary playbooks/site-nonprod.yml`
5. Refresh the NetBox inventory cache (Semaphore `refresh-netbox-inventory`) so the nonprod Semaphore templates see them; if the Zabbix host record did not register, re-run step 4 with `--tags zabbix-agent` (the fleet `zabbix-agent.yml` sweep excludes canaries).
6. Reboot-test one canary (`pct reboot 1190` on Urd) and re-check the agents.

## Destroy / recreate (lossless)

From the **main** checkout (never a worktree):

1. `terraform -chdir=terraform/proxmox/asgard-lxcs apply -replace='proxmox_virtual_environment_container.canary["canary-2"]'` (plan must show exactly one replace, name + vmid + IP unchanged, so NetBox needs no change).
2. Drop the stale SSH host key (`ssh-keygen -R 10.0.11.191`).
3. Repeat Build steps 3-4 with `--limit canary-2` (the nonprod apply/drift templates will report it unreachable as `info` until it is back). The Zabbix host record is re-registered idempotently; the NetBox record is untouched.

To retire a canary permanently: remove it from `canary_nodes` (Terraform), the `vms.tf` entries, `hosts.yml` and the `aiops/actions.yml` T1 list in one PR.

## The "Canary smoke test" Zabbix template (High and Disaster on demand)

The stock "agent not available" trigger is **Average**, and the diagnosis agent (Gná) only receives **High and Disaster**, so a stopped canary agent alone never reaches it. The canaries therefore carry their own template, `Canary smoke test` ([`ansible/playbooks/files/zabbix/canary-smoke-test.yml`](../../ansible/playbooks/files/zabbix/canary-smoke-test.yml)), imported by `playbooks/zabbix-host-groups.yml` and linked only through `group_vars/canary.yml` (so it never reaches a real host):

| Trigger | Severity | Fires when | Use |
|---|---|---|---|
| `Canary smoke: agent service down (zabbix-agent2)` | High | the agent has not answered for 3 min (`nodata` on a heartbeat item) | the 10f autonomous restart (`scripts/canary/fault stop <canary> zabbix-agent2.service`) |
| `Canary smoke: vlagent not active` | High | `systemd.unit.info[vlagent.service,ActiveState]` is not `active` | the same for `vlagent.service` |
| `Canary smoke: synthetic High flag set` | High | the file `/var/lib/aiops-smoke/high` exists | a High problem with no service touched (`scripts/canary/fault flag <canary> high`; `unflag` clears it) |
| `Canary smoke: synthetic Disaster flag set` | Disaster | `/var/lib/aiops-smoke/disaster` exists | the Disaster path end to end |

Canary alerts stay capped at the Hermod `info` tier (below), so none of these pages anyone. The two service-fault triggers are routed to `RB-UNIT-STOPPED-T1` (`aiops/alert-routing.yml` `zbx-canary-unit-down`); the synthetic ones fall to the Zabbix catch-all and exercise the plumbing. Item keys are deliberately not keys that "Linux by Zabbix agent" already defines (Zabbix refuses two linked templates defining the same key on one host). To change the template: edit the YAML, then `ansible-playbook playbooks/asgard-canary.yml --tags zabbix-agent:canary-template,zabbix-agent:register --limit canary`.

## Fault injection: stay scoped to `canary-*`

Urd also hosts **PBS (1101), Hugin (1102), Saga (1110), Bifrost (1113), Factorio (1120), Vör (1131), Hlin (1133), the asgard K3s CP gondul (2001) and worker einherjar-urd (2101)**. LXCs share Urd's kernel and its network/storage. Rules for any 10f/10g test:

- Act only through registry actions (Semaphore) or a test harness whose target is validated against `^canary-[123]$` **and** VMID 1190-1192. Never a group, a glob, or "all on Urd".
- Inject inside the guest: stop/kill a unit, fill the 4 GB rootfs, hog memory inside the 512 MB cgroup (the cap keeps the OOM in the container), spin its one vCPU. `pct stop|reboot|destroy` is allowed only for those three VMIDs.
- Never touch the PVE host or anything host-global: no `sysctl`, `modprobe`, `iptables`/`tc` on Urd or `vmbr0`, no `sysrq`/kernel-panic tests (shared kernel), no `systemctl` on Urd, no thin-pool-wide fills, no NFS (Munin) or `local-lvm` stress outside the guest rootfs. Network faults only inside the container's netns or via its own `net0` link.
- Do not use shared dependencies as the fault target (AdGuard VIP, Vault, NetBox, Zabbix): the canaries only exercise their own baseline.
- Whole-node scenarios (freeze, reboot of Urd) are out of scope for the canary pool: Urd is production.

## Alerting caps (canaries never alert above `info`)

How severity is chosen per path, and where the canary cap sits. Tags are Hermod's (`critical` Hrist, `alert` Mist, `info` Randgrid, `media` Olrun; [`notifications.md`](../services/notifications.md)).

| Path | How severity is chosen today | Canary cap | IaC or manual |
|------|------------------------------|------------|---------------|
| Semaphore prod templates (`asgard-drift-check` 6h, `asgard-apply`, `asgard-fleet-agents` daily) | `hermod_summary` callback keys on the wrapper filename: apply/fleet-agents failure -> `critical`; drift failure or changes -> `alert` | Canaries are **not in `site.yml`**, and the `vlagent.yml` / `zabbix-agent.yml` sweeps (which `fleet-agents.yml` runs) exclude `:!canary`, so none of these touch them | IaC |
| Semaphore non-prod (`asgard-nonprod-drift-check` daily, `asgard-nonprod-apply` manual; wrappers `nonprod-*.yml` -> `site-nonprod.yml`) | Same callback, `_NONPROD_MODES` | Tag forced to `info`, title `[non-prod] ...`; changes-only drift is silent | IaC (`terraform/semaphore` apply needed) |
| Zabbix | `hermod-webhook.js` maps trigger severity: Disaster/High -> `critical`, Average -> `alert`, lower -> suppressed; the Admin user media bitmask (Average+High+Disaster) is the first filter | For hosts matching `^canary-[0-9]+$` every alertable severity becomes `info` in the script | IaC (`asgard-zabbix.yml --tags zabbix:hermod-mediatype`) |
| S4 infra-health prober (`infra-health-check.yml`) | Per-finding `critical`/`alert` POSTs | Nothing to cap: it runs on `localhost`/`control` only and never enumerates canaries | n/a |
| aiops routing (`aiops/alert-routing.yml` + `normalize.py`) | Severity = the Hermod tag | Any alert whose host matches `canary-N` or whose title carries `[non-prod]` is emitted at severity `info` with label `aiops_canary: "true"`, even if a producer tagged it higher; non-prod semaphore alerts get host `nonprod` so they never share a fingerprint with the prod `fleet` ones | IaC |

**Optional, manual (not required):** the Zabbix trigger action "Report problems to Zabbix administrators" is UI state, not IaC. Belt and braces would be Alerts -> Actions -> Trigger actions -> that action -> Conditions -> add `Host group does not equal Asgard/LXCs/Canary`. Do NOT add it unless you want canaries silent in Discord entirely (it would also drop the `info` FYI). The webhook cap above already guarantees no `critical`/`alert` escapes.

Other notes: until the `asgard-hermod.yml` live run (step 0) renders the `info` block, an `info` POST matches no Apprise URL and is dropped, which is fail-safe for canaries. The reconcile no longer re-converges canaries on the 6h/manual prod path: they converge only via `asgard-nonprod-*`, a manual `site-nonprod.yml`, or `replay-role`.
