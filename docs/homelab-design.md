# Homelab design document
*Last updated: 2026-05-17 evening — draft v9 (Authentik+Redis deploy)*

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
- Urd (N5095) is too weak for K3s control plane. etcd IO storms observed under load. CPs run on Verd/Skuld only.
- Urd long-term: dedicated Jellyfin LXC with Intel QuickSync passthrough. Currently also running Einherjar-urd (K3s worker) and the Factorio LXC (1120).
- Göndul moved Urd → Verd on 2026-05-17 (was deferred since the 2026-05-14 incident; finally fixed during the asgard rebuild).
- All three CPs (Göndul/Hlökk/Sigrún) bumped 1vCPU/2GB → 2vCPU/4GB on 2026-05-17 evening during the Authentik deploy. Earlier doc text claimed Göndul was bumped during the rebuild; the Terraform `locals.control_planes` map was unchanged until tonight. Authentik's first-deploy migration + blueprint-reconciliation burst on hlokk and sigrun confirmed the under-spec; symmetric 2vCPU/4GB across all three is now the standard (CP sizing identical across nodes — same rule as PG nodes, failover symmetry).
- **CP-only workload posture (Phase 4a, 2026-05-18):** CPs not yet tainted as `node-role.kubernetes.io/control-plane:NoSchedule`. Tonight's deploy proved this is wrong — workload pressure on a CP starved etcd of fsync I/O and brought kubectl unresponsive cluster-wide. Phase 4a (before Traefik+cert-manager) lands the taint via `node-taint:` in `config-init.j2`/`config-server.j2`. After taint, CPs run only the control plane plus the DaemonSets that need cluster-wide presence (Calico-node, kube-proxy) — Synology CSI node-plugin gets evicted from CPs, fixing the cross-node iSCSI session-fight gotcha as a side effect. 4 GiB is the right size *with* the taint (control-plane working set ~1.5-2 GiB, leaves ~2 GiB headroom). 4 GiB was the wrong size *without* the taint (workload bursts ate the headroom). Same node spec, different posture, different outcome.
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
- **Asgard PostgreSQL:** Fulla (primary), Vör (replica), Idunn (replica) — Frigg's handmaidens / keeper goddesses

**Cluster naming meta-principle.** The primary node defines the theme; replicas are a logical expansion within it. Postgres is the *library* — Fulla guards the treasure chest, Vör knows what is in it, Idunn preserves what must not be lost. New service clusters follow the same shape: pick the primary by archetype, the replicas by the natural cohort that surrounds that archetype.

ASCII-only for hostnames (the inventory keys are `fulla`, `vor`, `idunn`); display names in tables keep the accents.

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
| `10.0.11.230` | 1130 | Skuld | Fulla (PostgreSQL 1) ✅ |
| `10.0.11.231` | 1131 | Urd | Vör (PostgreSQL 2) |
| `10.0.11.232` | 1132 | Verd | Idunn (PostgreSQL 3) |
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
| `10.0.21.11` | 2001 | Verd | K3s CP | Göndul |
| `10.0.21.12` | 2002 | Verd | K3s CP | Hlökk |
| `10.0.21.13` | 2003 | Skuld | K3s CP | Sigrún |
| `10.0.21.21` | 2101 | Urd | K3s Worker | Einherjar-urd |
| `10.0.21.22` | 2102 | Verd | K3s Worker | Einherjar-verd |
| `10.0.21.23` | 2103 | Skuld | K3s Worker | Einherjar-skuld |

Göndul moved from Urd → Verd on 2026-05-17 (was the deferred backlog item from the 2026-05-14 incident).

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
| Fulla (PostgreSQL 1) | 1130 | Skuld | `10.0.11.230` | DB primary, PG 17 + TLS | ✅ |
| Vör (PostgreSQL 2) | 1131 | Urd | `10.0.11.231` | DB replica (planned) | 🔲 |
| Idunn (PostgreSQL 3) | 1132 | Verd | `10.0.11.232` | DB replica (planned) | 🔲 |
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

### PostgreSQL LXC architecture (Fulla deployed 2026-05-17)

LXC 1130 (Fulla) hosts PostgreSQL 17 from PGDG, TLS-enabled, scram-sha-256 only. Standalone for now; Vör (1131, Urd) and Idunn (1132, Verd) join post-Authentik to validate clustering against a real consumer. Each cluster node lives on a different Proxmox host so a single-host failure never takes down >1 PG node.

**Why local LVM-thin, not NFS.** Postgres on NFS is an anti-pattern: Synology DSM's NFS isn't on anyone's validated list, fsync semantics vary between server implementations, soft mounts can silently drop writes under load, and WAL fsync over 1 GbE makes every commit a network round-trip. Database scale (Authentik + Teamspeak + future services = tens of GB) fits inside the 16 GB LXC disk; `pct resize` is one command if it ever fills.

**Sizing (cluster-wide, identical across nodes).** 2 vCPU, 4 GB RAM, 16 GB disk. Failover symmetry requires identical resources — asymmetric sizing means failover silently degrades performance. Tuning: `shared_buffers=1GB` (25%), `effective_cache_size=3GB` (75%), `work_mem=16MB`, `maintenance_work_mem=256MB`, `max_connections=100`, `wal_buffers=16MB`.

**TLS — self-signed, Ansible-managed.** 4096-bit RSA, 825-day cert. SAN covers `inventory_hostname`, `inventory_hostname.niflheim.xiiisins.com`, and the eth0 IP. `sslmode=require` is the practical client posture; `sslmode=verify-full` works once the cert ships to the client trust store. Long-term: cert-manager via ESO push to LXC.

**Authentication.**
- Local Unix socket: peer for `postgres` superuser (so `sudo -iu postgres psql` works without a password). scram-sha-256 for everything else.
- Network: `hostssl` only, scram-sha-256. Allowed source CIDRs in `postgres_allowed_cidrs`: VLAN 11 (asgard LXCs / Teamspeak future), VLAN 21 (asgard K3s nodes / Authentik via node NAT), VLAN 31 (jotunheim K3s, future), MacBook `/32`.
- Two SUPERUSER management roles: `admin` (hand-on-keyboard) and `ansible` (playbook / AWX automation). Passwords sourced from HashiCorp Vault via `community.hashi_vault` (`secret/ansible/postgres/admin-password`, `secret/ansible/postgres/ansible-password`). The Unix-side `postgres` superuser has no password — peer auth only, no network logins.

