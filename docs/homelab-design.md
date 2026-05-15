# Homelab design document
*Last updated: 2026-05-14 — draft v5*

---

## Purpose

A ground-up homelab rebuild demonstrating senior-level infrastructure design.

### Goals
- Provide reliable services to friends and family (Teamspeak, Factorio, Immich)
- Learn and demonstrate modern infrastructure concepts (Kubernetes, GitOps, IaC, network segmentation)
- Portfolio/resume project at senior/principal infrastructure level

### Principles
- Everything defined as code before it exists in production
- Complexity only where it serves a purpose
- Self-healing at every layer
- Full audit trail — every change traceable to a human or automated system

---

## Hardware

| Device | CPU | RAM | Role | Norse name |
|--------|-----|-----|------|------------|
| MINISFORUM DeskMini JB95 | Celeron N5095 | 32 GB | Proxmox node 1 | **Urd** |
| Beelink MINI-S12 | N100 | 16 GB, 1TB disk | Proxmox node 2 | **Verd** |
| Beelink MINI-S12 | N100 | 16 GB, ~450GB LVM-thin | Proxmox node 3 | **Skuld** |
| Synology DS223J | Realtek RTD1619B | 1 GB | NAS | **Munin** |

**Hardware notes:**
- Urd (N5095) is too weak for K3s control plane. etcd IO storms observed under load. CPs should run on Verd/Skuld only.
- Urd long-term: dedicated Jellyfin LXC with Intel QuickSync passthrough. Currently also running Einherjar-urd (K3s worker) and temporarily Göndul (CP — to move to Verd).
- All nodes 1 GbE. No 2.5 GbE planned.

### Naming convention
- **Proxmox cluster:** `niflheim`
- **Hypervisor nodes:** Urd (eldest/primary, 32GB), Verd, Skuld — the three Norns
- **NAS:** Munin — Odin's raven of memory
- **DNS zones:** `midgard.xiiisins.com` (public) + `niflheim.xiiisins.com` (internal)
- **Must-run K3s CP:** Göndul, Hlökk, Sigrún (Valkyries)
- **Must-run K3s workers:** Einherjar-urd/verd/skuld (army of the Norns)
- **Can-run K3s CP:** Rota, Hildr, Kára (Valkyries)
- **Can-run K3s workers:** Drengr-urd/verd/skuld (heroes of the Norns)
- **AdGuard Home:** Saga (primary), Mimir (replica), Kvasir (replica)

### Network hardware

| Device | Purpose |
|--------|---------|
| UniFi Cloud Gateway Ultra (UCG-Ultra) | Router, firewall, VLAN routing, zone-based firewall |
| Tailscale on Synology (Docker) | OOB access — survives complete homelab failure |
| Existing dumb switches (×2) | Pass VLAN tags transparently |

### Physical topology

```
Internet
  └── KPN Experia Box (192.168.2.0/24) — untouched
        ├── Settop box, family devices — unchanged
        └── UCG-Ultra WAN
              ├── LAN 1 → Dumb switch (your room)
              │             ├── MacBook dock (VLAN 60, static 10.0.60.10)
              │             ├── Game PC (VLAN 60)
              │             └── Hue bridge (VLAN 60)
              └── LAN 2 → Dumb switch (spare bedroom)
                            ├── Urd   (10.0.254.11)
                            ├── Verd  (10.0.254.12)
                            ├── Skuld (10.0.254.13)
                            └── Munin (10.0.254.20)
```

---

## Network design

> **MGMT subnet is `10.0.254.0/24`.** Drafts up to v4 of this document incorrectly recorded it as `10.0.1.0/24`. The live network is and always was `10.0.254.0/24`. This error nearly caused a *correct* iSCSI portal address to be "fixed" during the 2026-05-14 incident — corrected throughout in v5.

### VLAN table

| VLAN | Subnet | UCG name | Purpose |
|------|--------|----------|---------|
| 1 | `10.0.254.0/24` | HL-MGMT | Management — nodes, NAS, UCG-Ultra |
| 10 | `10.0.10.0/24` | HL-CORE-VIP | Must-run VIPs (keepalived) |
| 11 | `10.0.11.0/24` | HL-CORE-SVC | Must-run LXCs |
| 20 | `10.0.20.0/24` | HL-CORE-K3S-VIP | Must-run K3s MetalLB pool |
| 21 | `10.0.21.0/24` | HL-CORE-K3S-WRK | Must-run K3s nodes |
| 30 | `10.0.30.0/24` | HL-CR-K3S-VIP | Can-run K3s MetalLB pool |
| 31 | `10.0.31.0/24` | HL-CR-K3S-WRK | Can-run K3s nodes |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage / NFS — stable |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

