<!-- docs/services/adguard.md -->

# AdGuard Home (DNS, Phase 5b.2 IaC pending)

Three LXCs (Saga / Mimir / Kvasir) running AdGuard Home as the homelab's DNS resolver + ad/tracker blocker. Single VIP `10.0.10.200` floats across the trio via keepalived; clients only ever talk to the VIP, never the per-node IPs. Saga is the canonical config source; `adguardhome-sync` runs on Saga and pushes config to Mimir + Kvasir on a `*/1` cron.

**Status:** Manually installed during the early homelab build (2026-05-13-ish), functionally serving DNS for months. Phase 5b.2 lifts the install into IaC — the manual deploy gets replaced (or imported) once the Phase 5b.2 code commit lands and the operator's cutover runbook (`docs/procedures/agh-cutover.md`) is run. Until then, the AGH service is reliable but its config drifts from any IaC spec — operator edits via web UI on Saga are the truth.

## Architecture

| Layer | Stack | Notes |
|-------|-------|-------|
| Trio | 3 × LXC (Debian 13 trixie), VMIDs 1110/1111/1112 | Saga/Mimir/Kvasir on Urd/Verd/Skuld. Dual-NIC. |
| Network — eth0 | VLAN 11 (HL-ASG-SVC) | DNS queries (port 53) + web UI (port 80). Default gateway. Per-node IPs 10.0.11.201/202/203. |
| Network — eth1 | VLAN 10 (HL-ASG-VIP) | VRRP advertisements + VIP host. Per-node IPs 10.0.10.201/202/203. VIP `10.0.10.200/32` floats across whichever node holds it. |
| App | AdGuard Home v`0.107.74` (current) → v`0.107.76` (IaC pin) | Go binary, ~50MB RSS steady-state. Schema version 34 (`adguard_schema_version` in role defaults). |
| Web UI | port 80 (operator choice) | Lets clients hit `http://saga.niflheim.xiiisins.com/` without a port. To narrow to per-node IP only, override `adguard_web_bind_addresses` in group_vars. |
| Sync | `bakito/adguardhome-sync` v`0.9.0` on Saga only | Cron `*/1 * * * *`, opts out of DNS server_config + DHCP (per-node + UCG-owned respectively). Binary at `/usr/local/bin/adguardhome-sync`, config at `/etc/adguardhome-sync.yaml`. |
| VIP / VRRP | keepalived 1:2.3.3-1 (apt), VRID 51 | Spacing-by-10 convention vs PG HAProxy VIP's 61 (see CLAUDE.md "VRID collision"). |
| VRRP election | all-BACKUP, priority 100/90/80 | Avoids dual-MASTER on simultaneous boot (CLAUDE.md "keepalived all-BACKUP" gotcha). Saga = 100, Mimir = 90, Kvasir = 80. |
| VRRP track-script | `systemctl is-active --quiet AdGuardHome.service`, weight -50 | Demotes the VIP-holder if AdGuard is down; VIP follows a healthy node. |
| Upstream DNS | DoH only — `dns10.quad9.net`, `cloudflare-dns.com` | Bootstrap via Quad9 ECS-aware IPs (`9.9.9.10`, `2620:fe::10`). Fallback to plain `1.1.1.1` + DoH if all primaries fail. |
| Retention | 90 days querylog, 24h statistics | Long history for "what was that device doing last month?"; short aggregated stats keep the dashboard responsive. |

**Sizing.** 1 vCPU / 512 MB / 4 GB disk per LXC. AGH is light; DNS-cache + sqlite query-log are the dominant consumers. Trio total: 3 vCPU / 1.5 GB across all 3 hosts, ~12 GB disk.

**Why three nodes instead of one.** DNS is the most-load-bearing service in the homelab — losing it breaks everything that resolves a hostname. The VIP + keepalived pattern provides automatic failover with single-digit-seconds-of-downtime when the master dies. Three nodes (not two) ensures one can be down for maintenance + still have N+1 redundancy.

## Secrets

Three Vault paths (planned for Phase 5b.2 — not yet populated):

