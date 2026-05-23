# Homelab design document
*Last updated: 2026-05-22 — draft v10 (Phase 4b + einherjar-urd worker rebuild)*

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
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | Proxmox node 1 | **Urd** |
| Beelink MINI-S12 | N100 | 16 GB, 1TB disk | Proxmox node 2 | **Verd** |
| Beelink MINI-S12 | N100 | 16 GB, ~450GB LVM-thin | Proxmox node 3 | **Skuld** |
| Synology DS223J | Realtek RTD1619B | 1 GB | NAS | **Munin** |

**Storage tier (verified 2026-05-21 via `nvme id-ctrl`):**
- Urd: Lexar NM790 1TB NVMe (Gen 4, DRAM-less, HMB 3.0)
- Verd: Samsung 970 EVO 1TB NVMe (Gen 3, DRAM-equipped)
- Skuld: SK Hynix PC300 512GB NVMe (Gen 3, DRAM-equipped — `hmpre=0, hmmin=0` confirms direct DRAM, not HMB)
- For etcd fsync consistency: Verd ≈ Skuld > Urd. Urd's DRAM-less NVMe is the slowest of the three for sustained sync workloads even though it's Gen 4 (HMB adds PCIe round-trip latency per metadata update). All three are well within etcd's tolerance — much better than the original Urd mSATA.

**Hardware notes:**
- **Urd hardware migration, 2026-05-19 to 2026-05-21:** Migration trail was DeskMini JB95 → NUC7 (intermediate) → MSI Cubi, not a direct swap. The NUC7 was a brief stop that passed a 24h burn-in but proved hardware-flaky once running production Proxmox workload — crashed three times during einherjar-urd's life on it. Memory + disk were physically swapped from the NUC7 into the Cubi when replacement hardware arrived. Final hardware is the MSI Cubi (i3-1215u + 32GB DDR4 reused + 1TB Lexar NM790 NVMe). The original 2026-05-14 etcd-storm root cause (slow N5095 + slow mSATA fsync) **no longer exists**. The "never run CP on Urd" rule is retired. Phase 4b (Göndul Verd → Urd migration) applied 2026-05-22 as the deliberate realisation of this. One of the NUC7 crashes corrupted einherjar-urd's SSH hostkey files mid-write; see 2026-05-21 evening incident log entry. A separate NUC7-era partial migration left orphan LVs that blocked Phase 4b's first clone attempt on Urd — see 2026-05-22 incident log entry for the recovery.
- Urd long-term: dedicated Jellyfin LXC with Intel QuickSync passthrough. The i3-1215u's UHD Graphics is substantially stronger QSV than the N5095's UHD. Currently also running Einherjar-urd (K3s worker) and the Factorio LXC (1120).
- Göndul moved Urd → Verd on 2026-05-17 (was deferred since the 2026-05-14 incident; finally fixed during the asgard rebuild). Phase 4b (2026-05-22) returned Göndul to Urd post-hardware-refresh — back to the original 2026-05-14 design intent.
- All three CPs (Göndul/Hlökk/Sigrún) bumped 1vCPU/2GB → 2vCPU/4GB on 2026-05-17 evening during the Authentik deploy. Symmetric 2vCPU/4GB across all three is the standard.
- **CP-only workload posture (Phase 4a, applied 2026-05-21):** All three CPs tainted `node-role.kubernetes.io/control-plane=:NoSchedule`. Workload pods cannot land on them. Synology CSI node-plugin evicted from CPs as expected. K3s-shipped components (CoreDNS, metrics-server, calico-apiserver) and DaemonSets that tolerate the taint (Calico-node, kube-proxy, metallb-speaker) remain. 4 GiB is the correct CP size with this posture. **Caveat surfaced during deploy:** evicting CSI from CPs is *not* a free win — stateful pods left on a CP at taint-time hang in Terminating because kubelet can't reach the missing CSI driver. See incident log for the full picture.
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

Three-zone scheme:

| Zone | Resolver | Purpose |
|------|----------|---------|
| `xiiisins.com` (apex) | Cloudflare (public) | External / publicly-reachable services. Cloudflare-resolved. Traffic enters via Cloudflared. |
| `midgard.xiiisins.com` | AdGuard Home (internal) | Internal alias for services that ARE publicly reachable — homelab clients hit them via LAN instead of trombonning through Cloudflare. |
| `niflheim.xiiisins.com` | AdGuard Home (internal) | Internal-only services — never publicly reachable. |

