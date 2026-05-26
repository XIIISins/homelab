# patroni role

PostgreSQL HA via Patroni — etcd DCS, version-pinned tarball install, runs as
the `postgres` user against an existing PG 17 install (managed by the
`postgres` role).

## What this role does

1. Installs Patroni from a version-pinned source tarball
   (`patroni_version` + `patroni_sha256`) into a venv at `/opt/patroni`,
   with stable symlinks at `/usr/local/bin/{patroni,patronictl}`.
2. Renders `/etc/patroni/patroni.yml` with:
   - etcd3 endpoints discovered from `haproxy_etcd` group
   - DCS-level cluster-wide config (bootstrap.dcs.*)
   - pg_hba.conf rules (incl. per-replica replication rules)
   - PG paths matching Debian's layout (`/var/lib/postgresql/17/main`,
     `/etc/postgresql/17/main`, `/usr/lib/postgresql/17/bin`)
3. Renders `/etc/systemd/system/patroni.service` (runs as `postgres:postgres`).
4. On the adoption-time primary (Fulla): stops + disables
   `postgresql@17-main.service`, removes the vestigial
   `conf.d/niflheim.conf` drop-in (Patroni owns `postgresql.conf` going
   forward via its `include postgresql.base.conf` directive).
5. On fresh replicas (Vor/Idunn): no Debian PG service was ever started
   (`postgres` role suppressed `create_main_cluster`), so adoption steps
   are no-ops. Patroni discovers the leader in DCS and runs `pg_basebackup`
   to clone the cluster.
6. Waits for Patroni REST API readiness and exposes `is_patroni_leader`
   as a fact (consumed by `postgres` role's `bootstrap.yml`).

## Vault dependencies

| Path | Purpose | Schema |
|------|---------|--------|
| `secret/ansible/postgres/replicator-password` | Replication user password | `{ value: <password> }` |
| `secret/ansible/postgres/ansible-password` | Patroni-internal superuser + rewind user (re-used) | `{ value: <password> }` |
| `secret/ansible/patroni/restapi-password` | REST API basic-auth password (POST endpoints) | `{ value: <password> }` |

## Why source tarball, not apt

The PGDG apt repo would technically work but the version still floats
between apt-mark holds and repo updates. Concrete pinning via
`patroni_version` + `patroni_sha256` matches the existing K3s/etcd role
discipline — a Patroni upgrade is a deliberate operation (edit both
fields, verify with `curl | sha256sum`, re-run play).

Backlog ([open questions](../../../docs/operations/open-questions.md)): mirror
this tarball — and PGDG's PG packages, etcd's release tarballs, K3s binaries —
to a self-hosted internal repo so version control lives inside the
infrastructure, not on upstream CDNs.

## Bootstrap order

Run via `playbooks/asgard-postgres.yml` with `serial: 1`:

1. Fulla (adoption-time primary). Patroni starts → sees populated
   data dir → claims DCS leader → no initdb. The Debian PG service
   is stopped + disabled in this same play.
2. Vor. Patroni starts → sees DCS leader (Fulla) → runs
   `pg_basebackup` from Fulla as `replicator` → becomes synchronous
   replica.
3. Idunn. Same as Vor.

Authentik's PG connection (`AUTHENTIK_POSTGRESQL__HOST: 10.0.11.230`)
keeps working through the adoption — Fulla's IP doesn't change and PG
is briefly down only during the postgresql@17-main → patroni handoff
(seconds).

## Verifying

```fish
ssh fulla.niflheim.xiiisins.com sudo -u postgres /usr/local/bin/patronictl \
  -c /etc/patroni/patroni.yml list
```

Expected after all three nodes are up:

```
+ Cluster: niflheim-pg ----+---------+---------+----+-----------+
| Member | Host        | Role    | State   | TL | Lag in MB |
+--------+-------------+---------+---------+----+-----------+
| fulla  | 10.0.11.230 | Leader  | running |  N |           |
| idunn  | 10.0.11.232 | Replica | running |  N |         0 |
| vor    | 10.0.11.231 | Replica | running |  N |         0 |
+--------+-------------+---------+---------+----+-----------+
```

## Failover

Manual failover (testing):

```fish
sudo -u postgres patronictl -c /etc/patroni/patroni.yml failover --candidate vor
```

Automatic failover triggers on leader loss after `patroni_dcs_ttl` (30s)
without a heartbeat. HAProxy (deployed in the next 5g.2 step) reroutes
to the new leader within a few seconds of election.