| Vault path | Field | Used by |
|------------|-------|---------|
| `secret/ansible/adguard/admin-password-hash` | `hash` | adguard role — bcrypt hash for the admin user in `AdGuardHome.yaml` |
| `secret/ansible/adguardhome-sync/admin-password` | `password` | adguardhome-sync role — plaintext for HTTP basic-auth into origin + replicas (must match the bcrypt above) |
| `secret/ansible/keepalived/adguard_vrrp` | `auth_pass` | keepalived role — VRRP plaintext auth (limited to 8 chars, see CLAUDE.md "keepalived auth_pass" gotcha) |

For human lookups: 1P item "Asgard - AdGuard - admin login" with the plaintext admin password (used to log into the web UI on Saga).

## DNS query flow

1. Client resolves `<host>` against its configured DNS server.
2. Most clients are configured (via DHCP or static config) with `10.0.10.200` (the AGH VIP) as the DNS server.
3. ARP resolves `10.0.10.200` to whichever node currently holds the VIP — by default Saga (priority 100). If Saga is down, Mimir; if both down, Kvasir.
4. AGH on the VIP-holder receives the query, checks:
   - **Internal rewrites** (operator-managed via web UI on Saga, synced to replicas): if `<host>` matches one of the ~25 rewrites (e.g. `netbox.niflheim.xiiisins.com` → `10.0.20.10`), return the rewrite answer immediately.
   - **Hostsfile / runtime clients** (DHCP, ARP, mDNS): if known, return that.
   - **Filter lists**: if `<host>` is blocked by the enabled filters (`AdGuard DNS filter` is on by default), return NXDOMAIN.
   - Otherwise, forward upstream via DoH to Quad9 / Cloudflare. Bootstrap resolver gets the IP of the DoH endpoint; subsequent queries use the cached HTTPS connection.
5. Response cached locally (4 MiB cache).

For external DNS (the public apex zone `xiiisins.com`), AGH is **NOT** in the path — Cloudflare authoritative DNS handles those queries directly. AGH only answers for `niflheim.xiiisins.com` (internal-only) + `midgard.xiiisins.com` (internal alias for publicly-reachable services) + the few apex-aware rewrites the operator has set up.

## Tailscale clients

Tailscale's MagicDNS (configured in `terraform/tailscale/dns.tf`) split-resolves `niflheim.xiiisins.com` + `midgard.xiiisins.com` through the AGH VIP `10.0.10.200`. Off-LAN tailnet clients get internal hostnames working via the tailnet + the existing subnet routers (Bifrost / Heimdall on tag `subnet-router`, advertising `10.0.0.0/16`).

The apex `xiiisins.com` is deliberately NOT split-DNS'd — preserves "one zone, one resolution path" for the public apex.

## Source-of-truth model

The IaC-vs-NetBox-vs-runtime split for AdGuard:

| Data class | Spec lives in | Notes |
|------------|---------------|-------|
| LXC existence + sizing + placement | `terraform/proxmox/asgard-lxcs/lxcs.tf` `locals.adguard_nodes` | TF |
| LXC → NetBox mirror | `terraform/netbox/vms.tf` `local.vms` entries `saga/Mimir/kvasir` | TF, VMIDs 1110/1111/1112 |
| AGH base config (listen addrs, upstreams, retention, admin user, default filters) | `ansible/roles/adguard/` + `group_vars/adguard_hosts.yml` | Ansible |
| keepalived VIP + VRRP + chk_script | `ansible/inventory/group_vars/adguard_hosts.yml` | Ansible (reuses the generic `keepalived` role from Phase 5g.2) |
| **Rewrites** | NetBox-side: operator UI on Saga | Synced to replicas by adguardhome-sync. NOT in HCL — operator owns. |
| **Clients / persistent / user_rules** | Operator UI on Saga | Synced to replicas. |
| **Filter subscriptions (extra lists)** | Operator UI on Saga | Synced. Default set is in `adguard_filters` role default. |

The "operator owns via UI" surface is intentional — AGH's UI is the natural place to manage day-to-day rewrites + ad-block exceptions. The role only manages the slowly-changing baseline (upstreams, retention, admin auth, schema, listen addrs).

## Recovery model

**Single-node failure (e.g. Verd down):** keepalived demotes the dead node's priority via the track-script (or absence-of-VRRP-advert if it's hard-down); the next-priority peer (in this case Kvasir, with priority 80) wins the election and binds the VIP. Clients keep resolving — ARP cache invalidates in <30s, then routes to the new VIP holder.

