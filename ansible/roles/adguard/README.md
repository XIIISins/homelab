<!-- ansible/roles/adguard/README.md -->

# adguard

Installs + configures **AdGuard Home** as a DNS server. Pairs with:

- [`adguardhome-sync`](../adguardhome-sync/) — runs on the primary (Saga) and pushes config to replicas (Mimir, Kvasir) on a `*/1` cron.
- [`keepalived`](../keepalived/) — floats the AGH VIP `10.0.10.200` across the trio with VRID 51 (spacing-by-10 convention vs PG's 61 — see CLAUDE.md "VRID collision" gotcha).

Part of Phase 5b.2 — replaces the manually-installed AGH from the early homelab build.

## What this role does

1. Installs `ca-certificates` + `curl` + `tar`.
2. Stops + disables + masks `systemd-resolved` (releases port 53).
3. Replaces `/etc/resolv.conf` with a static file pointing at `127.0.0.1` (local AdGuard) + one external bootstrap fallback (used while AdGuard is down for upgrades).
4. Downloads the pinned AdGuard Home tarball from upstream + extracts to `/opt/AdGuardHome/`.
5. Renders `AdGuardHome.yaml` from template (idempotent — preserves operator + sync writes unless `adguard_force_overwrite_config: true`).
6. Installs + starts `AdGuardHome.service` systemd unit (capital A — matches upstream convention; chk_adguard keepalived script references this exact name).

## What this role does NOT do

- **Rewrites / clients / filters** — operator owns these via the Saga web UI; adguardhome-sync mirrors to replicas.
- **VIP floating** — separate keepalived role.
- **Bootstrapping the admin password** — the bcrypt hash lives in Vault at `secret/ansible/adguard/admin-password-hash` key `hash`. Mint with `htpasswd -bnBC 10 "" "<password>" | tr -d ':\n'` and stash in Vault + 1P "Asgard - AdGuard - admin login".

## Variables

See [`defaults/main.yml`](defaults/main.yml) for the full list. Most-tuned in `group_vars/adguard_hosts.yml`:

- `adguard_upstream_dns` — recursive resolvers (defaults: Cloudflare + Quad9).
- `adguard_rewrites` — DNS-level overrides applied via the template (optional; bulk lives in the operator-managed web UI state).
- `adguard_querylog_interval_hours` / `adguard_statistics_interval_hours` — retention (default 30 days).

## Operational notes

- **Port 53 conflict**: systemd-resolved is masked. Don't unmask without a coordinated cutover — AdGuard will fail to start otherwise.
- **Listen addresses**: DNS listens on `0.0.0.0` so the VIP and per-node IP both answer. Web UI listens on the per-node VLAN-11 IP only (never on the VIP — the VIP isn't bound when this node isn't VRRP master).
- **Schema version**: pinned in the template (currently `28`). Bumping `adguard_version` past a schema-changing release requires `adguard_force_overwrite_config: true` once + a manual schema reconcile.

## See also

- Phase 5b.2 in [`docs/operations/build-sequence.md`](../../../docs/operations/build-sequence.md)
- CLAUDE.md "Networking → AdGuard" + "Known gotchas → DNS"
