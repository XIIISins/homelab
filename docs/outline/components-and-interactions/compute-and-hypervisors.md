<!-- docs/outline/components-and-interactions/compute-and-hypervisors.md -->

# Compute & hypervisors

Everything runs on the three-node Proxmox cluster `niflheim`. This page covers what kinds of guests exist, why a workload is a VM, an LXC or a Kubernetes pod, how guests are numbered and sized, and how a new one goes from nothing to converged.

---

## One cluster, one K3s

The Proxmox cluster hosts a single production Kubernetes cluster, **asgard**, plus a set of LXCs and a few standalone VMs. K3s is the only orchestrator (no Swarm, no plain k8s). A second cluster, Jotunheim, was designed and then dropped for lack of RAM: six more VMs would not fit in the 6–8 GB of headroom left on each node. Its VLANs (30/31), VMIDs (3001–3999) and node names stay reserved, but nothing is built. Throwaway Kubernetes experiments go on an **ephemeral DigitalOcean burst cluster**, not asgard: asgard is built carefully and is not a sandbox.

---

## VM, LXC or pod?

| Choose | When | Examples |
|---|---|---|
| **K3s pod** | The default for application workloads: GitOps-managed, restartable, no host-level needs. | Authentik, Vault, NetBox, Outline, Immich, Teamspeak, Semaphore, Sonarr |
| **LXC** | The service needs host-level access, a failure domain independent of Kubernetes, or a simple long-lived daemon. Cheaper than a VM, shares the host kernel. | DNS, Postgres, HAProxy, Zabbix, PBS, Jellyfin, Factorio, AIOps hosts |
| **VM** | A separate kernel is needed: Kubernetes nodes and the control node. | The six K3s nodes, Frigg |

The reasons services sit outside Kubernetes are concrete, not stylistic:

- **Failure-domain independence.** DNS, Postgres, Zabbix and PBS must keep working when K3s is broken, because K3s depends on them (or is watched by them).
- **Hardware access.** Jellyfin needs the Intel iGPU (`/dev/dri`) for QuickSync, so it is a privileged LXC on Urd.
- **Operator model.** Factorio is a game server whose operator gets SFTP only; a root-owned timer converges state from JSON control files. It is the template for operator-managed services.
- **Backup behaviour.** PBS is a privileged LXC on Urd (never on Skuld, which freezes), with the Munin NFS datastore mounted inside the container.

---

## The guests

### Kubernetes VMs

| Role | Names | Count | Spec |
|---|---|---|---|
| Control plane | Göndul (Urd), Hlökk (Verd), Sigrún (Skuld) | 3 | 2 vCPU / 4 GB / 20 GB, VLAN 21, tainted `NoSchedule` |
| Worker | Einherjar-urd / -verd / -skuld | 3 | 2 vCPU / 16 GB / 30 GB OS disk + 50 GB `/data` (xfs); eth0 VLAN 21, eth1 VLAN 20 |

The three control planes are identical by rule, so failover is symmetric. They are tainted so only the workers run workloads. The workers are multi-homed because MetalLB announces service IPs on a dedicated VLAN (see **Network**). Authoritative specs live in `locals.control_planes` and `locals.workers` in `terraform/proxmox/asgard-k3s/main.tf`.

![Asgard K3s: the one production cluster](/api/attachments.redirect?id=a4973a96-6a88-43ac-9557-120ff03f1ee7)

### LXCs

One row per function; the full table with IPs is in the services docs.

| VMIDs | Function | Guests |
|---|---|---|
| 1101–1109 | Backup, monitoring, notifications | PBS (1101), Zabbix/Hugin (1102), Hermod (1103) |
| 1110–1119 | Network infrastructure | AdGuard Saga/Mimir/Kvasir (1110–1112), Tailscale Bifrost/Heimdall/Gjallarbru (1113–1115) |
| 1120–1129 | Services | Factorio (1120), Gná (1121), Ratatoskr (1122), Jellyfin (1123) |
| 1130–1139 | Database and routing | Postgres Fulla/Vör/Idunn (1130–1132), HAProxy+etcd Hlin/Eir/Snotra (1133–1135) |
| 1190–1199 | AIOps canary pool | Three disposable canaries (1190–1192), Urd only |