**WAL / replication readiness from day 1.** `wal_level=replica`, `max_wal_senders=10`, `max_replication_slots=10`, `hot_standby=on`, `archive_mode=on` with `archive_command='/bin/true'` (no-op placeholder). All four are restart-required to change, so they're set right from the start — adding 1131/1132 later doesn't require a config thrash + restart. When real WAL archiving is wanted, flip `archive_command` to the actual destination.

**Per-service DB provisioning.** Driven declaratively by `postgres_databases` in `inventory/group_vars/postgres_hosts.yml`. Each entry: `{name, owner, password_vault_path}`. Role iterates: creates the owning role (LOGIN only — not SUPERUSER, scoped to its DB), creates the DB owned by that role using `template0` + explicit encoding/collation pinned to `baseline_locale`. New consumer = new group_var entry + re-run playbook. No role editing required.

**Database deletion is deliberately NOT declarative** — removing an entry from `postgres_databases` does NOT drop the DB. Drop is a deliberate manual operation (`psql -c 'DROP DATABASE x'`). Group_var typos must not be able to drop data.

**Backups.** PBS captures the LXC filesystem (including data dir); restore is "PG replays WAL on startup." Acceptable initial posture. `pg_basebackup` to NFS (Munin) as a future enhancement for the canonical PG hot-backup pattern.

**Cluster build sequence.** Standalone-now / clustered-later, intentionally. The architectural rule "PostgreSQL LXC cluster only" is the *target* state, not the only valid intermediate. Fulla deployed first; cluster expansion (Vör, Idunn, streaming replication, HAProxy VIP frontend at 10.0.10.210) deferred until post-Authentik so clustering is validated against a real consumer rather than synthetic load. Authentik points at `fulla.niflheim.xiiisins.com` direct until the VIP exists, then re-points at `10.0.10.210` — single config flip.

**LXC features.** Unprivileged, `nesting=true` (systemd 257 on Debian 13). 2 vCPU / 4 GB RAM / 16 GB disk on Skuld.

### Asgard K3s cluster

Production cluster — core infrastructure (Vault, MetalLB, etc.), automation (AWX, Tofu Controller), and services whose absence either cascades into other failures or blocks recovery. Resiliency > simplicity.

**Status (2026-05-17 evening):** Core infrastructure ✅ running and stable. Cluster was fully torn down and rebuilt on 2026-05-17 morning as a deliberate validation exercise — see incident log. Rebuild confirmed end-to-end IaC works (Terraform → Ansible → Flux) and surfaced 9+ structural gaps that have since been closed. **Authentik + hand-rolled Redis deployed 2026-05-17 evening** — see incident log entry. Traefik + cert-manager is the next forward step, followed by AWX/Tofu Controller, then core services.

**Core infrastructure (cascade failure if down):**