### IP assignments

**Management VLAN 1 (10.0.254.0/24):**

| Address | Role |
|---------|------|
| `10.0.254.1` | UCG-Ultra (gateway) |
| `10.0.254.11` | Urd |
| `10.0.254.12` | Verd |
| `10.0.254.13` | Skuld |
| `10.0.254.20` | Munin (Synology) |

**Must-run VIP VLAN 10 (10.0.10.0/24):**

| Address | Role |
|---------|------|
| `10.0.10.200` | AdGuard Home VIP (keepalived) |
| `10.0.10.201` | AdGuard eth1 — Saga (Urd) |
| `10.0.10.202` | AdGuard eth1 — Mimir (Verd) |
| `10.0.10.203` | AdGuard eth1 — Kvasir (Skuld) |
| `10.0.10.210` | HAProxy VIP — PostgreSQL frontend |

**Must-run LXC VLAN 11 (10.0.11.0/24):**

| Address | LXC ID | Node | Role |
|---------|--------|------|------|
| `10.0.11.20` | 1101 | Skuld | PBS ✅ |
| `10.0.11.21` | 1102 | Skuld | Zabbix |
| `10.0.11.201` | 1110 | Urd | AdGuard Home — Saga ✅ |
| `10.0.11.202` | 1111 | Verd | AdGuard Home — Mimir ✅ |
| `10.0.11.203` | 1112 | Skuld | AdGuard Home — Kvasir ✅ |
| `10.0.11.213` | 1113 | Urd | Tailscale 1 |
| `10.0.11.214` | 1114 | Verd | Tailscale 2 |
| `10.0.11.215` | 1115 | Skuld | Tailscale 3 |
| `10.0.11.220` | 1120 | Urd | Factorio + SFTPGo |
| `10.0.11.221` | 1121 | Verd | Teamspeak |
| `10.0.11.230` | 1130 | Urd | PostgreSQL 1 |
| `10.0.11.231` | 1131 | Verd | PostgreSQL 2 |
| `10.0.11.232` | 1132 | Skuld | PostgreSQL 3 |
| `10.0.11.233` | 1133 | Urd | HAProxy 1 |
| `10.0.11.234` | 1134 | Verd | HAProxy 2 |
| `10.0.11.235` | 1135 | Skuld | HAProxy 3 |

**Must-run K3s MetalLB VLAN 20 (10.0.20.0/24):**

| Address | Role |
|---------|------|
| `10.0.20.11–.99` | LoadBalancer pool (MetalLB) |
| `10.0.20.201` | Einherjar-urd eth1 |
| `10.0.20.202` | Einherjar-verd eth1 |
| `10.0.20.203` | Einherjar-skuld eth1 |

**Must-run K3s nodes VLAN 21 (10.0.21.0/24):**

| Address | VM ID | Node | Role | Name |
|---------|-------|------|------|------|
| `10.0.21.11` | 2001 | Urd* | K3s CP | Göndul (*move to Verd) |
| `10.0.21.12` | 2002 | Verd | K3s CP | Hlökk |
| `10.0.21.13` | 2003 | Skuld | K3s CP | Sigrún |
| `10.0.21.21` | 2101 | Urd | K3s Worker | Einherjar-urd |
| `10.0.21.22` | 2102 | Verd | K3s Worker | Einherjar-verd |
| `10.0.21.23` | 2103 | Skuld | K3s Worker | Einherjar-skuld |

*Göndul to move from Urd → Verd on next full reprovision.

**Can-run K3s MetalLB VLAN 30 (10.0.30.0/24):**
`10.0.30.11–.99` — LoadBalancer pool

**Can-run K3s nodes VLAN 31 (10.0.31.0/24):**

| Address | VM ID | Node | Role | Name |
|---------|-------|------|------|------|
| `10.0.31.11` | 3001 | Urd | K3s CP | Rota |
| `10.0.31.12` | 3002 | Verd | K3s CP | Hildr |
| `10.0.31.13` | 3003 | Skuld | K3s CP | Kára |
| `10.0.31.21` | 3101 | Urd | K3s Worker | Drengr-urd |
| `10.0.31.22` | 3102 | Verd | K3s Worker | Drengr-verd |
| `10.0.31.23` | 3103 | Skuld | K3s Worker | Drengr-skuld |

**Personal VLAN 60 (10.0.60.0/24):**
- `10.0.60.1` — UCG-Ultra gateway
- `10.0.60.10` — MacBook (static)
- `10.0.60.11+` — Game PC, Hue bridge (DHCP)

**Storage VLAN 100 (10.0.100.0/24):**
NFS traffic only — no static assignments needed.

### Cluster CIDRs

