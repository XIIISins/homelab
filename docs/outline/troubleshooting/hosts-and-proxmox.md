<!-- docs/outline/troubleshooting/hosts-and-proxmox.md -->

# Hosts & Proxmox

Symptoms with the three hypervisors (Urd `10.0.254.11`, Verd `.12`, Skuld `.13`), their guests, the NAS (Munin `10.0.254.20`) and out-of-band access. **One host down is a tolerated state** (each host carries exactly one member of every quorum); **two hosts down at once is an emergency: escalate.**

```bash
PVE() { ssh -o ConnectTimeout=5 "root@$1.niflheim.xiiisins.com" "${@:2}"; }      # PVE urd|verd|skuld '<command>'
```

---

## 1. First diagnostics

```bash
for h in urd verd skuld; do
  echo "=== $h"
  ping -c1 -W2 $(dig +short $h.niflheim.xiiisins.com) >/dev/null 2>&1 && echo "pings" || echo "NO PING"
  PVE $h 'uptime; pvecm status | grep -E "Quorate|Nodes:"; echo "LXC running: $(pct list | grep -c running)  VM running: $(qm list | grep -c running)"' 2>&1 | tail -4
done
PVE urd 'ha-manager status; pvesm status'                                  # HA resources (Frigg), storage free space
PVE urd 'pvesh get /cluster/resources --type vm --output-format json' | jq -r '.[]|select(.status!="running")|"\(.vmid) \(.name) \(.node) \(.status)"'   # every guest that is not running
```

A host answering neither ping nor SSH, with its guests missing from Zabbix/Kubernetes, is down or frozen: section 2.

---

## 2. A host is unreachable or hard-frozen (Skuld's known fault)

**Symptoms:** ping and SSH time out; its K3s node (Sigrún/Einherjar-skuld) NotReady; its Patroni/etcd/AdGuard/Tailscale members gone; Zabbix "host unreachable". Skuld has an unresolved hardware fault that freezes the whole system with no panic and no log; this is a known recurring event, not a surprise.

**Step 1: confirm quorums are holding (they should be).**

```bash
kubectl get nodes | grep -v " Ready"                                           # expect sigrun + einherjar-skuld only
kubectl get --raw /readyz/etcd; echo                                           # ok (2 of 3)
ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@10.0.11.231 'sudo patronictl -c /etc/patroni/patroni.yml list'   # a leader exists; Fulla (Skuld) is missing
kubectl -n vault exec vault-0 -c vault -- vault status | grep -E "Sealed|HA Mode"         # 2 of 3 voters
dig +short @10.0.10.200 pbs.niflheim.xiiisins.com                              # DNS VIP moved to Saga/Mimir
PVE urd 'pvecm status | grep -E "Quorate|Nodes:"'                              # Quorate: Yes with 2 nodes
```

If all of those hold, services are fine on N-1 and you can recover the host at your pace. If **any** quorum is lost, or another member is also down: **escalate now**.

**Step 2: restart the host.** A frozen host does not recover by itself unless the hardware watchdog catches it (see Step 3); someone at the machine holds the power button for a hard power-off, then powers it on. Do not rebuild the VMs: the host is the problem.

**Step 3: after it boots, check what came back (do not touch anything until it settles, about 5 minutes).**

```bash
PVE skuld 'uptime; pvecm status | grep -E "Quorate|Nodes:"; journalctl --list-boots | tail -3'
PVE skuld 'journalctl -b -1 -p err --no-pager | tail -40'                       # the crashed boot: usually nothing (a hard freeze logs nothing)
PVE skuld 'pct list; qm list'                                                  # guests with start_on_boot return on their own
PVE skuld 'cat /sys/class/watchdog/watchdog0/identity'                          # iTCO_wdt = hardware watchdog active; "Software Watchdog" = cannot catch a hard freeze
kubectl get nodes                                                              # sigrun + einherjar-skuld Ready again
ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@10.0.11.231 'sudo patronictl -c /etc/patroni/patroni.yml list'    # Fulla back as a streaming replica (pg_rewind runs itself)
kubectl -n vault get pods -o wide
for i in 0 1 2; do printf 'vault-%s ' $i; kubectl -n vault exec vault-$i -c vault -- vault status 2>/dev/null | grep -E "Sealed|HA Mode" | tr -s ' ' | tr '\n' ' '; echo; done     # 3 unsealed, one active (needs no token)
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do curl -s -m 3 http://$ip:2379/health; echo; done
```