| Service | Replicas | Status |
|---------|----------|--------|
| Vault | 3 (Raft HA) | ✅ Running, AWS KMS auto-unseal. K8s + AppRole auth + KV engine + test entry in Terraform (`terraform/vault/`). Re-init recovery procedure validated 2026-05-17. |
| Authentik server | 3 | ✅ Deployed 2026-05-17. Chart 2026.2.3. LoadBalancer at `10.0.20.12` (`authentik.niflheim.xiiisins.com`). External Postgres pointed at Fulla. Day-1 blueprints in Git (niflheim brand + personal admin user). |
| Authentik worker | 1 | ✅ Migrations + blueprint reconciliation run from here. |
| Redis | 1 | ✅ Hand-rolled StatefulSet (`redis:7-alpine`, AOF persistence, 1Gi iSCSI PVC). NOT the Bitnami sub-chart — the 2026.x Authentik chart dropped its bundled Redis. |
| MetalLB | DaemonSet | ✅ L2 working end-to-end. VIP reachable from outside the cluster. Required nodeSelectors, Calico autodetection pin, rp_filter loose, route_localnet=1, and VLAN 20 source-based policy routing — all IaC'd. |
| Synology CSI (core) | 1 | ✅ iSCSI, synology-csi-iscsi-retain. SealedSecret split into `synology-csi-config/` Kustomization. |
| External Secrets Operator | 1 | ✅ ClusterSecretStore `vault` Ready. Authentik secrets in `secret/k8s/authentik/*` synced via ESO. |
| Sealed Secrets | 1 | ✅ Master keys backed up to 1Password as of 2026-05-17. |
| tigera-operator (Calico) | 1 | ✅ Fixed 2026-05-15 via MTU explicit workaround (upstream issue #7851) |
| Traefik | 1+ | 🔲 — ingress controller, paired with cert-manager. Next forward step. Will re-expose Authentik via TLS. |
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
| Göndul | 2001 | Verd | `10.0.21.11` | K3s CP | 2vCPU/4GB/10GB |
| Hlökk | 2002 | Verd | `10.0.21.12` | K3s CP | 2vCPU/4GB/10GB |
| Sigrún | 2003 | Skuld | `10.0.21.13` | K3s CP | 2vCPU/4GB/10GB |
| Einherjar-urd | 2101 | Urd | `10.0.21.21` | K3s Worker | 2vCPU/4GB/15GB |
| Einherjar-verd | 2102 | Verd | `10.0.21.22` | K3s Worker | 2vCPU/4GB/15GB |
| Einherjar-skuld | 2103 | Skuld | `10.0.21.23` | K3s Worker | 2vCPU/4GB/15GB |

CP cpu/memory parameterized per-node in `locals.control_planes` map (`terraform/proxmox/asgard-k3s/main.tf`). All three CPs sized identically — failover symmetry requires it (same rule as PG nodes). Bumped 1vCPU/2GB → 2vCPU/4GB on 2026-05-17 evening when the Authentik deploy revealed hlokk and sigrun couldn't handle the migration+blueprint burst. **CPs will be tainted `node-role.kubernetes.io/control-plane:NoSchedule` in Phase 4a (scheduled 2026-05-18)** — workload pods cannot land on them under any condition once that lands. The 4 GiB sizing is correct *with* the taint: control-plane working set is ~1.5-2 GiB, the rest is bursty kernel + buff/cache headroom. Without the taint, 4 GiB would be the bare-minimum-and-things-still-break number (tonight proved that). Promote sizing into per-node overrides only when deliberate per-CP tuning is needed.

**Workers have dual NICs:**
- eth0: VLAN 21 (K3s node traffic)
- eth1: VLAN 20 (MetalLB L2, IPs 10.0.20.201/202/203)
- ⚠️ The second NIC is a known landmine — see incident log. Required IaC: Calico autodetection pin (`cidrs: ["10.0.21.0/24"]`), rp_filter loose mode, route_localnet=1, and VLAN 20 source-based policy routing. All four in `roles/k3s/tasks/network.yml`.

**K3s install (Ansible `k3s` role — fully IaC):**
- Role task order: `prerequisites.yml` → `network.yml` (sysctls + VLAN 20 policy routing) → `detect-state.yml` (skip-install gate) → `install.yml` → `calico.yml` (init node only).
- `prerequisites.yml` — Rancher k3s-selinux repo, `iscsi-initiator-utils` + `iscsid`, `br_netfilter`/`overlay` modules (loaded + persisted), `ip_forward=1`, bridge-nf sysctls, swap off, `firewalld` disabled.
- `network.yml` — sysctls `rp_filter=2` / `route_localnet=1`, plus the `vlan20-policy-routing.service` systemd unit on workers (OS-independent, pure ip(8) + systemd).
- `detect-state.yml` — sets `k3s_already_healthy` if `systemctl is-active k3s == 'active'` AND the node is `Ready` in the cluster. When true, `install.yml` and `calico.yml` are skipped. Required for idempotent re-runs; without it, the restart-k3s handler can fire on a healthy CP and trigger duplicate-join failure.
- `install.yml` — binary from GitHub (`k3s_version`, currently `v1.33.1+k3s1`); bootstrap order init-node (`--cluster-init`) → joining CPs → workers, gated by `wait_for`/node-count checks; token slurped from init node and distributed; kubeconfig fetched to `~/.kube/niflheim-asgard.yaml`.
- Config templates: `config-init.j2` / `config-server.j2` (CPs — disable traefik/servicelb/local-storage, `flannel-backend: none`, `disable-network-policy: true`, cluster/service CIDRs, TLS SANs, `selinux: true`) / `config-agent.j2` (workers — minimal: server + token + selinux).
- CP rebuild scenario: override `k3s_init_node` if rebuilding the default init node (`-e k3s_init_node=hlokk`), and `kubectl delete node <name>` first to remove the stale etcd member. See Known gotchas.
- No node taints or labels are set — CP taint is a manual pending task.

**Calico CNI — NOT Flux-managed.** Installed as a K3s addon via `ansible/roles/k3s/tasks/calico.yml` (runs only on the init node): Tigera operator manifest + an `Installation` CR templated from `calico-installation.yaml.j2` to `/var/lib/rancher/k3s/server/manifests/`. Key config: `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]` (pins overlay to VLAN 21), `mtu: 1450` (workaround for projectcalico/calico#7851 — operator can't read `/var/lib/calico/mtu` under SELinux), `encapsulation: VXLANCrossSubnet`, pod CIDR `10.42.0.0/16`, `calico_version` currently `v3.29.3`. The K3s addon controller *merges* the CR — removed fields can persist; verify after changes.

**Flux Kustomization structure:**
- `infrastructure` — installs HelmReleases (sealed-secrets, synology-csi, vault, external-secrets, metallb). `interval: 10m`, `prune: true`, sourceRef `GitRepository/flux-system`. No `wait`/`timeout` set.
- `infrastructure-config` — configures ESO (ClusterSecretStore), `dependsOn: [infrastructure]`.
- `metallb-config` — configures MetalLB (IPAddressPool, L2Advertisement), `dependsOn: [infrastructure]`. Split from `infrastructure-config` after the 2026-05-14 incident where an ESO webhook failure blocked MetalLB config reconcile (shared failure domain).
- `vault-config` — vault-unseal SealedSecret, `dependsOn: [infrastructure]`. Split from `infrastructure/` on 2026-05-17 — SealedSecret resources can't live alongside the sealed-secrets HelmRelease (CRD doesn't exist at dry-run time).
- `synology-csi-config` — synology-csi SealedSecret, `dependsOn: [infrastructure]`. Same reason as vault-config.

The per-component-config pattern is now the standard: every CRD-dependent resource gets its own `<component>-config/` Kustomization that `dependsOn: infrastructure`. No more bundles sharing failure domains.

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
- **Ansible** — OS/node-level: baseline, hardening (security sysctls, SSH, SELinux, module blocklist), K3s *install* + prerequisites + network plumbing (sysctls + VLAN 20 policy routing) + the Calico addon manifest. Playbook `asgard-k3s.yml` runs roles `baseline → k3s → hardening` against the `asgard_k3s` group.
- **Flux** — in-cluster workloads: everything in `k8s/`.
- **Docs (this file + `docs/teardown-rebuild.md`)** — KPN Experia Box config, rebuild runbook, anything else without a useful API.

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
| 5c — Asgard K3s | ✅ | VMs ✅, K3s ✅, Flux ✅, Sealed Secrets ✅, Synology CSI ✅, Vault ✅, ESO ✅, MetalLB ✅, tigera-operator ✅. Full teardown+rebuild validation 2026-05-17 — see incident log. |
| 5d — KPN DMZ | ✅ | DMZ → UCG-Ultra WAN (IPv4 + IPv6) |
| 5e — Authentik + Redis | ✅ | Deployed 2026-05-17 evening. Bare LoadBalancer at `10.0.20.12`, plaintext HTTP. Traefik + cert-manager (next phase) will put it behind TLS. Then unblocks Tailscale LXCs. |
| 4a — CP taint (retro-sequenced) | 🔲 | `node-role.kubernetes.io/control-plane=:NoSchedule` via `config-init.j2`/`config-server.j2`. Closes the CP-as-workload-target gap that bit during Authentik deploy. Scheduled 2026-05-18 before Traefik. Side effect: evicts Synology CSI node-plugin from CPs, fixes the iSCSI cross-node session fight gotcha. Numbered 4a because architecturally it should have been done before any K8s workload deploys; lands now as the highest-priority correction. |
| 5e.1 — Traefik + cert-manager | 🔲 | Ingress + Let's Encrypt DNS-01. Re-expose Authentik behind TLS at `authentik.niflheim.xiiisins.com`. First-time validation of the cert-manager+Cloudflare-DNS-01 path. |
| 5e.2 — Tailscale OIDC blueprints + LXCs | 🔲 | Authentik OIDC provider+application blueprints for Tailscale, then LXCs 1113/1114/1115. Needs HTTPS Authentik (5e.1) for production-grade OIDC. |
| 5f — Factorio LXC | ✅ | Deployed 2026-05-16 — Terraform + Ansible end-to-end |
| 5g — PostgreSQL + Teamspeak LXCs | 🟡 | Fulla (1130) ✅ deployed 2026-05-17 standalone. Vör/Idunn deferred until post-Authentik (cluster expansion validates against real consumer). Teamspeak pending. |
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
| Göndul placement | Verd (2vCPU/4GB), moved 2026-05-17 | Off N5095 etcd thrashing. Bumped from 1/2 → 2/4 to absorb reconciliation load. |
| CP cpu/memory | Per-node via `locals.control_planes` map | Gondul needs more than hlokk/sigrun; map structure enables per-CP overrides cleanly |
| Per-component-config Kustomizations | `<component>-config/` next to `infrastructure/`, `dependsOn: infrastructure` | SealedSecrets and CRD-dependent resources can't live in same Kustomization as the chart that installs the CRD. Splitting per-component avoids shared failure domains too. |
| MetalLB multi-homed plumbing | rp_filter=2 + route_localnet=1 + VLAN 20 policy routing | All three required; missing any one breaks VIP reachability. Discovered iteratively; 2026-05-17 closed the last gap. |
| VLAN 20 policy routing implementation | systemd oneshot service, `ip rule` + `ip route` | OS-independent (no NetworkManager / netplan / ifcfg coupling). Pure ip(8) + systemd. |
| K3s + MetalLB sysctls location | `roles/k3s/tasks/network.yml` (not hardening) | Hardening role should be OS hardening; K3s-specific requirements belong with K3s. Refactored 2026-05-17. |
| K3s role idempotency | `detect-state.yml` skip-install on healthy nodes | Re-running play against a healthy cluster could fire restart-k3s handler → duplicate-join failure. Skip install when `systemctl is-active k3s && node is Ready`. |
| Sealed-secrets master keys | Back up to 1Password after every controller install | Loss makes every SealedSecret in Git undecryptable. Discovered 2026-05-17 when both SealedSecrets had to be re-sealed from plaintext. |
| Vault test KV entry | Managed declaratively in `terraform/vault/main.tf` | Was manual state on the old cluster; lost on rebuild. Anything Vault-side must be IaC. |
| OS-independent roles | All Ansible roles should be OS-independent where possible | Use systemd (universal) instead of nmcli/netplan/ifcfg (distro-specific). Kernel-level operations (sysctl, ip(8)) are universal too. |
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
| Service LXC naming convention | Norse, themed to function — DB → Frigg's handmaidens (Fulla/Vör/Idunn); HAProxy TBD; Factorio kept literal | Extends host-naming pattern (Norns → hypervisors, Valkyries → CP) to service LXCs. Single-purpose oddballs like Factorio stay literal — not every LXC needs a Norse name. |
| Cluster naming meta-principle | Primary defines the theme; replicas are a logical expansion within it | DB = library → librarian-keepers. New service cluster: pick primary by archetype, replicas by the natural cohort around it. |
| PG storage backend | Local LVM-thin only (not NFS) | NFS fsync semantics vary; WAL latency over 1 GbE kills throughput; Synology DSM NFS not on any validated list. Data scale fits in LVM-thin disk. |
| PG cluster build sequence | Standalone Fulla first; Vör/Idunn + HAProxy post-Authentik | Cluster expansion validated against real consumer (Authentik DB) rather than synthetic load. Authentik points at fulla direct until VIP exists; single config flip later. |
| PG cluster sizing | Identical across nodes (cluster-wide constants, not per-node map) | Failover symmetry — asymmetric sizing means failover silently degrades. Promote sizing into the locals map only when deliberate per-node tuning is needed. |
| PG TLS | Self-signed via Ansible (4096-bit RSA, 825d, SAN covers host/FQDN/IP) | In-LAN doesn't excuse cleartext at senior posture. cert-manager via ESO push to LXC is the long-term replacement. |
| PG management user split | Two SUPERUSER roles: admin (hand-on-keyboard), ansible (automation). Service DB roles are LOGIN only | Operational distinction (who is acting) without security boundary cost. Tighten later if any consumer warrants least-privilege. |
| PG replication-ready config day 1 | `wal_level=replica`, `max_wal_senders=10`, `archive_mode=on` with no-op `archive_command` even on a single node | These settings require a restart to change. Setting them right from the start avoids a config-thrash + restart later when 1131/1132 join. |
| PG per-service DB provisioning | Declarative list (`postgres_databases`) in group_vars; role iterates idempotently | New consumer = group_var entry + replay. No role editing. AWX automation later. |
| PG database deletion | Manual via `psql -c 'DROP DATABASE'` — NOT declarative from group_vars | Removing an entry from `postgres_databases` is config drift, not destructive intent. Drop must be explicit; typos must not be able to drop data. |
| Baseline role scope | Timezone, locale, minimal-template gap-fillers (sudo, acl, tzdata, gnupg, ca-certificates), **DNS resolver config** (`/etc/resolv.conf` + cloud-init `manage_resolv_conf: false` drop-in) live here, not in every consuming role | Roles authored against minimal Debian rediscover the same gaps. Baseline owns the class. DNS added 2026-05-17 evening after cloud-init's `1.1.1.1` fallback poisoned internal-zone resolution during Authentik deploy. List grows over time; that's expected. |
| K3s CP sizing | Identical across nodes (cluster-wide constants, not per-node map) | Failover symmetry — same rule as PG nodes. Asymmetric sizing means failover silently degrades. All three CPs 2vCPU/4GB as of 2026-05-17 evening. Promote sizing into the locals map only when deliberate per-CP tuning is needed. |
| K3s CP workload isolation | `node-role.kubernetes.io/control-plane=:NoSchedule` taint on all three CPs (Phase 4a — pending, 2026-05-18) | CPs will run only control-plane components + DaemonSets that explicitly tolerate the taint (Calico-node, kube-proxy). Workload pods cannot land on CPs under any condition — pressure can't reach etcd via fsync contention. Authentik deploy 2026-05-17 evening proved untainted-CP-as-fallback-workload-target is a cascade-failure path. With taint, 4 GiB is the correct CP size; without it, 4 GiB is the floor and bursts still hurt. Side effect: Synology CSI node-plugin evicted from CPs (the plugin doesn't tolerate this taint by default) → closes the CP-grabs-worker-LUN gotcha class. |
| DNS fallback resolver | Never a public resolver. UCG → AdGuard VIP only, optional fallback peer AdGuard | Public resolvers (Cloudflare `1.1.1.1`, Google `8.8.8.8`) return NXDOMAIN for internal zones. Glibc and CoreDNS treat NXDOMAIN as authoritative and cache it. Discovered 2026-05-17 during Authentik deploy — Authentik couldn't reach `fulla.niflheim.xiiisins.com` because CoreDNS had cached a stale NXDOMAIN from a brief moment where the K3s node had queried `1.1.1.1` via secondary fallback. |
| Authentik Redis | Hand-rolled StatefulSet (`redis:7-alpine`), not Bitnami sub-chart | Chart 2026.x dropped its bundled Redis. Hand-rolled is ~50 lines of YAML — simpler than adopting Bitnami's metrics/sentinel/HPA scaffolding for a single-replica homelab Redis. Single replica, AOF persistence on iSCSI. If a future service wants its own Redis (e.g. AWX fact caching), it gets its own — namespace-bundled. |
| Authentik service config injection | All env vars via ExternalSecret template, not chart `values:` block | The chart exposes config via both paths; env vars win silently. Setting only the values block produces ghost-localhost-fallback (Authentik tried `127.0.0.1:5432` for an hour before this was diagnosed 2026-05-17). Going forward: every service config knob settable via env goes through ExternalSecret. |
| Authentik day-1 GitOps | Blueprints in Git from day 1, no UI configuration ever | Single source of truth. Cluster rebuild reconstitutes identity. Brand + personal user as the first two blueprints; subsequent OIDC providers (Tailscale, Grafana, etc.) follow same pattern. Branding assets follow the same rule (populator init container reads from ConfigMap today, S3-compatible tomorrow — pattern preserved). |
| Sub-kustomization per component | Every component is a self-contained directory with its own `kustomization.yaml`; parent `infrastructure/kustomization.yaml` only references directories | Forced by Authentik's `configMapGenerator` for blueprints (Kustomize generators can't flow through flat-file-reference parents). Migrated all components for consistency. Closed the design doc's open nested-vs-flat question 2026-05-17 evening. |

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