**Saga (primary) down:** identical to any other node down — VIP fails over. Sync STOPS during the outage (sync runs only on Saga), so replicas remain frozen on the last-good config. Once Saga comes back, sync resumes; any operator changes made via Mimir's or Kvasir's UI during the outage get OVERWRITTEN by Saga's state. To make changes during a Saga outage stick: edit Saga's `AdGuardHome.yaml` directly (via SSH or Proxmox console), then start sync.

**All three down:** clients fall back to whatever fallback they have configured (typically `1.1.1.1` per the bootstrap-fallback in `/etc/resolv.conf`). Internal rewrites are LOST — `netbox.niflheim.xiiisins.com` etc. become unresolvable until at least one AGH is back. Most homelab clients tolerate this (browser caches; SSH known-host IPs; etc.) but K3s pods will likely flap.

**Disaster recovery (rebuild from scratch):** Saga's `/opt/AdGuardHome/AdGuardHome.yaml` is the canonical state. Back it up regularly (PBS already snapshots the LXC). Worst case: re-deploy via Ansible (gets the BASE config back), then restore the rewrites + clients + filters from a backup of the yaml.

## Operational notes

- **Port 80 collision.** AGH binds port 80 on each node for the web UI. If you ever want to put HTTP services on these LXCs, you'd have to either narrow `adguard_web_bind_addresses` to the per-node IP or change the port.
- **systemd-resolved disabled.** The Ansible role masks `systemd-resolved` to free port 53 for AGH. Don't unmask without a coordinated cutover.
- **`/etc/resolv.conf`** points at `127.0.0.1` (loopback) so DNS works regardless of VRRP state. The pre-IaC manual setup pointed at the VIP, which is a chicken-and-egg when this node isn't VRRP master.
- **Schema version bumps.** AdGuard's config schema can change between minor releases. Set `adguard_force_overwrite_config: true` (one-shot) when bumping `adguard_version` past a schema-changing release; otherwise the role won't overwrite an existing AdGuardHome.yaml.
- **DoH bootstrap.** Bootstrap DNS uses Quad9 ECS-aware IPs (`9.9.9.10` / `2620:fe::10`). If those become unreachable, AGH can't open the DoH connection on startup and answers go silent. Symptom: AGH starts but every query times out. Workaround: temporarily add a plain-DNS upstream (`1.1.1.1`) to `adguard_upstream_dns` until bootstrap recovers.
- **Sync `Sync done` log line is NOT trustworthy** for sub-microsecond durations (CLAUDE.md gotcha). Diagnose drift by comparing `/control/rewrite/list` on origin vs replicas directly.

## Pending follow-ups

- **Phase 5b.2 cutover** — apply the IaC, replace the manual install, populate Vault. See [`docs/procedures/agh-cutover.md`](../procedures/agh-cutover.md).
- **DoH endpoint on the homelab** (Phase 5b.3) — expose AGH via the cluster's wildcard cert so clients can do DoH-to-AGH instead of DoH-to-Quad9. Lets the operator drop plain DNS entirely (currently fallback) and gives mobile clients a homelab-trusted resolver over the tailnet.
- **DHCP** — UCG-Ultra is the DHCP server today. AGH could take over (it supports DHCPv4 + v6) but would couple DNS + DHCP failure domains in a way UCG already separates. Decision deferred.
- **Filter list curation** — operator-managed via UI for now. Could be hoisted into `adguard_filters` group_var if we want IaC-tracked filter subscriptions.

## See also

- Phase 5b.2 in [`docs/operations/build-sequence.md`](../operations/build-sequence.md)
- [`ansible/roles/adguard/README.md`](../../ansible/roles/adguard/README.md)
- [`ansible/roles/adguardhome-sync/README.md`](../../ansible/roles/adguardhome-sync/README.md)
- CLAUDE.md "Known gotchas → DNS", "HAProxy / keepalived" (shares the VIP/VRRP pattern with Phase 5g.2)
- [`docs/procedures/agh-cutover.md`](../procedures/agh-cutover.md) — deployment runbook
