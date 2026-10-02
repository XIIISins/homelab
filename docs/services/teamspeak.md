<!-- docs/services/teamspeak.md -->

# Teamspeak 3 (asgard K3s)

K3s-hosted TeamSpeak 3 server for friends/family voice. Single replica, PostgreSQL backend on the Patroni cluster, MetalLB-fronted UDP+TCP with source-IP preservation for per-IP bans.

Lives in `k8s/asgard/apps/teamspeak/`. Pivoted from a planned LXC (1121 on Verd) on 2026-05-25 — K3s wins on GitOps uniformity; no HA loss because TS failover is DNS-SRV at the client side (`do-ts3.xiiisins.com` on DigitalOcean covers the homelab being down).

## Architecture

| Piece | Where | Notes |
|-------|-------|-------|
| Image | `teamspeak:3.13.7` | Official, TeamSpeak Systems |
| Workload | StatefulSet `teamspeak/teamspeak` | 1 replica, RWO PVC |
| Data | PVC `data-teamspeak-0`, 5Gi, `synology-csi-iscsi-retain` | Logs + `files/` (file transfers); DB lives in PG. Expandable to 20Gi. |
| Database | PG role + DB `teamspeak3` on Patroni cluster | Via HAProxy VIP `10.0.10.210`, `sslmode=require`, scram-sha-256 |
| MetalLB VIP | `10.0.20.12` | Shared across two LB Services via `metallb.universe.tf/allow-shared-ip: teamspeak` |
| External DNS | `hel-ts3.xiiisins.com` → KPN public IPv4 | Terraform-managed (`terraform/cloudflare/ts3.tf`, 10a1). See "DNS" below. |
| SRV ring | `_ts3._udp.ts3.xiiisins.com` → `hel-ts3` (priority 1, homelab) + `do-ts3` (priority 99, DigitalOcean failover) | Terraform-managed (`terraform/cloudflare/ts3.tf`, 10a1); `do-ts3` = the offsite node, see "Offsite failover (do1)". |

### Traffic flow

```
Friend's TS3 client
  → DNS _ts3._udp.ts3.xiiisins.com SRV → hel-ts3.xiiisins.com A → KPN public IPv4
  → KPN Experia (DMZ passthrough)
  → UCG-Ultra WAN
  → Port-forwards (UCG, manual):
      UDP 9987  → 10.0.20.12
      TCP 30033 → 10.0.20.12
  → VLAN 20 (HL-ASG-K3S-VIP). MetalLB L2 elects a worker (eth1)
  → Worker's eth1 (10.0.20.20[1|2|3]) — kube-proxy DNAT (source IP preserved, ETP=Local)
  → Pod (10.42.x.x)
Reply path:
  → Pod → kube-proxy un-DNAT (src = 10.0.20.12) → kernel policy lookup table `vlan20`
  → default via 10.0.20.1 dev eth1   [existing vlan20-policy-routing.service]
  → UCG-Ultra (stateful NAT) → KPN → friend
```

When the homelab is down, the client's SRV resolver falls through to the priority-99 entry (`do-ts3.xiiisins.com`, DigitalOcean) — TeamSpeak's standard client-side failover.

## UCG-Ultra port-forwards (manual, not in IaC)

KPN is never in IaC; UCG port-forwards are documented or they don't exist.

| Public protocol/port | Internal target | Purpose |
|---|---|---|
| UDP 9987 | `10.0.20.12:9987` | TS3 voice |
| TCP 30033 | `10.0.20.12:30033` | TS3 file transfer (avatars, icons, channel files) |

ServerQuery (`10011/TCP`, `10022/TCP`) deliberately NOT forwarded — admin only, ClusterIP + `kubectl port-forward`.

## DNS