- Pod CIDR: `10.42.0.0/16` — Ansible var `k3s_pod_cidr`, used for both K3s `cluster-cidr` and the Calico ipPool.
- Service CIDR: `10.43.0.0/16` — Ansible var `k3s_service_cidr`.

### Resource ID scheme

| Range | Type |
|-------|------|
| 1101–1199 | Must-run LXCs (sub-grouped by function) |
| 2001–2999 | Must-run K3s VMs |
| 3001–3999 | Can-run K3s VMs |
| 10001+ | Templates |

### LXC ID grouping

| Range | Group |
|-------|-------|
| 1101–1109 | Backup & monitoring (PBS, Zabbix) |
| 1110–1119 | Network infrastructure (AdGuard ×3, Tailscale ×3) |
| 1120–1129 | Services (Factorio, Teamspeak) |
| 1130–1139 | Database (PostgreSQL ×3, HAProxy ×3) |

### DNS naming

**External (Cloudflare):**
`outline.xiiisins.com`, `immich.xiiisins.com`, `ts3.xiiisins.com` etc.

**Internal (AdGuard Home):**
```
midgard.xiiisins.com     — publicly reachable services
niflheim.xiiisins.com    — internal infrastructure only

Examples:
  saga.niflheim.xiiisins.com        → 10.0.11.201
  mimir.niflheim.xiiisins.com       → 10.0.11.202
  kvasir.niflheim.xiiisins.com      → 10.0.11.203
  adguard.niflheim.xiiisins.com     → 10.0.11.201 (admin alias)
  adguard-vip.niflheim.xiiisins.com → 10.0.10.200
  urd.niflheim.xiiisins.com         → 10.0.254.11
  verd.niflheim.xiiisins.com        → 10.0.254.12
  skuld.niflheim.xiiisins.com       → 10.0.254.13
  munin.niflheim.xiiisins.com       → 10.0.254.20
  pbs.niflheim.xiiisins.com         → 10.0.11.20
```

TLS: wildcard cert via Let's Encrypt DNS-01 using Cloudflare API. cert-manager manages lifecycle.

### Firewall (UCG-Ultra zone-based)

| From | To | Policy |
|------|----|--------|
| Internal | Any | Allow |
| External | Internal | Allow Return |
| Hotspot | Internal | Allow Return |
| Any | Any | Deny |

All VLANs (MGMT, CLIENT, CORE-VIP, CORE-SVC, CORE-K3S-VIP, CORE-K3S-WRK, CR-K3S-VIP, CR-K3S-WRK, STOR) are in the Internal zone — Internal→Internal is Allow All.

Node-level: `firewalld` is disabled on the K3s nodes (by the Ansible `k3s` prerequisites) — the UCG-Ultra is the firewall.

---

## Proxmox cluster

- **Version:** PVE 9.x (Debian 13 Trixie)
- **Cluster name:** `niflheim`
- **No-subscription repo** on all nodes
- **VLAN-aware bridge** (`vmbr0`) on all nodes
- **Proxmox root LV:** 20GB — leaves ~94GB LVM-thin on Urd/Skuld, ~950GB on Verd
- **VM/LXC disks:** local LVM-thin

---

## Synology (Munin)

Factory reset. Fresh DSM. Two volumes on single RAID 1 pool.

### Volume 1 — data (~553GB)

| Folder | Protocol | Purpose |
|--------|----------|---------|
| `proxmox-backup` | NFS | PBS datastore |
| `k3s-core-data` | NFS | Must-run K3s PVs (legacy, iSCSI now used) |
| `k3s-data` | NFS | Can-run K3s PVs |
| `db-backups` | NFS | DB dumps |
| `uploads` | NFS+SMB | Factorio SFTP |
| `hyper-backup` | Internal | Hyper Backup destination |

### Volume 2 — media (~2TB)

| Folder | Protocol | Purpose |
|--------|----------|---------|
| `media` | NFS+SMB | Movies, TV |
| `manga` | NFS+SMB | Manga — Komga |
| `downloads` | NFS | sabnzbd landing zone |
| `immich` | NFS | Photos/videos (~500GB reserved) |

**iSCSI:** SAN Manager installed. Synology CSI creates one target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). LUNs are single-session by default — see incident log / known issues re: stale sessions after ungraceful restarts. A vestigial target `iqn.2000-01.com.synology:munin.k3s-core.f954439fc46` exists from an abandoned NFS-CSI attempt — NOT in use, candidate for cleanup.

**OOB:** Tailscale Docker container, subnet router for `10.0.0.0/8`.
**K8s user:** `kubernetes` (admin) for Synology CSI driver.

---

## Two-tier service design

### Must-run LXCs

