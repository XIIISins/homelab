<!-- docs/outline/components-and-interactions/compute-and-hypervisors.md -->

# Compute & hypervisors

This subpage covers the software stack that runs on the physical nodes — Proxmox, the K3s clusters, and how compute is partitioned between VMs and LXCs. The physical boxes themselves (CPUs, drives, networking gear) live in the **Hardware** section.

---

## Proxmox cluster — `niflheim`

Three Proxmox VE 9.x (Debian 13 Trixie) nodes, clustered as `niflheim`. No-subscription repo on all three. A single VLAN-aware Linux bridge (`vmbr0`) carries every VLAN tag to every VM and LXC.

- **Cluster quorum** comes from the three nodes themselves — there's no external corosync witness. Two nodes are enough to maintain quorum, which means the cluster survives a single-node loss but not a network partition that isolates one node.
- **VM and LXC disks live on local LVM-thin.** NFS-backed VM disks were ruled out — fsync latency over 1 GbE is the wrong shape for write-heavy workloads like Postgres or etcd.
- **Proxmox root LV** is sized at 20 GB, leaving the rest of each node's NVMe for LVM-thin VM/LXC storage. See the Hardware section for per-node disk sizes.
- **HA is not enabled.** Workloads that need automatic failover do it at the application layer (Patroni for Postgres, K3s for pods). Proxmox-level HA would add a corosync ring constraint without buying anything for the workloads that need it.

---

## VMs vs LXCs

Two choices for hosting a workload. The decision is deliberate, not historical.

**VM** when the workload needs:

- Its own kernel — K3s nodes are VMs because Kubernetes interacts with kernel facilities (cgroups v2, eBPF, network namespaces) in ways that get awkward on a shared kernel.
- An isolated network stack — some networking experiments don't compose well inside an LXC's shared netns.
- Strong isolation from the host — running something untrusted, or something that needs to look like a normal Linux box to its software.

**LXC** when the workload is:

- A long-running service that runs fine on a shared kernel (databases, DNS resolvers, reverse proxies, file servers).
- Memory- or boot-time-sensitive — LXCs start in seconds and add no kernel overhead.
- A natural fit for "one process tree, one network IP, one service" without the ceremony of a full VM.

All LXCs are **unprivileged with `nesting=true`**. Unprivileged is the security default; nesting is required by systemd 257 on Debian 13 because modern systemd uses namespace operations that need `CAP_SYS_ADMIN` inside the user namespace. Nesting does *not* turn the container into a privileged container — it only relaxes the systemd-specific syscall constraints.

A small number of LXCs require root-level Proxmox API access at create time (currently just Tailscale, which needs `device_passthrough` for `/dev/net/tun`). Those live in a separate Terraform module (`terraform/proxmox/asgard-lxcs-root/`) authenticated with `root@pam` ticket auth, isolating the credential blast radius.

---

## K3s clusters

Two K3s clusters, both production. Separation is failure-domain risk management, not a maturity ladder.

### asgard — production-stable

- **Three control planes** (Göndul / Hlökk / Sigrún), one per Proxmox node. 2 vCPU / 4 GB / 10 GB disk each. Tainted `node-role.kubernetes.io/control-plane:NoSchedule` so workload pods can't land on them.
- **Three workers** (Einherjar-urd / verd / skuld), one per Proxmox node. 2 vCPU / 8 GB / 15 GB disk each. Multi-homed: `eth0` on VLAN 21 (cluster-internal), `eth1` on VLAN 20 (MetalLB L2 announcement).
- **RHEL 9** on every node (Red Hat developer subscription). SELinux enforcing.
- **CNI:** Calico, installed via a K3s addon manifest (not via Flux — bootstrapping a CNI through GitOps is a chicken-and-egg problem).
- **What runs here:** identity (Authentik), secrets (Vault), DNS-fronting (Traefik, Cloudflared), the in-cluster observability stack, the operator's own tooling (NetBox, Semaphore, Outline), and the production workloads that families rely on.

### jotunheim — production-experimental (planned)

- Mirrors asgard's topology: three CPs (Rota / Hildr / Kára), three workers (Drengr-urd/verd/skuld), same hardware split across the three Proxmox nodes.
- **What will run here:** services where breakage is acceptable, experimental workloads, anything that exercises new patterns before promoting them to asgard.

The split criterion: *failure-domain risk*. If a workload could plausibly take down the cluster it lives in (resource hog, runaway controller, novel CNI feature), it belongs in jotunheim. If it must stay up for the household to function, it belongs in asgard.

---

## Resource ID scheme

