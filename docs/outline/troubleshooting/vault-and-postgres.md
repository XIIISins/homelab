<!-- docs/outline/troubleshooting/vault-and-postgres.md -->

# Vault & Postgres

Symptoms with secrets, tokens, the database and failover. Many applications fail together when either is down: Postgres (`10.0.10.210`) backs Authentik, NetBox, Zabbix, Teamspeak, Outline, Semaphore and Immich; Vault feeds every secret.

```bash
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
V() { kubectl -n vault exec "vault-$1" -c vault -- "${@:2}"; }
```

Quorum: Vault needs 2 of 3. Patroni needs a healthy etcd (2 of 3 on the HAProxy trio). Two members down in either is an emergency: stop and escalate.

---

# Part A: Vault

## A1. First diagnostics

```bash
kubectl -n vault get pods -o wide
for i in 0 1 2; do echo "== vault-$i"; V $i vault status 2>&1 | grep -E "Initialized|Sealed|HA Mode|Raft Applied"; done
curl -s -o /dev/null -w '%{http_code}\n' https://vault.niflheim.xiiisins.com/v1/sys/health     # 200 active, 429 standby, 503 sealed, 501 uninitialised
kubectl get clustersecretstore vault                                                              # Ready True: ESO can read
vault token lookup >/dev/null 2>&1 && echo "my token ok" || echo "my token INVALID/expired"
```

## A2. Symptom table (fixes are in **Vault recovery**)

| Symptom | Likely cause | Do |
|---|---|---|
| A pod shows `Sealed true` | KMS unreachable or the `vault-unseal` Secret broken | `kubectl -n vault logs vault-<N> -c vault --tail=50 \| grep -iE "kms\|seal\|error"`; **Vault recovery** §2 |
| A pod is `Initialized false` / `stored unseal keys are supported, but none were found` | Wiped volume or missed the join window; no auto re-join | **Vault recovery** §3 |
| `list-peers` shows a member that is unreachable | Ghost entry from a rebuilt node | `raft remove-peer <id>` (root token): **Vault recovery** §3 |
| Config change merged but pods unchanged | StatefulSet is `OnDelete` | Roll one pod at a time, leader last: **Vault recovery** §4 |
| A Terraform step says `permission denied` / `invalid token` mid-deploy | The cached token is per-session and expired | `set-vault-token root` (operator) or `vault-homelab-env --refresh`; verify with `vault token lookup` |
| A module 403s on `secret/k8s/*`, on a write, or on `auth/token/create` | The shim's AppRole token reads `secret/ansible/*` only | The root token is needed for `terraform/vault`, `semaphore`, `tailscale`, `cloudflare`, `authentik`: operator runs them |
| `invalid role or secret ID` | A stale SecretID (re-seeding only copies the shell value) | **AppRole rotation**. To tell apart: `vault read -field=role_id auth/approle/role/<role>/role-id` vs the role id in the local secret-zero file; if they match, only the secret_id is bad |
| Rotation helper says `unknown role` | Wrong helper for Semaphore's role | `rotate-semaphore-approle` (fish) |
| SecretID dies after about 32 days, not 90 | The auth mount's max lease TTL clamps it | `vault read sys/auth/approle/tune` (want `2160h`); fix in `terraform/vault` |
| TLS error from a new tool | The internal CA is not in its trust store | Point it at the CA bundle (`vault-ca-bundle` ConfigMap). `vault_login` and raw `uri` calls do **not** auto-detect `ANSIBLE_HASHI_VAULT_CA_CERT` the way KV lookups do |
| A value in Vault is plausible but wrong | `vault kv put/patch` treats `@…` as a file and `-` as stdin | Compare hashes (`... \| shasum -a 256 \| cut -c1-16`); use **Secret mirroring** |
| Changed a secret but Frigg/another machine uses the old one | The three-hour env cache | `vault-homelab-env --refresh >/dev/null 2>&1` on every machine |
| HelmRelease for Vault failed and stays failed | Retry budget exhausted | `flux reconcile helmrelease vault -n vault --force` after fixing the cause |

If Vault is down, existing workloads keep running on their cached Kubernetes Secrets; new syncs and Ansible lookups fail. Do not restart consumers while Vault is down.

---

# Part B: Postgres

## B1. First diagnostics (read-only)

```bash
$SSH ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'               # one Leader, two streaming Replicas, Lag ~0
for ip in 10.0.11.230 10.0.11.231 10.0.11.232; do printf '%s ' $ip; curl -s -o /dev/null -w '%{http_code}\n' -m 3 http://$ip:8008/master; done    # exactly one 200
timeout 3 bash -c '</dev/tcp/10.0.10.210/5432' && echo "VIP open" || echo "VIP DOWN"
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do printf '%s ' $ip; curl -s -m 3 http://$ip:2379/health || echo DOWN; echo; done                       # {"health":"true"}
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do $SSH ansible@$ip 'echo -n "$(hostname) VIP: "; ip -br a show eth1 | grep -c 10.0.10.210; systemctl is-active haproxy keepalived etcd | tr "\n" " "; echo'; done
```

