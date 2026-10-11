<!-- docs/outline/troubleshooting/vault-and-postgres.md -->

# Vault & Postgres

Symptoms with secrets, tokens, the database and failover.

---

## First diagnostics

```
vault status                                  # sealed? HA mode? (via the UI/FQDN or a pod)
sudo patronictl -c /etc/patroni/patroni.yml list   # on a Postgres node: leader, replicas, lag
curl -s http://<postgres-node>:8008/master    # Patroni REST: 200 only on the leader (what HAProxy checks)
```

## Vault

| Symptom | Likely cause | What to do |
|---|---|---|
| Terraform: `permission denied` or `invalid token` on `vault_*` resources mid-deploy | The cached token is per-session and expired | Re-mint it (root login, then refresh the env cache) and verify with a token lookup. |
| A module 403s on `secret/k8s/*` or cannot write | The AppRole policy reads `secret/ansible/*` only | Use the root token for modules that write or touch `k8s/` paths. |
| `invalid role or secret ID` from an AppRole login | A stale SecretID. Re-seeding only copies what the shell holds, it does not mint | Properly rotate (see **AppRole rotation**); if 1Password holds the stale copy, only a rotation fixes it. |
| Rotation helper says "unknown role" | Used the wrong helper for Semaphore's role | Use the Semaphore-specific helper. |
| Semaphore's Vault login breaks after a rotation | A SecretID from the wrong role was written into Semaphore's slot | Re-mint for the right role and re-push through the Semaphore Terraform module. |
| The SecretID expires after 32 days, not 90 | The auth mount's max lease TTL clamps it | Tune the mount to 90 days in Terraform. |
| A HelmRelease install failed, then stays failed | Helm's retry budget is spent | Force a reconcile once the cause is fixed. |
| A follower is uninitialised and sealed | A wiped data volume; there is no automatic Raft re-join | Join by hand with the CA certificate (see **Vault recovery**). |
| A Vault config change merged but the pods are unchanged | The StatefulSet is `OnDelete` | Roll the pods one at a time, leader last. |
| TLS error reaching Vault from a new tool | The internal CA is not in that tool's trust store | Point it at the mounted CA bundle. Raw HTTP calls and `vault_login` do not auto-detect the CA the way the KV lookups do. |
| A stored secret has a plausible but wrong value | `vault kv put/patch` treats `@…` as a file and `-` as stdin; word-splitting is a risk | Use the mirror script, or compare hashes after any manual write. |
| Control node and workstation use different old values | The three-hour env cache | Refresh the cache on **every** machine after patching a secret. |

## Postgres

| Symptom | Likely cause | What to do |
|---|---|---|
| `no pg_hba.conf entry … no encryption` | Postgres accepts `hostssl` only; the same message appears for a disallowed CIDR, and the "no encryption" suffix gives it away | Set `sslmode=require` explicitly; libpq's default `prefer` can silently downgrade. |
| Node apps (Outline) fail with a self-signed certificate error | Newer Node libraries treat `require` as `verify-full` | Use the library's no-verify mode for that app; the cluster certificate is self-signed. |
| Immich: `permission denied to create extension` | Its migrations need superuser | The extensions are pre-created through `postgres_databases[].extensions`; add them there. |
| `cannot execute CREATE ROLE in a read-only transaction` | The provisioning ran against a replica | Target the **current** leader (`patronictl list`); leadership floats, so `--limit fulla` is not safe. |
| Pooled clients drop after a minute | HAProxy's default idle timeout is 50 s | The DB listener needs a long idle timeout. |
| A failover left clients on the old primary | Sessions were not shut down | HAProxy marks the old primary down and shuts its sessions; clients should reconnect through the VIP. |
| A callback matching only `master` never fires | Patroni 3.x+ emits `primary` | Match both names. Callbacks must always exit 0. |
| `pg_dump` on a standby is cancelled | Recovery conflict on a long read | The logical dump runs on the current leader only. |
| A restarted Patroni did not stop Postgres | The unit's kill mode leaves the PG child tree alive and restarts Patroni | Stop through Patroni or `patronictl`, not a bare kill. |
| `patronictl failover` / forced failover | A deliberate test | Observed RTO is 10–30 seconds; confirm the Hermod message and the new leader on the VIP. |
| A config edit in role defaults changed nothing | Patroni DCS settings seed only on first start | Edit the DCS (`patronictl show-config` / edit); the role reconciles reload-only parameters. |
| Bloat on Zabbix history tables | The stock autovacuum scale factor is too lax for huge tables | Per-table autovacuum settings (already applied). |

## Quorum arithmetic

- Vault: 2 of 3. Postgres: any one node can be lost; Patroni needs etcd quorum, which sits on the HAProxy trio (2 of 3).
- Two simultaneous node losses on any trio stop writes or leader election. Do not patch two Proxmox hosts at once.

---

## See also

- **Storage & data** and **Identity & secrets** (Components).
- **Vault recovery**, **AppRole rotation**, **Proxmox host patching** (Procedures).
