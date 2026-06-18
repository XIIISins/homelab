<!-- docs/outline/troubleshooting/vault-and-postgres.md -->

# Vault & Postgres

The secrets layer and the relational-database layer. Both are foundational — when they misbehave, many services fail at once.

---

## Vault is sealed or a follower won't rejoin

**Symptom:** Vault is sealed, or a follower logs `stored unseal keys are supported, but none were found`.

**Fix:** this is its own runbook — see **Vault recovery** (Procedures) for Raft rejoin, stuck-init recovery, and unseal. Quick version: manually join the follower to a healthy peer and let KMS auto-unseal it.

---

## `terraform apply` gets permission denied on Vault resources

**Symptom:** a long apply chain starts erroring `permission denied` / `invalid token` on `vault_*` resources.

**Cause:** the per-session `VAULT_TOKEN` expired mid-deploy.

**Fix:** re-mint it into the env cache, then verify:
```
fish -c 'homelab-env; set-vault-token root; homelab-env --refresh'
vault token lookup
```
For apply chains over a couple of hours, re-verify before each module.

---

## Semaphore drift-check fails with permission denied on AppRole login

**Symptom:** Semaphore reports `permission denied` on `/v1/auth/approle/login`.

**Cause:** a mismatched RoleID/SecretID pair — typically the `ansible-local` and `ansible-awx` AppRoles got conflated.

**Diagnose:**
```
vault read -field=role_id auth/approle/role/ansible-awx/role-id
```
Compare against what Semaphore holds at `secret/k8s/semaphore/vault-approle`.

**Fix:** re-mint with the correct helper — see **AppRole rotation** (Procedures). Never write `ansible-local`'s SecretID into Semaphore's slot.

---

## A Postgres client is rejected — "no pg_hba.conf entry ... no encryption"

**Symptom:** a client connection fails with `FATAL: no pg_hba.conf entry for host ... no encryption`.

**Cause:** the cluster is `hostssl`-only and the client connected in plaintext. The `no encryption` suffix is the giveaway — it's a TLS problem, not a CIDR problem.

**Fix:** set `sslmode=require` explicitly on the client (libpq's default `prefer` silently downgrades). For modern Node apps (Outline) that treat `require` as full verification against the self-signed cert, use `sslmode=no-verify` instead — keeps the TLS envelope, skips the chain check.

---

## CREATE ROLE / CREATE DATABASE fails read-only

**Symptom:** a provisioning task errors `cannot execute CREATE ROLE in a read-only transaction`.

**Cause:** the command hit a Patroni *replica*, not the leader. Leadership floats — addressing a fixed node isn't safe.

**Diagnose:** find the current leader from any node:
```
sudo patronictl -c /etc/patroni/patroni.yml list
```
The row marked `Leader running` is the target.

**Fix:** run the provisioning against the current leader.

---

## Forcing an ungraceful Patroni failover (for testing)

**Symptom:** you want to test failover but `systemctl kill` doesn't actually kill Patroni — it respawns.

**Cause:** the unit ships `KillMode=process` with `Restart=on-failure`; killing the main PID leaves the Postgres child tree and the supervisor respawns.

**Fix:**
```
systemctl mask patroni && pkill -9 -f patroni; pkill -9 -f postgres
# recover:
systemctl unmask patroni && systemctl start patroni
```

## See also

- **Vault recovery** / **AppRole rotation** (Procedures) — the deliberate recovery operations.
- **Identity & secrets** / **Storage & data** (Components) — the Vault and Patroni architecture.