Examples:
```
External (Cloudflare):
  authentik.xiiisins.com    → Cloudflared tunnel
  outline.xiiisins.com      → Cloudflared tunnel
  immich.xiiisins.com       → Cloudflared tunnel
  ts3.xiiisins.com          → Cloudflared tunnel (TCP non-HTTP)

Internal — midgard (publicly-reachable services, internal alias):
  authentik.midgard.xiiisins.com → 10.0.20.10 (Traefik VIP)
  outline.midgard.xiiisins.com   → 10.0.20.10

Internal — niflheim (internal-only):
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

TLS strategy:
- `*.niflheim.xiiisins.com` wildcard — issued in 5e.1, covers all internal-only services.
- `*.midgard.xiiisins.com` wildcard — added in 5e.2, covers internal aliases of publicly-reachable services.
- `*.xiiisins.com` apex wildcard — added in 5e.2, used on the origin side of Cloudflared (Traefik → Cloudflared origin pull). **Browser-visible cert for external traffic is Cloudflare's universal cert** — Cloudflare Tunnel terminates TLS at the edge with Cloudflare's cert (Custom SSL requires Business/Enterprise plan, which is out of scope) and re-encrypts to origin where our LE wildcard lives. This is the "pattern (C)" decision from 2026-05-22.

All three wildcards via cert-manager DNS-01 against the same Cloudflare zone-scoped token (`secret/k8s/cert-manager/cloudflare`, scope: `Zone:DNS:Edit + Zone:Zone:Read` on `xiiisins.com`).

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
| Tailscale 1 | 1113 | Urd | `10.0.11.213` | Tailscale subnet router (advertises 10.0.10/11/20/21/254/30/31.0/24 + 10.43.0.0/16; indefinite key, `tag:subnet-router`) | 🔲 |
| Tailscale 2 | 1114 | Verd | `10.0.11.214` | Tailscale subnet router (same routes as 1113; indefinite key, `tag:subnet-router`) | 🔲 |
| Tailscale 3 | 1115 | Skuld | `10.0.11.215` | Tailscale exit node (`--advertise-exit-node`; indefinite key, `tag:exit-node`) | 🔲 |
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

**Status (2026-05-22 evening):** Core infrastructure ✅ running and stable, **cluster edge stack now live**. Cluster was fully torn down and rebuilt on 2026-05-17 morning as a deliberate validation exercise — see incident log. Rebuild confirmed end-to-end IaC works (Terraform → Ansible → Flux) and surfaced 9+ structural gaps that have since been closed. Authentik + hand-rolled Redis deployed 2026-05-17 evening; Phase 4a (CP taints), Phase 4b (Göndul Verd → Urd), einherjar-urd worker rebuild, and Phase 5e.1 (Traefik + Gateway API + cert-manager + HTTPS cutover) all landed 2026-05-21 / 2026-05-22. AWX/Tofu Controller + core services next.

**Core infrastructure (cascade failure if down):**

| Service | Replicas | Status |
|---------|----------|--------|
| Vault | 3 (Raft HA) | ✅ Running, AWS KMS auto-unseal. K8s + AppRole auth + KV engine + test entry in Terraform (`terraform/vault/`). Re-init recovery procedure validated 2026-05-17. |
| Authentik server | 3 | ✅ Deployed 2026-05-17. Chart 2026.2.3. **Currently exposed via Traefik+Gateway at `https://authentik.niflheim.xiiisins.com`** (5e.1 cutover 2026-05-22); original LoadBalancer on `.12` released. External Postgres pointed at Fulla. Day-1 blueprints in Git (niflheim brand + personal admin user). **FQDN migration in 5e.2:** `authentik.niflheim.xiiisins.com` → `authentik.xiiisins.com` (external, via Cloudflared) + `authentik.midgard.xiiisins.com` (internal alias) — the `niflheim` zone is wrong for a publicly-reachable service. |
| Authentik worker | 1 | ✅ Migrations + blueprint reconciliation run from here. |
| Redis | 1 | ✅ Hand-rolled StatefulSet (`redis:7-alpine`, AOF persistence, 1Gi iSCSI PVC). NOT the Bitnami sub-chart — the 2026.x Authentik chart dropped its bundled Redis. |
| MetalLB | DaemonSet | ✅ L2 working end-to-end. VIP reachable from outside the cluster. Required nodeSelectors, Calico autodetection pin, rp_filter loose, route_localnet=1, and VLAN 20 source-based policy routing — all IaC'd. Pool `.10–.99` (extended `.11` → `.10` for Traefik 5e.1). |
| Synology CSI (core) | 1 | ✅ iSCSI, synology-csi-iscsi-retain. SealedSecret split into `synology-csi-config/` Kustomization. |
| External Secrets Operator | 1 | ✅ ClusterSecretStore `vault` Ready. Authentik secrets in `secret/k8s/authentik/*` synced via ESO. Cloudflare DNS-01 token in `secret/k8s/cert-manager/cloudflare`. |
| Sealed Secrets | 1 | ✅ Master keys backed up to 1Password as of 2026-05-17. |
| tigera-operator (Calico) | 1 | ✅ Fixed 2026-05-15 via MTU explicit workaround (upstream issue #7851) |
| Gateway API CRDs | — | ✅ v1.5.1 Standard channel, vendored from upstream into `infrastructure/gateway-api/` (deployed 5e.1.b, 2026-05-22). |
| cert-manager | 2 (HA pairs) | ✅ v1.19.0, `enableGatewayAPI: true`, chart-installed CRDs (`crds.enabled: true, keep: true`). ClusterIssuers `letsencrypt-staging` + `letsencrypt-prod` (Cloudflare DNS-01, zone-scoped to `xiiisins.com`). Deployed 5e.1.c–d, 2026-05-22. |
| Traefik | 3 (1/worker, required anti-affinity) | ✅ v40.2.0 chart / v3.7.1 proxy, Gateway API provider only (no IngressRoute, no Ingress; `kubernetesCRD` enabled for Middleware CRs). `NET_BIND_SERVICE` for direct 80/443 binding. MetalLB LB on `10.0.20.10`. `externalTrafficPolicy: Local` + 1 pod/worker for source IP preservation. `maxSurge: 0` mandatory rollout strategy. Deployed 5e.1.e, 2026-05-22. |
| Gateway `niflheim` | — | ✅ Single shared Gateway in `traefik` namespace, GatewayClass `traefik`, web + websecure listeners, `allowedRoutes.namespaces.from: All`. Wildcard cert ref via Secret `wildcard-niflheim-tls`. Internal-only routes only. Deployed 5e.1.f, 2026-05-22. |
| Gateway `midgard` | — | 🔲 — Second Gateway, planned 5e.2. Hosts apex (`*.xiiisins.com`) + midgard (`*.midgard.xiiisins.com`) listeners. Cloudflared backend targeting + internal AdGuard rewrites land HTTPRoutes here. Separated from `niflheim` Gateway by design to keep public-vs-internal route attachment policies distinct (decision 2026-05-22). |
| Wildcard `*.niflheim.xiiisins.com` | — | ✅ + apex `niflheim.xiiisins.com` SAN, ECDSA P-256, 90d duration / 30d renewBefore. Issued by `letsencrypt-prod` (E8 intermediate). Deployed 5e.1.f, switched staging → prod in 5e.1.h, 2026-05-22. |
| Wildcard `*.midgard.xiiisins.com` | — | 🔲 — Planned 5e.2. Same issuer + token + algo as the niflheim wildcard. Covers internal aliases of publicly-reachable services. |
| Wildcard `*.xiiisins.com` (apex) | — | 🔲 — Planned 5e.2. Origin-side cert for Cloudflared traffic (browser-visible cert is Cloudflare's universal cert; this is what Cloudflared connects to Traefik with). |
| Cloudflared | 1+ | 🔲 — Phase 5e.2. Cloudflare Tunnel for selected external exposure. Targets backend Services by ClusterIP DNS, never via MetalLB IPs. Tunnel credentials via ESO from Vault. |
| WebFinger middleware | — | 🔲 — Phase 5e.2. Traefik Middleware with static-response plugin at `xiiisins.com/.well-known/webfinger`, returning the Tailscale OIDC issuer pointer JSON. Lives on the `midgard` Gateway. |

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
  - `ansible-local` role: MacBook control node, manual playbook runs. RoleID + SecretID stored in 1P (Homelab vault, item `Ansible - Vault - k3s`) — single source of truth, no env file on disk. Loaded into env via `homelab-env`. 90-day rotation via `rotate-approle ansible-local`. See Control-node fish tooling sub-section.
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
4. Stash in 1Password Homelab vault as a Login item (one per role; for `ansible-local` the item name is `Ansible - Vault - k3s`) with fields `url`, `method`, `username` (= RoleID), `password` (= SecretID), `secret_id_accessor`, `expires_at` (today + 90 days). Map role name → 1P item name in `$__homelab_approle_items` inside `homelab.fish`.
5. Load via `homelab-env` (control-node fish tooling — see sub-section below). No env file on disk.
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

Store in 1Password under `Ansible - Vault - k3s` (Login item, Homelab vault):
- `url`: `http://10.0.20.11:8200`
- `method`: `approle`
- `username` (= RoleID): from `terraform output`
- `password` (= SecretID): from `vault write`
- `secret_id_accessor`: from `vault write` (used to revoke without knowing the SecretID; needed by `rotate-approle`)
- `expires_at`: today + 90 days

The field names `url`/`method`/`username`/`password` are what `homelab-env` reads via `$__homelab_env_map`. The item name is whatever you configured in `$__homelab_approle_items` — for `ansible-local`, that's `Ansible - Vault - k3s`.

The control-node fish tooling at `<repo>/.config/fish/conf.d/homelab.fish` reads those 1P fields and exports them as `VAULT_ADDR`, `ANSIBLE_HASHI_VAULT_AUTH_METHOD`, `ANSIBLE_HASHI_VAULT_ROLE_ID`, `ANSIBLE_HASHI_VAULT_SECRET_ID` when `homelab-env` is invoked. The `ANSIBLE_HASHI_VAULT_*` prefix is the canonical env-var form the `community.hashi_vault` collection reads; `VAULT_ADDR` stays as-is (standard Vault CLI env var).

Symlink once per control node:

```fish
ln -s <repo-path>/.config/fish/conf.d/homelab.fish ~/.config/fish/conf.d/homelab.fish
```

See the **Control-node fish tooling** sub-section below for details and extension points.

Usage: `homelab-env` once per shell session, then run playbooks normally.

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
# Load env, mint a root token, seed a test secret
homelab-env
set-vault-token root
vault kv put secret/ansible/test/hello value=world

# Switch to AppRole and run the test play
set-vault-token approle
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/test-vault-lookup.yml
# Expected: "Got value from Vault: world"

# Cleanup
set-vault-token root
vault kv delete secret/ansible/test/hello
set -e VAULT_TOKEN
```

**For AWX (when deployed):** same steps against `ansible-awx` role. SecretID goes into AWX's credential store rather than a local env file. After deploy, restrict via `token_bound_cidrs` in `terraform/vault/main.tf` once the asgard K3s pod CIDR is known.

**Rotation (every 90 days):**

```fish
set-vault-token root           # mints VAULT_TOKEN from 1P
rotate-approle ansible-local   # mints new SecretID, prompts to update 1P, revokes old
set -e VAULT_TOKEN             # don't leave root token in env
homelab-env                    # picks up new SecretID into env
```

`rotate-approle` snapshots the old accessor *before* minting (so the revoke step targets the correct SecretID even after 1P is updated), and revokes the old SecretID only after you confirm 1P is updated. No window where 1P holds a stale value.

*Partial rotation recovery.* If the rotation aborts after 1P is updated but before the revoke step completes (Ctrl+C between the paste and the prompt, or a failed revoke step), Vault holds a SecretID that 1P no longer references. Run `rotate-approle --fix <role>` to find and destroy orphans — it reads the canonical accessor from 1P, lists all Vault accessors for the role, and offers to destroy any that don't match (with metadata shown for verification).

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

### Control-node fish tooling

Single repo-tracked fish file at `<repo>/.config/fish/conf.d/homelab.fish`, symlinked to `~/.config/fish/conf.d/homelab.fish`. Sourced once at shell init; env vars only set when functions are invoked.

`conf.d/` rather than `functions/` because `functions/` uses autoload-by-filename, one function per file; `conf.d/foo.fish` holds many.

Public functions:

| Function | Purpose |
|----------|---------|
| `homelab-env` | Loads homelab env vars from 1P (`VAULT_ADDR`, `ANSIBLE_HASHI_VAULT_*`). Idempotent. |
| `set-vault-token <source>` | Sets `VAULT_TOKEN`. `root` pulls from 1P; `approle` mints via `vault write auth/approle/login` using already-loaded creds (fails loudly if `homelab-env` hasn't run). |
| `vault-root-token` | Pure value-producer; echoes the root token. |
| `rotate-approle <role>` | Mints a new SecretID, prints values to paste into 1P, prompts for confirmation, revokes the old SecretID. `--fix` destroys SecretIDs in Vault that aren't in 1P (recovery for partial rotations). `--help` for usage + hazard notes. |

Extension points:
- **New env var loaded by `homelab-env`:** append a line to `$__homelab_env_map` in the form `"ENV_VAR|1P item name|field"`. No other code change.
- **New `set-vault-token` source:** add a `case <name>` branch to the function's switch block. Wire to a 1P read or another Vault auth method.
- **New AppRole role for rotation:** append a line to `$__homelab_approle_items` in the form `"role-name|1P item name"`. The 1P item must have fields `username` (RoleID), `password` (SecretID), `secret_id_accessor`, `expires_at`.

The 1P vault name (`Homelab`) is lifted to `$__homelab_op_vault` at the top of the file — single-point edit if it ever changes.

Source-of-truth posture: AppRole RoleID/SecretID and the Vault root token live in 1P only. No copies on disk on the control node. Rotation = update the 1P item; next `homelab-env` picks up the change.

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
| 4a — CP taint (retro-sequenced) | ✅ | Deployed 2026-05-21. All three CPs tainted `node-role.kubernetes.io/control-plane=:NoSchedule` via kubectl on existing nodes (the K3s `node-taint:` config-template is registration-time only — covers future bootstraps). Workload pods migrated to workers. Vault HA naturally spread: vault-0/1/2 on einherjar-urd/skuld/verd. Synology CSI node-plugin evicted from CPs as intended. **Surfaced two outages and ~10 findings** — see incident log entry. |
| 4b — Göndul Verd → Urd | ✅ | Applied 2026-05-22. Göndul VM destroyed on Verd and recreated on Urd via `terraform apply --target='proxmox_virtual_environment_vm.control_plane["gondul"]'`; stale etcd member cleared via `kubectl delete node gondul`; rejoined as `--server` (not init) via `-e 'k3s_init_node=hlokk'` override. Surfaced one finding (orphan LVs from a NUC7-era partial migration blocked the first clone — recovery via `lvremove`, see incident log). Vault Raft stayed 3/3 voters throughout. CP topology is now Göndul on Urd, Hlökk on Verd, Sigrún on Skuld — back to the original 2026-05-14 design intent, now valid hardware-wise. |
| Worker rebuild — einherjar-urd template_node correction | ✅ | Applied 2026-05-22 evening, immediately after Phase 4b. Worker VM destroyed + recreated to correct stale `template` / `template_node` references in TF (pointed at Verd template even though VM ran on Urd). Doubled as deliberate validation of the worker-rebuild path. Surfaced four findings — see incident log entry. Vault accepted 2/3 voters during the ~25 min window by design (vault-0 Pending due to chart's hard-required pod anti-affinity; once new einherjar-urd was Ready it scheduled there, attached its PVC, rejoined Raft). |
| 5e.1 — Traefik + Gateway API + cert-manager | ✅ | Deployed 2026-05-22 evening. Gateway API v1.5.1 Standard CRDs (vendored), cert-manager v1.19.0 (`enableGatewayAPI: true`), Traefik v40.2.0/proxy v3.7.1 (Gateway API provider only, `NET_BIND_SERVICE` for direct 80/443 binding). MetalLB pool extended `.11–.99` → `.10–.99`. Wildcard `*.niflheim.xiiisins.com` (ECDSA P-256) issued by Let's Encrypt prod (E8 intermediate), DNS-01 via zone-scoped Cloudflare token. Authentik exposed at `https://authentik.niflheim.xiiisins.com` via HTTPRoute attached to `niflheim` Gateway in `traefik` namespace. Original direct LB on `.12` released back to MetalLB pool. Sub-phases 5e.1.a (Cloudflare token + Vault entry) → 5e.1.i (DNS cutover + Service → ClusterIP). Surfaced eight findings — see incident log. |
| 5e.2 — Cloudflared + apex zone + WebFinger | 🔲 | Cloudflare Tunnel deployed in asgard; apex `*.xiiisins.com` + internal-alias `*.midgard.xiiisins.com` wildcards added (cert-manager, same DNS-01 token); second `midgard` Gateway in Traefik for the apex+midgard zones (separated from `niflheim` by design); WebFinger as a static-response Traefik Middleware at `xiiisins.com/.well-known/webfinger`. Authentik HTTPRoute migrates off `niflheim` onto the new public Gateway + a midgard internal alias. Cloudflared targets backend Services by ClusterIP DNS (no in-cluster tromboning). Browser-visible cert externally is Cloudflare's universal cert (CF Tunnel terminates TLS at the edge; Custom SSL is out of scope on Free plan); our LE wildcards are origin-side. Unblocks 5e.3 — Tailscale's control plane needs publicly-reachable Authentik for OIDC discovery + token exchange, and a public WebFinger endpoint for tailnet creation. |
| 5e.3 — Tailscale OIDC blueprints + LXCs | 🔲 | Authentik OAuth2/OIDC provider+application blueprints for Tailscale (committed to Git, no UI config), strict redirect URI `https://login.tailscale.com/a/oauth_response`, signing key ES256. Tailscale ACL policy as code via the `tailscale/tailscale` Terraform provider (autoApprovers on `tag:subnet-router` + `tag:exit-node` so device-side `--advertise-routes` self-approves). LXCs 1113/1114 (subnet routers, same routes) + 1115 (exit node) — Ansible role for Tailscale on Alpine/RHEL minor variants. NAS (Munin) gets a Tailscale advertiser (package or container — TBD at deploy time) as the K3s-independent break-glass path. Tailscale Free plan (3-user cap on custom OIDC); split-auth: servers + one always-on user device on indefinite keys, phones/laptops with default expiry via Authentik OIDC. |
| 5f — Factorio LXC | ✅ | Deployed 2026-05-16 — Terraform + Ansible end-to-end |
| 5g — PostgreSQL + Teamspeak LXCs | 🟡 | Fulla (1130) ✅ deployed 2026-05-17 standalone. Vör/Idunn deferred until post-Authentik (cluster expansion validates against real consumer). Teamspeak pending. |
| 5h — Remaining LXCs | 🔲 | HAProxy, Zabbix, Jellyfin (Tailscale LXCs now part of 5e.3) |
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
| Control-node credential loading | All control-node env vars and tokens loaded from 1P at function-invoke time via `homelab.fish` in `conf.d/`. No env file on disk. | Eliminates the rotation-drift class between an on-disk env file and the 1P recovery copy. Single source of truth (1P) matches the "1P is the recovery copy" posture for AppRole creds. Cost: Touch-ID prompt per shell session (acceptable). Decided 2026-05-23. |
| Long-term Vault successor | OpenBao (migration ~12 months out) | HashiCorp BSL + IBM acquisition risk; LF governance preferred long-term |
| Repo visibility | Private (GitHub) | Reduces exposure; SealedSecrets still used for bootstrap secrets |
| VM naming | Valkyries (CP) + Einherjar/Drengr (workers) | Norse theme, conceptually fits K3s |
| Node naming | Urd, Verd, Skuld (Norns) | Fate controllers = hypervisors |
| NAS naming | Munin | Raven of memory |
| PBS type | Privileged LXC | NFS mount requires it |
| K3s on Urd | CP placement restored 2026-05-22 (Phase 4b) | The original constraint (N5095 + mSATA too slow for etcd) is gone after the MSI Cubi swap. Phase 4b moved Göndul back to Urd. Final CP topology: Göndul on Urd, Hlökk on Verd, Sigrún on Skuld — same as 2026-05-14 pre-incident design, now valid hardware-wise. |
| Göndul placement | Urd (Phase 4b applied 2026-05-22) | Originally on Urd at the 2026-05-14 incident, moved to Verd 2026-05-17 to escape Urd's etcd thrashing. Urd hardware refreshed 2026-05-21 (MSI Cubi); Phase 4b returned Göndul to Urd 2026-05-22. CPs are 2vCPU/4GB symmetrically. |
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
| K3s CP workload isolation | `node-role.kubernetes.io/control-plane=:NoSchedule` taint on all three CPs (Phase 4a — applied 2026-05-21) | CPs run only control-plane components + DaemonSets that explicitly tolerate the taint (Calico-node, kube-proxy, metallb-speaker) + K3s-shipped pods (CoreDNS, metrics-server, calico-apiserver). Workload pods cannot land on CPs — pressure can't reach etcd via fsync contention. With taint, 4 GiB is the correct CP size. Side effect: Synology CSI node-plugin evicted from CPs (closes the CP-grabs-worker-LUN gotcha class). **Caveat:** evicting CSI from CPs while stateful pods still live there breaks the unmount path — drain stateful workloads first, OR add a CP-toleration to the CSI DaemonSet. Architectural decision on the long-term posture pending — see Open questions. |
| K3s `node-taint` is registration-time only | Apply taints via `kubectl taint node` for existing clusters; config-template covers fresh bootstraps | Discovered 2026-05-21 Phase 4a: K3s does not re-apply `node-taint:` config to existing node objects on restart. Updating the config template alone leaves existing nodes untainted. Going-forward: both paths are in place — config-template for future cluster rebuilds + Phase 4b's gondul re-registration; kubectl for the current cluster. |
| K3s role config rendering | `config.yml` separate from `install.yml` | Until 2026-05-21, config-template tasks lived inside `install.yml`, which is wholesale-skipped on healthy nodes via `detect-state.yml`. Genuine config changes never rendered on healthy CPs. Split out during Phase 4a. The restart-k3s-on-healthy-CP concern (potential "duplicate node name") was empirically *not* observed — steady-state restart is safer than the fresh-bootstrap warning suggested. |
| Synology CSI node-plugin on CPs | **Open architectural question** — defaults to "off" (CSI runs only on workers) | Phase 4a evicted CSI from CPs as intended for the cross-node-iSCSI-fight gotcha class. But the eviction-while-stateful-pods-still-there path is a footgun (see Known gotchas "CSI eviction footgun"). Options: (a) accept the footgun, document the "drain first" rule; (b) add CP-taint toleration to CSI DaemonSet (production default for GKE/EKS) — keeps the cross-node-iSCSI-fight risk but eliminates the unmount-hang risk. Decision deferred to a separate session post-Phase 4a. |
| Helm chart pin policy | Concrete-pin all HelmRelease charts. No minor-floats (`0.x`, `2.x`, `0.15.x`). Renovate deferred until stable state. | Closed 2026-05-22 as Phase 4b prerequisite. The original position (`0.x` placeholders, pin at "2.0") was retired after the 2026-05-21 Phase 4a session — two outages from floating pins in one day (metallb 0.16 broken template, synology-csi 1.3.0 image not published) disproved the assumption that minor-version bumps would be safe. All four remaining float-pins tightened to concrete on 2026-05-22 (sealed-secrets 2.18.6, vault 0.32.0, external-secrets 0.20.4, metallb 0.15.3). Updates are deliberate operations until Renovate is activated post-stable-state. |
| IaC pin policy (generalized) | Concrete-pin ALL IaC versions — Helm charts, Terraform providers, Ansible role versions. Minor-floats (`~> 4.0`, `0.x`, `2.x`) banned across the board. | Decided 2026-05-23 as 5e.2.a prerequisite. The Helm-only rule above generalized — Terraform providers have the same failure mode (Cloudflare v4→v5 is 40+ resource renames in a minor-version stream; same shape as the metallb 0.15→0.16 break). New `terraform/cloudflare/` pins concrete from day 1. Existing `terraform/vault/` `~> 4.0` known pending tighten — see Pending tasks. Updates remain deliberate operations. |
| DNS fallback resolver | Never a public resolver. UCG → AdGuard VIP only, optional fallback peer AdGuard | Public resolvers (Cloudflare `1.1.1.1`, Google `8.8.8.8`) return NXDOMAIN for internal zones. Glibc and CoreDNS treat NXDOMAIN as authoritative and cache it. Discovered 2026-05-17 during Authentik deploy — Authentik couldn't reach `fulla.niflheim.xiiisins.com` because CoreDNS had cached a stale NXDOMAIN from a brief moment where the K3s node had queried `1.1.1.1` via secondary fallback. |
| Authentik Redis | Hand-rolled StatefulSet (`redis:7-alpine`), not Bitnami sub-chart | Chart 2026.x dropped its bundled Redis. Hand-rolled is ~50 lines of YAML — simpler than adopting Bitnami's metrics/sentinel/HPA scaffolding for a single-replica homelab Redis. Single replica, AOF persistence on iSCSI. If a future service wants its own Redis (e.g. AWX fact caching), it gets its own — namespace-bundled. |
| Authentik service config injection | All env vars via ExternalSecret template, not chart `values:` block | The chart exposes config via both paths; env vars win silently. Setting only the values block produces ghost-localhost-fallback (Authentik tried `127.0.0.1:5432` for an hour before this was diagnosed 2026-05-17). Going forward: every service config knob settable via env goes through ExternalSecret. |
| Authentik day-1 GitOps | Blueprints in Git from day 1, no UI configuration ever | Single source of truth. Cluster rebuild reconstitutes identity. Brand + personal user as the first two blueprints; subsequent OIDC providers (Tailscale, Grafana, etc.) follow same pattern. Branding assets follow the same rule (populator init container reads from ConfigMap today, S3-compatible tomorrow — pattern preserved). |
| Sub-kustomization per component | Every component is a self-contained directory with its own `kustomization.yaml`; parent `infrastructure/kustomization.yaml` only references directories | Forced by Authentik's `configMapGenerator` for blueprints (Kustomize generators can't flow through flat-file-reference parents). Migrated all components for consistency. Closed the design doc's open nested-vs-flat question 2026-05-17 evening. |
| Stateful worker rebuild — Vault quorum posture | Accept 2/3 voters for the duration of a single-worker rebuild; do NOT migrate the stateful pod off the doomed worker first | Vault's chart ships pod anti-affinity as `requiredDuringSchedulingIgnoredDuringExecution` — at 3 replicas on 3 workers, draining or cordoning any one worker leaves the displaced pod Pending until the doomed worker returns. The cordon+migrate dance designed around "always 3/3 voters" hits this wall. 2/3 is operationally fine for ~20-30 min windows: Vault stays fully read+write, etcd is untouched, only risk is a second voter failing during the window (low probability for healthy peers). Discovered 2026-05-22 during einherjar-urd worker rebuild. |
| Stateful worker rebuild — leadership step-down before drain | Step Raft leadership off the doomed worker before pod-delete or drain | Vault's pod-delete forces a Raft election under termination pressure if the doomed pod is leader. `vault operator step-down` first lets the cluster pick a new leader cleanly while the original leader is still healthy. Cost ~10s, eliminates the election-during-termination race. Applied 2026-05-22; would have been worth doing in Phase 4a too. |
| K3s playbook execution model | Default `serial: 1`; override for cluster-from-zero rebuilds (planned) | Default-parallel is a multi-node-outage footgun — a single role change that triggers `restart-k3s` will fire across all 6 nodes concurrently, defeating HA. Validates with Phase 4a finding that per-node K3s restart is empirically safe, but simultaneous restart across the cluster is not. Captured 2026-05-22 as a pending change; not yet implemented. |
| Baseline OS package update | Tagged `os-updates`, skip-by-default (planned) | Current behavior runs `dnf update "*" → latest` + reboot on every play, conflating config-idempotency runs with OS-maintenance runs. Default-on-with-reboot is wrong for incremental config; should be opt-in via `--tags os-updates` for explicit maintenance windows. Captured 2026-05-22 as a pending change; not yet implemented. |
| Cluster routing API | Gateway API (sigs.k8s.io/gateway-api) v1.5.1 Standard channel — over Ingress and Traefik IngressRoute | Picked 2026-05-22 Phase 5e.1. Gateway API is the K8s-spec successor to Ingress (GA in 1.30, widely adopted by 2026): role-separated (platform owns Gateway, app owns HTTPRoute), expressive enough to skip annotation soup, portable across controllers (Traefik, Envoy, Cilium, etc). Ingress was viable but dated; Traefik IngressRoute teaches Traefik specifically and doesn't generalize. Gateway API is the modern + portable + senior-signal answer. Middleware attachment via `ExtensionRef` filters pointing at Traefik `Middleware` CRs — small portability tax (routes stay portable, middleware definitions don't). |
| Gateway API CRD distribution | Vendored upstream manifest in `infrastructure/gateway-api/` | Raw YAML from upstream release (`standard-install.yaml`, ~200KB) committed to repo. Alternatives (Flux `GitRepository` source, Kustomize remote resource) introduce reconcile-time fetch dependency on upstream availability. Vendoring gives deterministic, auditable, offline-safe CRDs. Renovate-track-by-comment when Renovate is activated. |
| TLS cert strategy | Single wildcard `*.niflheim.xiiisins.com` (ECDSA P-256) | One Certificate covers all internal services. Per-FQDN certs add object count + renewal traffic without security benefit at this scope. ECDSA over RSA: smaller TLS handshake, modern default — Let's Encrypt has supported ECDSA for years. SAN includes apex (`niflheim.xiiisins.com`) so future apex route works without re-issuance. |
| ACME challenge | DNS-01 via Cloudflare API (zone-scoped token, `xiiisins.com` only) | DNS-01 works for wildcard certs (HTTP-01 cannot); also keeps cert issuance independent of inbound HTTP reachability. Token scoped to single zone with `Zone:DNS:Edit` + `Zone:Zone:Read` (the latter is needed for cert-manager's zone-discovery step, easy to miss). Token in Vault at `secret/k8s/cert-manager/cloudflare`, K8s Secret materialised via ExternalSecret with field rename `api-token` (kebab Vault) → `apiToken` (camel cert-manager expects). |
| ClusterIssuers — staging + prod day 1 | Both `letsencrypt-staging` and `letsencrypt-prod` deployed together | Wildcard cert starts on staging for chain validation (unlimited rate limit, no risk of burning prod's 50/week per-domain quota). Flipped to prod after end-to-end validation. Both Issuers reference the same Cloudflare token Secret; only the ACME endpoint differs. `dnsZones` selector locks each Issuer to `xiiisins.com` so misuse against a wrong zone fails noisily. |
| Traefik VIP | `10.0.20.10` — pool extended `.11–.99` → `.10–.99` for it | Asgard LB demand realistically caps at ~3 LBs ever (Vault, Traefik, occasional non-HTTP edge like Teamspeak). Cloudflared targets ClusterIP DNS, not MetalLB IPs — so external-only services don't consume MetalLB IPs. Reasoning for `.10`: memorable, sits just below the pool, single edge LB doesn't justify a block-allocation scheme. |
| Traefik privileged-port binding | `NET_BIND_SERVICE` capability + entrypoints on 80/443 directly (no Service-port-rewrite shim) | Chart default uses 8000/8443 internally (non-root can't bind <1024) with Service-level `exposedPort: 80/443` mapping. Works for traditional Service-IP access, but breaks Gateway API: Traefik's Gateway provider matches listener `port` to the entrypoint's *internal* port. With `NET_BIND_SERVICE` (the smallest possible Linux cap, nothing else added) Traefik binds 80/443 directly; Gateway listeners and entrypoints match 1:1, manifests read naturally. Pattern is what most production Traefik deployments use and what the chart's own examples recommend. |
| Traefik replicas + rollout strategy | 3 replicas (one per worker) with `requiredDuringScheduling` anti-affinity + `strategy.rollingUpdate.maxSurge: 0, maxUnavailable: 1` | Required anti-affinity ensures MetalLB L2 election (which picks one node for ARP) always lands on a node with a local Traefik pod — pairs with `externalTrafficPolicy: Local` for source IP preservation. 3 replicas on 3 workers fills every slot exactly. `maxSurge: 0` is mandatory: default 25% rounds to +1, but there's no 4th worker to satisfy anti-affinity → rollout deadlocks. `maxUnavailable: 1` keeps rolls serial. During a roll, briefly serves on 2/3 replicas — fine because only one is actively serving via L2 election anyway. |
| Gateway namespace policy | Single shared `niflheim` Gateway in `traefik` namespace, `allowedRoutes.namespaces.from: All` | One Gateway for asgard; HTTPRoutes live in their app's namespace (`authentik/`, future `outline/`, etc.) and attach cross-namespace via `parentRefs`. Pattern 1 (shared Gateway) is simpler and matches "single admin" homelab reality; Pattern 2 (Gateway-per-app) is more idiomatic for multi-tenant clusters but unnecessary here. Tighter scoping (`Selector`-based, only specific namespaces) is possible later if needed. |
| Cloudflared backend targeting | Always via ClusterIP DNS, never MetalLB LB IPs | Cloudflared is in-cluster; resolving a backend via its `<svc>.<ns>.svc.cluster.local` name keeps traffic in pod network. Targeting a MetalLB IP would create tromboning: packet leaves pod → hits LB IP outside cluster → MetalLB ARPs it back into the cluster → kube-proxy DNATs to a pod IP. Pointless extra hops with same eventual destination. **Exception:** if a route needs middleware policy (rate limit, IP allowlist) applied to externally-tunnelled traffic, target Traefik via its ClusterIP DNS with `originRequest.httpHostHeader` override + `noTLSVerify: true` for internal certs. Still no MetalLB IP consumed. |
| DNS — three-zone scheme | `xiiisins.com` apex (external, Cloudflare-resolved) / `midgard.xiiisins.com` (internal alias for publicly-reachable services, AdGuard-resolved) / `niflheim.xiiisins.com` (internal-only, AdGuard-resolved) | Closed 2026-05-22 during 5e.2 design. Earlier docs used `midgard` interchangeably for "public" without distinguishing apex from internal alias; this conflated three distinct concerns (external resolution + internal alias for public services + internal-only services). The three-zone framing keeps zone selection mechanical: external → apex, internal-but-publicly-reachable → midgard, internal-only → niflheim. Each zone gets its own wildcard cert (5e.1 issued niflheim, 5e.2 adds midgard + apex). |
| External TLS posture | Cloudflare Tunnel + Cloudflare universal cert externally; our LE wildcard `*.xiiisins.com` on the origin side (Traefik → Cloudflared origin-pull) | "Pattern (C)" decision 2026-05-22. Free-tier Cloudflare Tunnel terminates TLS at the edge with Cloudflare's universal cert; custom edge certs require Business/Enterprise plan or Advanced Certificate Manager. Our LE wildcard is what Cloudflared connects to Traefik with — invisible to browsers, real for origin authentication. This matches the prior Docker Swarm setup. Alternatives considered: pattern (A) DNS-only + UCG port-forward (drops DDoS protection + IP hiding, gains end-to-end LE), pattern (B) paid custom edge cert (~$10/mo+). Picked (C) for cost + tunnel benefits; can mix in (A) per-service if a service ever needs end-to-end LE. |
| Two-Gateway separation | `niflheim` Gateway for internal-only routes; `midgard` Gateway for apex + midgard routes | Decided 2026-05-22 during 5e.2 design. Single-Gateway alternative was simpler but mixed internal-only and publicly-reachable routes under one policy surface — easy to misroute a niflheim-named HTTPRoute onto the public zone. Two Gateways enforce the zone separation at the Gateway level: an HTTPRoute attaching to `midgard` is explicitly opting into public exposure. Both Gateways live in `traefik` namespace with `allowedRoutes.namespaces.from: All`. |
| WebFinger hosting | Tiny Caddy pod (`apps/apex-static/`) attached to the `midgard` Gateway's apex-bare listener at `xiiisins.com/.well-known/webfinger`. Returns RFC 7033 `application/jrd+json`. | Decided 2026-05-22 design; **revised 2026-05-23 during 5e.2.f after discovering Traefik v3 OSS has no built-in static-response middleware** (only header/redirect/auth middleware ship in OSS; static-response is a Yaegi-loaded community plugin). Switched to a pod (Caddy 2.11.2-alpine, 2 replicas, ConfigMap-backed Caddyfile + JSON). Trade vs. plugin approach: ~16MB extra memory + 1 extra Service vs. avoiding a Yaegi plugin pin and a Traefik values-change shortly after 5e.1 stabilized. Pod also generalizes — future apex static endpoints (`robots.txt`, `security.txt`, future startpage) reuse the same pod without re-touching infra. Pod is required only at tailnet creation per Tailscale docs but kept always-on for re-creation flows. |
| App vs infrastructure split | `k8s/asgard/apps/` = leaf consumer workloads (apex-static, startpage, future Outline/Immich/etc.). `k8s/asgard/infrastructure/` = platform services that other services depend on (cert-manager, traefik, gateway-api, ESO, MetalLB, Vault, sealed-secrets, synology-csi, **cloudflared**, **authentik**). | Decided 2026-05-23 as 5e.2.f prerequisite when wiring `apps/` for the first time. The split is "kill this and what breaks" — kill apex-static and only WebFinger breaks; kill cloudflared and all public ingress breaks (same tier as Traefik). Authentik is in `infrastructure/` because every service eventually authenticates through it — universal-dependency rule. Borderline cases get the explicit call here in the decision log when they land. `apps/` reconciles via its own Flux Kustomization (`flux-system/apps.yaml`) with `dependsOn: infrastructure` so apps don't start reconciling until cluster platform is Ready. |
| Module ownership of Vault KV secrets | Each Terraform module owns the Vault KV entries for secrets *it generates*. `terraform/vault/` owns Vault shape (mounts, auth, policies, roles) but not KV data. Manually-minted secrets (Cloudflare DNS-01 token, 1P-sourced creds) stay manual until a broader IaC pattern for those lands. | Decided 2026-05-23 during 5e.2.d. The pre-existing mixed pattern (Vault config in Terraform, KV contents manual) was an unintentional gap — for any secret a module generates, the only safe target IS Vault, and a manual `terraform output | vault kv put` pipe step is strictly less safe than letting the module do it directly (same tfstate exposure either way, but eliminates the shell-pipe window). Secret duplication in tfstate is an accepted homelab-simplicity tradeoff (private repo, same posture as the existing Vault test KV). Long-term: VSO/Vault Agent runtime fetch retires the K8s-Secret intermediate. |
| Tailscale plan + auth model | Free plan (3-user cap on custom OIDC). Split-auth: indefinite keys for servers (1113/1114/1115 + NAS) and one always-on user device; default-expiry keys for phones/laptops via Authentik OIDC. | Decided 2026-05-22. Free plan supports custom OIDC for up to 3 users (per Tailscale's own announcement) — enough for a personal homelab. Split-auth pattern: server-class devices never depend on Authentik for re-auth (they'd be locked out by an Authentik outage exactly when you need VPN access to fix it); user-class devices keep proper expiry rotation. The "one always-on device" lets the owner reach the tailnet without OIDC when traveling and Authentik is down. |
| Tailscale device scope | 3 LXCs (1113/1114/1115) + Tailscale on the NAS only. NOT universal-Tailscale across all servers. | Decided 2026-05-22. Earlier framing was "all servers get Tailscale + indefinite keys" — too broad. Universal Tailscale adds a daemon, an authkey, and a failure mode to every host for no gain over the subnet-router pattern (already gives full LAN access). Three LXCs + NAS gives redundancy (3 advertisers of the same routes; Tailscale picks one, fails over), keeps the daemon footprint to 4 devices, and isolates the break-glass path on the NAS (K3s-independent). |
| NAS Tailscale role | Subnet router advertising the same routes as 1113/1114, K3s-independent break-glass path. App-vs-container TBD at deploy time. | Decided 2026-05-22. NAS's role is "reachable when K3s is down." Munin runs DSM (independent of K3s), so a Tailscale advertiser on it keeps the tailnet's subnet-route path functional even if all three K3s nodes are down. No active/standby semantics needed — Tailscale picks one advertiser at a time and 1Gb links mean route selection is irrelevant. App-vs-container picked at deploy time based on which integrates cleanest with DSM's auto-start + update story. |
| Cloudflared management mode | Locally-managed tunnel: `config.yaml` ingress rules in Git as ConfigMap, per-tunnel `credentials.json` (JWT) via ESO from Vault. NOT remotely-managed (UI/dashboard config) NOR account-level `cert.pem`. | Decided 2026-05-23 as 5e.2 prerequisite. Locally-managed is the supported Cloudflare path that keeps all config in Git (the "Git is truth" rule). Remotely-managed parks ingress rules in the Cloudflare dashboard — UI as source of truth violates the same rule that produced the Authentik day-1 blueprints decision. Account-level `cert.pem` is for tunnel/DNS creation; that work is owned by Terraform in our flow, so the runtime pod only needs the per-tunnel JWT. |
| Cloudflare resources in IaC | Tunnel objects + DNS records via Terraform in `terraform/cloudflare/`. Same pattern as `terraform/vault/`. | Decided 2026-05-23 as 5e.2 prerequisite. Cloudflare side gets a new Terraform module with its own purpose-scoped API token. Token includes `Account:Cloudflare Tunnel:Edit` (cert-manager's existing token is zone-scoped only and insufficient for tunnel object creation). Token NEVER in Terraform state — generated manually, stored in 1Password + local env, same as the existing Cloudflare DNS-01 token pattern. |
| Cloudflared replica/scaling posture | 3 replicas, hostname anti-affinity, no autoscaling. | Decided 2026-05-23 as 5e.2 prerequisite. Per Cloudflare's own Kubernetes guidance: scaling down breaks existing user connections to the removed replica. 3 replicas pinned to 3 workers via anti-affinity matches the Traefik posture (and the worker-rebuild-tolerance argument that justified it). Each replica handles all tunnel ingress rules — no per-replica config split needed. |
| Personal admin identity | `ghost@xiiisins.com` — single human identity across Authentik admin user, WebFinger subject, and (forward) every OIDC consumer. | Decided 2026-05-23 as 5e.2 prerequisite. One identity, mailbox-backed at the apex zone (Proton). Picked over `admin@` because the WebFinger subject is identity, not role; `admin@` would read as a service-role account in Tailscale's `acct:` URI. Personal username carries naturally to Outline/Immich/Grafana/etc. as those services are deployed. |
| Terraform state storage | Local-only on operator workstation, gitignored across every `terraform/*/` module. Never in Git. Backup is operator's responsibility (1Password attachment, encrypted external drive). | Foundational rule, predates the doc — gitignore was correct from day 1, just unstated until 2026-05-23. State contains decrypted secrets (`random_password`, `vault_kv_secret_v2` `data_json`, etc.) and identity material; committing it inverts every Vault/1Password discipline. Long-term: remote backend (Vault KV-backed or similar) once enterprise-security posture justifies the complexity. |
| App vs infrastructure split (`k8s/asgard/`) | `infrastructure/` = platform services that other services depend on (cert-manager, traefik, gateway-api, ESO, MetalLB, Vault, sealed-secrets, synology-csi, **cloudflared**, **authentik**). `apps/` = leaf consumer workloads (apex-static, future startpage, future Outline/Immich/etc.). | Decided 2026-05-23 as 5e.2.f prerequisite when wiring `apps/` for the first time. The split is "kill this and what breaks" — kill apex-static and only WebFinger breaks; kill cloudflared and all public ingress breaks (same tier as Traefik). Authentik is in `infrastructure/` because every service eventually authenticates through it — universal-dependency rule. `apps/` reconciles via its own Flux Kustomization (`flux-system/apps.yaml`) with `dependsOn: infrastructure` so apps don't start reconciling until cluster platform is Ready. |
| WebFinger hosting | Tiny Caddy pod (`apps/apex-static/`) attached to the `midgard` Gateway's apex-bare listener at `xiiisins.com/.well-known/webfinger`. Returns RFC 7033 `application/jrd+json`. | Decided 2026-05-22 design; **revised 2026-05-23 during 5e.2.f after discovering Traefik v3 OSS has no built-in static-response middleware** (only header/redirect/auth middleware ship in OSS; static-response is a Yaegi-loaded community plugin). Switched to a pod (Caddy 2.11.2-alpine, 2 replicas, ConfigMap-backed Caddyfile + JSON). Trade vs. plugin approach: ~16MB extra memory + 1 extra Service vs. avoiding a Yaegi plugin pin and a Traefik values-change shortly after 5e.1 stabilized. Pod also generalizes — future apex static endpoints (`robots.txt`, `security.txt`, future startpage) reuse the same pod without re-touching infra. Pod is required only at tailnet creation per Tailscale docs but kept always-on for re-creation flows. |
| Module ownership of Vault KV secrets | Each Terraform module owns the Vault KV entries for secrets *it generates*. `terraform/vault/` owns Vault shape (mounts, auth, policies, roles) but not KV data. Manually-minted secrets (Cloudflare DNS-01 token, 1P-sourced creds) stay manual until a broader IaC pattern for those lands. | Decided 2026-05-23 during 5e.2.d. The pre-existing mixed pattern (Vault config in Terraform, KV contents manual) was an unintentional gap — for any secret a module generates, the only safe target IS Vault, and a manual `terraform output \| vault kv put` pipe step is strictly less safe than letting the module do it directly (same tfstate exposure either way, but eliminates the shell-pipe window). Secret duplication in tfstate is an accepted homelab-simplicity tradeoff (private repo, state never in Git). Long-term: VSO/Vault Agent runtime fetch retires the K8s-Secret intermediate. |
| Authentik IaC split: config in Terraform, content in blueprints | `terraform/authentik/` owns identity-and-access configuration (OAuth2Providers, Applications, PolicyBindings, **users, groups**, scope mappings). Blueprints own true content (brand, branding assets). | Decided 2026-05-23 during 5e.3.b. Pattern matches `terraform/vault/` (config in TF, contents in consumers): blueprints had been doing double-duty as both config and content, which conflicted with the broader "config-in-Terraform-when-a-provider-exists" rule established for Cloudflare. Users/groups belong in TF because identity IS config that other config (PolicyBindings, future per-service group assignments) references. Existing `personal-admin` user migrated via `terraform import` 2026-05-23. Future apps' OAuth2Provider+Application+group-binding all land in this module. |
| Identity-as-data (users.yaml/groups.yaml) | Users + groups defined in YAML data files, decoded by `for_each` in `terraform/authentik/identity.tf`. Membership defined from the user side only (each user lists their groups); group resources have no `users` field. Cross-reference validation at plan time via `terraform_data.identity_validation` precondition. | Decided 2026-05-23 during 5e.3.b. Naive shape (one HCL block per user) doesn't scale past ~5 users; identity-as-data is the standard medium-scale pattern (matches what real enterprises do before they switch to a true IdM system). Membership-from-one-side avoids the dual-side-of-relationship plan-flapping. Pre-existing users (whose passwords are in 1P) flag `skip_initial_password: true`; new users get a random initial password to Vault at `secret/k8s/authentik/users/<username>/initial-password` until first-login change. |

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

### 2026-05-21 — Urd hardware refresh + Phase 4a CP taint deploy

Phase 4a. Started as a simple "swap Urd's hardware then taint the CPs" session. Took ~6 hours and surfaced ~10 findings spanning storage, CSI, Helm, K3s, and the cluster-fragility-budget. Hardware refresh succeeded cleanly; everything downstream of it required real recovery work.

**What landed in IaC:**
- `terraform/proxmox/asgard-k3s/` — unchanged (Phase 4b deferred; CP topology stays Göndul+Hlökk on Verd, Sigrún on Skuld).
- `ansible/roles/k3s/templates/config-init.yaml.j2` + `config-server.yaml.j2` — added `node-taint: ["node-role.kubernetes.io/control-plane=:NoSchedule"]` above `selinux: true`.
- `ansible/roles/k3s/tasks/config.yml` — new file. Contains the config-template render tasks + systemd unit template that previously lived inside `install.yml`. Each task notifies `restart k3s` on change.
- `ansible/roles/k3s/tasks/install.yml` — config tasks removed; install now scoped strictly to binary download + service start + cluster join coordination.
- `ansible/roles/k3s/tasks/main.yml` — task order updated: `prerequisites → network → detect-state → config (always) → install (skip if healthy) → calico (skip if healthy)`.
- `k8s/asgard/infrastructure/metallb/helmrelease.yaml` — `version: "0.x"` → `version: "0.15.x"`. Pin against the 0.16.0 breaking template change.
- `k8s/asgard/infrastructure/synology-csi/helmrelease.yaml` — `version: "0.x"` → `version: "0.11.1"`. Pin against the 0.11.2's appVersion 1.3.0 image-not-published-on-Docker-Hub failure.

**Findings, in rough order of discovery:**

1. **Urd hardware refresh — the original etcd-storm root cause is gone.** New Urd: MSI Cubi (i3-1215u, 32GB DDR4 reused, 1TB Lexar NM790 NVMe). Original Urd (DeskMini JB95: N5095 + 120GB mSATA) was the slow-CPU + slow-disk combination that drove the 2026-05-14 incident. The 2026-05-21 swap removes both factors. The "never run CP on Urd" rule is retired. Phase 4b — Göndul Verd → Urd migration — becomes the deliberate follow-up, not a defensive move. Deferred to a separate session.

2. **Storage tier picture, characterized across the cluster.** Urd: Lexar NM790 (Gen 4, DRAM-less, HMB 3.0). Verd: Samsung 970 EVO 1TB (Gen 3, DRAM). Skuld: SK Hynix PC300 512GB (Gen 3, DRAM-equipped — `hmpre=0, hmmin=0` confirmed direct DRAM, not HMB). For etcd fsync consistency: Verd ≈ Skuld > Urd. Urd's DRAM-less NVMe is the slowest of the three for sustained sync workloads despite being Gen 4 — HMB adds PCIe round-trip latency per metadata update. All three are well within etcd tolerance. Storage tier informs Phase 4b: when Göndul moves to Urd, it becomes the slowest-disk CP, but all three are far better than the original mSATA.

3. **RHEL 9 e2fsprogs cannot fsck Synology-CSI-formatted ext4 LUNs.** Pre-flight check found vault-0 and vault-1 in CrashLoopBackOff with `failed to create fsm: failed to open bolt file: read-only file system` — caused by an iSCSI session timeout during the Urd hardware swap aftermath, which made ext4 abort the journal and remount RO. Standard recovery is `fsck.ext4 -y`. RHEL 9 ships e2fsprogs 1.46.5, which errors with `unsupported feature(s): FEATURE_C12 FEATURE_R16` — these are `orphan_file` (added in e2fsprogs 1.47.0) and ro-compat bit 16. The CSI driver formats with features RHEL 9's e2fsprogs can't read. **Workaround:** fsck from an Alpine 3.20 debug pod: `kubectl debug node/<worker> -it --image=alpine:3.20 --profile=sysadmin -- sh`, then `apk add --no-cache e2fsprogs && fsck.ext4 -y /dev/sdX`. Two PVCs repaired this way; Vault scaled back up cleanly.

4. **Released PVs with retain policy leave orphan iSCSI sessions** on the worker that last consumed them. Found three orphan sessions on einherjar-skuld pointing at PVCs whose PVs were in `Released` phase. Doesn't break anything immediately but consumes Synology's session quota. Cleanup pending (not done tonight). Added an iSCSI-session-vs-PV cross-reference to the local cluster-health script to catch these going forward.

5. **K3s role architectural gap — config templating was inside install.yml**, which is wholesale-skipped on healthy nodes by `detect-state.yml`'s `k3s_already_healthy` gate. Genuine config changes (the taint update we needed) never rendered on healthy CPs. Third k3s-role gap discovered this week. **Fixed:** config templating moved to its own `config.yml`; task order in `main.yml` reordered so config runs always while install + calico still skip when healthy. Restart-k3s handler still wired through the templates.

6. **K3s `node-taint` config is registration-time only.** After the config-template change and a play run, `kubectl describe node hlokk | grep Taints` showed `<none>`. K3s consults `node-taint:` only at fresh node registration — restarting K3s on an existing cluster member does NOT cause it to re-apply the taint to its existing node object. Applied manually via `kubectl taint node <cp> node-role.kubernetes.io/control-plane=:NoSchedule` for all three CPs. Going-forward: both paths in place (config-template for future bootstraps, kubectl for the current cluster).

7. **K3s restart on an existing CP member: empirically safer than feared.** The 2026-05-17-era doc warned that restarting K3s on a healthy CP would cause "duplicate node name found" via the etcd-join path. During Phase 4a, the restart-k3s handler fired cleanly when `config.yml` rendered changes; nodes rejoined via `--server` config without incident. The previous warning was a precaution against fresh-cluster-bootstrap edge cases, not an observed steady-state failure. The handler-safety task in Open questions remains as future-proofing but isn't blocking work.

8. **NoSchedule does not evict existing workload pods.** Only DaemonSets get reconciled against taint violations automatically (the DaemonSet controller respects taints in its scheduling decisions). Existing Deployment/StatefulSet/standalone pods on a newly-tainted node stay running until natural churn or explicit deletion. Don't taint a CP and expect stateful pods on it to move on their own.

9. **CSI eviction from CPs is a footgun, not a free side effect.** When vault-1 was deleted from gondul (where it had been running) so it could migrate to a worker, it hung in Terminating for ~25 minutes. The kubelet on gondul kept trying to unmount the PVC, but the synology-csi-node DaemonSet pod had been evicted by the new CP taint — kubelet errored with `Unmounter.TearDownAt failed to get CSI client: driver name csi.san.synology.com not found in the list of registered CSI drivers`. The volume's VolumeAttachment object stayed `ATTACHED=true NODE=gondul`, blocking re-attach elsewhere (`Multi-Attach error for volume`). Recovery sequence: iSCSI logout on gondul via debug pod, force-delete the pod, manually delete the stale VolumeAttachment object. **Architectural lesson:** the "evict CSI from CPs" framing from earlier docs was wrong as stated — it sounded like a free win but it sets up exactly this failure mode. Stateful workloads MUST be drained from a CP before the CP gets a CSI-incompatible taint, OR the CSI DaemonSet must tolerate the CP taint and stay present everywhere. The latter is what production K8s does (GKE/EKS default). Architectural decision deferred — see Open questions.

10. **Floating `0.x` Helm chart pins caused two outages in one session.** (a) MetalLB chart `0.x` resolved to `0.16.0`, which adds an unconditional `.Values.prometheus.serviceMonitor.enabled` reference — template render fails without the values block. (b) synology-csi chart `0.x` resolved to `0.11.2`, which bumps appVersion to `v1.3.0` — and `synology/synology-csi:v1.3.0` is NOT published on Docker Hub (`NotFound`). Both pinned to last-known-good (`metallb: 0.15.x`, `synology-csi: 0.11.1`). The "we'll pin at 2.0" position aged poorly in this session — see Open questions for the decision.

11. **Failed Helm reconcile may need `flux suspend` + `helm rollback` + `flux resume`.** The synology-csi HelmRelease entered the `failed` state from the 0.11.2 upgrade. Plain `flux reconcile helmrelease` retried the same broken upgrade and hung. Recovery: `flux suspend helmrelease`, `helm rollback synology-csi 3 -n synology-csi --wait=false` (revision 3 was last-deployed), `kubectl delete pod synology-csi-controller-0` to force template re-render, `flux resume helmrelease`. The HelmRelease's `spec.chart.spec.version` must be corrected *before* resume, or Flux fights the rollback. Same procedure will apply to any future failed-state HelmRelease.

12. **CSI controller down → cluster-wide VolumeAttachment stall.** The Synology CSI controller is a single-replica StatefulSet and the orchestrator for all attach/detach operations. When it was CrashLoopBackOff from the unpublished image, every pod that needed to attach a PVC stalled at `Init:0/1` indefinitely — even though the kubelet-side node-plugin DaemonSet was healthy on the workers. First diagnostic check when multiple unrelated pods are stuck on volume operations: controller pod status. Worth knowing as a single-point-of-fragility for storage.

**Post-deploy state:**
- All three CPs (Göndul/Hlökk/Sigrún) tainted; workload pods migrated to workers.
- Vault HA naturally well-spread: vault-0 on einherjar-urd, vault-1 on einherjar-skuld, vault-2 on einherjar-verd. Better than pre-Phase 4a state.
- Synology CSI DaemonSet pods on workers only — controller on einherjar-urd, node-plugin on all three workers.
- 44 ✓ / 2 ⚠ / 1 ✗ on the local cluster-health script. The 1 ✗ is einherjar-urd SSH (sshd dead, not investigated — kubelet/containerd/network all healthy, K8s ops work via kubectl debug). The 2 ⚠ are downstream of that (FS state unknown) + Authentik server pod with 53 restarts (pre-existing pattern from 2026-05-17, not addressed).

**Root-cause patterns:**
- Floating chart pins are a latent outage class. The "2.0 then pin" position assumed minor-version bumps wouldn't introduce breaking changes; both metallb 0.15 → 0.16 and synology-csi 0.11.1 → 0.11.2 disprove that assumption in one session. Pin everything, accept Renovate PR churn.
- CSI as cluster infrastructure: where CSI is required to be present (anywhere kubelet might unmount a PVC) is the wrong question. The right question is: *what's the cost when CSI is missing?* When stateful pods exist on a node, the cost is unmount-hang and Multi-Attach blockers. The CSI DaemonSet should be present on every node where stateful pods can land, which on a 6-node cluster with workload-isolation taints means: workers only. But the eviction transition is dangerous — drain stateful pods first.
- Assistant pattern across the session: asserting from priors before verifying. Caught at least ten times by the user during the deploy. Recurring themes: SATA-vs-NVMe assumption, StatefulSet scale-down ordinal misread, "CSI eviction is desirable side effect" repeated without checking, multiple version/feature assumptions. The pre-flight discipline in CLAUDE.md is exactly what prevents this; the gap is *applying* it consistently in long sessions, not in writing it down.

### 2026-05-21 evening — Phase 4a cleanup: hostkey corruption, iSCSI orphan reconciliation, Authentik restart pattern triage

Follow-on cleanup session after Phase 4a. Three items on the queue: einherjar-urd sshd dead, orphan iSCSI sessions on einherjar-skuld, authentik-server with 53 restarts. All three closed in one session.

**einherjar-urd sshd dead — root cause: NUC7-era crash corrupted hostkeys mid-write.**

Diagnosis ladder narrowed quickly. `journalctl` showed `sshd: no hostkeys available -- exiting.` on the 21:34 boot. `ls -la /etc/ssh/ssh_host_*_key` revealed three zero-byte files with mtime `2026-05-20 07:00`. `last reboot` showed three "still running" entries spanning May 17 11:38 → May 21 21:34 — phantom entries left by sessions that ended unclean. `journalctl --list-boots` showed only the current boot, meaning journald was running in volatile mode (`/var/log/journal/` didn't exist) on every prior boot — journal data from the crashes is permanently gone. `find /etc /var -size 0 -newermt '2026-05-20 06:30' ! -newermt '2026-05-20 07:30'` surfaced corroborating evidence: the cloud-init instance directory's semaphore files (zero-byte by design — markers only), but also `network-config.json` (real content, should not be empty). Five-minute gap between the corruption mtimes (07:00) and the next boot (07:05) supports an "in-flight writes lost during crash, journal replay restored inode metadata but not data" mechanism rather than a write-time bug.

Attribution: the NUC7 was running einherjar-urd between May 19 and the NUC7's terminal crash. The May 20 07:00 crash is one of the three "still running" entries and falls within NUC7's life. einherjar-urd's VM disk physically traveled NUC7 → Cubi (memory + disk swap when replacement hardware arrived), so the corruption survived intact onto the current hardware. sshd kept serving from in-memory hostkeys for ~25 hours after the corruption — it loads hostkeys at start and doesn't re-read them per-connection — until the May 21 21:34 boot (Phase 4a's reboot) was the first sshd start after the corruption. That's when the failure surfaced.

Recovery: `rm` the empties (`ssh-keygen -A` treats `exists` as `skip`), `ssh-keygen -A`, `systemctl start sshd`, then `ssh-keygen -R` on control nodes. Functional in ~30 seconds once diagnosed.

Cause closed at the architectural level rather than the proximate level: the corruption mechanism doesn't depend on cloud-init or NUC7 specifically — any hard crash mid-write to hostkey files produces the same outcome, and ext4's journal-restores-metadata-not-data behavior is by design. Going-forward defense: baseline role asserts hostkey existence + non-emptiness on every play, regenerates if not. Same OS-invariant class as resolv.conf management. CLAUDE.md gotchas added for the hostkey class itself and the `last reboot` diagnostic technique.

**Orphan iSCSI sessions on einherjar-skuld — partial cleanup, structural class identified.**

The three UUIDs flagged in the Phase 4a incident log (`pvc-c519f687`, `pvc-31b7c785`, `pvc-3264d489`) had already aged out by the time this session ran — no active sessions, no node records. Either cleaned earlier, or torn down by reboots between Phase 4a and now.

Current state on einherjar-skuld: two active sessions (`pvc-76ac564c` Redis, `pvc-e5b5e842` vault-1), both legitimate consumers running on this worker. Plus four node records — the two active sessions' records, plus two stale entries for `pvc-450130ba` (vault-0, actually on einherjar-urd) and `pvc-60a43ab8` (vault-2, actually on einherjar-verd). Cross-checked via `kubectl get volumeattachment` to confirm K8s thinks the latter two are bound elsewhere. Cleaned via `iscsiadm -m node -o delete` for the two orphan records; sessions left alone. No NAS-side cleanup needed (LUNs are legitimately in use by the relocated consumers).

**Structural finding:** This is a different class from the existing "stale session after ungraceful reboot" gotcha. iSCSI node records (the persistent reconnect config in `/var/lib/iscsi/nodes/`) are NOT touched by kubelet's CSI hooks when pods migrate. After every worker-to-worker pod migration involving an iSCSI PV, the source worker keeps its node record forever. Latent risk: on the source worker's next iscsid restart or boot, it will attempt to log in to all known targets — including stale ones — and may succeed, creating cross-node sessions that block legitimate consumers. The cleanup task is recurring, not one-time. Pending architectural decision: automate via timer + diff script vs accept periodic manual sweeps. CLAUDE.md gotcha added.

**authentik-server with 53 restarts on einherjar-skuld — not currently restarting, closed.**

Last restart was `2026-05-21 17:56 UTC`, exit code 0, reason `Completed`. Clean self-shutdown, not OOM (would be 137 `OOMKilled`), not crash (non-zero), not liveness-probe-kill in the usual signal sense. Logs from the previous container showed a normal shutdown sequence with a DNS issue ~80 minutes prior (unrelated to the shutdown). Container ran exactly 10 minutes (started 17:46:11, finished 17:56:11) — suggestive of probe-driven recycling or an internal lifecycle hook, but not investigated further since the pod has been stable for ~4 hours at session close and the two newer server replicas (born during Phase 4a's reshuffle) have 0 restarts each on einherjar-verd and einherjar-urd.

Interpretation: the accumulated 53 restarts are a historical artifact of the pre-4a workload-concentration period when this pod was colocated with Redis + vault-1 on the stressed einherjar-skuld node. Post-Phase 4a, load has redistributed and this pod has stabilized. Monitoring posture: if the restart count climbs past ~55 in the coming days, that's a recurrence worth investigating — at that point `kubectl get events` at the moment of a fresh restart will tell us whether it's probe-driven, OOM-driven, or something else.

**Resolution:** All three Phase 4a cleanup items closed. Two architectural decisions resolved: (a) Synology CSI DaemonSet stays workers-only — the cross-node-iSCSI-fight class is the bigger risk, and the unmount-hang risk is mitigated by a "drain stateful workloads from a CP before tainting" operational rule; (b) helm chart pin policy deferred to a separate decision (still pending — open questions section).

**Root-cause patterns:**
- *Failure modes that hide in in-memory state.* sshd was the example today: a daemon holding loaded state survives the on-disk corruption that should have killed it, until a restart forces a re-read. Long-lived daemons that load config/keys/state at start without re-reading per-operation are a category — they convert "broken now" into "broken on next restart, days later, with no obvious correlation."
- *Persistent-state cleanup that no system owns.* iSCSI node records are CSI-managed during attach but unowned during pod migration. K8s doesn't know about them, CSI doesn't clean them, the kubelet doesn't clean them. Recurring orphan classes that fall through ownership gaps need either explicit automation or operational discipline.
- *Diagnostic-data loss as a separate failure.* Journald running volatile meant we couldn't see what cloud-init was doing at the moment of corruption. Decided not to fix the journald gap (VictoriaLogs is the long-term home), but flagged as a known tradeoff — future post-mortems on similar incidents will be evidence-limited until VictoriaLogs lands.

### 2026-05-22 — Phase 4b + einherjar-urd worker rebuild

Two operations in one session, neither catastrophic. The Phase 4b CP migration (Göndul Verd → Urd) went clean. The follow-on worker rebuild (einherjar-urd template/template_node correction) surfaced four findings — three of them about the procedure rather than the infrastructure.

**What landed in IaC:**
- `terraform/proxmox/asgard-k3s/main.tf` — `locals.control_planes.gondul.node` Verd → Urd (Phase 4b).
- `terraform/proxmox/asgard-k3s/main.tf` — einherjar-urd worker `template` + `template_node` corrected to Urd (worker rebuild).
- `ansible/roles/k3s/tasks/network.yml` — `lineinfile` task for `/etc/iproute2/rt_tables` gained `create: yes` + explicit `owner: root` / `group: root` / `mode: '0644'`.
- `docs/teardown-rebuild.md` — Appendix C added for stateful worker rebuild procedure.
- `docs/homelab-design.md` Open questions — Phase 4b closed; new pending items for `serial: 1` default and OS-update tagging.
- `CLAUDE.md` — Known gotchas additions; current build status flipped for Phase 4b.

**Findings, in order of discovery:**

1. **Orphan LVs from a host that died mid-clone** blocked Phase 4b's first `terraform apply`. `lvcreate` failed because `pve/vm-2001-cloudinit` already existed on Urd. Source: an attempted Göndul migration during the Urd-on-NUC7 era that froze mid-clone, leaving LVs allocated with no VM config to manage them. The Proxmox UI couldn't clean them — the "Remove" action needs the VM config to exist. Recovery: `qm list | grep 2001` (empty — no phantom config), `lvs | grep 2001` (two orphans), `lvremove -f /dev/pve/vm-2001-cloudinit /dev/pve/vm-2001-disk-0`. Then `terraform apply` succeeded. Class of gotcha: **mid-clone host failure leaves orphan LVs**. Documented in CLAUDE.md.

2. **Worker rebuild — pod anti-affinity blocks cordon+migrate.** The drafted worker-rebuild procedure tried to migrate vault-0 off einherjar-urd by cordoning + `kubectl delete pod -n vault vault-0`, expecting the new pod to land on einherjar-verd or einherjar-skuld. It went Pending instead. `kubectl describe pod` showed `2 node(s) didn't match pod anti-affinity rules` — Vault's chart ships pod anti-affinity as `requiredDuringSchedulingIgnoredDuringExecution`. At 3 replicas on 3 workers, there's literally no other worker that satisfies the rule. **Architectural lesson:** the cordon+migrate dance is fundamentally incompatible with hard-required pod anti-affinity at full-replication. Two options: (a) relax anti-affinity to `preferred` for the maintenance window (HelmRelease values change + Flux suspend/resume — significant surface area), (b) accept 2/3 voters during the rebuild window. Picked (b) — the gain from staying at 3/3 voters for a ~25 min window is not worth the surface area of (a). Captured as decision row.

3. **iproute 6.17 doesn't ship `/etc/iproute2/rt_tables`.** Playbook ran cleanly on fresh einherjar-urd through baseline + most of the K3s role's `network.yml`, then errored at "Add custom routing table for VLAN 20" with `Destination /etc/iproute2/rt_tables does not exist !`. Diagnosis: `rpm -ql iproute | grep rt_tables` → `/usr/share/iproute2/rt_tables` (only). The package's stock file is at `/usr/share/`; `/etc/iproute2/rt_tables` is the user-editable override and is NOT shipped by iproute 6.17. Older workers (einherjar-verd, einherjar-skuld) have `/etc/iproute2/rt_tables` only because the role's earlier `lineinfile` task created it side-effect-style under a more permissive Ansible/iproute combination, or because earlier iproute versions did ship it at `/etc/`. Fresh install on RHEL 9 + iproute 6.17 reveals the latent role bug. **Fix shipped** in the same session: `lineinfile` task gained `create: yes` plus explicit `owner` / `group` / `mode`. The CLAUDE.md "always set owner/group/mode explicitly" gotcha — already documented — would have caught this if applied; reinforces that the rule is meant for *every* file-creating task, including `lineinfile`. Documented as its own gotcha class in CLAUDE.md.

4. **Procedure drafting pattern — failed to apply documented gotchas to the case at hand.** The worker-rebuild procedure draft missed two things that were already in CLAUDE.md and homelab-design.md: (a) the StatefulSet scale-down ordinal behavior (scaling Vault to 2 drops vault-2, not vault-0 — user caught this); (b) pod anti-affinity as a scheduling blocker for migration (no separate doc entry, but the related CSI-pinning gotchas were close enough that this should have been pre-flighted). Same pattern flagged at Phase 4a session close: "gap is applying the discipline consistently in long sessions, not in writing it down." Worth keeping the assistant's procedure drafts under review specifically for "what gotchas would I hit if I executed this exactly as written" — not just "what gotchas relate to the components involved."

**Successes worth noting:**
- Phase 4b itself: clean execution of Appendix B (kubectl delete node + `-e k3s_init_node=hlokk` override + ssh-keygen -R) once the orphan LVs were cleared. No duplicate-node-name loop, no surprise etcd churn, Vault Raft stayed 3/3 throughout.
- The 2/3-voters worker rebuild path: ~25 min window, vault-0 came back to the new einherjar-urd within ~30 seconds of the new node going Ready, Raft reconverged without manual intervention. The "operationally fine" framing of accepting 2/3 for short windows was empirically validated.
- All findings closed in the same session (orphan LVs cleaned, anti-affinity worked around by 2/3 acceptance, iproute fix landed in IaC, procedure documented as Appendix C).

**Resolution:** Cluster healthy. Göndul on Urd (Hardware refresh + Phase 4b realized the original 2026-05-14 design intent). einherjar-urd on Urd hardware with corrected TF template references. Vault Raft 3/3. asgard-health + vault-health both green. Phase 4b ticked in build sequence; worker rebuild added as a row.

**Root-cause patterns:**
- *Orphan state surviving host hardware events.* The orphan LVs that blocked Phase 4b came from a NUC7-era event days earlier; Proxmox's destroy path doesn't reach into LVs once the VM config is gone. Class of "state surviving outside the orchestrator's view" — same shape as the iSCSI node-record class flagged after Phase 4a. Whenever hardware-level state mutates without going through the IaC layer, the IaC layer's destroy is incomplete.
- *Hard-required scheduling constraints at full replication.* Pod anti-affinity tuned for "no two replicas on the same node" works as intended at HA-level — until you need to evict one of those replicas for maintenance. With N replicas spread across exactly N nodes, you can't move any of them anywhere. This argues for *either* (N+1) workers (always one spare slot) *or* `preferred` anti-affinity (degrades gracefully). The cluster has 3 workers and 3 Vault replicas; the constraint is structural.
- *Procedure-design discipline ≠ doc-writing discipline.* The same gotcha can be both well-documented and missed during procedure drafting. Worth a pre-flight step explicitly: "for each step of this procedure, what gotcha-class entries apply?" — not "for each component involved, what gotchas are documented?"

### 2026-05-22 evening — Phase 5e.1: Traefik + Gateway API + cert-manager + Authentik HTTPS cutover

**Goal:** Bring up the cluster edge stack. Gateway API CRDs, cert-manager with DNS-01 issuers, Traefik on a single edge LB, wildcard cert for `*.niflheim.xiiisins.com`, Authentik exposed at `https://authentik.niflheim.xiiisins.com` and the original `.12` LoadBalancer released. Sub-phases 5e.1.a → 5e.1.i.

**Process:** Cloudflare token + 1Password + Vault entry (5e.1.a) → vendor Gateway API v1.5.1 Standard CRDs (5e.1.b) → cert-manager v1.19.0 with `enableGatewayAPI: true` (5e.1.c) → ExternalSecret + ClusterIssuers staging+prod (5e.1.d) → MetalLB pool extension + Traefik v40.2.0 with Gateway API provider (5e.1.e) → Gateway `niflheim` + wildcard Certificate (5e.1.f) → Authentik HTTPRoute attaching cross-namespace (5e.1.g) → flip Certificate's `issuerRef` from staging to prod (5e.1.h) → AdGuard DNS cutover + Authentik Service `LoadBalancer` → `ClusterIP` (5e.1.i). Wall-clock ~4 hours including ~90 min of recovery from the Traefik upgrade-rollback deadlock.

**Findings, in rough order of discovery:**

1. **MetalLB-announced VIPs don't respond to ICMP.** Initial reachability test on `10.0.20.10` via `ping` returned "Destination Host Unreachable" sourced from the worker's VLAN 21 IP. Diagnosis path went down rp_filter / policy routing / speaker logs / worker plumbing — all fine. The actual reason: kube-proxy only DNATs for Service-defined TCP/UDP ports; ICMP falls through to the kernel's forwarding path, finds no route for the un-bound VIP, emits ICMP-Unreachable. **TCP curl on :80 and :443 immediately worked.** *All future MetalLB reachability checks use TCP on the Service's defined ports — never ICMP.* Documented as gotcha in CLAUDE.md.

2. **Traefik chart v39+ broke shorthand entrypoint syntax — three schema violations in one go.** Initial deploy failed Helm schema validation with `additional properties 'redirectTo' not allowed`, `additional properties 'tls' not allowed`, `additional properties 'kubernetesIngressRoute' not allowed`. Three separate breaking changes in chart v39/v40: (a) `redirectTo` → `http.redirections.entryPoint`, (b) `tls.enabled` → `http.tls: {}`, (c) `kubernetesIngressRoute` was never a provider toggle — it's `kubernetesCRD` and handles all Traefik CRDs together. Fixed by rewriting the `ports:` block and removing the provider toggle. Documented as gotcha. Lesson: when picking a new chart version, read the upstream changelog for breaking schema changes — relying on AI memory of the chart's value structure is unreliable across major chart releases.

3. **Gateway listener `port:` matches Traefik's internal entrypoint port, not the Service's `exposedPort`.** With chart-default `web` on `:8000` (Service exposes :80 → :8000) and Gateway listener `port: 80`, Traefik logged `PortUnavailable` ("no matching entryPoint for port 80 and protocol HTTPS"). Two fixes available: (a) Gateway listeners on `port: 8000 / 8443`, conceptually muddy; (b) Traefik binds 80/443 directly via `NET_BIND_SERVICE`. Picked (b) — canonical upstream pattern, manifests read naturally. Documented as gotcha + decision row.

4. **YAML key casing is silently dropped.** First attempt to add `strategy.rollingUpdate` to the Traefik HelmRelease had `rollingupdate:` (lowercase u) — schema-validated cleanly, ignored at apply, default `maxSurge: 25%` kicked in. Visible symptom was just "my override didn't apply." General lesson worth surfacing: **schema validation is not a typo checker** — when an override silently doesn't take, the manifest probably has a casing or spelling issue. Documented as gotcha.

5. **`Required` pod anti-affinity + RollingUpdate without `maxSurge: 0` deadlocks the cluster.** 3 Traefik replicas, `requiredDuringScheduling` anti-affinity on hostname, 3 workers — filled. Default `maxSurge: 25%` rounds to +1; Deployment controller tries to create a 4th pod with anti-affinity, no 4th worker, pod sits Pending. **Fix:** explicit `strategy.rollingUpdate.maxSurge: 0, maxUnavailable: 1`. Trade: briefly serves on 2/3 during a roll. Documented as gotcha + decision row.

6. **Helm rollback-on-failure removes the values-fix that was supposed to recover the deploy.** The combination of (4) and (5) put the HelmRelease into a stuck state: Helm's `install.remediation` did a clean rollback to v1, but v1's values lacked our `maxSurge: 0` (because the original install didn't have it either). Subsequent retries rolled forward to a v4 attempt that tried to apply the new strategy *on top of* a Deployment that had been reset to v1's strategy by the rollback — Deployment controller surged into a 4th pod *before* the upgrade could take effect → Pending pod → upgrade times out → rollback fires again → loop. Recovery: `flux suspend hr`, `kubectl rollout pause deploy traefik`, `kubectl patch` the live Deployment's strategy to `maxSurge: 0` directly, `kubectl delete rs <orphan>`, `kubectl rollout resume deploy`, `flux resume hr`. The key insight: **don't let Flux drive the upgrade until the live Deployment's strategy already matches Git** — otherwise the Deployment controller's reconcile races Helm's apply and re-deadlocks. Documented as gotcha. The wall-clock cost of recovering from this was the largest single time-sink of the night.

7. **AGH Sync drift — implementation present but sync interval was too long for operational tempo.** During 5e.1.i DNS cutover, only Saga had the new `authentik.niflheim.xiiisins.com → 10.0.20.10` rewrite; Mimir and Kvasir still served the old `.12`. Initial diagnosis sequence missed the actual binary on Saga (search commands didn't find it under the names tried — but the binary IS at `/usr/local/bin/adguardhome-sync`, service `adguardhome-sync.service`, config `/etc/adguardhome-sync.yaml`). Sync logs showed "Sync done" on a 30-minute cron, but the duration was reported as `2.29e-07s` — sub-microsecond, suspiciously low. Even after the 22:00 sync cycle, rewrites hadn't propagated. **Workaround during the session:** bumped cron from `*/30` to `*/1`, restarted the service, sync converged. **Pending investigation:** why the 30-min cron's "Sync done" was a no-op when origin had changes. Could be internal cache that survives between cron firings but not across restarts; could be a UI-vs-API state lag on AGH origin. **Pending Ansible role change:** sync interval `*/1 * * * *` (operational tempo), document the binary path in the role so future search-by-grep finds it. Documented as two gotchas + pending task.

8. **MetalLB pool extension landed alongside Traefik in same commit — clean.** Pool went `.11–.99` → `.10–.99` in `metallb-config/metallb-config.yaml` simultaneously with Traefik claiming `.10` via `loadBalancerIP`. No `<pending>` window; the kustomize-controller applied the pool update first (instant), then the HelmRelease started the chart install (minutes), and by the time the chart's Service requested `.10` the pool already had it. Validates the "ship pool + consumer in same commit" pattern for any future LB additions.

**Successes worth noting:**

- **End-to-end cert flow worked first try in concept.** Once cert-manager had `enableGatewayAPI: true` and the ClusterIssuers were Ready, the wildcard Certificate issued from staging in ~3 minutes (DNS-01 propagation against Cloudflare). The flip from staging → prod was a one-line `issuerRef` change + Flux reconcile; cert-manager re-issued cleanly, the Gateway picked up the new Secret without a Traefik restart, active TLS sessions kept their old cert (no drop), new sessions got the prod cert. Hot cert reload is a real thing.
- **Cross-namespace HTTPRoute attachment worked transparently.** Gateway in `traefik` namespace, HTTPRoute in `authentik` namespace, `parentRefs` references the cross-namespace Gateway by name + namespace. Allowed by Gateway's `allowedRoutes.namespaces.from: All`. No `ReferenceGrant` needed because the *backend* (authentik-server) is in the same namespace as the HTTPRoute. Clean separation of "platform owns Gateway, app owns route."
- **Authentik brand blueprint flowed through.** First request through the new HTTPRoute landed on Authentik, which matched the hostname `authentik.niflheim.xiiisins.com` against the brand blueprint and served the `<title>niflheim</title>` branded HTML. Independent decisions (blueprint as IaC, day-1 brand entry, custom domain) all composed correctly.
- **Browser-trusted HTTPS for internal services.** End-to-end: AdGuard DNS → MetalLB → Traefik → Gateway → HTTPRoute → Authentik. Padlock icon, real Let's Encrypt chain (E8 intermediate), no warnings. The goal of 5e.1.

**Resolution:** Cluster edge stack live. `https://authentik.niflheim.xiiisins.com` reachable from any device using the AdGuard VIP. Original Authentik LB on `.12` released. Pending: restore cert-manager + Traefik `install.remediation.retries: -1` → `3` post-success.

**Root-cause patterns:**
- *Schema validation is shape-checking, not correctness-checking.* Kubernetes (and Helm) only reject fields the schema *knows are wrong*. Misspelled/miscased fields slip through and become silent drops. When an override doesn't take effect, `kubectl get -o yaml` and grep for the actual key, don't trust the source-of-truth manifest.
- *Hard-required scheduling constraints need explicit rollout strategy planning.* Same pattern as the worker-rebuild finding — required anti-affinity at N replicas on N nodes is operationally brittle without explicit `maxSurge: 0`. Documented now as a gotcha class.
- *Helm rollback can erase the values that were trying to fix the failure.* The remediation logic helps for transient failures (network blip during install) but actively hurts when the new values are themselves the fix. For first-deploy debugging of charts with non-trivial values, `retries: -1` is the safe default; restore `retries: 3` only after success.
- *Operational tempo on infrastructure-sync tools needs to match operational reality.* 30-min sync cron made sense when AGH was being treated as static config (set up once, rarely touched); for an active homelab where DNS changes happen during cutovers, that interval is structurally wrong. Pattern generalizes: any "background sync" timer should be tuned to its actual change-rate, not to "what feels conservative."

### 2026-05-23 — Phase 5e.2 sub-phase ordering: HTTPRoute migration skipped, surfaced 12h later

During Phase 5e.2 close-out (sub-phases `5e.2.f`–`5e.2.j`), the Authentik HTTPRoute migration step (`5e.2.g` — attach to midgard Gateway with new hostnames) was effectively skipped. The validation step (`5e.2.i`) reported "all green" but only verified WebFinger (which has its own apex-static route) — the external Authentik check would have failed had it actually run. Cleanup (`5e.2.j`) ran on top of the unmigrated state, removing the `authentik.niflheim.xiiisins.com` AdGuard rewrite and leaving the HTTPRoute *still attached to the niflheim Gateway with the old hostname*.

Symptom didn't surface until ~12h later, at the start of `5e.3.b` Terraform work, when the Authentik provider couldn't reach `https://authentik.xiiisins.com/api/v3/...` — every request 404'd. After ruling out Cloudflare/tunnel/cert issues, `kubectl get httproute -n authentik` showed `HOSTNAMES: ["authentik.niflheim.xiiisins.com"]` — never updated. Internal access via `authentik.midgard.xiiisins.com` also 404'd because Traefik had no route matching that hostname (AdGuard rewrite existed but rewrote to nothing).

Fix was straightforward: edit `httproute.yaml` to attach to midgard Gateway across both `websecure-midgard` and `websecure-apex-wildcard` listeners, with hostnames `authentik.midgard.xiiisins.com` + `authentik.xiiisins.com`. Reconcile, immediate green on both internal and external paths.

**Root cause:** validation step trusted "looks green" rather than actually exercising the external Authentik path. The validation curls listed in `5e.2.i` included WebFinger but the Authentik external check was either skipped or its 404 misread as expected.

**Findings:**
- *Validation steps must EXERCISE the thing they claim to validate.* WebFinger 200 doesn't prove Authentik 200; they're separate routes. Each external endpoint claimed in a phase's "done" needs its own concrete curl test that actually hits THAT endpoint and verifies a non-error response. "Validated" with no captured output is not validated.
- *Cleanup sub-phases that depend on prior migration sub-phases should fail loudly if the migration didn't happen.* `5e.2.j` removed the old `niflheim` rewrite without verifying the migrated route was live. A precondition check before cleanup (e.g. `kubectl get httproute -n authentik -o json | jq -e '.items[0].spec.parentRefs[].name == "midgard"'`) would have caught the missed migration.
- *Sub-phase plans should include explicit validation gates between dependent steps.* The `5e.2.g → h → i → j` chain has implicit dependencies; making them explicit (validation must pass before cleanup can run) is cheap and catches this class of error.

### 2026-05-23 — Authentik PG DNS resolution flapping recurrence (58 restarts in 24h)

While diagnosing the HTTPRoute issue above, surfaced a separate latent issue: `authentik-server-86cf768686-7h4z6` showed **58 restarts in 24h** (other Authentik pods: 5 and 4 restarts in same window). Last 50 lines of `--previous` logs showed the pattern: pod boots → loops on `PostgreSQL connection failed, retrying... (failed to resolve host 'fulla.niflheim.xiiisins.com': [Errno -2] Name or service not known)` for ~30 seconds → gunicorn dies → pod restarts → next pod tries → same loop. Pods that ARE running successfully resolved the same name on their own boot — the failures are intermittent, not consistent.

This is the **cached-NXDOMAIN class** identified during the original 2026-05-17 Authentik deploy and documented in the DNS-fallback-resolver decision row. CoreDNS or pod-internal resolver caches NXDOMAIN from a brief moment where the K3s node queried `1.1.1.1` (public resolver) via secondary fallback; cached NXDOMAIN survives the AdGuard primary becoming reachable again. Cache eventually expires, next boot succeeds, but the failed boots accumulate as restart counts.

**Not blocking 5e.3 progress** — Authentik UI works, OIDC works, the pods that ARE up serve traffic correctly. But 58 restarts is *operationally* unhealthy. Needs a real root-cause investigation, not just "retry until it works."

Hypotheses to investigate (logged as pending task):
1. **CoreDNS NXDOMAIN cache TTL** — what TTL is CoreDNS using, and is it longer than the AGH-restart-recovery window?
2. **Pod resolver `options ndots`** — Authentik pods may be issuing queries with `ndots:5` and falling through to `1.1.1.1` on transient AGH unresponsiveness.
3. **AGH sync interval `*/1` recent change** — was Saga briefly unresponsive during a sync? Could explain timing-correlated failures.
4. **K3s node `/etc/resolv.conf` fallback** — if K3s nodes still have a public resolver as secondary in their host `resolv.conf`, that's the upstream path CoreDNS would consult.

Related to but distinct from the 2026-05-17 Authentik deploy finding — that one was a one-time hit during the deploy itself; this is a recurring class that's been firing intermittently for 24+ hours.

### 2026-05-23 — Cloudflare bot protections blocked Tailscale WebFinger probe (403)

During `5e.3.c` (Tailscale custom-OIDC tailnet creation via `login.tailscale.com/start/oidc`), Tailscale's WebFinger probe to `https://xiiisins.com/.well-known/webfinger?resource=acct:ghost@xiiisins.com` returned HTTP 403 from Cloudflare's edge. The pod-side WebFinger response was confirmed working via browser + curl-with-default-headers; only Tailscale's specific probe headers (likely missing or generic User-Agent) triggered the 403.

This is documented in the upstream Authentik-Tailscale community guides as a known Cloudflare-front interaction. Root cause is one or more of Cloudflare's bot/integrity protections (Bot Fight Mode, Browser Integrity Check, Managed Rules) firing on non-browser-shaped requests to public endpoints.

**Resolution:** Disabled the relevant Cloudflare protections globally for the `xiiisins.com` zone to unblock 5e.3.c. Tailscale WebFinger discovery succeeded immediately after, full OIDC + token exchange flow completed, tailnet created with Authentik-bound `ghost@xiiisins.com` user.

**Tracked: Cloudflare re-harden after stability.** Heavy-handed global disable is the *unblock* fix, not the *correct* fix. Correct fix is per-path WAF skip rules scoped to:
- `/.well-known/webfinger` (Tailscale's probe path)
- `/.well-known/openid-configuration` (Tailscale also fetches this for OIDC discovery; will be similarly blocked if protections re-enable globally)
- Possibly the Authentik `/application/o/token/` endpoint if Tailscale's token-exchange POST also fires bot rules

WAF rules should land in `terraform/cloudflare/` (Cloudflare resources in IaC decision row) — not manual UI. Tracked as pending task.

**Findings:**
- *Public endpoints behind Cloudflare need explicit allowlisting for non-browser callers.* The "bot protections on by default" posture is fine for browser-driven traffic but breaks every machine-to-machine integration (Tailscale WebFinger, OIDC, ActivityPub, RSS, ACME — anything HTTP that isn't Chrome).
- *Per-path WAF skip is the right granularity.* Globally disabling bot protection is the unblock; production posture is "default protected, narrowly skipped where protocol-public endpoints exist."
- *WAF/security rules belong in Terraform.* Manual UI configuration drifts; the `terraform/cloudflare/` module already exists and should grow to include zone-level security configuration.

---

## Open questions / pending tasks

**High priority — forward path:**
- [x] **Deploy Authentik + Redis** in asgard K3s. ✅ Deployed 2026-05-17 evening. See incident log entry for the deploy story + the gotchas closed in IaC. Bare-LB / plaintext-HTTP for now; HTTPS lands in Phase 5e.1.
- [x] **Phase 4a — CP workload isolation via taint** ✅ Applied 2026-05-21. All three CPs tainted via kubectl (config-template change registers the taint at fresh bootstrap only — existing nodes need explicit kubectl). Workload pods migrated to workers. Vault HA naturally spread: vault-0/1/2 on einherjar-urd/skuld/verd. See incident log for the ~10 findings from the deploy.
- [x] **Phase 4b — Göndul Verd → Urd migration.** ✅ Applied 2026-05-22. Göndul VM destroyed on Verd and recreated on Urd via `terraform apply --target='proxmox_virtual_environment_vm.control_plane["gondul"]'`; stale etcd member cleared via `kubectl delete node gondul`; rejoined as `--server` (not init) via `-e 'k3s_init_node=hlokk'` override. Surfaced orphan-LV class (NUC7-era partial migration left LVs blocking the first clone — recovered via `lvremove`). Vault Raft stayed 3/3 voters throughout. CP topology is now Göndul on Urd, Hlökk on Verd, Sigrún on Skuld — back to the original 2026-05-14 design intent.
- [x] **Worker rebuild — einherjar-urd template_node correction.** ✅ Applied 2026-05-22 evening (immediately after Phase 4b). Doubled as deliberate validation of the worker-rebuild path; surfaced three new findings (Vault chart's hard-required pod anti-affinity, iproute 6.17 `/etc/iproute2/rt_tables` not shipped, orphan-LV class). Vault accepted 2/3 voters during the ~25 min window by design — see incident log + Appendix C in `docs/teardown-rebuild.md`.
- [x] **Phase 5e.1 — Traefik + Gateway API + cert-manager + Authentik HTTPS cutover.** ✅ Applied 2026-05-22 evening. Gateway API v1.5.1 Standard CRDs vendored, cert-manager v1.19.0 with Gateway API support, Traefik v40.2.0 chart (proxy v3.7.1) with `NET_BIND_SERVICE` for direct 80/443 binding. Wildcard `*.niflheim.xiiisins.com` issued from Let's Encrypt prod (E8 intermediate, ECDSA). Cluster edge stack live. Surfaced eight findings — see incident log entry. Pending immediate: restore cert-manager + Traefik `install.remediation.retries: -1` → `3`.
- [x] **Phase 5e.2 — Cloudflared + apex zone + WebFinger.** ✅ Applied 2026-05-23. Cloudflared 2026.5.0 deployed in `infrastructure/cloudflared/` (3× replicas, hostname anti-affinity, locally-managed tunnel `asgard-k3s`), `credentials.json` via ESO from Vault (`secret/k8s/cloudflared/credentials`) written by `terraform/cloudflare/`. Two new wildcards `*.midgard.xiiisins.com` + apex `xiiisins.com`/`*.xiiisins.com` via cert-manager DNS-01. Second `midgard` Gateway with three HTTPS listeners (`websecure-midgard`, `websecure-apex-wildcard`, `websecure-apex-bare`). WebFinger served by Caddy pod in `apps/apex-static/` (revised from Traefik middleware — see decision row). Authentik migrated to `authentik.xiiisins.com` (external) + `authentik.midgard.xiiisins.com` (internal). End-to-end validated. Surfaced findings: Traefik v3 OSS lacks built-in static-response middleware (revised WebFinger to pod); `tunnel:` field accepts NAME (used `asgard-k3s` to keep config.yaml static across tunnel recreation); QUIC UDP rmem warning benign (kernel default 416KiB < QUIC's 7MiB target, no impact in our traffic envelope).

    **5e.2 prerequisites — closed 2026-05-23:**
    - [x] **Cloudflare account/zone.** ✅ Same Cloudflare account as cert-manager DNS-01 token. Tunnel + DNS records live in the same `xiiisins.com` zone.
    - [x] **Cloudflare Tunnel credential type.** ✅ Locally-managed tunnel: per-tunnel `credentials.json` (JWT) in Vault at `secret/k8s/cloudflared/credentials`, mounted via ESO. `config.yaml` ingress rules in Git as ConfigMap. NOT the `TUNNEL_TOKEN` / remote-managed path (UI-managed config violates "Git is truth"). Account-level `cert.pem` not used at runtime — Terraform handles tunnel/DNS creation.
    - [x] **WebFinger / Authentik admin email.** ✅ `ghost@xiiisins.com` — single human identity across Authentik admin user, WebFinger `subject` field, and (forward) every other OIDC consumer.
    - [x] **5e.2 service scope.** ✅ Authentik + WebFinger + plumbing only. Other externally-exposed services land in their own phases when their internal-only deploys exist.

    **5e.2 sub-phase plan:**
    - [x] **5e.2.a** — Bootstrap `terraform/cloudflare/` module. ✅ 2026-05-23. Cloudflare provider 5.19.0 pinned (IaC pin policy generalized to all providers per row above), Terraform-purpose API token stored in 1Password + local env (NEVER in state). Zone data lookup as smoke test.
    - [x] **5e.2.b** — Two new Certificates in `gateway-config/`. ✅ 2026-05-23. `*.midgard.xiiisins.com` + apex (covers bare `xiiisins.com` + `*.xiiisins.com`). Skipped staging → straight to prod since these are net-new certs (staging is a renewal-validation tool). Renamed existing `certificate-wildcard.yaml` → `certificate-wildcard-niflheim.yaml` for consistency with the per-zone-per-file naming.
    - [x] **5e.2.c** — Second `midgard` Gateway. ✅ 2026-05-23. Three HTTPS listeners (no HTTP — niflheim's no-hostname `web` listener already advertises :80 cluster-wide, second one would trigger Gateway API listener-conflict). Both Gateways on VIP `10.0.20.10`. All listeners `Accepted/ResolvedRefs/Programmed=True`.
    - [x] **5e.2.d** — Terraform tunnel + DNS + Vault KV write. ✅ 2026-05-23. `cloudflare_zero_trust_tunnel_cloudflared.asgard` (v5 provider resource name), `cloudflare_dns_record.authentik` (apex CNAME `proxied=true`), `vault_kv_secret_v2.cloudflared_credentials` writes the `{AccountTag, TunnelSecret, TunnelID}` JSON blob to `secret/k8s/cloudflared/credentials`. Module-ownership decision row settled here.
    - [x] **5e.2.e** — Cloudflared K8s manifests in `infrastructure/cloudflared/`. ✅ 2026-05-23. Hand-rolled Deployment (no Helm chart — same reasoning as Authentik Redis). 3 replicas → 12 tunnel connections to Cloudflare AMS PoPs. Targets Traefik (`https://traefik.traefik.svc.cluster.local:443` with `originRequest.httpHostHeader` + `noTLSVerify: true`) so apex Caddy pod's WebFinger Middleware applies via the apex-bare listener.
    - [x] **5e.2.f** — WebFinger via Caddy pod in `apps/apex-static/` + wire `apps/` Flux Kustomization. ✅ 2026-05-23. Revised from middleware approach (see decision row). Bare-apex DNS record + cloudflared ConfigMap entry for `xiiisins.com` added at the same time. Caddy 2.11.2-alpine, 2 replicas, RFC 7033 `application/jrd+json` response. Cross-namespace HTTPRoute (`apex-static` ns → `traefik` ns Gateway) works without ReferenceGrant because midgard Gateway listeners have `allowedRoutes.namespaces.from: All`.
    - [x] **5e.2.g** — Authentik HTTPRoute migrated to midgard Gateway. ✅ 2026-05-23. Hostnames `authentik.xiiisins.com` + `authentik.midgard.xiiisins.com`. Old niflheim HTTPRoute kept live during cutover for safety.
    - [x] **5e.2.h** — AdGuard rewrite `authentik.midgard.xiiisins.com → 10.0.20.10`. ✅ 2026-05-23. Apex deliberately NOT rewritten — internal clients use midgard FQDN, external clients let public DNS resolve to the tunnel.
    - [x] **5e.2.i** — End-to-end validation. ✅ 2026-05-23. Internal `https://authentik.midgard.xiiisins.com`, external `https://authentik.xiiisins.com`, WebFinger `curl https://xiiisins.com/.well-known/webfinger?resource=acct:ghost@xiiisins.com` (Content-Type `application/jrd+json`) — all three paths green.
    - [x] **5e.2.j** — Cleanup. ✅ 2026-05-23. Old niflheim Authentik HTTPRoute + matching AGH rewrite removed. Cloudflared first-deployed with retries `3` (didn't need the `-1` debug-pattern from 5e.1).
- 🟡 **Phase 5e.3 — Tailscale OIDC blueprints + LXCs** (in progress 2026-05-23). Sub-phases 5e.3.a through 5e.3.c complete; remaining: Terraform tailscale ACL module, LXCs 1113/1114/1115, Munin DSM Tailscale package.

    **5e.3 prerequisites — closed 2026-05-23:**
    - [x] **Tailscale account state.** ✅ Tailnet existed on GitHub signup; replaced 2026-05-23 with fresh custom-OIDC tailnet via `login.tailscale.com/start/oidc`. Free plan, ghost@xiiisins.com primary user.
    - [x] **Tailscale OIDC + WebFinger flow validation.** ✅ 5e.3.c completed 2026-05-23. WebFinger discovery + Authentik OIDC + token exchange validated end-to-end.
    - [x] **LXC OS / template choice.** ✅ Debian 13 (Proxmox default). `baseline` Ansible role already supports both RHEL-family and Debian via `include_tasks` split (`debian.yml` / `redhat.yml`) — no new role needed.
    - [ ] **`/dev/net/tun` passthrough for unprivileged LXCs.** Pending — Tailscale needs TUN; unprivileged LXCs need explicit cgroup device rule + bind-mount. Decision: add `enable_tun` boolean variable to existing LXC Terraform module (single conditional block). Lands in 5e.3.e.
    - [x] **Authentik OIDC provider/application blueprint shape.** ✅ Settled in 5e.3.b. Scopes `openid email profile offline_access`, `sub_mode = user_email`, `default-provider-authorization-implicit-consent` flow, default `scope_profile` provides `groups` claim out of the box (no custom PropertyMapping needed day-1).
    - [x] **Munin Tailscale install path.** ✅ DSM package (Synology's official Tailscale package). Set-and-forget, integrates with DSM auto-start + update story. Note: design doc previously referenced an existing Docker-container OOB Tailscale advertiser on Munin — that was incorrect, Munin currently runs nothing after the wipe.

    **5e.3 sub-phase plan:**
    - [x] **5e.3.a** — Bootstrap `terraform/authentik/` module. ✅ 2026-05-23. Provider `goauthentik/authentik 2026.2.0` pinned. AUTHENTIK_URL + AUTHENTIK_TOKEN via env (token from 1P `Asgard - Authentik - akadmin API token`, also mirrored to Vault at `secret/k8s/authentik/bootstrap-token`). Smoke test: `data "authentik_flow" "default_authentication"` lookup confirms provider auth.
    - [x] **5e.3.b** — Authentik IaC: OAuth2Provider + Application + identity-as-data. ✅ 2026-05-23. Files split: `identity.tf` (users + groups + cross-reference validation via `terraform_data.identity_validation` precondition), `tailscale.tf` (OAuth2 + Application + PolicyBinding), `users.yaml` + `groups.yaml` (data files). Groups: `authentik-admins` (is_superuser), `tailscale-users`, `ssh-users` (future SSSD). Existing `ghost` user migrated via `terraform import` from blueprint to TF; blueprint `01-users.yaml` deleted. Client secret generated → Vault at `secret/k8s/authentik/tailscale-client-secret` per module-ownership rule. Surfaced findings: (a) Terraform state-rename hiccup mid-apply (had to `terraform state mv` to rename `authentik_group.tailscale_users` → `authentik_group.this["tailscale-users"]` after first failed apply left the group orphaned in Authentik); (b) email mismatch — Authentik had `xiii@xiiisins.com`, GitHub primary flipped to `ghost@xiiisins.com`, TF apply corrected Authentik to match.
    - [x] **5e.3.c** — Tailscale OIDC smoke test (gate for LXC work). ✅ 2026-05-23. Initial attempt blocked by Cloudflare bot protections 403'ing the WebFinger probe (see incident log + Cloudflare re-harden pending task). After unblock, full flow worked: WebFinger discovery → Authentik OIDC → token exchange → tailnet provisioned with Authentik IdP. Note: tailnet creation is signup-time-only via `login.tailscale.com/start/oidc` — existing tailnets can't switch IdP via UI (would need support ticket). Old GitHub-bound tailnet was empty so cleanest path was delete-and-recreate.
    - [ ] **5e.3.d** — Bootstrap `terraform/tailscale/` module. Provider `tailscale/tailscale`, API token from Tailscale-issued OAuth client OR a Tailscale-issued API key (TBD at start of sub-phase). ACL policy file as JSON with `autoApprovers` on `tag:subnet-router` + `tag:exit-node`. Initial ACL: full mesh within tailnet (default-allow), tag definitions for subnet-router + exit-node.
    - [ ] **5e.3.e** — LXCs 1113 / 1114 / 1115 in Proxmox via Terraform. Debian 13 cloud-init template, unprivileged, `/dev/net/tun` passthrough via new `enable_tun` variable. Ansible role for Tailscale daemon + authkey (from Vault) + tag application.
    - [ ] **5e.3.f** — Munin DSM Tailscale package install. Manual (DSM Package Center → Tailscale → install), tailnet join via authkey from Vault, tag with `tag:subnet-router`. Documented procedurally — no IaC for the DSM-side install itself, but the authkey provisioning and tag assignment ARE in Terraform ACL.
- [ ] **Create 1Password "Homelab" vault** if not already done. Migrate existing scattered homelab credentials in. Authentik bootstrap creds (akadmin pw + API token + personal user pw) added 2026-05-17. Cloudflare DNS-01 token added 2026-05-22.

**Immediate post-flight from 2026-05-22 Phase 5e.1:**
- [x] **Restore HelmRelease `install.remediation.retries: 3`** on cert-manager + Traefik. ✅ Confirmed already at `3` going into 5e.2; was not changed during 5e.1 close-out.
- [ ] **Document `adguardhome-sync` location and config in the AdGuard role.** Binary at `/usr/local/bin/adguardhome-sync`, systemd unit `adguardhome-sync.service`, config `/etc/adguardhome-sync.yaml`. The Ansible role needs explicit "where this lives" so future search-by-grep finds it without scaling Mt. Documentation.
- [ ] **AGH sync interval `*/30` → `*/1`** in the Ansible role. 30-min cron is too long for operational DNS changes — Phase 5e.1.i cutover exposed this. Sync is cheap and idempotent; overkill is fine.
- [ ] **Investigate `adguardhome-sync` "Sync done" sub-microsecond duration when origin had changes.** During 5e.1.i, multiple cron cycles logged "Sync done" with `2.29e-07s` duration but rewrites didn't propagate to replicas. Restarting the service + bumping interval recovered it. Could be internal cache surviving cron firings, could be UI-vs-API state lag on AGH origin. Worth a real root-cause investigation before assuming `*/1` interval is a real fix.

**Control-node tooling consolidation (pending):**
- [ ] Create 1P item `Ansible - Vault - k3s` (Homelab vault) with fields:
  - `url` = `http://10.0.20.11:8200`
  - `method` = `approle`
  - `username` = current `ANSIBLE_HASHI_VAULT_ROLE_ID` (copy from `~/.config/ansible/vault-approle.env`)
  - `password` = current `ANSIBLE_HASHI_VAULT_SECRET_ID` (copy from same)
  - `secret_id_accessor` = current accessor (from prior 1P storage or `vault list auth/approle/role/ansible-local/secret-id`)
  - `expires_at` = today + 90 days from original generation
- [ ] Confirm 1P item `Asgard - Vault - Root Token` has the root token in its `password` field
- [ ] Commit `<repo>/.config/fish/conf.d/homelab.fish` to repo
- [ ] Symlink: `ln -s <repo-path>/.config/fish/conf.d/homelab.fish ~/.config/fish/conf.d/homelab.fish`
- [ ] `exec fish` (or open a new shell) to source it
- [ ] Smoke test: `homelab-env` loads vars, `set-vault-token root` + `set-vault-token approle` both work, `vault kv get secret/ansible/test/hello` returns the test value after `set-vault-token approle`
- [ ] Dry-run rotation round-trip:
  1. `set-vault-token root`
  2. `rotate-approle ansible-local` — at the "Press enter" prompt, **Ctrl+C** (does NOT update 1P; creates an orphan SecretID in Vault)
  3. `rotate-approle --fix ansible-local` — should find one orphan, show its metadata, prompt; confirm to destroy
  4. `set -e VAULT_TOKEN`

  Validates both flows without rotating the real credential.
- [ ] Delete `~/.config/ansible/vault-approle.env`
- [ ] Delete the standalone `~/.config/fish/functions/ansible-vault-env.fish` (superseded by `homelab.fish`)

**Architectural decisions surfaced by 2026-05-21 Phase 4a — pending:**
- [x] **Synology CSI DaemonSet — should it tolerate CP taint?** ✅ Closed 2026-05-21 evening. Decision: **No, CSI stays workers-only.** The Phase 4a posture (CSI evicted from CPs) is the correct steady state — it closes the cross-node iSCSI session-fight class which was the original motivation. The "stateful pods hung Terminating on tainted CP" footgun is acknowledged and mitigated by an operational rule: **drain stateful workloads from any CP before retainting** in future operations. Current state is stable (workloads are on workers, not CPs, by virtue of the taint plus normal scheduling), so the failure mode is dormant unless we ever untaint + retaint. No HelmRelease values change. CLAUDE.md "CSI eviction footgun" gotcha already captures the operational rule. Decision logged in Key decisions table.
- [x] **Helm chart pin policy — tighten now, not at 2.0?** ✅ Closed 2026-05-22 as Phase 4b prerequisite. Decision: concrete-pin all HelmRelease charts, no minor-floats. Tightened: sealed-secrets 2.18.6, vault 0.32.0, external-secrets 0.20.4, metallb 0.15.3. Renovate stays deferred until stable state — updates are deliberate operations until then. The "pin at 2.0" deferral was retired after the 2026-05-21 Phase 4a session disproved the underlying assumption that minor-version bumps would be safe.

**Cleanup pending from 2026-05-21 Phase 4a:**
- [x] **Diagnose einherjar-urd sshd dead.** ✅ Closed 2026-05-21 evening. Root cause: NUC7-era hard crash on 2026-05-20 07:00 corrupted hostkey files to zero bytes mid-write. Recovery: delete empties, `ssh-keygen -A`, restart sshd. CLAUDE.md gotchas added for hostkey-zero-byte class and `last reboot` "still running" diagnostic. See incident log entry.
- [x] **Clean up orphan iSCSI sessions on einherjar-skuld.** ✅ Closed 2026-05-21 evening. Original three UUIDs had aged out by the time this ran; cleaned two newly-surfaced orphan *node records* from Phase 4a's reshuffle (vault-0 and vault-2 PVs that are now legitimately bound elsewhere). NAS-side cleanup not needed. Structural finding: iSCSI node records persist across pod migrations and are not cleaned by CSI — recurring class, automation-vs-manual-sweep decision pending below. CLAUDE.md gotcha added.
- [x] **Investigate authentik-server pod with 53 restarts on einherjar-skuld.** ✅ Closed 2026-05-21 evening. Not currently restarting; last restart was clean Completed exit (code 0), 4h before session close. Pre-existing pattern attributed to pre-4a workload concentration on einherjar-skuld. Monitoring posture: investigate if count climbs past ~55 in coming days.

**Service backlog (in revised LXC order):**
- [x] **Factorio LXC** (1120, Urd) — ✅ Deployed 2026-05-16. UDP 34197 + TCP 22022 port-forwards active.
- [x] **PostgreSQL Fulla** (1130, Skuld) — ✅ Deployed 2026-05-17. Standalone PG 17 + TLS + management users + DB provisioning machinery. Per-service DB provisioning validated by Authentik (2026-05-17 evening). Cluster expansion to Vör/Idunn (and HAProxy VIP 10.0.10.210) deferred until further consumers exist.
- [ ] **Teamspeak LXC** (1121, Verd) — pointed at Fulla (later at HAProxy VIP once 1133/1134/1135 are up).
- [ ] **Tailscale LXCs** (1113/1114/1115) — Phase 5e.3.
- [ ] **HAProxy** (1133/1134/1135) — PostgreSQL frontend, after PG cluster.
- [ ] **Zabbix LXC** (1102, Skuld).
- [ ] **Jellyfin LXC** (privileged, Urd, QuickSync).
- [ ] **Startpage** (asgard K3s, `apps/`). Personal browser homepage (most-used / daily-use). Pulled into the existing `apex-static` Caddy pod as a second content source — pattern TBD between (1) initContainer `git clone` into `emptyDir` at pod start, refreshed on Flux reconcile, or (2) `git-sync` sidecar polling continuously. Repo is private; pattern (2) needs a deploy key (Vault → ESO → K8s Secret) added before it can start. Phase scope: choose pattern, add the mechanism to `apex-static` pod, expose at apex `/` (Caddyfile `handle` block currently 404s). No new HTTPRoute / no new Service — the existing apex-bare route already serves `xiiisins.com/`.

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
- [x] **Urd hardware refresh** ✅ 2026-05-21. MSI Cubi (i3-1215u + 32GB DDR4 reused + 1TB Lexar NM790) replaces DeskMini JB95 (N5095 + 120GB mSATA). The original 2026-05-14 etcd-storm root cause is removed.
- [x] **Phase 4b — Göndul Verd → Urd** ✅ 2026-05-22. Realises the post-hardware-refresh intent — CP topology now matches the original 2026-05-14 design (Göndul on Urd, Hlökk on Verd, Sigrún on Skuld), valid hardware-wise this time. See incident log entry.
- [x] **Worker rebuild — einherjar-urd template_node** ✅ 2026-05-22. Corrected stale TF `template` / `template_node` references that pointed at Verd's template even though the VM ran on Urd. Doubled as deliberate worker-rebuild path validation.

**K3s role correctness (from 2026-05-17 rebuild + 2026-05-21 Phase 4a + 2026-05-22 worker rebuild):**
- [x] **Config rendering on healthy nodes** ✅ 2026-05-21. `config.yml` split out of `install.yml`; runs always while install + calico still skip when healthy. Config changes now actually render. Discovered during Phase 4a when the new `node-taint:` config wasn't applying.
- [x] **K3s role `lineinfile` task for `/etc/iproute2/rt_tables` needs `create: yes`** ✅ 2026-05-22. Fixed during einherjar-urd worker rebuild — iproute 6.17 doesn't ship the file at `/etc/`, only at `/usr/share/`. Older workers had it from side-effect creation on prior runs. Task now sets `create: yes` + explicit `owner` / `group` / `mode`. CLAUDE.md gotcha added.
- [ ] **Add kubectl-taint convergence task to k3s role.** Companion to the registration-time `node-taint:` config: an idempotent task that runs `kubectl taint node <self> ... --overwrite` if the desired taint state differs from the actual. Closes the gap that bit Phase 4a (config templates only apply at fresh registration, not on existing nodes). Should run on the K3s init node after detect-state confirms cluster reachability.
- [ ] **Default `asgard-k3s.yml` to `serial: 1`.** Currently runs in default-parallel mode. A single role change that triggers `restart-k3s` will fire across all 6 nodes simultaneously — defeating HA. Override path for cluster-from-zero rebuilds: explicit per-invocation `--forks` or `-e ansible_serial=...`. Validated by Phase 4a finding that per-node K3s restart is empirically safe, but simultaneous restart across the cluster is not. Captured 2026-05-22.
- [ ] **Baseline role: tag OS package-update task as `os-updates`, skip-by-default.** Currently `dnf update "*" → latest` + reboot runs on every play. Conflates config-idempotency runs with OS-maintenance runs; the latter should be explicit opt-in via `--tags os-updates` for declared maintenance windows. Captured 2026-05-22. Documented as known behavior in CLAUDE.md gotchas; this task is to change the behavior.
- [ ] **Make restart-k3s handler safe for existing CP members — future-proofing, not blocking.** Phase 4a empirically did NOT trigger "duplicate node name" when the handler fired on healthy CPs. The previous warning was a precaution against fresh-cluster-bootstrap edge cases. The task remains as belt-and-suspenders: options are (a) `kubectl delete node` before restart, (b) `systemctl reload` instead of `restart` where K3s supports it, (c) conditional handler based on existing-member vs fresh-node state.
- [ ] **Backup Flux deploy key.** `flux bootstrap github` reissues idempotently if the deploy key still exists in repo settings, but it's not captured anywhere. Consider an out-of-band backup as a Secret manifest in 1Password, or document the regenerate-on-rebuild flow if leaving as-is.
- [x] **Decide nested-vs-flat Kustomization structure** in `k8s/asgard/infrastructure/` (✅ 2026-05-17 evening — sub-kustomizations per component; forced by Authentik's `configMapGenerator`. Closed during the Authentik deploy. Going-forward standard: every component is a self-contained directory with its own `kustomization.yaml`; parent `infrastructure/kustomization.yaml` only references directories.)
- [ ] **Baseline role: assert SSH hostkeys exist and are non-empty.** Idempotent task that stats `/etc/ssh/ssh_host_{rsa,ecdsa,ed25519}_key`, deletes any zero-byte file (so `ssh-keygen -A` doesn't skip it), runs `ssh-keygen -A` to regenerate missing keys, notifies restart-sshd handler. Same OS-invariant class as the existing `resolv.conf` management. Closes the failure mode that hit einherjar-urd post-NUC7 crash — would have caught the corruption on the very next ansible play after May 20 instead of letting it bide until Phase 4a's reboot. Discovered 2026-05-21 evening.
- [ ] **Automated iSCSI node-record cleanup, or accept periodic manual sweeps.** After every pod migration involving an iSCSI PV, the source worker is left with a stale node record in `/var/lib/iscsi/nodes/`. Options: (a) systemd timer on each worker that diffs `iscsiadm -m node` against `iscsiadm -m session` and removes records with no session AND no PV bound to that worker (cross-checked via `kubectl get volumeattachment`); (b) accept manual sweeps as periodic operational work; (c) check whether Synology CSI's node-stage hooks can be configured to clean up. Defer the decision until enough migrations have happened to feel the pain. Surfaced 2026-05-21 evening — two orphan records on einherjar-skuld from Phase 4a's reshuffle, no active harm but latent footgun if iscsid restarts.

**Authentik-specific follow-ups:**
- [ ] **`/media` PVC for user avatars.** Currently `emptyDir` — pod restart loses uploaded avatars. Pattern when needed: NFS volume from Munin (acceptable here, unlike PG, because `/media` is small reads with rare writes), or revisit the Synology-CSI-RWO-with-spread-affinity question for genuinely multi-writer needs. Defer until users actually upload avatars and complain.
- [ ] **Branding ConfigMap → S3 swap path.** Populator init container is already wired for the flip (busybox `cp` becomes `mc cp`). Pattern preserved as a forward-fit. Trigger: when a branding asset would exceed the 1MiB ConfigMap-per-key ceiling, OR when versioning/rotation of branding assets becomes a thing.

**Standing:**
- [ ] **Cloudflare re-harden after stability.** Bot protections globally disabled on `xiiisins.com` zone during 5e.3.c to unblock Tailscale's WebFinger probe (see incident log entry). Correct posture is per-path WAF skip rules in `terraform/cloudflare/` — narrowly scoped to `/.well-known/webfinger`, `/.well-known/openid-configuration`, possibly Authentik `/application/o/token/`. Production target: "default protected, narrowly skipped where protocol-public endpoints exist." Lands when the immediate 5e.3 work stabilizes; not blocking but reduces security posture in the meantime.
- [ ] **Authentik PG DNS resolution flapping — root cause investigation.** authentik-server pod had 58 restarts in 24h (5e.3.b finding); cached-NXDOMAIN class, same as 2026-05-17. Hypotheses to validate: CoreDNS NXDOMAIN cache TTL, pod resolver `options ndots`, AGH sync timing on Saga, K3s node `/etc/resolv.conf` fallback to `1.1.1.1`. Not blocking but operationally unhealthy.
- [ ] **Authentik force-password-reset-on-first-login.** Authentik has no native flag (upstream issue #19681, still open as of 2026-05-23). Implementation requires custom Expression Policy + Prompt Stage + Stage Binding to the authentication flow. Deferred until needed (single-user homelab + 1P-managed passwords for existing users); revisit when multi-user onboarding starts firing or when upstream lands native support.
- [ ] **Cleanup stale comments after 5e.2.g HTTPRoute migration.** `k8s/asgard/infrastructure/authentik/httproute.yaml` has a comment line that still says "Attach to the niflheim Gateway's HTTPS listener" — fix to reflect midgard. Trivial; bundle with next touch of the file.
- [ ] **Authentik email-change side effect on existing sessions.** 5e.3.b TF apply updated ghost's Authentik email from `xiii@xiiisins.com` to `ghost@xiiisins.com`. Existing Authentik-issued sessions/credentials tied to the old email may need re-issue. Single user, single session, minor — but worth a check that nothing's silently broken.
- [ ] Pin HelmRelease chart versions + activate Renovate. Position revisited after 2026-05-21 Phase 4a — see Architectural decisions section above. Two pins tightened during recovery (`metallb: 0.15.x`, `synology-csi: 0.11.1`); remaining `0.x` pins still floating. Authentik already pinned to `2026.2.3` (identity infra is too important to float). cert-manager pinned to `v1.19.0` and Traefik chart to `40.2.0` from initial 5e.1 deploy.
- [ ] Document the AWS KMS key ARN.
- [ ] Decide which services get external Cloudflare Tunnel exposure (in addition to direct port-forwards via UCG). Standing decision; pick services as their deploy phases land. First entry locked in 5e.2: Authentik. Process going forward — each new service's phase explicitly answers "external via tunnel? yes/no" in its prereqs block.
- [ ] **Retroactive sweep: add file-path header `# <repo-relative-path>` to all source files** (`.tf`, `.yaml`, `.yml`, `.j2`, `.sh`, `.fish`, `.py`) that pre-date the convention (added 2026-05-23). Single sweep commit, grep `head -1 <file>` to identify uncovered files. Convention documented in CLAUDE.md Conventions section.
- [ ] Fallback static HTML doc on Munin for core K3s recovery.
- [ ] AdGuard DNS records for new VMs/services as provisioned.
- [ ] Proxmox HA for asgard LXCs.
- [ ] Jotunheim K3s cluster.
- [ ] Confirm whether Vault's `tls_disable` posture should change — revisit at Vault hardening.
- [ ] Tighten `terraform/vault/versions.tf` Vault provider pin from `~> 4.0` to concrete. Known pending after the IaC pin policy generalized 2026-05-23. Trivial change; bundle with the next intentional Vault provider bump or do standalone.
- [ ] Long-term: migrate from ESO sync-and-cache to Vault Agent / VSO runtime retrieval. Pilot on jotunheim first.
- [ ] Long-term: migrate Vault → OpenBao (~12 months out, once OpenBao has more production track record).
- [ ] **PG: `pg_basebackup` to NFS (Munin)** beyond PBS filesystem snapshot — canonical PG hot-backup pattern. Acceptable to defer; PBS-only is functional crash recovery.
- [ ] **PG cluster expansion** (Vör 1131 / Urd; Idunn 1132 / Verd; HAProxy 1133/1134/1135) — after multiple PG consumers exist to drive failover validation.

---

*This document is a living reference. Update it as decisions change during the build.*