| LXC | ID | Node | IP | Role | Status |
|-----|----|------|----|------|--------|
| PBS | 1101 | Skuld | `10.0.11.20` | Proxmox Backup Server | ✅ |
| Zabbix | 1102 | Skuld | `10.0.11.21` | Infrastructure monitoring | 🔲 |
| Saga (AdGuard 1) | 1110 | Urd | `10.0.11.201` | DNS primary | ✅ |
| Mimir (AdGuard 2) | 1111 | Verd | `10.0.11.202` | DNS replica | ✅ |
| Kvasir (AdGuard 3) | 1112 | Skuld | `10.0.11.203` | DNS replica | ✅ |
| Tailscale 1 | 1113 | Urd | `10.0.11.213` | Subnet router | 🔲 |
| Tailscale 2 | 1114 | Verd | `10.0.11.214` | Subnet router | 🔲 |
| Tailscale 3 | 1115 | Skuld | `10.0.11.215` | Exit node | 🔲 |
| Factorio | 1120 | Urd | `10.0.11.220` | Game server + SFTPGo | 🔲 |
| Teamspeak | 1121 | Verd | `10.0.11.221` | Voice + PostgreSQL | 🔲 |
| PostgreSQL 1 | 1130 | Urd | `10.0.11.230` | Database node | 🔲 |
| PostgreSQL 2 | 1131 | Verd | `10.0.11.231` | Database node | 🔲 |
| PostgreSQL 3 | 1132 | Skuld | `10.0.11.232` | Database node | 🔲 |
| HAProxy 1 | 1133 | Urd | `10.0.11.233` | Load balancer | 🔲 |
| HAProxy 2 | 1134 | Verd | `10.0.11.234` | Load balancer | 🔲 |
| HAProxy 3 | 1135 | Skuld | `10.0.11.235` | Load balancer | 🔲 |
| Jellyfin | TBD | Urd | TBD | Media + QuickSync LXC | 🔲 |

**AdGuard Home:** VIP at `10.0.10.200`. Sync via `adguardhome-sync` binary on Saga. ✅

> **Tailscale LXCs are blocked on Authentik.** Tailscale ACL/SSO integration depends on Authentik being up, and Authentik runs in the must-run K3s cluster. This is why must-run K3s is being built before the remaining LXCs.

### Must-run K3s cluster

True core services — cascade failures if down. Resiliency > simplicity.

**Status (2026-05-14, end of day):** ✅ Running and stable after a major incident (see incident log). Vault 3/3 healthy, etcd healthy, overlay healthy, MetalLB working. One known unresolved issue: tigera-operator failing every reconcile on a SELinux denial (CNI functional but unmanaged).

**Status (2026-05-15 20:24):** Tigera-operator fixed by explicitly setting the MTU size via config in ansible. MTU workaround is suggested by maintainer in upstream issue #7851.

| Service | Replicas | Status |
|---------|----------|--------|
| Vault | 3 (Raft HA) | ✅ Running, AWS KMS auto-unseal. K8s auth method configured (imperatively — see below) |
| Authentik server | 3 | 🔲 |
| Authentik worker | 1 | 🔲 |
| Redis | 1 | 🔲 |
| MetalLB | DaemonSet | ✅ L2 working — VIP reachable, announcing from a worker. Required nodeSelectors + Calico/rp_filter fixes |
| Synology CSI (core) | 1 | ✅ iSCSI, synology-csi-iscsi-retain |
| External Secrets Operator | 1 | ✅ Deployed; ClusterSecretStore `vault` present (verify Ready post-incident) |
| Sealed Secrets | 1 | ✅ |
| tigera-operator (Calico) | 1 | 🔴 Failing every reconcile — SELinux `/var/lib/calico/mtu` denied. CNI works, operator does not. |

**VMs:**

| Name | VM ID | Node | IP | Role | Spec |
|------|-------|------|----|------|------|
| Göndul | 2001 | Urd* | `10.0.21.11` | K3s CP | 1vCPU/2GB/10GB |
| Hlökk | 2002 | Verd | `10.0.21.12` | K3s CP | 1vCPU/2GB/10GB |
| Sigrún | 2003 | Skuld | `10.0.21.13` | K3s CP | 1vCPU/2GB/10GB |
| Einherjar-urd | 2101 | Urd | `10.0.21.21` | K3s Worker | 2vCPU/4GB/15GB |
| Einherjar-verd | 2102 | Verd | `10.0.21.22` | K3s Worker | 2vCPU/4GB/15GB |
| Einherjar-skuld | 2103 | Skuld | `10.0.21.23` | K3s Worker | 2vCPU/4GB/15GB |

