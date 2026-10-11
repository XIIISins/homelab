<!-- docs/outline/procedures/proxmox-host-patching.md -->

# Proxmox host patching

Rolling OS patching of the three hypervisors (Urd, Verd, Skuld). A host reboot takes down every VM and LXC on it, so this has its own deliberately-triggered, health-gated playbook, `ansible/playbooks/proxmox-host-patching.yml`. The fleet-wide `os-updates` template excludes the hypervisors on purpose.

**Use when:** the owner schedules host patching (kernel/PVE updates).
**Operator-run, watched.** Do not start this unattended.
**Duration:** about 15 to 25 minutes per host including re-convergence; serial, so 45 to 75 minutes total.

---

## Why it is safe

Every host carries **exactly one member of every quorum**: one K3s control plane, one worker, one Patroni node, one etcd/HAProxy member, one AdGuard node, one Tailscale router. One host down is N-1 on every quorum at once, and every one of them tolerates that. Two hosts down at once is catastrophic: etcd, Patroni and K3s lose quorum together. So the playbook is **strictly serial** and does not advance until the host it just rebooted is back to 3 of 3 on everything.

Per host, the work is **discovered live**, never hard-coded: guests and HA state from `pvesh /cluster/resources`, K3s nodes from `kubectl`, the Patroni leader from the Patroni REST `/cluster`. A `PLAN` line prints what will be switched over, migrated and cordoned **before** anything changes.

## Where to run it (important)

From the **MacBook** (or any machine that is neither a Proxmox guest nor Frigg). An orchestrator cannot live on a host it reboots: **Semaphore** is a pod that reschedules when its worker's host reboots (killing the job), and **Frigg** restarts cold (about 6 seconds) when Verd reboots. The playbook needs no Vault lookups (switchover is Patroni REST, K3s is `kubectl`, migration is `ha-manager` over SSH), so the Mac's Ansible fork bug does not apply.

---

## Prerequisites (read-only checks; all must pass)

```bash
# Session setup done on the Mac; KUBECONFIG and the SSH key are loaded
kubectl get nodes                                               # 6 Ready
kubectl get --raw /readyz/etcd; echo                            # ok
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'     # one leader, two streaming replicas
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do curl -s http://$ip:2379/health; echo; done   # all healthy
for h in urd verd skuld; do ssh root@$h.niflheim.xiiisins.com 'pvecm status | grep Quorate'; done   # Quorate: Yes
ssh root@pbs.niflheim.xiiisins.com 'proxmox-backup-manager task list --limit 5'                  # NO backup/verify/GC task running
flux get kustomizations | grep -v True
```

Pick a time **outside the nightly backup window (01:00 UTC) and the PBS verify/GC jobs**: PBS lives on Urd, so Urd's reboot takes the backup server down regardless. Optional insurance before a large upgrade: a Vault Raft snapshot (**Backups & restore drills**, "Vault, by hand").

Also check nothing else is running Ansible: Semaphore project queue empty (`curl -s "$SEMAPHOREUI_API_BASE_URL/project/1/tasks/last" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq '.[0].status'`).

---

## Run it

```bash
cd ~/homelab/ansible

# 1) DRY RUN: gathers real state, prints the per-host PLAN, changes nothing
ansible-playbook playbooks/proxmox-host-patching.yml --check

# 2) CANARY: one host, full orchestration. Validate the real path on one host first.
ansible-playbook playbooks/proxmox-host-patching.yml --limit urd

# 3) FULL ROLLING PATCH: all three, serial, health-gated
ansible-playbook playbooks/proxmox-host-patching.yml
```

Overrides:

