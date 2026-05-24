<!-- ansible/roles/adguardhome-sync/README.md -->

# adguardhome-sync

Installs + configures [`bakito/adguardhome-sync`](https://github.com/bakito/adguardhome-sync) as a systemd service. Runs on the **primary AdGuard node only** (Saga in our topology) and pushes config to N replicas (Mimir, Kvasir) on a `*/1` cron — closing the long-pending `*/30 → */1` open question.

Part of Phase 5b.2. Pairs with the [`adguard`](../adguard/) role (provides the AGH binary + base config on every node) and the generic [`keepalived`](../keepalived/) role (floats the VIP across the trio).

## When to apply this role

In the AGH playbook, conditionally:

```yaml
- role: adguardhome-sync
  when: inventory_hostname == adguard_primary_host  # i.e. saga
```

The `when:` gate prevents Mimir/Kvasir from accidentally becoming sync sources.

## What it syncs

See `adguardhome_sync_features` in [`defaults/main.yml`](defaults/main.yml). Notable opt-outs:

- **DNS `server_config`** — per-node listen addresses differ (each node binds its own VLAN-11 IP for the web UI). Syncing this would clobber the per-node config.
- **DHCP** — UCG-Ultra is the DHCP server, not AdGuard. Both `serverConfig` + `staticLeases` are off.

Everything else (clients, filters, rewrites, services, query log + stats config, theme) flows from Saga → replicas.

## Secrets

The admin password (plaintext) lives at Vault `secret/ansible/adguardhome-sync/admin-password` key `password`. **Must match** the bcrypt hash configured in `secret/ansible/adguard/admin-password-hash` — sync logs into both origin + replicas using the same credentials.

## Operational notes

- **Runs as `adguardhome-sync` system user** (NOT root). Limited filesystem access via systemd hardening (`ProtectSystem=strict`, etc.).
- **Sub-microsecond "Sync done" log entries** are NOT trustworthy (CLAUDE.md gotcha) — compare `/control/rewrite/list` on origin vs replicas directly if you suspect drift.
- **Idempotent restart**: `runOnStart: true` triggers an immediate sync on service restart, which is what gets replicas caught up after a reboot without waiting for the next cron tick.

## See also

- Phase 5b.2 in [`docs/operations/build-sequence.md`](../../../docs/operations/build-sequence.md)
- [`adguard`](../adguard/) role
- CLAUDE.md "Known gotchas → DNS"