*Göndul to move from Urd → Verd on next full reprovision.
⚠️ **CP VM sizing (1vCPU/2GB) is unvalidated under load.** They were briefly oversized (2vCPU/4GB) during IaC iteration to speed repeated apply/destroy cycles, then reset to the intended baseline (1GB was found insufficient, raised to 2GB). The 2026-05-14 etcd storm means 1 vCPU for etcd-bearing nodes should be treated as an open question — revisit at the Göndul reprovision.

**Workers have dual NICs:**
- eth0: VLAN 21 (K3s node traffic)
- eth1: VLAN 20 (MetalLB L2, IPs 10.0.20.201/202/203)
- ⚠️ The second NIC is a known landmine — see incident log. It silently broke Calico autodetection (`firstFound` bound to eth1) and rp_filter (strict mode dropped MetalLB traffic on eth1). Both now explicitly configured.

**K3s install (Ansible `k3s` role — fully IaC):**
- `prerequisites.yml` — Rancher k3s-selinux repo, `iscsi-initiator-utils` + `iscsid`, `br_netfilter`/`overlay` modules (loaded + persisted), `ip_forward=1`, bridge-nf sysctls, swap off, `firewalld` disabled.
- `install.yml` — binary from GitHub (`k3s_version`, currently `v1.33.1+k3s1`); bootstrap order init-node (`--cluster-init`) → joining CPs → workers, gated by `wait_for`/node-count checks; token slurped from init node and distributed; kubeconfig fetched to `~/.kube/niflheim-must-run.yaml`.
- Config templates: `config-init.j2` / `config-server.j2` (CPs — disable traefik/servicelb/local-storage, `flannel-backend: none`, `disable-network-policy: true`, cluster/service CIDRs, TLS SANs, `selinux: true`) / `config-agent.j2` (workers — minimal: server + token + selinux).
- No node taints or labels are set — CP taint is a manual pending task.

**Calico CNI — NOT Flux-managed.** Installed as a K3s addon via `ansible/roles/k3s/tasks/calico.yml` (runs only on the init node): Tigera operator manifest + an `Installation` CR templated from `calico-installation.yaml.j2` to `/var/lib/rancher/k3s/server/manifests/`. Key config: `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]` (pins overlay to VLAN 21), `encapsulation: VXLANCrossSubnet`, pod CIDR `10.42.0.0/16`, `calico_version` currently `v3.29.3`. The K3s addon controller *merges* the CR — removed fields can persist; verify after changes.