Trios are spread one per node, so a single node loss costs one member of each trio. Singleton roles avoid Skuld.

![Asgard LXCs: what runs outside K3s, and where](/api/attachments.redirect?id=ead5cdd6-34d9-4db3-84e7-f895e9e55dff)

### Standalone VMs

**Frigg** (VMID 2900) is the control node: it runs the full Terraform, Ansible and Flux cycle with its own Vault AppRole, hosts the AIOps Toolbelt API and the remote-control session. It is Proxmox HA-managed.

---

## ID scheme

| Range | Meaning |
|---|---|
| 1101–1199 | Asgard LXCs, grouped by function as above |
| 2001–2103 | K3s VMs (control planes 2001–2003, workers 2101–2103) |
| 2900–2999 | Other asgard VMs (Frigg = 2900) |
| 3001–3999 | Jotunheim (reserved, unused) |
| 9900–9999 | DigitalOcean offsite nodes (a NetBox cross-reference only, not Proxmox guests; `do1` = 9900) |
| 10001+ | Templates |

IDs are stable identities: NetBox, Ansible inventory and Terraform state all key off them.

---

## How a guest comes to exist

Terraform creates it, Ansible converges it, and NetBox records it. Day 1 and Day N differ only in who can log in.

1. **Terraform** appends the guest to the right module's `lxcs.tf` (or the K3s `locals`) and applies. There are two LXC modules: `asgard-lxcs` uses an API token for normal LXCs; `asgard-lxcs-root` uses root ticket authentication and exists only for LXCs whose creation needs it (device passthrough such as `/dev/net/tun` for Tailscale or `/dev/dri` for Jellyfin). Applies run only from the main checkout.
2. **NetBox** gets matching `netbox_virtual_machine`, interface and IP declarations in the same change. NetBox is the queryable view of what Git specifies, and also feeds the Ansible dynamic inventory.
3. **Ansible, Day 1**: run the baseline as `root` (`--tags baseline`). The baseline creates the `ansible` user and keys; the hardening role then locks root SSH.
4. **Ansible, Day N**: run the full play as `ansible`. Break-glass access is the separate `recovery` user.
5. **Semaphore** re-runs the plays in `--check --diff` mode every six hours and alerts on drift.

### K3s on the VMs

The `k3s` Ansible role installs everything: prerequisites, network plumbing (the multi-homed-worker fixes), the first control plane with `--cluster-init`, then the other control planes, then workers. Bumping the version in role defaults does nothing on a healthy cluster by design; minor upgrades go through a dedicated one-minor-per-run playbook. Calico is installed as K3s addon files by Ansible, not by Flux, which is how the CNI avoids the chicken-and-egg problem and why its upgrades are likewise a playbook. Everything inside the cluster after that is Flux.

---

## Failure surfaces

| What fails | What happens |
|---|---|
| One Proxmox node | One control plane, one worker and one member of each LXC trio go away. etcd keeps quorum (2 of 3), Patroni elects a new leader, the AdGuard VIP moves, Vault runs 2 of 3 voters. Guests are restored from PBS or migrated back once the node returns. |
| Skuld freezes | The same, plus the rule that singletons are not placed there, so nothing irreplaceable goes with it. |
| A worker VM is lost | Its `local-path` volumes are unreachable until it is rebuilt from the template and restored. Vault runs on 2 of 3 voters meanwhile, by design. |
| Terraform state drift | Plans are reviewed before apply. Moving an LXC between nodes uses `pct migrate` plus a state patch, never destroy and recreate. |

---

## See also

- **Hypervisors** (Hardware): the physical nodes behind this.
- **Network** (this section): VLANs, the multi-homed workers and the VIPs.
- **Storage & data** (this section): where guest and pod data lands.
- **GitOps & automation** (this section): the Terraform, Ansible and Flux ownership boundary.
- **Procedures**: K3s node rebuild, whole-cluster rebuild, Proxmox host patching.
- **Asgard K3s diagram** (Diagrams): the VMs, the Flux chain and the platform.
- **Asgard LXCs diagram** (Diagrams): which LXC runs on which host.
