<!-- docs/architecture/network.md -->

# Network design

> **MGMT subnet is `10.0.254.0/24`.** Drafts up to v4 of this document incorrectly recorded it as `10.0.1.0/24`. The live network is and always was `10.0.254.0/24`.

## VLAN table

| VLAN | Subnet | UCG name | Purpose |
|------|--------|----------|---------|
| 1 | `10.0.254.0/24` | HL-MGMT | Management — nodes, NAS, UCG-Ultra |
| 10 | `10.0.10.0/24` | HL-ASG-VIP | Asgard VIPs (keepalived) |
| 11 | `10.0.11.0/24` | HL-ASG-SVC | Asgard LXCs |
| 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP | Asgard K3s MetalLB pool |
| 21 | `10.0.21.0/24` | HL-ASG-K3S-NODE | Asgard K3s nodes (CPs + workers; renamed from `-WRK` 2026-05-23 — both CP and worker nodes share this VLAN) |
| 30 | `10.0.30.0/24` | HL-JOT-K3S-VIP | Jotunheim K3s MetalLB pool |
| 31 | `10.0.31.0/24` | HL-JOT-K3S-NODE | Jotunheim K3s nodes (CPs + workers; renamed from `-WRK` 2026-05-23 for symmetry with VLAN 21) |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage / NFS — stable |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

## IP assignments

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
| `10.0.11.21` | 1102 | Urd | Zabbix — Hugin ✅ |
| `10.0.11.201` | 1110 | Urd | AdGuard Home — Saga ✅ |
| `10.0.11.202` | 1111 | Verd | AdGuard Home — Mimir ✅ |
| `10.0.11.203` | 1112 | Skuld | AdGuard Home — Kvasir ✅ |
| `10.0.11.213` | 1113 | Urd | Tailscale 1 |
| `10.0.11.214` | 1114 | Verd | Tailscale 2 |
| `10.0.11.215` | 1115 | Skuld | Tailscale 3 |
| `10.0.11.220` | 1120 | Urd | Factorio + SFTPGo |
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
| `10.0.21.11` | 2001 | Urd | K3s CP | Göndul |
| `10.0.21.12` | 2002 | Verd | K3s CP | Hlökk |
| `10.0.21.13` | 2003 | Skuld | K3s CP | Sigrún |
| `10.0.21.21` | 2101 | Urd | K3s Worker | Einherjar-urd |
| `10.0.21.22` | 2102 | Verd | K3s Worker | Einherjar-verd |
| `10.0.21.23` | 2103 | Skuld | K3s Worker | Einherjar-skuld |

Göndul moved Urd → Verd on 2026-05-17 (the deferred backlog item from the 2026-05-14 incident), then back Verd → Urd in Phase 4b (2026-05-22). Current placement is **Urd** (Hlökk on Verd, Sigrún on Skuld).

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

## Cluster CIDRs

- Pod CIDR: `10.42.0.0/16` — Ansible var `k3s_pod_cidr`, used for both K3s `cluster-cidr` and the Calico ipPool.
- Service CIDR: `10.43.0.0/16` — Ansible var `k3s_service_cidr`.

## Resource ID scheme

| Range | Type |
|-------|------|
| 1101–1199 | Asgard LXCs (sub-grouped by function) |
| 2001–2999 | Asgard K3s VMs |
| 3001–3999 | Jotunheim K3s VMs |
| 10001+ | Templates |

## LXC ID grouping

| Range | Group |
|-------|-------|
| 1101–1109 | Backup & monitoring (PBS, Zabbix) |
| 1110–1119 | Network infrastructure (AdGuard ×3, Tailscale ×3) |
| 1120–1129 | Services (Factorio; Teamspeak pivoted to a K3s app — no longer an LXC) |
| 1130–1139 | Database (PostgreSQL ×3, HAProxy ×3) |

## DNS naming

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

## Internet exposure & firewall

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
