<!-- docs/services/postgres.md -->

# PostgreSQL LXC architecture (Fulla deployed 2026-05-17)

LXC 1130 (Fulla) hosts PostgreSQL 17 from PGDG, TLS-enabled, scram-sha-256 only. Standalone for now; Vör (1131, Urd) and Idunn (1132, Verd) join post-Authentik to validate clustering against a real consumer. Each cluster node lives on a different Proxmox host so a single-host failure never takes down >1 PG node.

**Why local LVM-thin, not NFS.** Postgres on NFS is an anti-pattern: Synology DSM's NFS isn't on anyone's validated list, fsync semantics vary between server implementations, soft mounts can silently drop writes under load, and WAL fsync over 1 GbE makes every commit a network round-trip. Database scale (Authentik + Teamspeak + future services = tens of GB) fits inside the 16 GB LXC disk; `pct resize` is one command if it ever fills.

**Sizing (cluster-wide, identical across nodes).** 2 vCPU, 4 GB RAM, 16 GB disk. Failover symmetry requires identical resources — asymmetric sizing means failover silently degrades performance. Tuning: `shared_buffers=1GB` (25%), `effective_cache_size=3GB` (75%), `work_mem=16MB`, `maintenance_work_mem=256MB`, `max_connections=100`, `wal_buffers=16MB`.

**TLS — self-signed, Ansible-managed.** 4096-bit RSA, 825-day cert. SAN covers `inventory_hostname`, `inventory_hostname.niflheim.xiiisins.com`, and the eth0 IP. `sslmode=require` is the practical client posture; `sslmode=verify-full` works once the cert ships to the client trust store. Long-term: cert-manager via ESO push to LXC.

**Authentication.**
- Local Unix socket: peer for `postgres` superuser (so `sudo -iu postgres psql` works without a password). scram-sha-256 for everything else.
- Network: `hostssl` only, scram-sha-256. Allowed source CIDRs in `postgres_allowed_cidrs`: VLAN 11 (asgard LXCs / Teamspeak future), VLAN 21 (asgard K3s nodes / Authentik via node NAT), VLAN 31 (jotunheim K3s, future), MacBook `/32`.
- Two SUPERUSER management roles: `admin` (hand-on-keyboard) and `ansible` (playbook / AWX automation). Passwords sourced from HashiCorp Vault via `community.hashi_vault` (`secret/ansible/postgres/admin-password`, `secret/ansible/postgres/ansible-password`). The Unix-side `postgres` superuser has no password — peer auth only, no network logins.

**WAL / replication readiness from day 1.** `wal_level=replica`, `max_wal_senders=10`, `max_replication_slots=10`, `hot_standby=on`, `archive_mode=on` with `archive_command='/bin/true'` (no-op placeholder). All four are restart-required to change, so they're set right from the start — adding 1131/1132 later doesn't require a config thrash + restart. When real WAL archiving is wanted, flip `archive_command` to the actual destination.

**Per-service DB provisioning.** Driven declaratively by `postgres_databases` in `inventory/group_vars/postgres_hosts.yml`. Each entry: `{name, owner, password_vault_path}`. Role iterates: creates the owning role (LOGIN only — not SUPERUSER, scoped to its DB), creates the DB owned by that role using `template0` + explicit encoding/collation pinned to `baseline_locale`. New consumer = new group_var entry + re-run playbook. No role editing required.

**Database deletion is deliberately NOT declarative** — removing an entry from `postgres_databases` does NOT drop the DB. Drop is a deliberate manual operation (`psql -c 'DROP DATABASE x'`). Group_var typos must not be able to drop data.

**Backups.** PBS captures the LXC filesystem (including data dir); restore is "PG replays WAL on startup." Acceptable initial posture. `pg_basebackup` to NFS (Munin) as a future enhancement for the canonical PG hot-backup pattern.

**Cluster build sequence.** Standalone-now / clustered-later, intentionally. The architectural rule "PostgreSQL LXC cluster only" is the *target* state, not the only valid intermediate. Fulla deployed first; cluster expansion (Vör, Idunn, streaming replication, HAProxy VIP frontend at 10.0.10.210) deferred until post-Authentik so clustering is validated against a real consumer rather than synthetic load. Authentik points at `fulla.niflheim.xiiisins.com` direct until the VIP exists, then re-points at `10.0.10.210` — single config flip.

**LXC features.** Unprivileged, `nesting=true` (systemd 257 on Debian 13). 2 vCPU / 4 GB RAM / 16 GB disk on Skuld.