| Variable | Default | Effect |
|---|---|---|
| `pve_patch_reboot` | `true` | `false` = apt only; skips the drain/switchover **and** the reboot gate (tests the apt path; does not activate a new kernel) |
| `pve_patch_force_reboot` | `false` | Reboot even if apt changed nothing (activates a kernel staged by an earlier `reboot:false` run) |
| `pve_patch_drain` | `false` | Also `kubectl drain` (cordon-only by default: the reboot takes the pods anyway, and node-pinned `local-path` pods cannot reschedule) |
| `pve_patch_reboot_timeout` | `900` | Seconds to wait for SSH after the reboot |

Example: `ansible-playbook playbooks/proxmox-host-patching.yml --limit verd -e pve_patch_force_reboot=true`.

## What it does per host

```
PRE-FLIGHT (once)
  Play 1: ping all 3 PVE hosts           Play 2: assert the cluster is fully healthy
          (Patroni 3/3 + leader, etcd 3/3, all K3s nodes Ready). Refuses to start on a degraded cluster.

PER HOST (serial: 1)
  1 discover guests / HA / K3s nodes / Patroni leader     2 print the PLAN
  3 abort if this host holds the leader but no off-host candidate exists
  4 switch the Patroni leader off this host (patronictl)  5 ha-manager migrate HA guests (Frigg) away
  6 cordon this host's K3s nodes                           7 apt update && apt dist-upgrade
  8 reboot (if packages changed, or forced)
  -- RECONVERGENCE GATE (blocks the next host) --
  9 all expected guests running   10 pvecm quorate   11 Patroni 3/3 streaming + leader
 12 etcd 3/3 healthy             13 K3s node(s) Ready -> uncordon           14 next host
```

Frigg is **not** migrated back; it stays wherever HA placed it. LXCs and VMs that are not HA-managed are node-pinned and return through `start_on_boot`.

## Watch it

In a second terminal while it runs:

```bash
watch -n10 'kubectl get nodes; echo; ssh root@urd.niflheim.xiiisins.com "pvecm status | grep -E \"Quorate|Nodes\"" 2>/dev/null'
```

You should see exactly **one** node NotReady at a time. If two are down at once, stop and escalate.

## Afterwards (verify)

```bash
kubectl get nodes                                                     # 6 Ready, none SchedulingDisabled
for h in urd verd skuld; do ssh root@$h.niflheim.xiiisins.com 'uname -r; pvecm status | grep Quorate; pct list | grep -c running; qm list | grep -c running'; done
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'      # leader + 2 replicas, lag ~0
ha-manager status 2>/dev/null || ssh root@verd.niflheim.xiiisins.com 'ha-manager status'
flux get kustomizations | grep -v True; kubectl get externalsecrets -A | grep -v SecretSynced
curl -s https://smoketest.niflheim.xiiisins.com/anything
```

The next Semaphore drift check may report LXC-only drift after a fleet reboot (hardened sysctls, resolv.conf and similar are reset at container start); re-run the play for the affected hosts. That is expected and fixable, not a failure of the patch.

## If a gate fails

The play **halts** at the failing host and never advances, so a second host is never taken down. The cluster is left at most N-1, which is the tolerated state.

1. Read the failing task: it names which quorum did not reconverge.
2. Check that quorum directly (`patronictl list`, the etcd `/health` curls above, `kubectl get nodes`).
3. Uncordon the host's K3s node if it is healthy but still cordoned: `kubectl uncordon <node>`.
4. Fix the cause, confirm full health, then **re-run** (idempotent: patched hosts no-op the apt step). `--limit <next-host>` resumes where it stopped.

If a host does not come back within the timeout: suspect the NIC-rename class (`/etc/network/interfaces` referencing a renamed interface), which needs the **physical console** (`ip -br link`, fix the file, `ifreload -a`). Skuld has a known hard-freeze fault; if it hangs rather than reboots, power-cycle it with its power button (a person at the machine) and continue from the gate.

---

## See also

- **Hypervisors** (Hardware): the nodes and Skuld's fault.
- **Postgres operations** and **Everyday operations** (this section).
- **Hosts & Proxmox** (Troubleshooting).
