<!-- docs/outline/procedures/postgres-operations.md -->

# Postgres operations

The relational database for Authentik, NetBox, Zabbix, Teamspeak, Outline, Semaphore and Immich is one **Patroni** cluster, `niflheim-pg`, with its consensus store (**etcd**) and its entry point (**HAProxy + keepalived VIP `10.0.10.210`**) on a separate trio. This runbook is how to look at it, move the leader, repair a replica, and add or restore a database.

| Role | Guests (Proxmox host) | IPs |
|---|---|---|
| PostgreSQL 17 + Patroni | `fulla` 1130 (Skuld), `vor` 1131 (Urd), `idunn` 1132 (Verd) | `10.0.11.230`, `.231`, `.232` |
| HAProxy + etcd + keepalived | `hlin` 1133 (Urd), `eir` 1134 (Verd), `snotra` 1135 (Skuld) | `10.0.11.233`, `.234`, `.235` |
| VIP | keepalived on VLAN 10 | `10.0.10.210` |

**Rules:** all consumers connect through the VIP, read and write. HAProxy sends traffic only to the node whose Patroni REST `/master` answers 200. Quorum is 2 of 3 for both Patroni's etcd and the HAProxy trio. **Never touch two members at once.** Patroni owns `postgresql.conf` and `pg_hba.conf`; do not edit them by hand.

---

## 1. Health check (read-only, always safe)

```bash
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"

# Who is the leader? Are replicas streaming? Lag in MB?
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'
#   Expect: 3 members, one "Leader running", two "Replica streaming", Lag in MB ~0, same TL

# The same through the REST API (open for reads)
curl -s http://10.0.11.230:8008/cluster | jq '.members[]|{name,role,state,lag}'

# Which node does HAProxy think is the leader? Exactly one answers 200
for ip in 10.0.11.230 10.0.11.231 10.0.11.232; do
  printf '%s ' "$ip"; curl -s -o /dev/null -w '%{http_code}\n' http://$ip:8008/master
done

# Is the VIP up and accepting connections?
timeout 3 bash -c '</dev/tcp/10.0.10.210/5432' && echo "VIP accepts TCP" || echo "VIP DOWN"
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do $SSH ansible@$ip 'ip -br a show eth1 | grep -c 10.0.10.210' ; done   # exactly one "1": the VIP holder

# etcd (Patroni's consensus store): all three healthy
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do
  printf '%s ' "$ip"; curl -s http://$ip:2379/health; echo
done                                   # {"health":"true",...}
```

What good looks like: one leader, two streaming replicas with ~0 lag, three healthy etcd members, the VIP on exactly one HAProxy node, TCP to the VIP succeeds.

Without SSH (Proxmox host as the path in): `ssh root@skuld.niflheim.xiiisins.com 'pct exec 1130 -- patronictl -c /etc/patroni/patroni.yml list'`.

---

## 2. Planned leader switch (switchover)

**Use when:** patching the leader's Proxmox host, or testing failover. Needs a healthy replica to take over.

```bash
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'            # note the leader name
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml switchover --leader <leader> --candidate <replica> --force'
sleep 15
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'            # new leader, old leader now a replica
```

Verify: the old leader is `Replica streaming`; `curl -s -o /dev/null -w '%{http_code}\n' http://<new-leader>:8008/master` is `200`; a **Hermod message** reports the role change within seconds (the Patroni callback); the apps recover by connection retry within 10 to 30 seconds. Do not POST the REST API for switchover: the mutating endpoints are authenticated, and a raw POST fails with "connection reset"; use `patronictl`.

---

## 3. The leader died (unplanned)

Patroni fails over by itself in 10 to 30 seconds. Confirm, do not interfere:

```bash
$SSH ansible@<a-surviving-node> 'sudo patronictl -c /etc/patroni/patroni.yml list'   # a new Leader exists?
```

- **New leader exists, old node missing:** the guest or host is down. Bring it back (see **Hosts & Proxmox** troubleshooting). When it returns it rejoins as a replica; if it reports a diverged timeline, `pg_rewind` runs automatically. If it will not rejoin, use *Re-seed a replica* below.
- **No leader after 60 seconds:** check etcd quorum first (section 1). With etcd quorum lost, Patroni demotes everything and writes stop; **fix etcd before Postgres**. If two nodes are down, stop and escalate.
- A stuck failover can be forced with `patronictl failover --candidate <name> --force` on a surviving node. Use it only when the cluster has no leader and etcd is healthy.

---

## 4. Re-seed a replica from the leader

**Use when:** a replica shows `stopped`, `start failed`, a diverged timeline, or lag that never recovers.

