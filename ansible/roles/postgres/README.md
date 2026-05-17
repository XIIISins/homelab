# postgres role

PostgreSQL cluster member: PG 17 from PGDG, TLS-enabled, two SUPERUSER
management roles with Vault-sourced credentials, per-service database
provisioning driven by group_vars.

## What this role does

1. Adds the PGDG apt repo, installs `postgresql-{{ postgres_version }}`
2. Verifies the default cluster (`main`) is up
3. Generates a self-signed TLS cert, owned by `postgres:postgres`
4. Templates `postgresql.conf` (tuning, WAL, TLS, logging)
5. Templates `pg_hba.conf` from `postgres_allowed_cidrs`
6. Creates management PG roles (`admin`, `ansible`) — SUPERUSER, scram-sha-256,
   passwords from HashiCorp Vault
7. Iterates `postgres_databases` (group_vars) to create per-service DBs + owning roles

## Vault dependencies

| Path | Purpose | Schema |
|------|---------|--------|
| `secret/ansible/postgres/admin-password` | admin user password | `{ value: <password> }` |
| `secret/ansible/postgres/ansible-password` | ansible user password | `{ value: <password> }` |
| `secret/ansible/postgres/<service>-password` | per-service role password | `{ value: <password> }` |

These must be populated in Vault before running the playbook. See
`docs/homelab-design.md` § Secrets management for the Vault KV pattern.

## Bootstrap flow

Day 1 (root SSH still open from Terraform):
```fish
ansible-playbook -i inventory/hosts.yml playbooks/postgres-host.yml \
  --limit fulla -e ansible_user=root --tags baseline
ansible-playbook -i inventory/hosts.yml playbooks/postgres-host.yml \
  --limit fulla
```

Day N (after hardening locks root):
```fish
ansible-playbook -i inventory/hosts.yml playbooks/postgres-host.yml \
  --limit fulla
```

## Pass plan

- **Pass 1** (this commit): scaffolding only — playbook, group_vars, role skeleton with stubs
- **Pass 2**: packages, cluster, TLS, config, hba — fulla has running TLS-enabled PG, no users yet
- **Pass 3**: management users — admin + ansible roles, network-accessible
- **Pass 4**: per-service database provisioning — ready for first consumer (Authentik)
- **Pass 5** (later, post-Authentik): cluster expansion — vor, idunn, streaming replication, HAProxy VIP

## Gotchas to remember

- TBD as Pass 2-4 surface them
