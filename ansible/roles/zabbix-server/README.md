<!-- ansible/roles/zabbix-server/README.md -->

# zabbix-server

Installs + configures Zabbix server + frontend on the Zabbix LXC (1102, Skuld). Backend is the niflheim Patroni PG cluster via the HAProxy VIP `10.0.10.210`.

Part of Phase 5h. Pairs with [`zabbix-agent`](../zabbix-agent/) for cluster-wide agent rollout.

## What this role does

1. Resolves Vault-backed secrets once (centralized in `tasks/vault-lookup.yml`) — downstream tasks reuse the facts.
2. Installs Zabbix's apt repo (`zabbix-release_latest_<major>+debian<X>_all.deb`).
3. Installs server + frontend + agent2 + sql-scripts + postgresql-client + php-pgsql + the pinned `php-fpm` runtime (Zabbix's frontend-php doesn't declare an FPM dep — see `zabbix_php_version` in `defaults/main.yml`).
4. Bootstraps the schema from `/usr/share/zabbix-sql-scripts/postgresql/server.sql.gz` against the existing `zabbix` DB (idempotent — skips if the `users` table already exists; uses `pipefail` + `ON_ERROR_STOP=1` so partial loads can't silently succeed).
5. Renders `/etc/zabbix/zabbix_server.conf` (Patroni VIP target, sized caches).
6. Renders the nginx + PHP frontend config — nginx accepts every FQDN in `zabbix_web_server_names` (direct + midgard + apex).
7. Starts `zabbix-server`, `nginx`, the pinned `php-fpm`, `zabbix-agent2`.

## Prerequisites

**The `zabbix` DB + user must exist on the Patroni leader BEFORE running this role.** Add to `ansible/inventory/group_vars/postgres.yml`:

```yaml
postgres_databases:
  # ... existing entries ...
  - name: zabbix
    owner: zabbix
    password_vault_path: ansible/postgres/zabbix-password
```

Then run `ansible-playbook ansible/playbooks/asgard-postgres.yml` (the postgres-common role creates the user + DB idempotently). The Vault path `ansible/postgres/zabbix-password` must be populated first.

## Secrets

| Vault path | Field | Used by |
|------------|-------|---------|
| `secret/ansible/postgres/zabbix-password` | `value` | postgres-common role for DB user creation; this role for connection |
| `secret/ansible/zabbix/admin-password` | `value` | NOT auto-applied — initial Zabbix admin password is `zabbix`, operator rotates via UI on first login + then stashes in this Vault path for IaC tracking. Deferred to API-driven rotation in a future iteration. |

Field defaults to `value` (TF-minted convention; see `zabbix_db_password_vault_field` / `zabbix_admin_password_vault_field`). Override per-deployment if a path stores the secret under a different key.

## Operational notes

- **DB sslmode is `require`** — libpq enforces TLS to the Patroni VIP. If the HAProxy VIP loses TLS termination (unusual config), Zabbix server fails to connect.
- **Schema bootstrap is slow** — first-deploy schema load takes 30-60 seconds. The check-before-load pattern keeps re-runs fast.
- **Default credentials**: after first deploy, log into the web UI at `http://zabbix.niflheim.xiiisins.com/` with `Admin` / `zabbix`. **Change immediately.**
- **Connection retries**: `zabbix-server.service` will fail to start if the Patroni VIP isn't reachable yet. systemd retries automatically; if the cluster is in a bad state, server stays down.

## See also

- Phase 5h in [`docs/operations/build-sequence.md`](../../../docs/operations/build-sequence.md)
- [`zabbix-agent`](../zabbix-agent/) — agent rollout to every other host
- CLAUDE.md "Architectural invariants → Services / placement" (Zabbix-stays-LXC rule)
