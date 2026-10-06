<!-- docs/incidents/2026-10-06-fulla-stale-replica.md -->

# Fulla replica stale for about 15 days (found 2026-10-06)

Found while sizing the Jellyfin LXC (the memory checks led to a look at the Postgres nodes). Not a user-visible outage: the Patroni cluster kept a leader and consumers kept working.

## What happened

- Fulla (replica, LXC 1130 on Skuld) had stopped replicating roughly 15 days earlier, probably when Skuld crashed (Skuld hard-freezes, see [`CLAUDE.md`](../../CLAUDE.md)).
- The replica had lost its replication slot: `max_slot_wal_keep_size` caps retained WAL at 4 GB per slot, and an outage longer than about 9 to 13 hours at this write rate exceeds that, after which the slot is invalidated and the replica cannot catch up by itself.
- **No alert fired for the whole period.** That is the real finding.

## Recovery

`patronictl reinit` on Fulla rebuilt it from the leader. Verified streaming with zero lag afterwards.

## Side findings the same investigation fixed

- Postgres memory and autovacuum tuning on the nodes. Patroni's `bootstrap.dcs` is first-start-only, so live reload-only parameters are reconciled through the Patroni REST `/config` endpoint; see [`../known-issues/postgres.md`](../known-issues/postgres.md).
- LXC `memory` caps are not reservations and page cache counts in `memory.current`; see [`../known-issues/lxc-proxmox.md`](../known-issues/lxc-proxmox.md).

## Follow-ups

- [ ] Alert on a replica that is not streaming or whose slot is inactive/invalidated, and on replication lag above a threshold (Zabbix Patroni/PG items). Until then, a replica loss is silent.
- [ ] Decide whether 4 GB of slot retention is enough for the longest tolerable node outage, or raise it.
- [ ] Consider why an unhealthy replica never reached the Hermod/AIOps path at all.
