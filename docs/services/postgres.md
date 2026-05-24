<!-- docs/services/postgres.md -->

# PostgreSQL LXC architecture (Fulla deployed 2026-05-17, HA cluster live 2026-05-24)

LXC 1130 (Fulla, Skuld) hosts PostgreSQL 17 from PGDG, TLS-enabled, scram-sha-256 only. Cluster expanded 2026-05-24 in Phase 5g.2 — Vör (1131, Urd) + Idunn (1132, Verd) joined as Patroni-managed replicas via in-place adoption of Fulla. Patroni cluster `niflheim-pg` runs 3/3 streaming on a single timeline; etcd DCS lives on the HAProxy/etcd LXC trio (Hlin/Eir/Snotra at 1133/1134/1135) — see [decisions](../operations/decisions.md) for the DCS-placement reasoning. Each cluster node lives on a different Proxmox host so a single-host failure never takes down >1 PG node.

**Why local LVM-thin, not NFS.** Postgres on NFS is an anti-pattern: Synology DSM's NFS isn't on anyone's validated list, fsync semantics vary between server implementations, soft mounts can silently drop writes under load, and WAL fsync over 1 GbE makes every commit a network round-trip. Database scale (Authentik + Teamspeak + future services = tens of GB) fits inside the 16 GB LXC disk; `pct resize` is one command if it ever fills.

**Sizing (cluster-wide, identical across nodes).** 2 vCPU, 4 GB RAM, 16 GB disk. Failover symmetry requires identical resources — asymmetric sizing means failover silently degrades performance. Tuning: `shared_buffers=1GB` (25%), `effective_cache_size=3GB` (75%), `work_mem=16MB`, `maintenance_work_mem=256MB`, `max_connections=300` (bumped from 100 per 5g.2.a — cluster-wide demand from all 7 planned consumers is ~150-200; pgbouncer skipped, see decisions), `wal_buffers=16MB`.

**TLS — self-signed, Ansible-managed.** 4096-bit RSA, 825-day cert. SAN covers `inventory_hostname`, `inventory_hostname.niflheim.xiiisins.com`, and the eth0 IP. `sslmode=require` is the practical client posture; `sslmode=verify-full` works once the cert ships to the client trust store. Long-term: cert-manager via ESO push to LXC.

**Authentication.**
- Local Unix socket: peer for `postgres` superuser (so `sudo -iu postgres psql` works without a password). scram-sha-256 for everything else.
- Network: `hostssl` only, scram-sha-256. Allowed source CIDRs in `postgres_allowed_cidrs`: VLAN 11 (asgard LXCs / Teamspeak future), VLAN 21 (asgard K3s nodes / Authentik via node NAT), VLAN 31 (jotunheim K3s, future), MacBook `/32`.
- Three management roles: `admin` (SUPERUSER, hand-on-keyboard), `ansible` (SUPERUSER, playbook + AWX + Patroni's internal superuser/rewind user), `replicator` (REPLICATION + LOGIN, Patroni-internal for streaming + `pg_basebackup`). Passwords from HashiCorp Vault via `community.hashi_vault` (`secret/ansible/postgres/{admin,ansible,replicator}-password`). The Unix-side `postgres` superuser has no password — peer auth only, no network logins.
- Patroni REST API basic-auth (`patroni_admin`, `secret/ansible/patroni/restapi-password`) — gates write operations (failover/switchover/reinit); reads (cluster status) are open.

**WAL / replication readiness from day 1.** `wal_level=replica`, `max_wal_senders=10`, `max_replication_slots=10`, `hot_standby=on`, `archive_mode=on` with `archive_command='/bin/true'` (no-op placeholder). All four are restart-required to change, so they're set right from the start — adding 1131/1132 later doesn't require a config thrash + restart. When real WAL archiving is wanted, flip `archive_command` to the actual destination.

**Per-service DB provisioning.** Driven declaratively by `postgres_databases` in `inventory/group_vars/postgres_hosts.yml`. Each entry: `{name, owner, password_vault_path}`. Role iterates: creates the owning role (LOGIN only — not SUPERUSER, scoped to its DB), creates the DB owned by that role using `template0` + explicit encoding/collation pinned to `baseline_locale`. New consumer = new group_var entry + re-run playbook. No role editing required.

**Database deletion is deliberately NOT declarative** — removing an entry from `postgres_databases` does NOT drop the DB. Drop is a deliberate manual operation (`psql -c 'DROP DATABASE x'`). Group_var typos must not be able to drop data.

**Backups.** PBS captures the LXC filesystem (including data dir); restore is "PG replays WAL on startup." Acceptable initial posture. `pg_basebackup` to NFS (Munin) as a future enhancement for the canonical PG hot-backup pattern.

**Cluster build sequence.** Standalone-first, clustered-later, intentionally. Fulla deployed 2026-05-17 to validate the per-service DB provisioning machinery against Authentik (the first real consumer); HA cluster expansion (Vör + Idunn under Patroni) pulled forward 2026-05-24 by operational pressure (Authentik PG-DNS flapping mitigated by literal-IP stopgap; structural fix is HA + VIP). Patroni adoption was in-place — Fulla's existing data became the cluster's initial state, Vör + Idunn joined via Patroni's `pg_basebackup` path. Remaining: HAProxy + keepalived VIP `10.0.10.210` → Authentik cutover (`AUTHENTIK_POSTGRESQL__HOST` flips from literal `10.0.11.230` to VIP `10.0.10.210`, retiring the IP stopgap). See [build-sequence](../operations/build-sequence.md) for current Phase 5g.2 state.

**Patroni management.** Cluster lifecycle (`initdb` skip on adoption, replica basebackup, failover election, leader-only writes), `postgresql.conf`, and `pg_hba.conf` are all owned by Patroni now. The `postgres` Ansible role is Patroni-aware: in patroni-managed mode it stops at packages + TLS cert generation + replicator-user creation pre-adoption (on the existing primary), then hands off to the `patroni` role. Post-Patroni, the same role's `bootstrap.yml` task runs leader-only (gated by the `patroni_is_leader` fact from Patroni's REST API) for management-user upserts + per-service DB provisioning. Manual cluster ops via `patronictl -c /etc/patroni/patroni.yml {list,failover,switchover,restart}`.

**LXC features.** Unprivileged, `nesting=true` (systemd 257 on Debian 13). 2 vCPU / 4 GB RAM / 16 GB disk on Skuld.
