# postgres role

PostgreSQL 17 from PGDG, TLS, scram-sha-256, three management roles
(`admin`, `ansible`, `replicator`) with Vault-sourced credentials,
per-service database provisioning driven by group_vars. Patroni-aware:
yields cluster lifecycle / postgresql.conf / pg_hba.conf to the patroni
role when `postgres_patroni_managed: true`.

## Two modes

### Standalone (`postgres_patroni_managed: false`, default)

Role owns end-to-end: cluster init via `pg_createcluster`, `postgresql.conf`
via `templates/postgresql-niflheim.conf.j2`, `pg_hba.conf` via
`templates/pg_hba.conf.j2`, users + per-service DBs via `users.yml` +
`databases.yml`. Single-node, no HA.

### Patroni-managed (`postgres_patroni_managed: true`)

Used by the 5g.2 PG HA cluster (Fulla/Vor/Idunn). This role contributes
*only*:
- PG package install (with `create_main_cluster = false` so fresh replicas
  don't get a conflicting empty cluster before Patroni's basebackup runs)
- TLS cert generation (lives on disk where Patroni's `postgresql.parameters`
  reference it)
- Pre-Patroni adoption-primary user creation (admin / ansible /
  **replicator** — replicator must exist before any replica tries to
  basebackup)
- Post-Patroni users + DBs creation, gated leader-only via the
  `patroni_is_leader` fact set by the patroni role

Playbook orchestrates as `postgres(prepare) → patroni → postgres(bootstrap)`
via `tasks_from`. See `ansible/playbooks/postgres-host.yml`.

## Vault dependencies

| Path | Purpose | Schema |
|------|---------|--------|
| `secret/ansible/postgres/admin-password` | admin SUPERUSER password | `{ value: <password> }` |
| `secret/ansible/postgres/ansible-password` | ansible SUPERUSER + Patroni's internal superuser/rewind password | `{ value: <password> }` |
| `secret/ansible/postgres/replicator-password` | Patroni-internal REPLICATION user password (used for pg_basebackup + streaming) | `{ value: <password> }` |
| `secret/ansible/postgres/<service>-password` | per-service role password | `{ value: <password> }` |

These must be populated in Vault before running the playbook. See
[`docs/architecture/identity-secrets.md`](../../../docs/architecture/identity-secrets.md) for the Vault KV pattern.

## Usage

Day N (after hardening locks root SSH):

```fish
ansible-vault-env
ansible-playbook -i inventory/hosts.yml playbooks/postgres-host.yml \
  --limit fulla,vor,idunn
```

`serial: 1` enforces Fulla-finishes-before-Vor/Idunn-start. Patroni's own
DCS logic handles leader election on first run; no hostname branching in
the role.

## Notes

- `postgres_max_connections: 300` cluster-wide (per 5g.2.a decision row).
- `postgres_management_users` schema uses `role_attr_flags` verbatim (e.g.
  `SUPERUSER,LOGIN` or `REPLICATION,LOGIN`) — the older `superuser: bool`
  was dropped 2026-05-24 when `replicator` joined the list.
- The `conf.d/niflheim.conf` drop-in is removed at adoption time; Patroni's
  `postgresql.base.conf` (auto-included via Patroni's appended `include`
  line) supersedes it. All those params live in
  `roles/patroni/templates/patroni.yml.j2` now.
