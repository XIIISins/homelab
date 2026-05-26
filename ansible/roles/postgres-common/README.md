<!-- ansible/roles/postgres-common/README.md -->
# `postgres-common` role

PostgreSQL object provisioning shared between standalone PG and
Patroni-managed clusters: management roles (`admin`, `ansible`,
`replicator`) and per-service databases.

Split out from the `postgres` role 2026-05-24 to fix a bug where the
`postgres` role's `bootstrap.yml` was never reaching its leader-gated
DB-creation tasks (the `roles:` block silently ignores `tasks_from`,
so the playbook's `tasks_from: bootstrap` invocation fell through to
`main.yml` which gates the include on `not postgres_patroni_managed`).
Separating concerns gives both standalone + Patroni-managed flows a
direct path to the common provisioning logic without conditional
chains in the wrong file.

## Invocation patterns

### Patroni-managed (asgard PG today)

In `playbooks/asgard-postgres.yml`, after the `patroni` role has set
`patroni_is_leader`:

```yaml
roles:
  - role: baseline
  - role: postgres        # packages + TLS + (adoption-primary-only) pre-Patroni users
  - role: patroni         # install + config + adoption + service + leader-fact
  - role: postgres-common # this role — leader-gated user/DB creation
  - role: hardening
```

`main.yml` gates both sub-tasks on
`(not postgres_patroni_managed) or (patroni_is_leader | default(false))`
→ runs only on the leader.

### Standalone (future jotunheim or isolated single-node PG)

```yaml
roles:
  - role: baseline
  - role: postgres        # full standalone path: packages + cluster + TLS + config + hba
  - role: postgres-common # users + DBs (no patroni gate, runs unconditionally)
  - role: hardening
```

When `postgres_patroni_managed: false` (the default), the gate
short-circuits to true and tasks run as normal.

### Sub-tasks via `tasks_from`

Used by `postgres/tasks/prepare.yml` for the adoption-time primary
case (create management users BEFORE Patroni takes over, so replicas
can authenticate during basebackup):

```yaml
- name: Pre-Patroni management role bootstrap (adoption primary only)
  ansible.builtin.include_role:
    name: postgres-common
    tasks_from: users
  when:
    - postgres_patroni_managed
    - postgres_is_adoption_primary | default(false)
```

`include_role` (unlike the `roles:` block) honors `tasks_from`, so
`users.yml` runs directly without going through `main.yml`'s gate.
The outer `when:` provides the adoption-specific conditional.

## Variables

See `defaults/main.yml` for the annotated schema:

| Variable | Default | Purpose |
|----------|---------|---------|
| `postgres_patroni_managed` | `false` | Toggle the leader-gate in main.yml |
| `postgres_management_users` | admin + ansible + replicator | Cluster-wide management roles |
| `postgres_databases` | `[]` | Per-service DBs + their owning LOGIN roles |

Group_vars for `postgres` (`ansible/inventory/group_vars/postgres.yml`)
overrides `postgres_databases` to declare the per-service DBs that exist
on this cluster.