The Raft voter list (`list-peers`) needs the root token, so it is an operator step: **Vault recovery** §1. Then clean up: `kubectl get pods -A | grep -E "Evicted|Error|ContainerStatusUnknown"` (delete them), check `df -h` on the control plane that kept logging (see **Kubernetes & Flux** §2), and re-run the Semaphore drift check; LXC-only drift after a reboot is expected (§6).

**If a guest did not start:** `PVE skuld 'pct start <id>'` and read §4. If the host will not boot at all, escalate: hardware.

**Log it.** A freeze is a recorded event type; note the time in the incident notes (`docs/incidents/`), because the pattern is what will justify hardware replacement.

## 3. HA and the control node (Frigg)

Frigg (VM 2900) is the only HA-managed guest and restarts on another host if its host dies.

```bash
PVE verd 'ha-manager status'                        # vm:2900 started on <node>
PVE verd 'qm status 2900'
```

- `qm stop 2900` is a **graceful HA stop**: HA then keeps it stopped. To bring it back: `ha-manager set vm:2900 --state started`.
- To test HA crash recovery for real, kill the QEMU process (`kill -9 $(cat /var/run/qemu-server/2900.pid)`), not `qm stop`.
- Never restart `watchdog-mux` while HA is attached: it trips the watchdog and resets the node.

## 4. A guest will not start

```bash
PVE <host> 'pct status <ctid>; pct config <ctid> | head -20'
PVE <host> 'pct start <ctid> 2>&1 | tail -5'                   # for VMs: qm start <vmid>
PVE <host> 'journalctl -u pve-container@<ctid> -n 40 --no-pager'
PVE <host> 'pvesm status; lvs | head -20'                       # storage full? thin pool at 100%?
```

| Message | Cause | Fix |
|---|---|---|
| `lvcreate … already exists` when cloning/creating | Orphan LVs from a host that died mid-clone | `qm list \| grep <id>` (no config) and `lvs \| grep vm-<id>`; `lvremove -f /dev/pve/vm-<id>-cloudinit /dev/pve/vm-<id>-disk-0` (or `qm destroy <id> --purge` if a phantom config exists) |
| `thin pool … is full` / write failures | The LVM-thin pool is full | Free space: delete scratch guests, old snapshots (`qm listsnapshot`, `qm delsnapshot`); do not resize blind. Escalate if production disks are involved |
| `startup for container failed` on a passthrough LXC (Jellyfin/Tailscale) | The device or ticket-auth config | `pct config <id> \| grep dev`; `ls -l /dev/dri /dev/net/tun` on the host |
| Container starts but services are down | Provision-time config was reset at container start | §6 |
| Cloning/restoring onto a VMID that exists | The ID is taken | `qm list; pct list`; never reuse; the reserved scratch ID is `1199` |

## 5. Sizes, memory and disks

```bash
# Real memory use of an LXC = anon + shmem (page cache fills the cap and is reclaimable)
PVE <host> 'awk "/^(anon|shmem|file) /{print \$1, int(\$2/1048576)\"MB\"}" /sys/fs/cgroup/lxc/<ctid>/memory.stat; cat /sys/fs/cgroup/lxc/<ctid>/memory.peak'
# Grow an LXC disk (online, no restart). Prefer changing disk.size in Terraform; this is the same operation by hand.
PVE <host> 'pct resize <ctid> rootfs +10G'
# A VM disk grow needs a guest-side step too
ssh … ansible@<vm-ip> 'sudo growpart /dev/sda 4 && sudo xfs_growfs /'        # RHEL cloud image: sda4 is the XFS root
```

A pending VM disk flag (`ssd=1`) is only applied by a Proxmox-level `qm reboot <vmid>`, not a guest reboot. **Quorum-critical VMs (K3s control planes) are changed and rebooted one at a time**, gated on 6/6 Ready and `etcd ok`. Postgres LXCs: do not cut their memory caps on the anon number; the cache is their working set.