**Terraform-managed since Phase 10a1** (decision D2; supersedes the earlier "leave hand-managed" stance): `terraform/cloudflare/ts3.tf` adopts the pre-existing `hel-ts3` / `do-ts3` / `do1` A records and both `_ts3._udp.ts3.xiiisins.com` SRV records via `import {}` blocks, unchanged. The IPs are variables in the gitignored `terraform.tfvars` (the home IP is never committed); `offsite_ip` is the cutover lever for `do-ts3` + `do1`. Edit DNS there, not in the dashboard.

LAN/tailnet clients currently trombone via the public IP (DNS resolves `hel-ts3.xiiisins.com` to the KPN IP, packets exit out the WAN and re-enter via UCG). If this becomes a real cost (latency, bandwidth, conntrack pressure), add an AGH rewrite for `hel-ts3.xiiisins.com` → `10.0.20.12` (same pattern as the existing `factorio.xiiisins.com` apex bypass). Deferred — friends overwhelmingly connect from outside the LAN.

## Offsite failover (do1)

**Status 2026-10-02:** live since 2026-10-01 — `do-ts3.xiiisins.com` and `do1.xiiisins.com` point at `do1`'s reserved IP, TS3 listens on 9987/udp there, restored from a point-in-time snapshot of the legacy DB (135 clients, 26 channels). This failover is **temporary and best-effort**: if it ever misbehaves, wiping its DB is acceptable (users simply reconnect). The homelab TS3 (priority 1) stays the real server.

The priority-99 SRV target (`do-ts3.xiiisins.com`) is the DigitalOcean node `do1` (Phase 10a; procedure [`procedures/offsite-do1.md`](../procedures/offsite-do1.md)). It runs its **own** `teamspeak:3.13.7` container (same version as the K3s server) with an independent **SQLite** DB — it does not share the homelab's PG, since it exists precisely for when the homelab is down. Only `9987/udp` is published (DO cloud firewall + compose); ServerQuery 10011, file transfer 30033 and TSDNS 41144 stay closed. State lives in the bind mount `/opt/do1/ts3-data` (owned by uid 9987); the stack is `/opt/do1/docker-compose.yml`, rendered by the `offsite-node` role. The DB is restored from a SQLite-backup-API dump of the legacy droplet's server (the live DB is WAL-mode — a plain file copy misses data); `PRAGMA integrity_check` must return `ok` before restoring.