Proxmox VM/LXC IDs are partitioned by purpose so that the role of any resource is recognisable from the ID alone.

| Range | Purpose |
|---|---|
| 1101–1199 | Asgard LXCs |
| 2001–2999 | Asgard K3s VMs |
| 3001–3999 | Jotunheim K3s VMs |
| 10001+ | Templates |

The LXC subranges are further structured by service class:

| Subrange | Class | Examples |
|---|---|---|
| 1101–1109 | Backup + monitoring | PBS, Zabbix (Hugin), Hermod |
| 1110–1119 | Network | AdGuard trio, Tailscale trio |
| 1120–1129 | Application services | Factorio |
| 1130–1139 | Databases + HAProxy | Postgres trio, HAProxy/etcd trio |

Resource IDs and last IP octets are loosely correlated for at-a-glance recognition — LXC 1130 (Fulla) is `10.0.11.230`, LXC 1133 (Hlin) is `10.0.11.233`, LXC 1120 (Factorio) is `10.0.11.220`. The correlation is a convention for fast lookup, not a strict rule; the lower 1101–1109 subrange and a few edge cases deviate, and that's fine.

---

## VM specs

### K3s control planes

- **2 vCPU / 4 GB RAM / 10 GB disk.** Sized identically across all three so failover is symmetric. CP-only posture (NoSchedule taint) makes 4 GB the right ceiling — there's no workload pressure on these.
- **Single NIC** on VLAN 21. Control planes don't participate in MetalLB and don't need an `eth1`.
- **K3s server config:** disables Traefik, servicelb, local-storage (we install our own); sets `flannel-backend: none` + `disable-network-policy: true` to make room for Calico; pod CIDR `10.42.0.0/16`, service CIDR `10.43.0.0/16`.

### K3s workers

- **2 vCPU / 8 GB RAM / 15 GB disk.** Memory bumped from the original 4 GB after cumulative workload pressure during the NetBox + Valkey rolling-restart cascade in May 2026.
- **Multi-homed:** `eth0` on VLAN 21 carries cluster-internal traffic; `eth1` on VLAN 20 is where MetalLB L2 announces service VIPs.
- **Multi-homing requires four landmine fixes** in the K3s Ansible role — Calico autodetection pinned to the right CIDR, loose `rp_filter`, `route_localnet=1`, and source-based policy routing for VLAN 20. The **Network** subpage covers these in detail.

---

## LXC specs

- **Debian 13 (Trixie) template.** Same OS across the fleet — single set of Ansible roles, single patching cadence.
- **Unprivileged + `nesting=true`** as the default. Other features (`keyctl`, `fuse`, `device_passthrough`) are added per-LXC when a workload genuinely needs them.
- **Typical sizing** is 1–2 vCPU and 2–4 GB RAM. Heavier LXCs (Postgres, HAProxy with VRRP) get more.
- **Single NIC on VLAN 11** for normal services. Dual-NIC for LXCs that carry a keepalived VRRP VIP — the VIP's L2 segment differs from the host's default route, so a second interface on the VIP VLAN is required for VRRP advertisement source + symmetric reply routing.

---

## Bootstrap flow

The "VM/LXC creation" flow from the parent page, in detail:

1. **Operator declares** the LXC or VM in the appropriate Terraform module (`terraform/proxmox/asgard-lxcs/`, `terraform/proxmox/asgard-lxcs-root/`, or `terraform/proxmox/asgard-k3s/`) and runs `terraform apply`. Terraform calls the Proxmox API (via the `bpg/proxmox` provider) to clone the template, seed cloud-init, and assign the static IP.
2. **Operator declares** the matching NetBox record in `terraform/netbox/vms.tf` (or `devices.tf` for physical) and runs `terraform apply`. NetBox stays the IPAM/DCIM truth — every LXC and VM has a NetBox row.
3. **Operator runs** the appropriate Ansible playbook to bootstrap the host. Day 1 the run targets `ansible_user=root` for initial baseline + hardening; the hardening role locks root SSH out at the end. Day N runs as the `ansible` user.
4. **Inventory refresh.** Semaphore re-pulls the NetBox dynamic inventory every four hours; on the next refresh the new host is included in drift-check automatically.

A break-glass `recovery` user (key in 1Password) exists on every hardened host. It exists so that a host can be rescued without rebuilding from scratch when normal SSH access breaks.

---

## See also

- **Hardware** section — physical nodes, NVMe tier comparison, network gear.
- **Network** (this section) — VLAN table, multi-homed worker landmine fixes, source-based routing.
- **GitOps & automation** (this section) — how Terraform, Ansible, and Flux layer over each other; Semaphore template structure.
