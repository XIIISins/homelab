# Homelab design document
*Last updated: 2026-05-16 — draft v7*

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
- **Asgard K3s CP:** Göndul, Hlökk, Sigrún (Valkyries)
- **Asgard K3s workers:** Einherjar-urd/verd/skuld (army of the Norns)
- **Jotunheim K3s CP:** Rota, Hildr, Kára (Valkyries)
- **Jotunheim K3s workers:** Drengr-urd/verd/skuld (heroes of the Norns)
- **AdGuard Home:** Saga (primary), Mimir (replica), Kvasir (replica)

### Network hardware

| Device | Purpose |
|--------|---------|
| UniFi Cloud Gateway Ultra (UCG-Ultra) | Router, firewall, VLAN routing, zone-based firewall. Static WAN IP. **Sole firewall policy boundary** (KPN is in DMZ → UCG mode). |
| KPN Experia Box | ISP-provided modem/router. Configured in "exposed host" / DMZ mode pointing all unsolicited inbound (IPv4 + IPv6) at UCG-Ultra WAN. Outbound NAT for `192.168.2.0/24` family devices only. Not in IaC — change record lives in these docs. |
| Tailscale on Synology (Docker) | OOB access — survives complete homelab failure |
| Existing dumb switches (×2) | Pass VLAN tags transparently |

### Physical topology