Interpretation:

| Result | Meaning |
|---|---|
| 1 leader + 2 streaming | Healthy |
| 0 leader, etcd unhealthy | **Fix etcd first** (it is the consensus store); Patroni demotes everything without quorum |
| 0 leader, etcd healthy | Failover stuck; wait 60 s, then **Postgres operations** §3 |
| 1 leader, 1 replica missing | Degraded but writing; **Postgres operations** §4 (re-seed) if it will not return |
| VIP closed, leader fine | HAProxy/keepalived problem: check the trio below |
| VIP held by 0 or 2 nodes | keepalived trouble: VRRP auth/VRID, see **DNS & networking** §4 |

## B2. Symptom table

| Symptom | Likely cause | Do |
|---|---|---|
| App log: `no pg_hba.conf entry … no encryption` | Postgres is `hostssl`-only; this message also appears for a disallowed CIDR (the "no encryption" tail is the giveaway) | Client must use `sslmode=require` (libpq's default `prefer` can silently downgrade) |
| Node app (Outline) fails: `self-signed certificate` | Newer Node libraries treat `require` as `verify-full` | Use `sslmode=no-verify` in that app's URL (keeps TLS, skips chain checks) |
| Immich: `permission denied to create extension "earthdistance"` | Its migration needs superuser | Extensions are pre-created via `postgres_databases[].extensions` (`cube` before `earthdistance`, plus `pgvector`); add and run the leader-targeted provisioning (**Postgres operations** §6) |
| `cannot execute CREATE ROLE in a read-only transaction` | You hit a replica | Target the **current** leader (`patronictl list`); never `--limit fulla` |
| Pooled/long-lived clients drop after a minute | HAProxy's default idle timeout is 50 s | The DB listener needs the long idle timeout (set in `group_vars/haproxy_etcd.yml`); do not remove it |
| Clients stay on the old primary after a failover | Sessions were not shut | HAProxy `on-marked-down shutdown-sessions` should do it; restart the client |
| A Patroni callback never fires on promotion | Case statement matches only `master`; Patroni 3.x+ emits `primary` | Match both; callbacks must always exit 0 |
| `pg_dump` on a standby fails `canceling statement due to conflict with recovery` | Long read vs WAL replay | Logical dumps run on the leader only (`pg-backup.service`) |
| `systemctl kill patroni` left Postgres running and Patroni respawned | `KillMode=process` | Use `patronictl restart niflheim-pg <member>` or `systemctl restart patroni` |
| Replica fails with `no pg_hba.conf entry for replication` | Patroni's `bootstrap.pg_hba` only applies at initdb; on adoption it never ran | `patronictl edit-config` to add the replication entries (including `127.0.0.1/32` and `::1/128` for local `pg_rewind`) |
| Editing role defaults changed nothing | Patroni DCS settings seed only on first start | `patronictl show-config` / `edit-config`; the role reconciles reload-only parameters |
| Disk full on a Postgres LXC | WAL or dumps | Check `df -h` and `/var/backups/postgresql`; an LXC disk grows online with `pct resize`; WAL write failure is an outage |
| Zabbix history tables bloated, cache pressure | Stock autovacuum scale factor too lax for huge tables | Per-table autovacuum settings are already applied; check they persisted |
| `psycopg.DataError: cannot dump lists of mixed types` crashing Patroni at init | Patroni 4.x with psycopg3 | Install Patroni with the etcd3 extra on the supported pins (already in the role); reinstall via `asgard-postgres.yml` |
| Postgres service handler fails on a Patroni-managed node | The distro `postgresql` service is stopped on purpose | Handlers are gated on `postgres_patroni_managed`; do not start the distro unit |

## B3. HAProxy and the VIP

```bash
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do
  echo "== $ip"; $SSH ansible@$ip 'sudo systemctl status haproxy keepalived --no-pager | grep -E "Active|Loaded" ; sudo journalctl -u keepalived -n 8 --no-pager | tail -5'
done
# HAProxy's own view (stats bind to localhost on each node)
$SSH ansible@10.0.11.233 'curl -s "http://127.0.0.1:9000/;csv" | cut -d, -f1,2,18 | head -10'      # backend rows: UP for the leader only
```

`received an invalid passwd from <peer>` in keepalived logs: differing `auth_pass`, or another keepalived cluster on VLAN 10 with the same VRID (AdGuard = 51, Postgres = 61; never reuse).

## B4. What to do next

| Finding | Procedure |
|---|---|
| Need to move the leader / patch its host | **Postgres operations** §2 |
| Leader died | **Postgres operations** §3 |
| Replica broken | **Postgres operations** §4 |
| Data lost or wrong | **Postgres operations** §7 (dump restore) or **Backups & restore drills** §4 |
| Two nodes or etcd quorum gone | **Escalate** |

---

## See also

- **Storage & data** and **Identity & secrets** (Components).
- **Vault recovery**, **Postgres operations**, **AppRole rotation** (Procedures).
- **Identity & edge** (this section): when logins, not the database, are broken.
