<!-- docs/services/asgard-lxcs.md -->

# Asgard LXCs

| LXC | ID | Node | IP | Role | Status |
|-----|----|------|----|------|--------|
| PBS | 1101 | Skuld | `10.0.11.20` | Proxmox Backup Server | ✅ |
| Zabbix (Hugin) | 1102 | Urd | `10.0.11.21` | Infrastructure monitoring (Zabbix 7.0 LTS) | ✅ |
| Hermod (Notifications) | 1103 | Verd | `10.0.11.22` | AppriseAPI aggregator → Discord (Phase 5h.2). Tag-driven routing (`critical`/`alert`/`media`); routine notifications stay in VL via vlagent. See [`notifications.md`](notifications.md). | ✅ |
| Saga (AdGuard 1) | 1110 | Urd | `10.0.11.201` | DNS primary | ✅ |
| Mimir (AdGuard 2) | 1111 | Verd | `10.0.11.202` | DNS replica | ✅ |
| Kvasir (AdGuard 3) | 1112 | Skuld | `10.0.11.203` | DNS replica | ✅ |
| Bifrost (Tailscale 1) | 1113 | Urd | `10.0.11.213` | Tailscale subnet router (advertises `10.0.0.0/16` supernet; auto-renewing key — Tailscale caps at 90d, see decision row + auth-key gotcha — `tag:subnet-router`). Primary of the bridge-pair. | ✅ |
| Heimdall (Tailscale 2) | 1114 | Verd | `10.0.11.214` | Tailscale subnet router (same routes as Bifrost; auto-renewing key, `tag:subnet-router`). Guardian-of-the-bridge replica — naming-principle: primary defines theme, replica expands within it. | ✅ |
| Gjallarbru (Tailscale 3) | 1115 | Skuld | `10.0.11.215` | Tailscale exit node (`--advertise-exit-node`; auto-renewing key, `tag:exit-node`). The bridge to Helheim — way out of the realm. | ✅ |
| Factorio | 1120 | Urd | `10.0.11.220` | Game server + SFTPGo | ✅ |
| Fulla (PostgreSQL 1) | 1130 | Skuld | `10.0.11.230` | Patroni PG member (PG 17 + TLS, cluster `niflheim-pg`) | ✅ |
| Vör (PostgreSQL 2) | 1131 | Urd | `10.0.11.231` | Patroni PG member | ✅ |
| Idunn (PostgreSQL 3) | 1132 | Verd | `10.0.11.232` | Patroni PG member | ✅ |
| HAProxy 1 (Hlin) | 1133 | Urd | `10.0.11.233` | HAProxy + etcd DCS (PG VIP `10.0.10.210`) | ✅ |
| HAProxy 2 (Eir) | 1134 | Verd | `10.0.11.234` | HAProxy + etcd DCS | ✅ |
| HAProxy 3 (Snotra) | 1135 | Skuld | `10.0.11.235` | HAProxy + etcd DCS | ✅ |
| Jellyfin | TBD | Urd | TBD | Media + QuickSync LXC | 🔲 |

**AdGuard Home:** VIP at `10.0.10.200`. Sync via `adguardhome-sync` binary on Saga. ✅

**Terraform module split.** Most LXCs live in `terraform/proxmox/asgard-lxcs/` (single API-token provider). The Tailscale trio (1113/1114/1115) lives in `terraform/proxmox/asgard-lxcs-root/` (single root@pam provider, needs `PROXMOX_VE_PASSWORD` env) because `device_passthrough` for `/dev/net/tun` is one of the Proxmox API endpoints that only accepts ticket auth — see CLAUDE.md "bpg/proxmox API token can change nesting, NOT other LXC features". The split keeps the main module API-token-only so most applies don't need an `op read` on every run; root-needing LXCs (today Tailscale, future `fuse`/`keyctl`/additional passthroughs) join the `-root` module.

> **LXC build order — revised (2026-05-15):** The original sequence said "asgard K3s before all LXCs" because of Tailscale's dependency on Authentik. That conflates services that *don't* share that dependency. Revised sequence: Factorio (no deps, ship it standalone), then PostgreSQL + Teamspeak (no Authentik dependency), then Authentik + Redis in K3s, then Tailscale LXCs (needs Authentik for SSO).