```
Internet
  └── KPN Experia Box (192.168.2.0/24) — DMZ → UCG-Ultra WAN
        ├── Settop box, family devices — unchanged
        └── UCG-Ultra WAN (static IP, public)
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

> **MGMT subnet is `10.0.254.0/24`.** Drafts up to v4 of this document incorrectly recorded it as `10.0.1.0/24`. The live network is and always was `10.0.254.0/24`.

### VLAN table

| VLAN | Subnet | UCG name | Purpose |
|------|--------|----------|---------|
| 1 | `10.0.254.0/24` | HL-MGMT | Management — nodes, NAS, UCG-Ultra |
| 10 | `10.0.10.0/24` | HL-ASG-VIP | Asgard VIPs (keepalived) |
| 11 | `10.0.11.0/24` | HL-ASG-SVC | Asgard LXCs |
| 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP | Asgard K3s MetalLB pool |
| 21 | `10.0.21.0/24` | HL-ASG-K3S-WRK | Asgard K3s nodes |
| 30 | `10.0.30.0/24` | HL-JOT-K3S-VIP | Jotunheim K3s MetalLB pool |
| 31 | `10.0.31.0/24` | HL-JOT-K3S-WRK | Jotunheim K3s nodes |
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

**Asgard VIP VLAN 10 (10.0.10.0/24):**

| Address | Role |
|---------|------|
| `10.0.10.200` | AdGuard Home VIP (keepalived) |
| `10.0.10.201` | AdGuard eth1 — Saga (Urd) |
| `10.0.10.202` | AdGuard eth1 — Mimir (Verd) |
| `10.0.10.203` | AdGuard eth1 — Kvasir (Skuld) |
| `10.0.10.210` | HAProxy VIP — PostgreSQL frontend |

**Asgard LXC VLAN 11 (10.0.11.0/24):**

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

**Asgard K3s MetalLB VLAN 20 (10.0.20.0/24):**

| Address | Role |
|---------|------|
| `10.0.20.11–.99` | LoadBalancer pool (MetalLB) |
| `10.0.20.201` | Einherjar-urd eth1 |
| `10.0.20.202` | Einherjar-verd eth1 |
| `10.0.20.203` | Einherjar-skuld eth1 |

**Asgard K3s nodes VLAN 21 (10.0.21.0/24):**

| Address | VM ID | Node | Role | Name |
|---------|-------|------|------|------|
| `10.0.21.11` | 2001 | Urd* | K3s CP | Göndul (*move to Verd) |
| `10.0.21.12` | 2002 | Verd | K3s CP | Hlökk |
| `10.0.21.13` | 2003 | Skuld | K3s CP | Sigrún |
| `10.0.21.21` | 2101 | Urd | K3s Worker | Einherjar-urd |
| `10.0.21.22` | 2102 | Verd | K3s Worker | Einherjar-verd |
| `10.0.21.23` | 2103 | Skuld | K3s Worker | Einherjar-skuld |

*Göndul to move from Urd → Verd on next full reprovision.

**Jotunheim K3s MetalLB VLAN 30 (10.0.30.0/24):**
`10.0.30.11–.99` — LoadBalancer pool

**Jotunheim K3s nodes VLAN 31 (10.0.31.0/24):**

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
| 1101–1199 | Asgard LXCs (sub-grouped by function) |
| 2001–2999 | Asgard K3s VMs |
| 3001–3999 | Jotunheim K3s VMs |
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

### Internet exposure & firewall

**KPN Experia Box → UCG-Ultra DMZ.** The KPN is configured in "exposed host" / DMZ mode, forwarding all unsolicited inbound traffic (IPv4 *and* IPv6, both tabs configured) to the UCG-Ultra's WAN IP. KPN keeps doing outbound NAT for the `192.168.2.0/24` settop/family-device subnet only. UCG-Ultra has a static public IP; no CGNAT (verified). This means **the UCG-Ultra is the sole firewall policy boundary** between the internet and the homelab — the KPN is effectively a dumb pipe.

**UCG-Ultra zone-based firewall:**

| From | To | Policy |
|------|----|--------|
| Internal | Any | Allow |
| External | Internal | Allow Return |
| Hotspot | Internal | Allow Return |
| Any | Any | Deny (last rule — verify in UI before flipping DMZ) |

All VLANs (MGMT, CLIENT, CORE-VIP, CORE-SVC, CORE-K3S-VIP, CORE-K3S-WRK, CR-K3S-VIP, CR-K3S-WRK, STOR) are in the Internal zone — Internal→Internal is Allow All.

Node-level: `firewalld` is disabled on the K3s nodes (by the Ansible `k3s` prerequisites) — the UCG-Ultra is the firewall.

**Port-forwards are configured on UCG-Ultra only.** Adding a service that needs internet exposure: create the port-forward + auto-generated firewall allow rule in the UCG UI, nothing else. The KPN does not get touched.

**KPN as non-IaC infrastructure.** The KPN Experia Box is consumer hardware with no useful API. Any change to it lives in these docs or nowhere. Current config recorded above; if it ever changes, update here.

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
| `k3s-core-data` | NFS | Asgard K3s PVs (legacy, iSCSI now used) |
| `k3s-data` | NFS | Jotunheim K3s PVs |
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

**iSCSI:** SAN Manager installed. Synology CSI creates one target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). LUNs are single-session by default — see incident log / known issues re: stale sessions after ungraceful restarts.

**OOB:** Tailscale Docker container, subnet router for `10.0.0.0/8`.
**K8s user:** `kubernetes` (admin) for Synology CSI driver.

---

## Two-tier service design

### Asgard LXCs

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
| Factorio | 1120 | Urd | `10.0.11.220` | Game server + SFTPGo | ✅ |
| Teamspeak | 1121 | Verd | `10.0.11.221` | Voice + PostgreSQL | 🔲 |
| PostgreSQL 1 | 1130 | Urd | `10.0.11.230` | Database node | 🔲 |
| PostgreSQL 2 | 1131 | Verd | `10.0.11.231` | Database node | 🔲 |
| PostgreSQL 3 | 1132 | Skuld | `10.0.11.232` | Database node | 🔲 |
| HAProxy 1 | 1133 | Urd | `10.0.11.233` | Load balancer | 🔲 |
| HAProxy 2 | 1134 | Verd | `10.0.11.234` | Load balancer | 🔲 |
| HAProxy 3 | 1135 | Skuld | `10.0.11.235` | Load balancer | 🔲 |
| Jellyfin | TBD | Urd | TBD | Media + QuickSync LXC | 🔲 |

**AdGuard Home:** VIP at `10.0.10.200`. Sync via `adguardhome-sync` binary on Saga. ✅

> **LXC build order — revised (2026-05-15):** The original sequence said "asgard K3s before all LXCs" because of Tailscale's dependency on Authentik. That conflates services that *don't* share that dependency. Revised sequence: Factorio (no deps, ship it standalone), then PostgreSQL + Teamspeak (no Authentik dependency), then Authentik + Redis in K3s, then Tailscale LXCs (needs Authentik for SSO).

### Factorio LXC architecture (deployed 2026-05-16)

LXC 1120 hosts the Factorio headless server *and* SFTPGo on the same host. The operator (a trusted friend) manages the server via SFTP only — no shell access, no web UI, no SSH. Everything happens through JSON control files in `/factorio/control/` edited via SFTPGo's virtual filesystem.

**Filesystem layout** (under `/factorio/`):

| Path | Owner | Operator access | Purpose |
|------|-------|-----------------|---------|
| `README.txt` | `root:factorio` | read | Operator-facing guide |
| `mods/` | `sftpgo:factorio` | read/write | Mod `.zip`s, manually managed |
| `saves/` | `sftpgo:factorio` | read/write | Save files |
| `scenarios/` | `sftpgo:factorio` | read/write | Custom scenarios |
| `script-output/` | `factorio:factorio` | read-only | Factorio writes here |
| `control/factorio-control.json` | `sftpgo:factorio` | read/write | `{version, state, restart}` |
| `control/restart-now` | `sftpgo:factorio` | write (touch) | Triggers instant restart |
| `status/factorio-status.json` | `root:factorio` | read-only | Reconciler-written state |
| `logs/reconcile.log` | `factorio:factorio` | read-only | Rotating reconcile log |

RCON password lives outside `/factorio/` at `/var/lib/factorio-secrets/rconpw` (root:factorio 0750 dir, factorio:factorio 0640 file) so it's not in the operator's SFTP home tree at all.

**Reconcile loop.** `/usr/local/bin/factorio-reconcile` is a Python script (stdlib only) run by a systemd timer every 30s as root. It:
- Reads `/factorio/control/factorio-control.json` to determine desired version + state
- Resolves "stable"/"experimental" via the Factorio API (cached)
- Installs missing versions to `/opt/factorio-<version>/` (atomic symlink swap)
- Verifies SHA256 against `factorio.com/download/sha256sums/`
- Reconciles `factorio.service` running state to match control file
- Honors the one-shot `restart-now` trigger (path unit fires the handler)
- Writes `/factorio/status/factorio-status.json` after each tick
- Chowns its writes back to `factorio:factorio` so SFTPGo can serve them

**Cross-service file sharing.** The `sftpgo` system user is added to the `factorio` group via `sftpgo_extra_groups: [factorio]` in group_vars. `/factorio/*` dirs are setgid (2775) with UMask=0002, so files created by either service are mode 0664 with group=factorio. Both can read/write.

**Operator auth.** Username `operator`, password (in vault). SFTPGo's built-in defender provides brute-force protection (15 failures / 30 min ban). TOTP available as opt-in; pubkey auth deferred. Operator's SFTPGo virtual permissions are scoped per-directory — they can write to `mods/`/`saves/` but `status/` and `logs/` are read-only, and `config/` is denied entirely.

**Internet exposure.** UCG-Ultra port forwards:
- UDP 34197 → 10.0.11.220:34197 (Factorio game protocol)
- TCP 22022 → 10.0.11.220:22022 (SFTPGo SFTP — mnemonic: 22-0-22)

DNS: `factorio.xiiisins.com` (Cloudflare A → WAN IP) and `factorio.niflheim.xiiisins.com` (AdGuard → 10.0.11.220).

**LXC features.** Unprivileged, `nesting=true` (required for systemd 257 on Debian 13). 4 vCPU / 8 GB RAM / 8 GB disk on Urd. No nested containerization — `nesting` flag is for systemd's namespace ops, not Docker.

### Asgard K3s cluster

Production cluster — core infrastructure (Vault, MetalLB, etc.), automation (AWX, Tofu Controller), and services whose absence either cascades into other failures or blocks recovery. Resiliency > simplicity.

**Status (2026-05-16):** Core infrastructure ✅ running and stable. Authentik+Redis is the next forward step, followed by AWX/Tofu Controller, then core services.

**Core infrastructure (cascade failure if down):**

| Service | Replicas | Status |
|---------|----------|--------|
| Vault | 3 (Raft HA) | ✅ Running, AWS KMS auto-unseal. K8s + AppRole auth configured (Terraform `terraform/vault/`) |
| Authentik server | 3 | 🔲 — next forward step |
| Authentik worker | 1 | 🔲 |
| Redis | 1 | 🔲 |
| MetalLB | DaemonSet | ✅ L2 working — VIP reachable, announcing from a worker. Required nodeSelectors + Calico/rp_filter fixes |
| Synology CSI (core) | 1 | ✅ iSCSI, synology-csi-iscsi-retain |
| External Secrets Operator | 1 | ✅ ClusterSecretStore `vault` Ready |
| Sealed Secrets | 1 | ✅ |
| tigera-operator (Calico) | 1 | ✅ Fixed 2026-05-15 via MTU explicit workaround (upstream issue #7851) |
| Traefik | 1+ | 🔲 — ingress controller, paired with cert-manager |
| cert-manager | 1 | 🔲 — Let's Encrypt DNS-01 via Cloudflare API |
| Cloudflared | 1+ | 🔲 — Cloudflare Tunnel for selected external exposure |

**Automation (the git-push-and-walk-away path):**

| Service | Replicas | Status |
|---------|----------|--------|
| AWX | 1 | 🔲 — Ansible CI/CD. `ansible-awx` AppRole role already configured in Vault, SecretID generated at deploy time |
| Tofu Controller | 1 | 🔲 — Terraform/OpenTofu GitOps via Flux. Flux-native (flux-iac org, formerly Weave TF-Controller). Push to main → controller reconciles |

**Core services (production, expected to work):**

| Service | Status |
|---------|--------|
| Outline | 🔲 — wiki / knowledge base |
| Immich | 🔲 — photos/videos |
| Grafana | 🔲 — dashboards (sourced from VictoriaMetrics/VictoriaLogs) |
| VictoriaMetrics | 🔲 — metrics store (PromQL-compatible) |
| VictoriaLogs | 🔲 — log aggregation |
| Netbox | 🔲 — IPAM/DCIM |
| n8n | 🔲 — workflow automation |
| Privatebin | 🔲 — secure paste service |
| Startpage | 🔲 — personal browser homepage (most-used; promoted to asgard) |

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
⚠️ **CP VM sizing (1vCPU/2GB) is unvalidated under load.** The 2026-05-14 etcd storm means 1 vCPU for etcd-bearing nodes should be treated as an open question — revisit at the Göndul reprovision.

**Workers have dual NICs:**
- eth0: VLAN 21 (K3s node traffic)
- eth1: VLAN 20 (MetalLB L2, IPs 10.0.20.201/202/203)
- ⚠️ The second NIC is a known landmine — see incident log. It silently broke Calico autodetection (`firstFound` bound to eth1) and rp_filter (strict mode dropped MetalLB traffic on eth1). Both now explicitly configured.

**K3s install (Ansible `k3s` role — fully IaC):**
- `prerequisites.yml` — Rancher k3s-selinux repo, `iscsi-initiator-utils` + `iscsid`, `br_netfilter`/`overlay` modules (loaded + persisted), `ip_forward=1`, bridge-nf sysctls, swap off, `firewalld` disabled.
- `install.yml` — binary from GitHub (`k3s_version`, currently `v1.33.1+k3s1`); bootstrap order init-node (`--cluster-init`) → joining CPs → workers, gated by `wait_for`/node-count checks; token slurped from init node and distributed; kubeconfig fetched to `~/.kube/niflheim-asgard.yaml`.
- Config templates: `config-init.j2` / `config-server.j2` (CPs — disable traefik/servicelb/local-storage, `flannel-backend: none`, `disable-network-policy: true`, cluster/service CIDRs, TLS SANs, `selinux: true`) / `config-agent.j2` (workers — minimal: server + token + selinux).
- No node taints or labels are set — CP taint is a manual pending task.

**Calico CNI — NOT Flux-managed.** Installed as a K3s addon via `ansible/roles/k3s/tasks/calico.yml` (runs only on the init node): Tigera operator manifest + an `Installation` CR templated from `calico-installation.yaml.j2` to `/var/lib/rancher/k3s/server/manifests/`. Key config: `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]` (pins overlay to VLAN 21), `mtu: 1450` (workaround for projectcalico/calico#7851 — operator can't read `/var/lib/calico/mtu` under SELinux), `encapsulation: VXLANCrossSubnet`, pod CIDR `10.42.0.0/16`, `calico_version` currently `v3.29.3`. The K3s addon controller *merges* the CR — removed fields can persist; verify after changes.

**Flux Kustomization structure:**
- `infrastructure` — installs HelmReleases (sealed-secrets, synology-csi, vault, external-secrets, metallb). `interval: 10m`, `prune: true`, sourceRef `GitRepository/flux-system`. No `wait`/`timeout` set.
- `infrastructure-config` — configures ESO (ClusterSecretStore), `dependsOn: [infrastructure]`. Same minimal spec.
- `metallb-config` — configures MetalLB (IPAddressPool, L2Advertisement), `dependsOn: [infrastructure]`. Split from `infrastructure-config` after the 2026-05-14 incident where an ESO webhook failure blocked MetalLB config reconcile (shared failure domain).

**HelmRelease chart versions** are currently `version: "0.x"` placeholders across metallb / external-secrets / vault / sealed-secrets — a deliberate temporary state. Real pinning + Renovate is planned once the homelab reaches a working "2.0" state.

**Fallback documentation:** static HTML file on Munin with recovery procedures, IPs, and commands. Accessible even if both K3s clusters are down. (Not yet created — pending task.)

### Jotunheim K3s cluster

Non-critical services and experiments. Failure here doesn't cascade and doesn't block recovery — downtime of hours-to-days is acceptable. Same physical-cluster shape as asgard; the distinction is *failure-domain risk*, not "experimental vs production." Most jotunheim services have real users — just not me-needing-them-right-now users.

**Status:** Not yet deployed.

**Planned VMs:**

| Name | VM ID | Node | IP | Role |
|------|-------|------|----|------|
| Rota | 3001 | Urd | `10.0.31.11` | K3s CP |
| Hildr | 3002 | Verd | `10.0.31.12` | K3s CP |
| Kára | 3003 | Skuld | `10.0.31.13` | K3s CP |
| Drengr-urd | 3101 | Urd | `10.0.31.21` | K3s Worker |
| Drengr-verd | 3102 | Verd | `10.0.31.22` | K3s Worker |
| Drengr-skuld | 3103 | Skuld | `10.0.31.23` | K3s Worker |

**Services:**

| Service | Status |
|---------|--------|
| Arr stack (Sonarr/Radarr/Prowlarr/etc.) | 🔲 — media automation |
| Komga | 🔲 — manga server |
| Homepage | 🔲 — service-grid dashboard (distinct from Startpage; Startpage is in asgard) |
| Wallpaper gallery | 🔲 — toy gallery for desktop wallpapers |
| Synology CSI (jotunheim) | 🔲 — iSCSI, second StorageClass instance |
| External Secrets Operator (jotunheim) | 🔲 — pulls from same Vault as asgard instance |

Plus ad-hoc namespaces for genuine experiments (kubevirt trial, ArgoCD comparison, etc.).

---

## Identity and access management

- **Personal user** — Authentik LDAP + SSSD. SSH key only.
- **`ansible` user** — local, AWX service account. Passwordless sudo. SSH key `~/.ssh/ansible_niflheim`.
- **`recovery` / break-glass user** — created by the `baseline` role on all nodes: SSH-key-only, NOPASSWD sudo. SSH key in 1Password.
- **`kubernetes` user** — Synology admin for CSI driver.
- **Proxmox API token** — scoped for Terraform only.

Local admin accounts on all web services (Vault, AWX, Grafana, Netbox, Outline) as Authentik break-glass fallback. Stored in 1Password.

---

## Secrets management

### The architecture: two access patterns, three stores

**The rule:** *Human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

| Layer | Tool | Consumer | Contents |
|---|---|---|---|
| Human-operated credentials | **1Password — "Homelab" vault** | You, via UI/mobile/CLI | Web admin passwords (Authentik admin, Grafana admin, etc.), API tokens you paste manually, LXC template root passwords, homelab-hosted DB admin credentials, TLS recovery keys, AppRole RoleID/SecretID for Ansible control nodes |
| Machine-consumed (runtime) | **HashiCorp Vault (asgard K3s)** | K8s workloads via ESO, Ansible via AppRole, automation | Authentik signing key, DB passwords pulled by apps, K8s workload secrets, Ansible-pulled service passwords (e.g. `secret/ansible/sftpgo/admin-password`) |
| Machine-consumed (bootstrap) | **Ansible Vault** (`group_vars/all/vault.yml`) | Ansible, on a fresh node before HashiCorp Vault is reachable | `k3s_token`, `rhel_activation_key`, `rhel_org_id`, `ansible_user_ssh_public_key`, `breakglass_ssh_public_key`. Plus `aws_access_key_id` / `aws_secret_access_key` / `aws_kms_key_id` as a re-seal recovery copy (not pulled by Ansible — used manually with `kubeseal` to regenerate the vault-unseal SealedSecret; see "Bootstrap-of-bootstrap" row for the runtime path). Narrow scope: only what's needed to make a node usable up to the point HashiCorp Vault can take over. |
| Failure-independent | **1Password — other vaults** | You, when homelab is down | Break-glass user SSH key, AWS KMS unseal token, Vault root token, Proxmox/Synology/UCG-Ultra/KPN admin |
| Bootstrap-of-bootstrap | **SealedSecret + Ansible Vault recovery copy** | Cluster controller (sealed-secrets controller decrypts and mounts as K8s Secret; Vault pod reads as env vars) | `vault-unseal` SealedSecret in `k8s/asgard/infrastructure/vault/vault-unseal-secret.yaml` holds the AWS KMS credentials Vault uses for auto-unseal. Runtime path = SealedSecret → K8s Secret → Vault pod env. Recovery path: if the SealedSecret blob is lost (cluster rebuild, sealed-secrets key rotation), the plaintext AWS values in `group_vars/all/vault.yml` are the source to re-run `kubeseal` against. |

### Scope rule

"Things that exist *because the homelab exists*" go in the Homelab vault or Vault. Personal credentials, external service accounts, and infrastructure *under* the homelab (bare-metal Proxmox root, Synology admin, UCG-Ultra admin, KPN router) live in 1Password but **outside** the Homelab vault — they're personal/external. This scope boundary keeps the Homelab vault tightly defined.

### Why two layers and not one

Considered and rejected:
- **Vaultwarden self-hosted** — adds new infrastructure for human-cred storage that 1Password already does well. The "everything self-hosted" instinct is wrong for credentials needed *when the homelab is down*.
- **1Password Connect as the only store** — would tear down working Vault and replace it with a SaaS-dependent K8s integration path. Vault is already running, stable, and matches enterprise patterns for machine secrets.
- **HashiCorp Vault as the only store, with humans using its UI** — Vault's UI is austere; daily human credential use needs better UX (mobile, autofill, search) than Vault is built for.
- **Centralized everything in one tool** — looked into how enterprises do this. Industry pattern (verified across 2025-2026 sources) is *layered*: EPM (1Password/Bitwarden/Keeper) for humans, secrets manager / PAM (Vault/CyberArk/Delinea) for machines. Even enterprises running 1Password's "unified" platform also run Vault alongside it. Layered is the standard, not the workaround.

**Discoverability concern handled by rule, not by tool unification.** "Scattered truth" was the legitimate worry. The fix is a clean rule (above) and clean boundaries, not forcing one tool to do both jobs poorly.

### Why bootstrap-vs-runtime within the machine tier

HashiCorp Vault lives in asgard K3s. Anything K3s itself needs to come up — `k3s_token` to join nodes to the cluster, SSH keys to even reach the nodes via Ansible — cannot live in Vault. That's a circular dependency: Vault depends on K3s, K3s depends on Vault.

The split: Ansible Vault holds the minimum needed to bootstrap a fresh node *up to the point* where HashiCorp Vault becomes reachable. Everything beyond that goes to HashiCorp Vault. This matches the enterprise pattern (Red Hat IPI installer, AWX bootstrap, every K8s-hosted-Vault deployment hits this) — even though "everything in HashiCorp Vault with a fallback cache" is theoretically possible, the bootstrap layer is small, near-permanent, and rarely-touched, so the simpler approach wins.

The line is essentially: *is this secret needed before HashiCorp Vault is reachable from its consumer?* Yes → bootstrap (Ansible Vault). No → runtime (HashiCorp Vault).

### Long-term direction

The current implementation has ESO sync-and-cache for K8s secrets (Vault → ESO → K8s Secret → pod env var). The enterprise pattern is *runtime retrieval* — pods pull from Vault at use time, no caching in K8s Secrets. The migration target is Vault Agent or Vault Secrets Operator. This is a future project on jotunheim cluster first (use the learning environment for that learning), then migrate asgard workloads.

### Vault current state

- 3-node Raft HA, AWS KMS auto-unseal (eu-west-1, single-key AWS account, `vault-unseal` IAM user is decrypt-only on that one key)
- iSCSI storage (5Gi PVC, `synology-csi-iscsi-retain`)
- Listener `tls_disable = 1` — deliberate (see Known gotchas in CLAUDE.md)
- KV-v2 engine at `secret/`
- **Kubernetes auth method** at `auth/kubernetes/` (`kubernetes_host=https://kubernetes.default.svc`, Vault's own SA token used as reviewer — Vault SA has `system:auth-delegator` via `vault-server-binding` ClusterRoleBinding)
  - `eso` policy: read on `secret/data/*`
  - `eso` role: binds SA `external-secrets` in ns `external-secrets` → `eso` policy, TTL 1h
- **AppRole auth method** at `auth/approle/` (added 2026-05-16, D1)
  - `ansible` policy: read on `secret/data/ansible/*` — narrower than `eso`, Ansible only reads its own subtree
  - `ansible-local` role: MacBook control node, manual playbook runs. SecretID at `~/.config/ansible/vault-approle.env` + 1Password recovery copy. 90-day rotation.
  - `ansible-awx` role: AWX automated runs (deployed later, in asgard K3s). Role exists; SecretID generated at AWX deploy time and stored in AWX's credential store.
  - SecretIDs are NEVER managed by Terraform — generated manually with `vault write -f auth/approle/role/<role>/secret-id`, stored externally. See AppRole bootstrap runbook below.
- ESO ClusterSecretStore `vault` points at `http://vault.vault.svc.cluster.local:8200`, path `secret`, v2, kubernetes auth mount `kubernetes`, role `eso`
- All Vault config (mounts, auth methods, policies, roles) captured in Terraform (`terraform/vault/`). Local state, `VAULT_ADDR`/`VAULT_TOKEN` via env. Provider `hashicorp/vault ~> 4.0`. Scoped Terraform token and remote state deferred.
- **KV contents** (as of 2026-05-16): `secret/ansible/sftpgo/admin-password` (migrated from Ansible Vault as the D1 proof-of-pattern). Authentik bootstrap secrets pending.

### AppRole bootstrap runbook

Setting up a fresh control node (or re-bootstrapping after credential loss). Use this when:
- Setting up Ansible on a new MacBook / control node
- Rotating SecretID at the 90-day cadence
- Recovering from a lost SecretID
- Deploying AWX (same steps, against the `ansible-awx` role)

**Checklist:**

1. ✅ Terraform module `terraform/vault/` applied — AppRole auth method, `ansible` policy, and the relevant role exist.
2. Generate SecretID via `vault write -f auth/approle/role/<role>/secret-id`.
3. Capture RoleID via `terraform output <role>_role_id` (RoleID is not secret).
4. Stash both in 1Password Homelab vault as "Ansible AppRole — <role>" (API Credentials item) with `role_id`, `secret_id`, `secret_id_accessor`, `expires_at` (today + 90 days).
5. Write the local env file at `~/.config/ansible/vault-approle.env` (mode 0600).
6. Install `community.hashi_vault` Galaxy collection + `hvac` Python lib in the Ansible venv.
7. Test the lookup with `playbooks/test-vault-lookup.yml`.

**Detailed steps — MacBook → `ansible-local` role:**

Prerequisites: `pipx`-installed Ansible (Homebrew Ansible bundles its own externally-managed Python; injecting Python deps is awkward), `hvac` injected, fish shell, OBJC fork-safety env var set.

Generate the SecretID. Vault uses your root token here — anything that can mint SecretIDs is by definition privileged:

```fish
set -x VAULT_ADDR http://10.0.20.11:8200
set -x VAULT_TOKEN <root token from 1Password>
vault write -f auth/approle/role/ansible-local/secret-id
```

Output:
```
Key                   Value
---                   -----
secret_id             a8b3f7e2-...
secret_id_accessor    1f2a-...
secret_id_num_uses    0
secret_id_ttl         7776000
```

Capture the matching RoleID (non-secret) from Terraform output:

```fish
cd terraform/vault
terraform output -raw ansible_local_role_id
```

Store both in 1Password under "Ansible AppRole — ansible-local" (API Credentials item):
- `URL`: `http://10.0.20.11:8200`
- `role_id`: from `terraform output`
- `secret_id`: from `vault write`
- `secret_id_accessor`: from `vault write` (used to revoke without knowing the SecretID)
- `expires_at`: today + 90 days

Write the local env file (fish heredocs don't work; use `echo` to a file):

```fish
mkdir -p ~/.config/ansible
echo "VAULT_ADDR=http://10.0.20.11:8200
ANSIBLE_HASHI_VAULT_AUTH_METHOD=approle
ANSIBLE_HASHI_VAULT_ROLE_ID=<paste role_id>
ANSIBLE_HASHI_VAULT_SECRET_ID=<paste secret_id>" > ~/.config/ansible/vault-approle.env
chmod 0600 ~/.config/ansible/vault-approle.env
```

The `ANSIBLE_HASHI_VAULT_*` prefix is the canonical env-var form the `community.hashi_vault` collection reads. `VAULT_ADDR` stays as-is (it's the standard Vault CLI env var).

Install the fish helper as `~/.config/fish/functions/ansible-vault-env.fish`:

```fish
function ansible-vault-env --description "Source AppRole credentials for community.hashi_vault lookups"
    set -l env_file ~/.config/ansible/vault-approle.env
    if not test -f $env_file
        echo "Error: $env_file not found" >&2
        return 1
    end
    set -l count 0
    while read -l line
        if string match -qr '^\s*$|^\s*#' -- $line
            continue
        end
        set -l key (string split -m 1 -f1 = $line)
        set -l value (string split -m 1 -f2 = $line)
        set -gx $key $value
        set count (math $count + 1)
    end < $env_file
    echo "Loaded $count variables from $env_file"
end
```

Usage: `ansible-vault-env` once per shell session, then run playbooks normally.

Install collection + Python dep:

```fish
ansible-galaxy collection install -r ansible/requirements.yml
pipx inject ansible hvac
```

macOS-specific (one-time, fish universal variable). Prevents Python's macOS fork-safety crashes when Ansible workers fork after loading ObjC-linked libs like `urllib3`:

```fish
set -Ux OBJC_DISABLE_INITIALIZE_FORK_SAFETY YES
```

Verify end-to-end:

```fish
# Seed a test secret (requires root token, AppRole is read-only)
set -x VAULT_TOKEN <root token>
vault kv put secret/ansible/test/hello value=world
set -e VAULT_TOKEN

# Pick up AppRole creds and run the test play
ansible-vault-env
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/test-vault-lookup.yml
# Expected: "Got value from Vault: world"

# Cleanup
set -x VAULT_TOKEN <root token>
vault kv delete secret/ansible/test/hello
```

**For AWX (when deployed):** same steps against `ansible-awx` role. SecretID goes into AWX's credential store rather than a local env file. After deploy, restrict via `token_bound_cidrs` in `terraform/vault/main.tf` once the asgard K3s pod CIDR is known.

**Rotation (every 90 days):**

```fish
set -x VAULT_TOKEN <root token>

# Revoke the old SecretID by its accessor (from 1Password)
vault write auth/approle/role/ansible-local/secret-id-accessor/destroy \
    secret_id_accessor=<old accessor>

# Generate new SecretID
vault write -f auth/approle/role/ansible-local/secret-id

# Update 1Password + ~/.config/ansible/vault-approle.env
```

**Vault lookup syntax cheatsheet** for `community.hashi_vault.vault_kv2_get`:

| Accessor | Returns |
|---|---|
| `.raw` | Full Vault API response |
| `.data` | Wrapper (contains both KV data and metadata) |
| `.metadata` | Just version metadata (version number, created_time, etc.) |
| `.secret` | Just the KV key-value pairs — what you usually want |

Use `.secret.<key>` for the actual value:
```yaml
"{{ lookup('community.hashi_vault.vault_kv2_get', 'ansible/sftpgo/admin-password').secret.value }}"
```

### OpenBao migration (future)

HashiCorp Vault moved to BSL in 2023 and HashiCorp was acquired by IBM in 2025. OpenBao is the Linux Foundation fork at near-parity. Migration is essentially a binary swap (same API, same Terraform provider, same Ansible lookups). Plan: migrate at the next major maintenance window once OpenBao has another ~12 months of production track record, likely 2026 late-year or 2027.

---

## IaC

| Tool | Responsibility |
|------|---------------|
| Terraform (`bpg/proxmox`) | VMs, LXCs, DNS, AWS KMS |
| Terraform (`hashicorp/vault`) | Vault config: auth method, policies, roles, KV engine |
| Ansible + AWX | OS config, drift correction, audit trail. Also: K3s install + the Calico addon manifest |
| Flux CD | K3s workload lifecycle |
| Renovate | Dependency version PRs (planned — activated at "2.0" state) |

**IaC layering — explicit model:**
- **Terraform** — anything with an API/provider: Proxmox VMs/LXCs, Cloudflare DNS, AWS KMS, Vault config.
- **Ansible** — OS/node-level: baseline, hardening (sysctl, SSH, SELinux, module blocklist), K3s *install* + prerequisites, and K3s addon manifests (Calico) since those are files-on-disk on the server nodes. Playbook `asgard-k3s.yml` runs roles `baseline → k3s → hardening` against the `must_run_k3s` group.
- **Flux** — in-cluster workloads: everything in `k8s/`.
- **Docs (this file)** — KPN Experia Box config and anything else without a useful API.

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
| 5c — Asgard K3s | ✅ | VMs ✅, K3s ✅, Flux ✅, Sealed Secrets ✅, Synology CSI ✅, Vault ✅, ESO ✅, MetalLB ✅, tigera-operator ✅ |
| 5d — KPN DMZ | ✅ | DMZ → UCG-Ultra WAN (IPv4 + IPv6) |
| 5e — Authentik + Redis | 🔲 | Next forward step on the K8s side. Blocks Tailscale LXCs. |
| 5f — Factorio LXC | ✅ | Deployed 2026-05-16 — Terraform + Ansible end-to-end |
| 5g — PostgreSQL + Teamspeak LXCs | 🔲 | After Factorio. Teamspeak needs PostgreSQL. |
| 5h — Remaining LXCs | 🔲 | Tailscale (after Authentik), HAProxy, Zabbix, Jellyfin |
| 6 — Jotunheim K3s | 🔲 | Terraform VMs, Flux, services |
| 7 — Observability | 🔲 | VictoriaMetrics + Logs + Grafana + Zabbix |
| 8 — Secrets runtime retrieval | 🔲 | Migrate from ESO sync-and-cache to Vault Agent / VSO. Pilot on jotunheim first. |

---

## Key decisions log

| Decision | Choice | Reason |
|----------|--------|--------|
| Orchestrator | K3s only | Single orchestrator |
| Two K3s clusters | Asgard + Jotunheim | Separation by failure-domain risk, not by maturity — both host production services |
| Asgard K3s contents | Core infrastructure + automation + production services | See "Asgard K3s cluster" section for current planned list |
| Cluster split criterion | Cascade-failure OR recovery-blocking criterion for asgard | Earlier "cascade only" framing missed automation (recovery-blocking) and daily-use services (user-blocking). Not production-vs-experimental — both clusters host real services |
| GitOps | Flux CD | Terminal-native |
| Flux structure | infrastructure + per-component config Kustomizations | CRD timing — CRDs must exist before dependent resources; per-component split avoids shared failure domains |
| Terraform GitOps | Tofu Controller (flux-iac org) | Flux-native, push-to-main matches the existing Flux model. Atlantis rejected — PR-flow friction not worth it for solo dev |
| Identity | Authentik (asgard K3s) | OIDC + LDAP, cascade risk |
| DNS | AdGuard Home (not Pi-hole) | More polished, fully free, self-hosted sync |
| Galera | Dropped | Nothing requires MySQL — all on PostgreSQL |
| Database | PostgreSQL LXC cluster only | Zabbix migrated to PostgreSQL |
| IPAM | Netbox (replaces phpIPAM) | Enterprise standard, API-driven |
| Log aggregation | VictoriaLogs | Better performance, lower resources than Loki |
| Metrics | VictoriaMetrics | PromQL compatible, lower resources than Prometheus |
| Jellyfin | Privileged LXC on Urd | QuickSync /dev/dri passthrough |
| Router | UCG-Ultra | Zone-based firewall, polished UI |
| Internet exposure | KPN DMZ → UCG-Ultra | UCG is sole policy boundary; KPN is dumb pipe |
| OOB | Tailscale on Synology | Independent of Proxmox |
| Storage | iSCSI (not NFS) for K3s PVs | NFS creates polluting shared folders on Synology |
| StorageClass | synology-csi-iscsi-retain only | Single default class, no NFS/SMB pollution |
| K3s storage workaround | Init container chown /vault/data | SKIP_CHOWN + iSCSI fsGroup issue |
| Calico install method | K3s addon manifest, templated by Ansible | Not Flux-managed; addon controller applies it |
| Calico IP autodetection | Pinned to `cidrs: [10.0.21.0/24]` | Workers are multi-homed; `firstFound` bound overlay to the VLAN 20 NIC |
| Calico MTU | Explicit `mtu: 1450` | Workaround for upstream issue #7851 — operator can't read `/var/lib/calico/mtu` under SELinux MCS |
| Worker rp_filter | Loose mode (`2`) | Strict mode drops MetalLB traffic on multi-homed nodes |
| MetalLB L2Advertisement | `nodeSelectors` exclude CP nodes | CP nodes have no eth1 — L2 election must not pick them |
| Vault TLS | `tls_disable = 1` (no listener/cluster TLS) | Conscious homelab simplicity tradeoff — revisit at hardening |
| Secrets architecture | Three stores by access pattern: 1Password (humans), HashiCorp Vault (machines at runtime), Ansible Vault (machines at bootstrap) | "Centralize everything" rejected after considering Vaultwarden, 1Password Connect, Infisical, OpenBao alternatives — see Secrets management section |
| Bootstrap-vs-runtime split | Ansible Vault for bootstrap, HashiCorp Vault for runtime | Resolves circular dependency: HashiCorp Vault lives in asgard K3s, so anything K3s itself needs to come up cannot live there. Bootstrap layer is narrow, stable, and rare-touch |
| Ansible Vault scope | Bootstrap secrets only (k3s_token, RHEL keys, SSH pubkeys, AWS KMS re-seal copy) | Narrow permanent role, not legacy — runtime machine secrets go to HashiCorp Vault |
| Ansible → Vault auth | AppRole, two roles (`ansible-local` for MacBook, `ansible-awx` for cluster) | Industry-standard for non-K8s automation; RoleIDs from Terraform outputs, SecretIDs generated manually and kept out of TF state (90-day rotation, 1Password recovery copy) |
| Long-term Vault successor | OpenBao (migration ~12 months out) | HashiCorp BSL + IBM acquisition risk; LF governance preferred long-term |
| Repo visibility | Private (GitHub) | Reduces exposure; SealedSecrets still used for bootstrap secrets |
| VM naming | Valkyries (CP) + Einherjar/Drengr (workers) | Norse theme, conceptually fits K3s |
| Node naming | Urd, Verd, Skuld (Norns) | Fate controllers = hypervisors |
| NAS naming | Munin | Raven of memory |
| PBS type | Privileged LXC | NFS mount requires it |
| K3s on Urd | Worker only | N5095 too slow for etcd — causes IO storms |
| Göndul placement | Urd (temp) → Verd (next reprovision) | Fix etcd IO issues |
| LXC build order | Revised: Factorio → PG+Teamspeak → Authentik → Tailscale → rest | Original sequence conflated Authentik-dependent and Authentik-independent LXCs |
| LXC provisioning | Terraform module `asgard-lxcs/` parallel to `asgard-k3s/` | Same provider, same auth, consistent pattern |
| Factorio operator UX | SFTP-only self-service via SFTPGo virtual user | Zero shell access; control via JSON files in `/factorio/control/` |
| Factorio reconcile pattern | Root systemd timer (30s), Python stdlib script, owns state | Operator declares intent in JSON, reconciler converges. Decoupled. |
| Factorio operator auth | Password + SFTPGo defender | Ease-of-use over pubkey complexity; brute-force protected |
| Factorio install location | `/opt/factorio-<version>/`, symlinked to `/opt/factorio` | Atomic version swap, side-by-side versions, easy rollback |
| Factorio chown after extract | Recursive `factorio:factorio` on install dir | Factorio writes `.lock` in install dir — needs runtime user write access |
| LXC bootstrap flow | `ansible-playbook -e ansible_user=root --tags baseline` then full playbook as ansible | Root SSH only for the brief baseline window; hardening locks root after |
| Debian LXC user creation | `password: '*'` in `user` task | Ansible default `!` triggers PAM account lock even for key auth |
| LXC nesting flag | `nesting=true` on Debian 13 LXCs | Required for systemd 257; orthogonal to nested containerization |

---

## Incident log

### 2026-05-14 — etcd storm cascade (≈15h)

**Trigger:** Adding a second NIC (VLAN 20, for MetalLB) to the worker VMs and the resulting reboot of all worker nodes. The reboot, combined with Göndul's etcd member running on the underpowered Urd (N5095), caused an etcd IO storm.

**What it exposed (all latent, all pre-existing — the storm just made them fire at once):**
- **Calico `firstFound` autodetection** bound the overlay to the workers' new eth1 (VLAN 20) instead of eth0 (VLAN 21). Broke cross-node vxlan. → Fixed by pinning `nodeAddressAutodetectionV4` to `cidrs: ["10.0.21.0/24"]`. (The K3s addon controller *merged* the CR, leaving a stale `firstFound` alongside the new `cidrs` — had to be stripped with `kubectl patch`.)
- **Broken overlay → ESO admission webhook unreachable** from the API server → `infrastructure-config` Flux Kustomization stuck failing dry-run.
- **iSCSI session chaos** — ungraceful reboots left stale sessions/node records; one LUN got pinned to a CP node (the Synology CSI node plugin is a DaemonSet with no CP taint, so it runs on CP nodes). Vault pods stuck `Init:0/1` unable to mount. The MGMT-subnet doc error (`10.0.1.x` vs real `10.0.254.x`) nearly caused the *correct* iSCSI portal address to be "fixed".
- **Strict rp_filter** (set by the hardening role) silently dropped MetalLB LoadBalancer traffic arriving on the multi-homed workers' eth1. → Fixed by setting `rp_filter=2` (loose).
- **MetalLB L2 election** could pick a CP node (no eth1) and announce nowhere. → Fixed with `nodeSelectors` excluding CP nodes.
- **tigera-operator SELinux denial** on `/var/lib/calico/mtu` — surfaced during diagnosis. Resolved next day (see 2026-05-15 below).

**Resolution:** Overlay pinned to VLAN 21, rp_filter loosened, MetalLB nodeSelectors added, iSCSI sessions cleared, Vault recovered (3/3, KMS auto-unseal), etcd healthy, Flux reconciling. Cluster stable end of day.

**Root-cause pattern:** every failure was config that predated the workers' second NIC (or predated the current topology) and was never reconciled with it. Lens for the Göndul reprovision: *what here assumes a single NIC, or one role per node?*

**Fixes committed:** Ansible — Calico template (`firstFound`→`cidrs`), hardening role (`rp_filter` 1→2). Terraform — worker VLAN 20 NIC, CP VM resize to 1vCPU/2GB.

### 2026-05-15 — tigera-operator SELinux fix

Carry-over from the 2026-05-14 incident. The operator was failing every reconcile with `open /var/lib/calico/mtu: permission denied`, leaving the CNI functional but unmanaged.

**Diagnosis:**
- `ausearch` returned nothing — the denial is `dontaudit`'d by default policy.
- `semodule -DB` (disable dontaudit, keep enforcing) revealed the AVC: `scontext=...container_t:s0:c322,c902 tcontext=...container_var_lib_t:s0` — **MCS category mismatch**. The operator has categories (normal container); the file is written by privileged calico-node with no categories. MCS dominance fails on read.
- This is upstream bug **projectcalico/calico#7851**, open since July 2023, never fixed. Tigera maintainer's recommended workaround: set MTU explicitly so the operator never needs to read the file.

**Fix shipped:** Added `mtu: 1450` to `spec.calicoNetwork` in `ansible/roles/k3s/templates/calico-installation.yaml.j2`. 1450 = 1500 (host MTU) - 50 (VXLAN overhead). After playbook re-run, the `Degraded` condition cleared and the operator stopped logging MTU read errors.

**Why not a SELinux policy module:** Would have meant authoring/maintaining a custom policy artifact per node, deploying via `semodule -i`, owning forever. The upstream-recommended workaround eliminates the need to read the file at all — cleaner posture, smaller surface, captured in IaC as a 3-line template change. Considered carefully (was nearly the chosen path until the upstream issue was found); MTU-explicit is the right call.

**`semodule -DB` discipline:** Re-enabled dontaudit (`semodule -B`) after diagnosis. Never leave dontaudit disabled — it floods audit log with noise the policy authors intentionally suppress.

### 2026-05-16 — Factorio LXC end-to-end deploy

Initial deploy of LXC 1120 (Factorio + SFTPGo). Surfaced seven bugs in the freshly-built `factorio`/`sftpgo` roles plus general Debian-LXC issues, all fixed in this session.

**Bugs and fixes:**

1. **Factorio SHA256 lookup target string was wrong.** Script looked for `factorio_headless_x64_<v>.tar.xz`; actual filename is `factorio-headless_linux_<v>.tar.xz`. Reverse-engineered from a stale reference. Fix: corrected the target string in `fetch_sha256()`.

2. **Factorio install dir not chowned after extraction.** Factorio writes `.lock` in its install dir at startup; runtime user needs write access. Fix: `os.walk` + `os.chown` over `/opt/factorio-<v>/` after rename.

3. **Reconcile timer auto-started in role.** `state: started` on the timer caused background reconcile to race ahead of `initial-install.yml`, installing Factorio and starting the service before the role could generate the default save. Fix: timer is only `enabled` in `reconcile.yml`; `state: started` moved to end of `initial-install.yml`.

4. **Initial install race.** With control file defaulting to `state=running`, the synchronous reconcile call started `factorio.service` immediately after install, then `factorio --create` failed on the lock. Fix: `initial-install.yml` detects first-run via stat on `/opt/factorio`, writes `state=stopped`, runs sync reconcile, generates save, flips to `state=running`, then starts the service.

5. **Multiple "directory does not exist" failures.** Minimal Proxmox Debian 13 template lacks dirs the roles assumed:
   - `/etc/sudoers.d/` (no `sudo` package) → baseline now installs `sudo`
   - Ansible's `become_user` temp path (no `acl`) → baseline installs `acl`
   - `/etc/systemd/system/sftpgo.service.d/` → sftpgo role creates explicitly
   - `/var/lib/sftpgo/` → sftpgo role creates explicitly

6. **SFTPGo sqlite path was CWD-relative.** `SFTPGO_DATA_PROVIDER__NAME=sftpgo.db` resolves against the package unit's `WorkingDirectory=/etc/sftpgo`. Worked accidentally; would have broken on any override that changed WorkingDirectory. Fix: absolute path `/var/lib/sftpgo/sftpgo.db`.

7. **`ansible.builtin.user` creates locked accounts.** Default `password` value in Ansible is `!`, which PAM treats as "account locked" even for pubkey auth (since `UsePAM yes` invokes `account` stack). Symptom: SSH key offered, accepted, then rejected with "User ansible not allowed because account is locked" in auth.log. Fix: `password: '*'` (valid form, no usable password) on both ansible and recovery user tasks in baseline.

**Discovered along the way:**
- bpg/proxmox: only `nesting` can be changed via API token; other features (`keyctl`, `fuse`) require `root@pam` → destroy/recreate when changing.
- `-u root` CLI flag does *not* beat `ansible_user` in group_vars. Must use `-e ansible_user=root` for the bootstrap override.
- `group_vars/` is auto-discovered only adjacent to inventory or playbook dirs. `ansible/group_vars/` was being ignored; moved to `ansible/inventory/group_vars/`.

**Resolution:** All bugs fixed in role source, then verified with a clean destroy + apply from Terraform. End-to-end: Terraform → Ansible → operator SFTPs in from outside the LAN via tethered mobile, lists `/factorio/` contents successfully.

**Root-cause pattern:** Roles authored against assumed-richer base images (RHEL or fuller Debian). Proxmox's `debian-13-standard` template is minimal and surfaces every implicit "this dir exists" / "this binary is installed" assumption. Lesson: roles targeting minimal LXC templates need explicit `file: state: directory` and `package: state: present` tasks for every non-FHS-core path/binary they touch.

---

## Open questions / pending tasks

**High priority — forward path:**
- [ ] **Deploy Authentik + Redis** in asgard K3s. Bootstrap secrets (signing key, DB connection, Redis password) go in Vault and are pulled via ESO. Unblocks Tailscale LXCs.
- [ ] **Create 1Password "Homelab" vault** if not already done. Migrate existing scattered homelab credentials in.

**Service backlog (in revised LXC order):**
- [x] **Factorio LXC** (1120, Urd) — ✅ Deployed 2026-05-16. UDP 34197 + TCP 22022 port-forwards active.
- [ ] **PostgreSQL** (start with 1130 on Urd; cluster later).
- [ ] **Teamspeak LXC** (1121, Verd) — pointed at PostgreSQL.
- [ ] **Tailscale LXCs** (1113/1114/1115) — after Authentik.
- [ ] **HAProxy** (1133/1134/1135) — PostgreSQL frontend, after PG cluster.
- [ ] **Zabbix LXC** (1102, Skuld).
- [ ] **Jellyfin LXC** (privileged, Urd, QuickSync).

**Vault-for-Ansible migration (in progress — pattern built D1, secrets migrating individually):**

Per the bootstrap-vs-runtime architecture: bootstrap secrets stay in Ansible Vault permanently (narrow scope: `k3s_token`, RHEL keys, SSH pubkeys for ansible/breakglass users — needed before HashiCorp Vault is reachable). Runtime secrets — those needed *after* a node is up — move to HashiCorp Vault.

- [x] **Build the Vault-for-Ansible lookup pattern** (✅ D1, 2026-05-16). AppRole + `ansible` policy + `ansible-local`/`ansible-awx` roles in `terraform/vault/`. `community.hashi_vault` collection + lookup pattern in `roles/sftpgo/defaults/main.yml`. AppRole bootstrap runbook in this doc.
- [x] **Migrate `sftpgo_admin_password`** → `secret/ansible/sftpgo/admin-password` (✅ D1, proof-of-pattern). Removed from `group_vars/all/vault.yml`.
- [ ] **Migrate `vault_factorio_operator_password`** → `secret/ansible/factorio/operator-password`. Drop the `vault_` prefix at migration time (the prefix was aspirational; the var now actually comes from HashiCorp Vault).
- [ ] **Fix sftpgo role check-mode compatibility.** `roles/sftpgo/tasks/bootstrap.yml` does a POST to fetch an admin JWT, then references `result.json.access_token`. In `--check` mode `uri:` skips POSTs by default, returning a stub dict without `.json` — breaks downstream tasks. Add `check_mode: false` to the token-fetch and any downstream state-reading API calls.

**Reprovision (deliberate, on a healthy cluster):**
- [ ] Move Göndul from Urd → Verd (next full reprovision).
- [ ] Add `node-role.kubernetes.io/control-plane:NoSchedule` taint to CP nodes — would be a `node-taint` key in `config-init.j2`/`config-server.j2`. Stops the Synology CSI node plugin (and other untolerated DaemonSets) from running on CP nodes — root of the iSCSI cross-node session fight.
- [ ] Revisit CP VM sizing — 1vCPU for etcd is unvalidated; consider 2vCPU and/or faster etcd disk.

**Standing:**
- [ ] Pin HelmRelease chart versions + activate Renovate (at "2.0" working state).
- [ ] Document the AWS KMS key ARN.
- [ ] Cloudflare Tunnel — which services get external exposure (in addition to direct port-forwards via UCG).
- [ ] Fallback static HTML doc on Munin for core K3s recovery.
- [ ] AdGuard DNS records for new VMs/services as provisioned.
- [ ] Proxmox HA for asgard LXCs.
- [ ] Jotunheim K3s cluster.
- [ ] Confirm whether Vault's `tls_disable` posture should change — revisit at Vault hardening.
- [ ] Long-term: migrate from ESO sync-and-cache to Vault Agent / VSO runtime retrieval. Pilot on jotunheim first.
- [ ] Long-term: migrate Vault → OpenBao (~12 months out, once OpenBao has more production track record).

---

*This document is a living reference. Update it as decisions change during the build.*