<!-- ansible/roles/pg-backup/README.md -->
# pg-backup

Daily **logical** backup of the local Postgres node — a systemd timer that runs
`pg_dumpall --globals-only` + a per-database `pg_dump -Fc` (custom format).

## What it does

- Installs `/usr/local/bin/pg-backup` + `pg-backup.{service,timer}`.
- Runs as the `postgres` user over the local peer-auth socket (no password).
- Writes `globals.sql` + `<db>.dump` into `/var/backups/postgresql/`, one file
  per database, discovered dynamically (new app DBs are picked up automatically).
- Each file is written to a `.tmp` and **atomically renamed** on success, so PBS
  (which snapshots this LXC nightly) can never capture a half-written dump.
- Keeps only the latest set on disk (`umask 077`, files 0600). That's **1-day
  local** retention; PBS captures the LXC nightly and keeps **7 days** via
  file-restore, so the effective history is one week of daily restore points.

## Leader-only (timer on all three nodes, dump on the primary)

All of Fulla/Vör/Idunn install the timer, but the script self-skips on replicas
via a Patroni REST check (`GET <node-ip>:8008/primary` → 200 leader / 503
replica) — so the dump always runs on whoever is the current leader, and PBS
captures it. This is deliberate: a long `pg_dump` of a large high-churn table
(e.g. zabbix `history_uint`) on a hot standby gets cancelled with `ERROR:
canceling statement due to conflict with recovery` when the read collides with
WAL replay; the primary has no such conflict. A former leader keeps its last
backup set frozen on disk after a failover (harmless — a valid older copy, also
PBS-captured); only the current leader's set is refreshed daily.

## Restore

```sh
# globals first (roles/grants), then the DB:
psql -h /var/run/postgresql -f /var/backups/postgresql/globals.sql
createdb -h /var/run/postgresql <db>
pg_restore -h /var/run/postgresql -d <db> /var/backups/postgresql/<db>.dump
```

For PITR (restore to an arbitrary second) rather than daily restore points, see
the deferred WAL-archiving item in `docs/operations/open-questions.md`
(`archive_command` is already scaffolded as a no-op in the Patroni config).

## Key vars (`defaults/main.yml`)

| var | default | note |
|-----|---------|------|
| `pg_backup_oncalendar` | `*-*-* 01:00:00` | after the 00:00 PBS window |
| `pg_backup_dir` | `/var/backups/postgresql` | local disk, never NFS |
| `pg_backup_pg_version` | `17` | versioned bindir, not pg_wrapper |
| `pg_backup_exclude_dbs` | template0/1 | additional non-template excludes |