### 2026-05-17 — Asgard rebuild + 9 architectural findings

Deliberate full teardown + rebuild of the asgard K3s cluster to validate end-to-end IaC. Triggered by the realization that the previous cluster had accumulated manual state during incident response (sysctls, iptables-shaped rules, imperative Vault config) that wasn't captured in code. Rebuild proves the IaC works AND surfaces what's missing.

Renamed `must-run` → `asgard` and `can-run` → `jotunheim` in the same arc — see `docs/teardown-rebuild.md` for the full runbook.

**Process:** state capture → per-tier rename commits (5) → graceful teardown (Vault drain, terraform destroy) → Synology LUN cleanup → terraform apply → ansible play → Flux bootstrap → Vault re-init → workload validation. Total wall-clock ~5 hours.

**Findings, all closed during the same session:**

1. **SealedSecret CRD timing race.** Putting SealedSecret resources alongside the sealed-secrets HelmRelease in the `infrastructure/` Kustomization failed Flux dry-run (CRD doesn't exist at validation time). Fix: split `vault-config/` and `synology-csi-config/` Kustomizations next to the existing `metallb-config/`, all `dependsOn: infrastructure`. Per-component-config pattern formalized.

2. **Sealed-secrets master keys not backed up.** Fresh controller = fresh keypair = old SealedSecrets undecryptable. Both had to be re-sealed from plaintext (vault-unseal from Ansible Vault, synology-csi from 1Password). Master keys now backed up to 1Password.

3. **Vault test KV entry was manual state.** The `secret/ansible/test/hello` entry used by `playbooks/test-vault-lookup.yml` was created by `vault kv put` originally and never captured. Now a `vault_kv_secret_v2` resource in Terraform.

4. **Vault Raft followers don't auto-join cleanly.** vault-1 and vault-2 sat sealed after vault-0 init. Manual `vault operator raft join` on each. Recovery procedure documented.

5. **MetalLB `route_localnet=1` missing.** VIPs unreachable from outside the cluster — kernel dropped packets destined for the VIP because MetalLB only ARPs, doesn't bind. Manual `sysctl` got the cluster working; refactor landed it in `roles/k3s/tasks/network.yml`.

6. **VLAN 20 source-based policy routing missing.** Replies from MetalLB VIPs went out via the default eth0 route (asymmetric) — UCG's stateful firewall dropped them. Manual `ip rule` / `ip route` got it working; landed as `vlan20-policy-routing.service` systemd unit in `network.yml`. OS-independent.

7. **CP rebuild → "duplicate node name."** Destroying/recreating gondul VM and re-running the playbook failed with etcd join error. Fix: `kubectl delete node gondul` from a surviving CP. The K3s native node-delete handler evicts the stale etcd member, allowing the rebuilt VM to join.

8. **CP rebuild of default init node needs override.** If you destroy the node that's `k3s_init_node` (default `gondul`), the role would `--cluster-init` it as a fresh cluster. Override: `-e k3s_init_node=hlokk`.

9. **K3s role install lacks idempotency guard.** Re-running the play against a healthy CP could fire the restart-k3s handler, which causes K3s to re-attempt join, which fails with "duplicate node name." Fix: new `roles/k3s/tasks/detect-state.yml` sets `k3s_already_healthy` if `systemctl is-active k3s` AND node is `Ready`. `install.yml` and `calico.yml` skip when true. **Deeper bug remains:** the restart handler itself isn't safe for existing CP members — a genuine config template change would still trigger duplicate-join. Tracked as separate item.

**Plus:** Vault init can leave a stuck partial state (recovery: delete StatefulSet + PVCs + reconcile). Flux deploy key not in IaC (`flux bootstrap github` re-uses if present, regenerates if not).

**Bonus during the session:** moved Göndul from Urd to Verd (deferred since 2026-05-14), bumped to 2vCPU/4GB after OOM during reconciliation churn. Parameterized CP cpu/memory in the Terraform locals map. Refactored `roles/k3s/tasks/` to split `network.yml` (sysctls + policy routing) from the install path, and `detect-state.yml` for idempotency.

**Resolution:** 9 findings closed in IaC (commits visible in `git log --oneline -20`). Cluster healthy, all workloads recovered, doc sweep on 2026-05-17 captured everything into CLAUDE.md and this doc.

**Root-cause pattern:** every finding was either (a) accumulated manual state that survived in the live cluster but not in Git, or (b) a structural gap that only surfaces during cluster-from-zero (CRD timing, idempotency, init node identity). The pre-rebuild cluster ran fine because none of the gaps fired in steady state. Lesson: deliberate teardown is the only honest test of IaC completeness. Worth doing periodically.

### 2026-05-17 evening — Authentik + Redis deploy

Phase 5e. First real K8s workload consuming the post-rebuild infrastructure. Took ~5 hours start to functional; ~half of that was Authentik-specific learning, the other half was infrastructure gaps the deploy surfaced.

**What landed in IaC:**
- `k8s/asgard/infrastructure/authentik/` — namespace, helmrepository (`charts.goauthentik.io`, pinned `2026.2.3`), externalsecret (5 keys from `secret/k8s/authentik/*` + 1 for blueprint user), helmrelease (3 server, 1 worker, external PG pointed at Fulla, embedded media volume as emptyDir, branding via configMap+populator init pattern preserved for future S3 swap), redis.yaml (hand-rolled StatefulSet, AOF persistence, 1Gi iSCSI PVC, chown init container), kustomization with `configMapGenerator` for blueprints.
- `k8s/asgard/infrastructure/authentik/blueprints/00-brand.yaml` — demotes shipped `authentik-default` brand, claims default for `authentik.niflheim.xiiisins.com`, wires the three default flows.
- `k8s/asgard/infrastructure/authentik/blueprints/01-users.yaml` — personal admin user, password sourced from Vault via `!Env AUTHENTIK_BLUEPRINT_USER_PASSWORD`.
- `ansible/roles/baseline/` — adds `/etc/resolv.conf` management + cloud-init `manage_resolv_conf: false` drop-in. Closes the DNS-fallback poisoning class.
- `terraform/proxmox/asgard-k3s/main.tf` — all three CPs (Göndul/Hlökk/Sigrún) bumped 1vCPU/2GB → 2vCPU/4GB symmetrically.
- `k8s/asgard/infrastructure/sealed-secrets/kustomization.yaml` — added `namespace.yaml` to resources (was missing post-migration, parent reconcile broke until found).
- `k8s/asgard/infrastructure/kustomization.yaml` — parent migrated to sub-kustomization-only references.

**Findings, in rough chronological order of discovery:**

1. **Parent kustomization migration gap.** Migrating the parent `infrastructure/kustomization.yaml` to reference sub-directories (forced by Authentik's `configMapGenerator`) surfaced that `sealed-secrets/kustomization.yaml` was missing `namespace.yaml` from its resources. Worked previously because of accumulated state — `install.createNamespace: true` had created the ns as a side-effect on the original deploy. Same class as the asgard rebuild's findings.

2. **`prometheus.serviceMonitor` deprecated key** in chart 2026.2.3. Fail-fast deprecation guard in `templates/deprectations.yaml`. Lesson: when you set a values block to its default value for "documentation," you risk the key name moving and breaking install. Going forward, set only non-defaults.

3. **Authentik PG client connects to localhost when `authentik.postgresql.host` is set in values block but `AUTHENTIK_POSTGRESQL__HOST` env is unset.** The env var wins; the values block doesn't backfill it. Set all PG connection params explicitly in the ExternalSecret. Same rule for Redis. (CLAUDE.md gotcha.)

4. **CoreDNS cached NXDOMAIN poisoning.** Authentik worker couldn't resolve `fulla.niflheim.xiiisins.com` despite the same name resolving from `kubectl run dnstest` busybox. Traced: cloud-init had left `nameserver 1.1.1.1` as secondary on every K3s node; during a brief primary-resolver hiccup, libresolv fell back to Cloudflare, which returned NXDOMAIN for the internal zone; CoreDNS cached the NXDOMAIN and served it to long-lived consumers for the 30s TTL — but ongoing fallbacks kept the cache poisoned indefinitely. Fixed at the right layer: `roles/baseline` now manages resolv.conf via Ansible (cloud-init's `manage_resolv_conf: false`), only UCG as nameserver, no public-resolver fallback. CoreDNS restart purged the cache. (CLAUDE.md gotcha.)

5. **Postgres `hostssl`-only rejects plaintext with "no pg_hba.conf entry" — same error as CIDR mismatch.** The "no encryption" suffix is the diagnostic clue. Authentik's libpq default `sslmode=prefer` was being clobbered by something to plaintext. Fix: explicit `AUTHENTIK_POSTGRESQL__SSLMODE=require` in ExternalSecret. (CLAUDE.md gotcha.)

6. **Authentik brand `default: true` is mutually exclusive.** Blueprint must demote shipped `authentik-default` brand first. Single-entry blueprint fails with `Only a single brand can be set as default.` Two-entry blueprint (demote + claim) works, applies in document order. (CLAUDE.md gotcha.)

7. **`authentik_tenants.tenant` is wrong for single-instance deploys.** Was including the model entry by analogy with the brand. Tenants are for multi-schema Postgres isolation; on a single-tenant deploy the brand is the only object you need. Removed the tenant entry, renamed the blueprint file to `00-brand.yaml`.

8. **All three CPs under-spec'd.** Earlier doc claimed gondul was bumped during the rebuild; Terraform was actually unchanged. Authentik's first-deploy burst — Django migrations + blueprint reconciliation + 3 server pods starting + ESO sync — pushed hlokk into kernel-panic-adjacent I/O thrash, which dragged etcd quorum down via fsync contention, which made kubectl unresponsive cluster-wide. Recovery via systemd-driven k3s restart on the failed CP. **Fixed properly:** all three CPs bumped to 2vCPU/4GB symmetrically via Terraform locals, sequential reboots restored quorum cleanly.

9. **Workload concentration on einherjar-skuld.** 5 of 7 stateful PVCs landed on `.23` due to iSCSI session pinning persisting across rebuilds. Load 1.84 vs 0.34 on peers. Structural issue, not hot bug — flagged for the pending CP-taint work (gets CSI node-plugin off CPs) and possibly explicit StatefulSet pod anti-affinity. (CLAUDE.md gotcha.)

10. **HelmRelease remediation thrash masks the real failure.** Default `install.remediation.retries: 3` runs `helm uninstall` between retries, and ESO-managed Secrets' finalizers leave the uninstall stuck on Secret-termination, so by the time you go look at the failure, the original error is gone and the visible state is the cleanup's failure. Set `retries: -1` and `timeout: 15m` during first-deploy debugging. Restored to `retries: 3` after success. (CLAUDE.md gotcha.)

11. **`getaddrinfo` failures in Python on otherwise-resolving hosts** look identical to NSS library issues, broken `resolv.conf`, or glibc dual-stack bugs. The actual diagnostic that cut through it: raw DNS over UDP to CoreDNS at `10.43.0.10` showing `rcode=NXDOMAIN` for one name and `NOERROR` for another from the same pod. Once the answer was wire-level, the search narrowed to CoreDNS upstream rather than client-side. Worth documenting the technique — fastest path to root cause when DNS feels weird.

**Plus:** Sub-kustomization-per-component pattern formalized (closes the open design doc question). The branding-via-populator-init-container pattern was preserved as a forward-fit even though branding is currently empty — flipping to S3-sourced is a one-edit change later.

**Validation:** Phase 1 (PG DB provisioning) validated the `postgres_databases` per-service iteration pattern against a real consumer. Phase 3 deploy validated three classes of Postgres pattern (per-service DB, TLS-required, scram-sha-256) through Authentik's PG client. Standalone-Fulla-first was the right call — three PG-related findings surfaced cleanly through a real consumer rather than synthetic load.

**Resolution:** Authentik functional, niflheim brand is default, personal admin user exists with admin group membership. Bare-LB at `10.0.20.12`. Traefik + cert-manager next (Phase 5e.1, scheduled 2026-05-18) to put it behind HTTPS.

**Root-cause patterns:**
- Accumulated state in the live cluster but not in Git: sealed-secrets namespace, cloud-init's `1.1.1.1` fallback, undersized CPs. Same pattern as the morning's asgard rebuild — the cluster ran fine in steady state, then a new workload's burst load fired the latent gaps. Real consumers beat synthetic load every time.
- Chart values blocks vs env vars: when both exist, env vars win silently. Document-by-default (the chart's values.yaml) lies about which knob is actually live. ExternalSecret env vars are the authoritative path for service config going forward.
- DNS NXDOMAIN caching: a single bad answer from a fallback resolver poisons a service for the cache TTL. The fix is architectural (no public fallbacks for hosts that need internal resolution), not tactical (cache-flush + hope).

---

## Open questions / pending tasks

**High priority — forward path:**
- [x] **Deploy Authentik + Redis** in asgard K3s. ✅ Deployed 2026-05-17 evening. See incident log entry for the deploy story + the gotchas closed in IaC. Bare-LB / plaintext-HTTP for now; HTTPS lands in Phase 5e.1.
- [ ] **Phase 4a — CP workload isolation via taint** (scheduled 2026-05-18, before Traefik). Add `node-role.kubernetes.io/control-plane=:NoSchedule` to K3s config templates (`config-init.j2` + `config-server.j2`). Re-run k3s role against CPs sequentially. Workaround for the known restart-handler-unsafe gotcha: `kubectl delete node <name>` before each CP's restart, or one-at-a-time letting etcd absorb the brief absence. Verify Synology CSI node-plugin gets evicted from CPs (desired outcome); Calico-node and kube-proxy remain (they tolerate the taint by default). 4 GiB CP sizing is correct *with* the taint applied — do not bump to 6 GiB.
- [ ] **Phase 5e.1 — Deploy Traefik + cert-manager** (after 4a). Let's Encrypt DNS-01 via Cloudflare API. Re-expose Authentik at `authentik.niflheim.xiiisins.com` over HTTPS. First real cert-manager exercise.
- [ ] **Phase 5e.2 — Tailscale OIDC blueprints + LXCs** (after 5e.1). Provider + application blueprints in Git (continuing the "blueprints from day 1, no UI config" rule), then LXCs 1113/1114/1115. Needs HTTPS Authentik for production-grade OIDC.
- [ ] **Create 1Password "Homelab" vault** if not already done. Migrate existing scattered homelab credentials in. Authentik bootstrap creds (akadmin pw + API token + personal user pw) added 2026-05-17.

**Service backlog (in revised LXC order):**
- [x] **Factorio LXC** (1120, Urd) — ✅ Deployed 2026-05-16. UDP 34197 + TCP 22022 port-forwards active.
- [x] **PostgreSQL Fulla** (1130, Skuld) — ✅ Deployed 2026-05-17. Standalone PG 17 + TLS + management users + DB provisioning machinery. Per-service DB provisioning validated by Authentik (2026-05-17 evening). Cluster expansion to Vör/Idunn (and HAProxy VIP 10.0.10.210) deferred until further consumers exist.
- [ ] **Teamspeak LXC** (1121, Verd) — pointed at Fulla (later at HAProxy VIP once 1133/1134/1135 are up).
- [ ] **Tailscale LXCs** (1113/1114/1115) — Phase 5e.2.
- [ ] **HAProxy** (1133/1134/1135) — PostgreSQL frontend, after PG cluster.
- [ ] **Zabbix LXC** (1102, Skuld).
- [ ] **Jellyfin LXC** (privileged, Urd, QuickSync).

**Vault-for-Ansible migration (in progress — pattern built D1, secrets migrating individually):**

Per the bootstrap-vs-runtime architecture: bootstrap secrets stay in Ansible Vault permanently (narrow scope: `k3s_token`, RHEL keys, SSH pubkeys for ansible/breakglass users — needed before HashiCorp Vault is reachable). Runtime secrets — those needed *after* a node is up — move to HashiCorp Vault.

- [x] **Build the Vault-for-Ansible lookup pattern** (✅ D1, 2026-05-16). AppRole + `ansible` policy + `ansible-local`/`ansible-awx` roles in `terraform/vault/`. `community.hashi_vault` collection + lookup pattern in `roles/sftpgo/defaults/main.yml`. AppRole bootstrap runbook in this doc.
- [x] **Migrate `sftpgo_admin_password`** → `secret/ansible/sftpgo/admin-password` (✅ D1, proof-of-pattern). Removed from `group_vars/all/vault.yml`.
- [x] **Provision Authentik database via Ansible** → `secret/ansible/postgres/authentik-password` (✅ 2026-05-17 evening). Proves the `postgres_databases` declarative-iteration pattern against a real consumer.
- [ ] **Migrate `vault_factorio_operator_password`** → `secret/ansible/factorio/operator-password`. Drop the `vault_` prefix at migration time (the prefix was aspirational; the var now actually comes from HashiCorp Vault).
- [ ] **Fix sftpgo role check-mode compatibility.** `roles/sftpgo/tasks/bootstrap.yml` does a POST to fetch an admin JWT, then references `result.json.access_token`. In `--check` mode `uri:` skips POSTs by default, returning a stub dict without `.json` — breaks downstream tasks. Add `check_mode: false` to the token-fetch and any downstream state-reading API calls.

**Reprovision (deliberate, on a healthy cluster):**
- [x] Move Göndul from Urd → Verd (✅ 2026-05-17 morning during asgard rebuild).
- [x] CP VM sizing across all three CPs (✅ 2026-05-17 evening — all three Göndul/Hlökk/Sigrún at 2vCPU/4GB. Forced by Authentik deploy; closes the validation question. Stays at 4 GiB with the Phase 4a taint applied — see High priority section.).

**K3s role correctness (from 2026-05-17 rebuild):**
- [ ] **Make restart-k3s handler safe for existing CP members.** Current handler causes "duplicate node name" if a genuine config template change triggers a restart on a healthy CP. The 2026-05-17 detect-state.yml fix only skips install on healthy nodes; it doesn't address the handler. Options: (a) `kubectl delete node` before restart, (b) `systemctl reload` instead of `restart` where K3s supports it, (c) conditional handler based on existing-member vs fresh-node state.
- [ ] **Backup Flux deploy key.** `flux bootstrap github` reissues idempotently if the deploy key still exists in repo settings, but it's not captured anywhere. Consider an out-of-band backup as a Secret manifest in 1Password, or document the regenerate-on-rebuild flow if leaving as-is.
- [x] **Decide nested-vs-flat Kustomization structure** in `k8s/asgard/infrastructure/` (✅ 2026-05-17 evening — sub-kustomizations per component; forced by Authentik's `configMapGenerator`. Closed during the Authentik deploy. Going-forward standard: every component is a self-contained directory with its own `kustomization.yaml`; parent `infrastructure/kustomization.yaml` only references directories.)

**Authentik-specific follow-ups:**
- [ ] **`/media` PVC for user avatars.** Currently `emptyDir` — pod restart loses uploaded avatars. Pattern when needed: NFS volume from Munin (acceptable here, unlike PG, because `/media` is small reads with rare writes), or revisit the Synology-CSI-RWO-with-spread-affinity question for genuinely multi-writer needs. Defer until users actually upload avatars and complain.
- [ ] **Branding ConfigMap → S3 swap path.** Populator init container is already wired for the flip (busybox `cp` becomes `mc cp`). Pattern preserved as a forward-fit. Trigger: when a branding asset would exceed the 1MiB ConfigMap-per-key ceiling, OR when versioning/rotation of branding assets becomes a thing.

**Standing:**
- [ ] Pin HelmRelease chart versions + activate Renovate (at "2.0" working state). Authentik already pinned to `2026.2.3` (identity infra is too important to float).
- [ ] Document the AWS KMS key ARN.
- [ ] Cloudflare Tunnel — which services get external exposure (in addition to direct port-forwards via UCG).
- [ ] Fallback static HTML doc on Munin for core K3s recovery.
- [ ] AdGuard DNS records for new VMs/services as provisioned.
- [ ] Proxmox HA for asgard LXCs.
- [ ] Jotunheim K3s cluster.
- [ ] Confirm whether Vault's `tls_disable` posture should change — revisit at Vault hardening.
- [ ] Long-term: migrate from ESO sync-and-cache to Vault Agent / VSO runtime retrieval. Pilot on jotunheim first.
- [ ] Long-term: migrate Vault → OpenBao (~12 months out, once OpenBao has more production track record).
- [ ] **PG: `pg_basebackup` to NFS (Munin)** beyond PBS filesystem snapshot — canonical PG hot-backup pattern. Acceptable to defer; PBS-only is functional crash recovery.
- [ ] **PG cluster expansion** (Vör 1131 / Urd; Idunn 1132 / Verd; HAProxy 1133/1134/1135) — after multiple PG consumers exist to drive failover validation.

---

*This document is a living reference. Update it as decisions change during the build.*