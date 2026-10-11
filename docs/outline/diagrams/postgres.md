<!-- docs/outline/diagrams/postgres.md -->

# PostgreSQL diagram

Patroni high availability behind one address. Every database consumer connects to a single VIP, and HAProxy only sends traffic to the current leader.

![PostgreSQL: Patroni HA behind one address](/api/attachments.redirect?id=8c5c36f5-61db-43e7-9fd8-729d5e5eab37)

---

## How to read it

- **Top left: consumers.** The Kubernetes apps (Authentik, NetBox, Outline, Immich, TeamSpeak, Semaphore) and the Zabbix server (an LXC outside K3s) all connect to one endpoint, read and write, TLS only. keepalived floats that address across the HAProxy trio, so it follows a healthy HAProxy.
- **Upper box: HAProxy plus etcd on Hlin, Eir and Snotra** (one per host). HAProxy uses `balance first`. etcd is the DCS: it holds the leader lock and the Patroni configuration, deliberately on these three and not on the database nodes.
- **Lower box: PostgreSQL 17 on Fulla, Vör and Idunn** (one per host). Each is a Patroni member with TLS and identical sizing, so a failover does not degrade performance.
- **The two arrows between the boxes.** Down: traffic goes to the leader only. Up: Patroni takes the leader lock in etcd.
- **The leader rule.** The leader is whoever holds the etcd lock. HAProxy asks each node's Patroni API and only the leader answers yes. The other two stream WAL from it and are marked down for clients.
- **Left and right.** Ansible creates each service's role and database (leader-only, and never dropped by a config edit). PBS snapshots every LXC, a `pg-backup` timer takes a daily dump on the leader, and a Patroni callback posts leader changes to Discord through Hermod.
- Disks are local LVM-thin, never NFS: WAL fsync over 1 GbE would turn every commit into a network trip.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[postgres.excalidraw 43421](/api/attachments.redirect?id=31a5709a-8323-491e-ac12-df0e0ff68086)

[postgres.svg 54597](/api/attachments.redirect?id=bc00341b-0108-457f-8210-6f0def3f9045)

---

## See also

- **Storage & data** (Components): the PostgreSQL section and backups.
- **Asgard LXCs diagram** (Diagrams): where the six containers sit.
- **Hermod** (Services): the failover callback.