```bash
# On any PG node. This wipes the replica's data directory and re-copies from the leader.
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml reinit niflheim-pg <replica-name> --force'
watch -n5 "$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'"
```

Never `reinit` the leader. Check the replica's journal on failure: `$SSH ansible@<replica-ip> 'sudo journalctl -u patroni -n 80 --no-pager'`. A restart that does nothing: Patroni runs under a unit with `KillMode=process`, so `systemctl kill` leaves Postgres running; use `patronictl restart niflheim-pg <member>` or `systemctl restart patroni`.

---

## 5. Look inside a database

```bash
# On the LEADER node
$SSH ansible@<leader-ip> 'sudo -iu postgres psql -c "select datname, pg_size_pretty(pg_database_size(datname)) from pg_database order by 2 desc;"'
$SSH ansible@<leader-ip> 'sudo -iu postgres psql -c "select client_addr, state, sent_lsn, replay_lsn from pg_stat_replication;"'
$SSH ansible@<leader-ip> 'sudo -iu postgres psql -c "select count(*), state from pg_stat_activity group by 2;"'
$SSH ansible@<leader-ip> 'sudo -iu postgres psql -c "show max_connections;"'    # 300
```

Replicas are read-only: `cannot execute CREATE ROLE in a read-only transaction` means you are on a replica.

---

## 6. Add a database for a new service

Databases are declared, not created by hand. **Dropping a database is never declarative**; removing an entry never drops data.

1. In `ansible/inventory/group_vars/postgres.yml` add an entry to `postgres_databases` (`name`, `owner`, `password_vault_path`, and `extensions:` if the app needs any; `cube` before `earthdistance`).
2. Mint the password in Vault (Terraform, `terraform/vault/main.tf`) at both `secret/ansible/postgres/<name>-password` and `secret/k8s/<name>/postgres-password`.
3. Run the provisioning **against the current leader**. Leadership floats; `--limit fulla` is not safe.

```bash
LEADER=$($SSH ansible@10.0.11.230 "sudo patronictl -c /etc/patroni/patroni.yml list -f json" | jq -r '.[]|select(.Role=="Leader")|.Member')
echo "leader: $LEADER"
cd ~/homelab/ansible
ansible-playbook -i inventory/hosts.yml playbooks/asgard-postgres.yml --limit "$LEADER" \
  --skip-tags baseline,postgres,patroni,hardening -e '{"patroni_is_leader": true}'
```

The JSON form of `-e` is mandatory. Node-based clients (Outline) need `?sslmode=no-verify` in their URL; libpq clients use `sslmode=require`.

---

## 7. Backups and restoring a database

Two layers exist: PBS images of each Postgres LXC (nightly), and a daily **logical dump** written by whichever node is the leader to `/var/backups/postgresql` (`globals.sql` and one `<db>.dump` per database). There is no point-in-time recovery: the effective history is one day.

```bash
# Take a dump now (on the leader; each node's timer skips itself unless it is the leader)
$SSH ansible@<leader-ip> 'sudo systemctl start pg-backup.service && sudo ls -lh /var/backups/postgresql'

# Restore ONE database from its dump (on the leader). globals first only for a fresh cluster.
$SSH ansible@<leader-ip> "sudo -iu postgres psql -c \"select pg_terminate_backend(pid) from pg_stat_activity where datname='<db>';\""
$SSH ansible@<leader-ip> 'sudo -iu postgres dropdb <db> && sudo -iu postgres createdb -O <owner> <db>'
$SSH ansible@<leader-ip> 'sudo -iu postgres pg_restore -d <db> /var/backups/postgresql/<db>.dump'
```

This **destroys the current contents** of `<db>`: confirm with the owner before running it, and stop the consuming app first (`kubectl -n <ns> scale deploy/<app> --replicas=0`; scale back afterwards). To recover a dump file that no longer exists on disk, restore the leader's LXC backup as a file-restore from PBS (see **Backups & restore drills**). A full-cluster restore is in **Teardown & rebuild**, section 5.

---

## 8. Maintenance mode (use sparingly)

To stop Patroni from reacting while you work on the cluster:

```bash
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml pause'      # no automatic failover
# ... work ...
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml resume'     # ALWAYS resume
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'       # "Maintenance mode: on" must be gone
```

Parameters set in Ansible role defaults do nothing on a running cluster: live values are in etcd (`patronictl show-config`; edit with `patronictl edit-config`).

---

## See also

- **Storage & data** (Components): the design behind this.
- **Vault & Postgres** (Troubleshooting): symptoms and fixes.
- **Proxmox host patching** (this section): patches the nodes one at a time and switches the leader for you.