## 6. After a reboot, an LXC's config drifted (drift-check reports LXC-only changes)

Container start re-initialises its network namespace and file management, so provision-time settings may be reset: hardened `net.ipv4.conf.*` sysctls (`accept_redirects`, `log_martians`), policy-routing units that lost the boot race (`vlan10-policy-routing.service` failed, `ip rule` empty), `/etc/resolv.conf` (Proxmox rewrites it from the container's `dns {}` block; owned by Terraform), `/etc/default/locale`.

```bash
ssh … ansible@<host> 'sysctl net.ipv4.conf.all.accept_redirects; systemctl --failed; ip rule; cat /etc/resolv.conf'
ssh … ansible@<host> 'sudo systemctl restart niflheim-sysctl-reapply vlan10-policy-routing 2>&1'
cd ~/homelab/ansible && ansible-playbook -i inventory/hosts.yml playbooks/asgard-<group>.yml --limit <host>      # re-converge
```

`resolv.conf` is not Ansible-managed (Proxmox owns it): a `dns {}` change in Terraform takes effect on the **next container start**.

## 7. Cluster quorum and Terraform traps

- **Proxmox quorum lost** (`Quorate: No`): do **not** run `pvecm expected` on your own; it overrides safety. Escalate.
- **Moving a guest between nodes:** `pct migrate <id> <target> --restart` plus a Terraform state patch (`node_name` is create-only, so editing it in HCL plans a destroy and recreate). Never destroy and recreate a guest with downstream identity (a joined K3s node, a Patroni member, a Tailscale machine). Escalate before attempting.
- **A `terraform plan` that says "must be replaced … `+ pool_id` # forces replacement" on guests you did not touch:** stop. Put existing guests into a pool via `pvesh set /pools/<pool> --vms <ids>` and list `pool_id` in `ignore_changes`; do not apply.
- **`terraform import` of an LXC proposing destroy-recreate:** the module needs `lifecycle.ignore_changes` for `operating_system[0].template_file_id` and `initialization[0].user_account`.
- `terraform apply` can power-cycle a VM (the provider's `reboot_after_update`); worker VMs are set not to. Never apply an untargeted change across all three control planes at once.

## 8. Munin (Synology NAS)

```bash
ping -c2 10.0.254.20; ssh admin@10.0.254.20 'uptime; df -h | grep volume; sudo synosystemctl status nfsd 2>&1 | head -3'
showmount -e 10.0.254.20 | head
```

- **Everything NFS/iSCSI stalls and Munin is unreachable:** it is the storage single point of failure for NFS apps, iSCSI Garage metadata and the PBS datastore. K3s control plane, Postgres and Vault keep running. Power-cycle only if it is truly hung (the DS223J is headless; the button is the only console). After boot, apps recover when mounts return; check `kubectl get pods -A | grep -v Running`.
- **Munin's own LAN vanished but it shows online in the tailnet:** Tailscale applied bad flags. Reach it on its Tailscale IP and `sudo tailscale down` (do **not** power-cycle). See **DNS & networking** §8.
- **A Synology volume will not delete (`Device or resource busy`):** invisible holders (LUNs, DSM packages, `s2s_daemon`, nfsd locks, indexing marks). Work the chain; if all visible checks are clean and it is still busy, **reboot the NAS**. This is a planned change: escalate.
- **A moved shared folder breaks NFS clients:** the export path changed with the volume. **Storage & iSCSI** §10.

## 9. PBS

```bash
PVE urd 'pct status 1101; pct exec 1101 -- systemctl is-active proxmox-backup proxmox-backup-proxy; pct exec 1101 -- df -h | grep -iE "nfs|proxmox"'
```

PBS lives on **Urd** (never on Skuld). If its datastore NFS mount is missing, mount it inside the container and `systemctl restart proxmox-backup proxmox-backup-proxy`; then restore-verify. For failed or missing backups see **Monitoring & automation**.

---

## See also

- **Hypervisors** (Hardware) and **Compute & hypervisors** (Components).
- **Proxmox host patching**, **K3s node rebuild**, **Backups & restore drills** (Procedures).
- **Kubernetes & Flux** (this section): when a node is NotReady.