**Validation once do1 exists:** (1) stop the homelab TS3 briefly (`kubectl scale statefulset/teamspeak -n teamspeak --replicas=0` — via the operator's normal flow, restore to 1 after), (2) connect a TS3 client to `ts3.xiiisins.com` and confirm it lands on do1 via the SRV fallback with the restored channels/permissions, (3) from an independent vantage confirm `9987/udp` answers and `10011/30033/41144` do not.

## Deploy runbook

Files are staged but not deployed until step 4 commits them.

1. **Mint the PG password in Vault.**
   ```bash
   . ~/.cache/homelab/env.sh && (cd terraform/vault && terraform apply)
   ```
   Creates `random_password.teamspeak_postgres` and writes to:
   - `secret/ansible/postgres/teamspeak3-password` (postgres-common reads this)
   - `secret/k8s/teamspeak/postgres-password` (ESO reads this)

2. **Provision the PG role + DB on Patroni.**
   ```bash
   ansible-playbook playbooks/postgres-host.yml --tags postgres-common-databases
   ```
   Runs leader-only (gated on `patroni_is_leader`). Idempotent — re-run is a no-op. Verify on the current Patroni leader:
   ```bash
   sudo -u postgres psql -c '\du teamspeak3' -c '\l teamspeak3'
   ```

3. **Configure UCG-Ultra port-forwards.**
   UCG-Ultra UI → Settings → Internet → Port Forwarding. Add the two entries from the table above. Confirm UCG's `Internal → Any: Allow` posture lets the elected worker accept inbound on eth1.

4. **Wire into Flux.** Edit `k8s/asgard/apps/kustomization.yaml`, append `- teamspeak` to `resources:`. Commit + push. Flux reconciles within 10 min (or `flux reconcile kustomization apps`).

5. **Capture the admin token.** First-run only — the server prints both `serveradmin` query password and the ServerAdmin privilege token to stdout, once. Capture immediately:
   ```bash
   kubectl logs -n teamspeak teamspeak-0 | grep -A1 -E 'token|loginname|password'
   ```
   Stash in 1Password ("Asgard - Teamspeak - serveradmin" for query creds, "Asgard - Teamspeak - admin token" for the privilege key).

6. **Claim admin in a TS3 client.** Connect to `hel-ts3.xiiisins.com` (or the bare IP). Tools → Use Privilege Key → paste the token from step 5. You're now ServerAdmin.

## Operations

**Restart the server cleanly:** `kubectl rollout restart statefulset/teamspeak -n teamspeak`. Clients disconnect, identity keypair persists in PG, reconnect transparently within a few seconds.

**Admin via ServerQuery:**
```bash
kubectl port-forward -n teamspeak svc/teamspeak-query 10011:10011 &
telnet localhost 10011
# login serveradmin <password-from-1P>
# use sid=1
# <commands>
```

**Reset the serveradmin password** if lost: edit `ts3server.ini` on the PVC and set `serveradmin_password=<new>`, then restart. Or wipe + re-run; clients re-trust on first connect.

**Expand the PVC** when `files/` grows past comfort (up to 20Gi):
```bash
kubectl edit pvc data-teamspeak-0 -n teamspeak
# bump spec.resources.requests.storage to e.g. 10Gi
```
Synology CSI online-resizes the iSCSI LUN, kernel re-reads block geometry, ext4 auto-extends. No restart required.

**Backup posture.** PG is the canonical source of truth (channels, perms, identity keypair). Patroni does WAL streaming + nightly basebackup (Phase 5g.2). The PVC only holds logs + file-transfer files; losing it loses uploaded avatars/icons + log history, not server identity. PBS captures the LUN via Synology iSCSI snapshots — verify post-deploy.

## Why PostgreSQL and not SQLite

TS3 3.13+ ships a first-party PostgreSQL plugin (`ts3db_postgresql`). Using PG instead of the default SQLite:
- Consistent backup/restore through Patroni's WAL pipeline.
- No PVC backup orchestration to invent for a single-row-SQLite-database use case.
- Resilient against PVC-side mishaps (file-transfer dir corruption doesn't drag down server identity).

Tradeoff: an extra moving part. Patroni is rock-solid post-5g.2, so the failure-mode exposure is modest. If PG goes hard-down, the TS server fails to start until it comes back — acceptable, same posture as Authentik/NetBox.

## Why externalTrafficPolicy: Local is non-negotiable

With `Cluster`, kube-proxy SNATs the inbound packet to the elected node's IP. TS3 sees every client as coming from a worker eth1 address — breaking IP-based bans, the built-in flood/connection-rate protection, and `clientconnectip` logging. `Local` + MetalLB L2 preserves the source IP end-to-end; the only constraint is that the MetalLB-elected node must run a pod, which is automatic at one replica.

## Known gotchas referenced

- CLAUDE.md "Synology CSI iSCSI volumes need a chown initContainer for non-root pods" — applied; UID 9987 (`ts3server`) chowned at init.
- CLAUDE.md "PG `hostssl`-only rejects plaintext clients" — `PGSSLMODE=require` on the container so the plugin's libpq doesn't default to `prefer` and silently downgrade.
- CLAUDE.md "VLAN 20 source-based policy routing" — relied on; reply src `10.0.20.12` exits via eth1 thanks to `vlan20-policy-routing.service`.
- CLAUDE.md "Cloudflared targets ClusterIP DNS, NEVER MetalLB IPs" — N/A here. TS is raw UDP/TCP, not behind Traefik. No in-cluster pod needs to dial it, so no CoreDNS rewrite is required either.