**Flux Kustomization structure:**
- `infrastructure` — installs HelmReleases (sealed-secrets, synology-csi, vault, external-secrets, metallb). `interval: 10m`, `prune: true`, sourceRef `GitRepository/flux-system`. No `wait`/`timeout` set.
- `infrastructure-config` — configures CRD resources (ClusterSecretStore, IPAddressPool, L2Advertisement), `dependsOn: [infrastructure]`. Same minimal spec.
- PENDING: split `metallb-config` out of `infrastructure-config` into its own Flux Kustomization — currently MetalLB config and the ESO ClusterSecretStore share a failure domain for no reason (an ESO webhook failure blocked MetalLB config reconcile during the 2026-05-14 incident). When writing the new Kustomization, decide consciously whether it needs `wait`/`timeout` (the existing ones don't set them).

**HelmRelease chart versions** are currently `version: "0.x"` placeholders across metallb / external-secrets / vault / sealed-secrets — a deliberate temporary state. Real pinning + Renovate is planned once the homelab reaches a working "2.0" state.

**Fallback documentation:** static HTML file on Munin with recovery procedures, IPs, and commands. Accessible even if both K3s clusters are down. (Not yet created — pending task.)

### Can-run K3s cluster

Learning environment. Not yet deployed.

**Planned VMs:**

| Name | VM ID | Node | IP | Role |
|------|-------|------|----|------|
| Rota | 3001 | Urd | `10.0.31.11` | K3s CP |
| Hildr | 3002 | Verd | `10.0.31.12` | K3s CP |
| Kára | 3003 | Skuld | `10.0.31.13` | K3s CP |
| Drengr-urd | 3101 | Urd | `10.0.31.21` | K3s Worker |
| Drengr-verd | 3102 | Verd | `10.0.31.22` | K3s Worker |
| Drengr-skuld | 3103 | Skuld | `10.0.31.23` | K3s Worker |

**Can-run services:** AWX, Netbox, Outline, n8n, Immich, Grafana, VictoriaMetrics, VictoriaLogs, Traefik, cert-manager, ESO, Cloudflared, Arr stack, Homepage, Komga, Privatebin, Startpage, Wallpaper gallery, SMTP relay, Synology CSI (can-run)

---

## Identity and access management

- **Personal user** — Authentik LDAP + SSSD. SSH key only.
- **`ansible` user** — local, AWX service account. Passwordless sudo. (This is the inventory `ansible_user`; SSH key `~/.ssh/ansible_niflheim`.)
- **`recovery` / break-glass user** — created by the `baseline` role on all nodes: SSH-key-only, NOPASSWD sudo. SSH key in 1Password.
- **`kubernetes` user** — Synology admin for CSI driver.
- **Proxmox API token** — scoped for Terraform only.

Local admin accounts on all web services (Vault, AWX, Grafana, Netbox, Outline) as Authentik break-glass fallback. Stored in 1Password.

---

## Secrets management

| Layer | Tool |
|-------|------|
| Vault auto-unseal | AWS KMS eu-west-1 (~$1/month) |
| K8s secrets | External Secrets Operator → Vault |
| Bootstrap | Sealed Secrets (one-time) |
| Terraform/Ansible | Ansible Vault |
| Must-run LXCs | Ansible Vault (permanent) |

Cloudflare API tokens split by consumer (Terraform, cert-manager, cloudflared).

**AWS / KMS:** The AWS account holds exactly one KMS key (the Vault unseal key). The `vault-unseal` IAM user is scoped to decrypt-only on that key. The unseal credentials are stored in-repo as a Bitnami `SealedSecret` (`vault-unseal` in the `vault` namespace) — safe because only the cluster's sealed-secrets controller can decrypt it, and the repo is private regardless.

**Vault configuration (current state):**
- KV-v2 secrets engine at `secret/`
- Kubernetes auth method enabled AND configured (`kubernetes_host=https://kubernetes.default.svc`; uses Vault's own SA token as reviewer — Vault SA has `system:auth-delegator` via the `vault-server-binding` ClusterRoleBinding)
- `eso` policy: `read` on `secret/data/*`
- `eso` role: binds SA `external-secrets` in namespace `external-secrets` → `eso` policy
- ESO ClusterSecretStore `vault` points at `http://vault.vault.svc.cluster.local:8200`, path `secret`, v2, kubernetes auth mount `kubernetes`, role `eso`.
- ⚠️ The auth method config was applied **imperatively** (`vault write auth/kubernetes/config`) on 2026-05-14 — it exists in no IaC. The KV engine, policy, and role were created in an earlier interactive session, also not in IaC.
- **PENDING:** capture all Vault configuration (engines, auth methods, policies, roles) in a Terraform Vault provider config under `terraform/vault/`. Secrets values stay out of Git; structure goes in.
- KV store is currently empty — no application secrets written yet.

**Vault listener:** plaintext, `tls_disable = 1` — deliberate. No TLS on the listener or cluster traffic. Conscious homelab tradeoff; the `vault-ui` LoadBalancer (`10.0.20.11:8200`) is internal/VLAN-only. Revisit only as part of deliberate Vault hardening.

---

## IaC

| Tool | Responsibility |
|------|---------------|
| Terraform (`bpg/proxmox`) | VMs, LXCs, DNS, AWS KMS — and (planned) Vault config via Vault provider |
| Ansible + AWX | OS config, drift correction, audit trail. Also: K3s install + the Calico addon manifest |
| Flux CD | K3s workload lifecycle |
| Renovate | Dependency version PRs (planned — activated at "2.0" state) |

**IaC layering — explicit model:**
- **Terraform** — anything with an API/provider: Proxmox VMs/LXCs, Cloudflare DNS, AWS KMS, Vault config.
- **Ansible** — OS/node-level: baseline, hardening (sysctl, SSH, SELinux, module blocklist), K3s *install* + prerequisites, and K3s addon manifests (Calico) since those are files-on-disk on the server nodes. Playbook `must-run-k3s.yml` runs roles `baseline → k3s → hardening` against the `must_run_k3s` group.
- **Flux** — in-cluster workloads: everything in `k8s/`.

**Known IaC debt (2026-05-14):**
- Vault config configured via CLI, not yet captured (see above).
- General CLI-era K3s *workload* config from the build/troubleshooting period should be backfilled into Flux deliberately as a tracked task. (Note: the K3s *install* itself IS fully IaC'd in Ansible — the debt is the in-cluster workload layer, not the cluster bootstrap.)
- Some manifest files have stale path-comment headers predating the `infrastructure` / `infrastructure-config` split (e.g. `clustersecretstore.yaml`).

---

## Build sequence

| Phase | Status | Description |
|-------|--------|-------------|
| 1 — Design | ✅ | Complete |
| 2 — UCG-Ultra | ✅ | VLANs, zones, firewall |
| 3 — Synology | ✅ | Factory reset, volumes, NFS, iSCSI target |
| 4 — Proxmox cluster | ✅ | Urd/Verd/Skuld, cluster niflheim formed |
| 5a — PBS | ✅ | LXC 1101 on Skuld, connected |
| 5b — AdGuard Home | ✅ | Saga/Mimir/Kvasir + keepalived VIP + sync |
| 5c — Must-run K3s | 🟡 | VMs ✅, K3s ✅, Flux ✅, Sealed Secrets ✅, Synology CSI ✅, Vault ✅, ESO ✅, MetalLB ✅. Outstanding: tigera-operator SELinux failure; Vault config not in IaC. |
| 5d — Authentik + Redis | 🔲 | Next forward step. Blocks Tailscale LXCs. |
| 5e — Remaining LXCs | 🔲 | Tailscale, Factorio, Teamspeak, PostgreSQL, HAProxy, Zabbix, Jellyfin |
| 6 — Can-run K3s | 🔲 | Terraform VMs, Flux, services |
| 7 — Observability | 🔲 | VictoriaMetrics + Logs + Grafana + Zabbix |

---

## Key decisions log

| Decision | Choice | Reason |
|----------|--------|--------|
| Orchestrator | K3s only | Single orchestrator |
| Two K3s clusters | Must-run (core) + Can-run | Core services isolated from experimental |
| Core K3s services | Vault, Authentik, Redis, MetalLB, Synology CSI, ESO | Cascade failure criterion |
| GitOps | Flux CD | Terminal-native |
| Flux structure | infrastructure + infrastructure-config Kustomizations | CRD timing — CRDs must exist before dependent resources |
| Identity | Authentik (must-run K3s) | OIDC + LDAP, cascade risk |
| DNS | AdGuard Home (not Pi-hole) | More polished, fully free, self-hosted sync |
| Galera | Dropped | Nothing requires MySQL — all on PostgreSQL |
| Database | PostgreSQL LXC cluster only | Zabbix migrated to PostgreSQL |
| IPAM | Netbox (replaces phpIPAM) | Enterprise standard, API-driven |
| Log aggregation | VictoriaLogs | Better performance, lower resources than Loki |
| Metrics | VictoriaMetrics | PromQL compatible, lower resources than Prometheus |
| Jellyfin | Privileged LXC on Urd | QuickSync /dev/dri passthrough |
| Router | UCG-Ultra | Zone-based firewall, polished UI |
| OOB | Tailscale on Synology | Independent of Proxmox |
| Storage | iSCSI (not NFS) for K3s PVs | NFS creates polluting shared folders on Synology |
| StorageClass | synology-csi-iscsi-retain only | Single default class, no NFS/SMB pollution |
| K3s storage workaround | Init container chown /vault/data | SKIP_CHOWN + iSCSI fsGroup issue |
| Calico install method | K3s addon manifest, templated by Ansible | Not Flux-managed; addon controller applies it |
| Calico IP autodetection | Pinned to `cidrs: [10.0.21.0/24]` | Workers are multi-homed; `firstFound` bound overlay to the VLAN 20 NIC |
| Worker rp_filter | Loose mode (`2`) | Strict mode drops MetalLB traffic on multi-homed nodes |
| MetalLB L2Advertisement | `nodeSelectors` exclude CP nodes | CP nodes have no eth1 — L2 election must not pick them |
| Vault TLS | `tls_disable = 1` (no listener/cluster TLS) | Conscious homelab simplicity tradeoff — revisit at hardening |
| Repo visibility | Private (GitHub) | Reduces exposure; SealedSecrets still used for bootstrap secrets |
| VM naming | Valkyries (CP) + Einherjar/Drengr (workers) | Norse theme, conceptually fits K3s |
| Node naming | Urd, Verd, Skuld (Norns) | Fate controllers = hypervisors |
| NAS naming | Munin | Raven of memory |
| Secrets | Vault + AWS KMS | Production pattern |
| PBS type | Privileged LXC | NFS mount requires it |
| K3s on Urd | Worker only | N5095 too slow for etcd — causes IO storms |
| Göndul placement | Urd (temp) → Verd (next reprovision) | Fix etcd IO issues |

---

## Incident log

### 2026-05-14 — etcd storm cascade (≈15h)

**Trigger:** Adding a second NIC (VLAN 20, for MetalLB) to the worker VMs and the resulting reboot of all worker nodes. The reboot, combined with Göndul's etcd member running on the underpowered Urd (N5095), caused an etcd IO storm.

**What it exposed (all latent, all pre-existing — the storm just made them fire at once):**
- **Calico `firstFound` autodetection** bound the overlay to the workers' new eth1 (VLAN 20) instead of eth0 (VLAN 21). Broke cross-node vxlan: FDB/VTEP entries pointed at VLAN 20 addresses. → Fixed by pinning `nodeAddressAutodetectionV4` to `cidrs: ["10.0.21.0/24"]`. (Note: the K3s addon controller *merged* the CR, leaving a stale `firstFound` alongside the new `cidrs` — had to be stripped with `kubectl patch`.)
- **Broken overlay → ESO admission webhook unreachable** from the API server → `infrastructure-config` Flux Kustomization stuck failing dry-run.
- **iSCSI session chaos** — ungraceful reboots left stale sessions/node records; one LUN got pinned to a CP node (the Synology CSI node plugin is a DaemonSet with no CP taint, so it runs on CP nodes). Vault pods stuck `Init:0/1` unable to mount. The MGMT-subnet doc error (`10.0.1.x` vs real `10.0.254.x`) nearly caused the *correct* iSCSI portal address to be "fixed".
- **Strict rp_filter** (set by the hardening role) silently dropped MetalLB LoadBalancer traffic arriving on the multi-homed workers' eth1. → Fixed by setting `rp_filter=2` (loose).
- **MetalLB L2 election** could pick a CP node (no eth1) and announce nowhere. → Fixed with `nodeSelectors` excluding CP nodes.
- **tigera-operator SELinux denial** on `/var/lib/calico/mtu` — surfaced during diagnosis; still unresolved.

**Resolution:** Overlay pinned to VLAN 21, rp_filter loosened, MetalLB nodeSelectors added, iSCSI sessions cleared, Vault recovered (3/3, KMS auto-unseal), etcd healthy, Flux reconciling. Cluster stable end of day.

**Root-cause pattern:** every failure was config that predated the workers' second NIC (or predated the current topology) and was never reconciled with it. Lens for the Göndul reprovision: *what here assumes a single NIC, or one role per node?*

**Fixes committed:** Ansible — Calico template (`firstFound`→`cidrs`), hardening role (`rp_filter` 1→2). Terraform — worker VLAN 20 NIC, CP VM resize to 1vCPU/2GB. **Not yet committed/captured:** Vault imperative auth config.

---

## Open questions / pending tasks

**High priority (post-incident):**
- [ ] **tigera-operator SELinux denial** — `/var/lib/calico/mtu` permission denied; operator fails every reconcile. CNI works but is unmanaged. Diagnose via `ausearch`/reproduce-and-watch; fix into Ansible k3s/hardening role.
- [ ] **Capture Vault config in Terraform** — auth method, `eso` policy/role, KV engine. Currently imperative-only. (`terraform/vault/`)
- [ ] **Deploy Authentik + Redis** in must-run K3s — the next forward step; unblocks Tailscale.

**Cleanup / hygiene:**
- [ ] Split `metallb-config` into its own Flux Kustomization (separate failure domain from ESO config)
- [ ] Re-parameterize the Calico template's ipPool `cidr` back to `{{ k3s_pod_cidr }}` — it was hardcoded to `10.42.0.0/16` during the incident; the variable still exists and is used for K3s `cluster-cidr`, so there are now two sources of truth for the same value
- [ ] Update stale path-comment headers in manifests (predate the infrastructure / infrastructure-config split — e.g. `clustersecretstore.yaml`)
- [ ] Delete the stray empty `ansible/ansible/` directory (mkdir -p slip)
- [ ] Remove or clearly mark the vestigial `k3s-core` iSCSI target on Munin (leftover from abandoned NFS-CSI attempt)
- [ ] Backfill CLI-era K3s *workload* config into Flux (deliberate, tracked — not a blocker)

**Reprovision (deliberate, on a healthy cluster):**
- [ ] Move Göndul from Urd → Verd (next full reprovision)
- [ ] Add `node-role.kubernetes.io/control-plane:NoSchedule` taint to CP nodes — would be a `node-taint` key in `config-init.j2`/`config-server.j2`. Stops the Synology CSI node plugin (and other untolerated DaemonSets) from running on CP nodes — root of the iSCSI cross-node session fight. Optionally also label workers in `config-agent.j2`.
- [ ] Revisit CP VM sizing — 1vCPU for etcd is unvalidated; consider 2vCPU and/or faster etcd disk

**Standing:**
- [ ] Pin HelmRelease chart versions + activate Renovate (at "2.0" working state)
- [ ] Document the AWS KMS key ARN
- [ ] Cloudflare Tunnel — which services get external exposure
- [ ] Fallback static HTML doc on Munin for core K3s recovery
- [ ] AdGuard DNS records for new VMs/services as provisioned
- [ ] Proxmox HA for must-run LXCs
- [ ] Can-run K3s cluster
- [ ] Confirm whether Vault's `tls_disable` posture should change — revisit at Vault hardening

---

*This document is a living reference. Update it as decisions change during the build.*
