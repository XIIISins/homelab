<!-- docs/outline/procedures/vault-recovery.md -->

# Vault recovery

Vault runs as three pods (`vault-0..2`, namespace `vault`), one per worker, with Raft storage on each worker's `local-path` disk, TLS from the internal CA, and AWS KMS auto-unseal. Most failures are one node out of step and are cheap to fix. The expensive ones lose data on more than one node, which is what snapshots are for.

**Use when:** a Vault pod is sealed, uninitialised, missing from Raft, a token or AppRole login fails, or Vault storage must be touched.
**Quorum:** 2 of 3. One pod down is degraded but fully working; **two down stops writes: escalate.**

---

## 0. Know the shape (read first)

- The chart has **no `retry_join`**: a pod whose data volume was wiped starts **uninitialised and sealed** and does **not** rejoin by itself. You join it by hand.
- The StatefulSet is **`OnDelete`**: a config or resource change merged through Flux updates the spec but does **not** touch running pods. You roll them by hand.
- The listener is TLS from `homelab-internal-ca`; in-pod CLI already has `VAULT_ADDR=https://127.0.0.1:8200` and `VAULT_CACERT` set. For anything outside the pod, use the CA bundle (`vault-ca-bundle` ConfigMap) or go through the FQDN.
- Operator CLI/Terraform use `VAULT_ADDR=https://vault.niflheim.xiiisins.com` (Traefik, public LE cert). The `.11` LoadBalancer (`https://10.0.20.11:8200`) is a **break-glass** path that bypasses Traefik; it needs `VAULT_CACERT`.
- The root token lives in 1Password (`[Bootstrap] - Manual - Vault - Root token`, UUID `7g4grolyien2yqkm7me2jficmy`). **Operator only.** The recovery keys are in 1Password too.

Shorthand for this page:

```bash
V() { kubectl -n vault exec "vault-$1" -c vault -- "${@:2}"; }                            # V <n> <cmd...>
VR() { kubectl -n vault exec "vault-$1" -c vault -- env VAULT_TOKEN="$(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password')" "${@:2}"; }   # with the root token, operator only
```

---

## 1. Health check (read-only)

```bash
kubectl -n vault get pods -o wide                       # 3 pods, 1/1 Running, one per worker
for i in 0 1 2; do echo "== vault-$i"; V $i vault status | grep -E "Initialized|Sealed|HA Mode|Raft Applied Index|Active Node"; done
VR 0 vault operator raft list-peers                      # 3 members, all Voter=true, one leader
VR 0 vault operator raft autopilot state | grep -E "Healthy|Failure Tolerance"     # Healthy true, Failure Tolerance 1
kubectl get externalsecrets -A | grep -v SecretSynced     # none: ESO can read Vault
curl -s -o /dev/null -w '%{http_code}\n' https://vault.niflheim.xiiisins.com/v1/sys/health    # 200 active / 429 standby / 503 sealed
```

Good: three unsealed pods, same Applied Index (within a few), one `active` and two `standby`, `Failure Tolerance: 1`.

---

## 2. A pod is sealed

KMS auto-unseal normally does this itself. If a pod stays sealed:

```bash
kubectl -n vault logs vault-<N> -c vault --tail=50 | grep -iE "seal|kms|error"
```

- `AccessDenied`/`could not reach KMS`/timeouts: AWS KMS is unreachable or the `vault-unseal` credentials are wrong. Check egress and the `vault-unseal` Secret exists (`kubectl -n vault get secret vault-unseal`; it is a SealedSecret, decrypted by the sealed-secrets controller). **Do not delete it.** If the sealed-secrets key was lost, see **Teardown & rebuild** §3.4. Escalate.
- Pod restarted and is sealed for a few seconds: wait 30 seconds, re-check.

## 3. A follower is uninitialised and not in the cluster

Symptom: `vault status` says `Initialized false`, logs say `stored unseal keys are supported, but none were found`, or `list-peers` shows only two members.

```bash
# Join it to the leader (use the leader's name; vault-0 is shown). TLS: pass the CA.
V <N> vault operator raft join -leader-ca-cert=@/vault/userconfig/vault-tls/ca.crt https://vault-0.vault-internal:8200
sleep 30                                                  # KMS auto-unseals it
V <N> vault status | grep -E "Initialized|Sealed|HA Mode"  # true / false / standby
VR 0 vault operator raft list-peers
VR 0 vault operator raft autopilot state | grep -E "Voter|Failure Tolerance"
```

**Wait for promotion:** a freshly joined node is a non-voter until autopilot promotes it (about the `server_stabilization_time` window), during which `Failure Tolerance` is **0**. Do not touch another pod until the new one is `Voter: true` and `Failure Tolerance: 1`.

If a ghost entry for the old node remains (`list-peers` shows a member that is unreachable): `VR 0 vault operator raft remove-peer <node-id>`.

## 4. Roll the pods after a config or resource change (OnDelete)

One pod at a time, **standbys first, leader last**:

```bash
VR 0 vault operator raft list-peers                       # note the leader; roll the other two first

# For ONE pod N (repeat for the next only after the checks pass):
kubectl -n vault delete pod vault-<N>
kubectl -n vault wait --for=condition=Ready pod/vault-<N> --timeout=300s
sleep 20
V <N> vault status | grep -E "Sealed|HA Mode"             # Sealed false; HA Mode standby (the leader's replacement is elected)
VR 0 vault operator raft autopilot state | grep "Failure Tolerance"       # must be back to 1
for i in 0 1 2; do V $i vault status | grep "Raft Applied Index"; done    # all within a few of each other
```

Deleting the leader is fine; Vault hands over on graceful shutdown. A step-down needs a token with `sys/step-down` (the root token); Frigg's AppRole and the pod shell do not have it. The StatefulSet's `currentRevision` keeps the old hash after an OnDelete roll; compare the pods' `controller-revision-hash` to see they are updated.

## 5. Replacing a node's storage, or a wiped volume (per node, leader last)

```bash
# From a healthy node, drop the node from Raft (voters 3 -> 2; quorum still 2)
VR 0 vault operator raft remove-peer vault-<N>
# Wipe its volume and let the StatefulSet make a fresh one
kubectl -n vault delete pvc data-vault-<N> --wait=false
kubectl -n vault delete pod vault-<N>
kubectl -n vault wait --for=condition=Ready pod/vault-<N> --timeout=300s     # Ready may stay false (sealed/uninitialised): that is fine, join next
# Join (section 3), wait for promotion, THEN the next node
```

Step leadership off the leader first (`VR <leader> vault operator step-down`) so the leader lands on an already-migrated node. Doing two nodes before promotion drops you below quorum.

## 6. Stuck partial init

```bash
kubectl -n vault delete statefulset vault --cascade=orphan
kubectl -n vault delete pvc data-vault-0 data-vault-1 data-vault-2
kubectl -n vault delete pod vault-0 vault-1 vault-2 --force --grace-period=0
flux reconcile helmrelease vault -n vault --force
# then Teardown & rebuild 3.5 (init, restore a snapshot, join followers)
```

**Only with the owner's go-ahead: this destroys Vault's data. Take or locate a snapshot first.**

## 7. HelmRelease exhausted its install retries

A plain reconcile will not retry: `flux reconcile helmrelease vault -n vault --force` (or `--reset`) once the cause is fixed.

---

## 8. Tokens and logins

| Symptom | Fix |
|---|---|
| `terraform` or `vault` returns `permission denied` / `invalid token` mid-deploy | The cached token is per-session and expired. Re-mint: `set-vault-token root` (operator), then `homelab-env --refresh`; verify `vault token lookup`. |
| A Terraform module 403s on `secret/k8s/*`, on writes or on `auth/token/create` | The shim's AppRole token reads `secret/ansible/*` only. These modules need the root token: `terraform/vault`, `semaphore`, `tailscale`, `cloudflare`, `authentik`. Operator runs them. |
| `invalid role or secret ID` | A stale SecretID. Re-seeding only copies what the shell holds. Do a real rotation: **AppRole rotation**. Distinguish: `vault read -field=role_id auth/approle/role/<role>/role-id` against the role id in the secret-zero file; if they match, the secret_id is the problem. |
| Lookup works locally but fails in Semaphore | Semaphore uses `ansible-awx` with `secret/k8s/semaphore/vault-approle`, and needs `ANSIBLE_HASHI_VAULT_CA_CERT` for the in-cluster TLS endpoint. Rotate with `rotate-semaphore-approle`. |
| Root token lost | `vault operator generate-root` using the recovery keys from 1Password (init, then supply keys until the threshold, decode the OTP); rotate afterwards. Escalate. |

## 9. Backups and restoring from a snapshot

A Raft snapshot is the right Vault backup. A `kv get` dump is not (KV only, plaintext on disk).

```bash
# Take one (before ANY storage-touching maintenance)
VR 0 vault operator raft snapshot save /tmp/vault.snap
kubectl -n vault cp vault/vault-0:/tmp/vault.snap ./vault-$(date +%Y%m%d).snap -c vault
kubectl -n vault exec vault-0 -c vault -- rm /tmp/vault.snap

# Restore (OPERATOR + OWNER ONLY; replaces everything in Vault with the snapshot)
kubectl -n vault cp ./vault-<date>.snap vault/vault-0:/tmp/vault.snap -c vault
VR 0 vault operator raft snapshot restore -force /tmp/vault.snap
kubectl -n vault exec vault-0 -c vault -- rm /tmp/vault.snap
```

Daily snapshots are in S3 (`vault-raft/`, 30 days). A restore re-encrypts under the current KMS key; recovery keys and root token revert to the snapshot's (1Password's bootstrap values). AppRole **SecretIDs** are not in a snapshot: re-mint them (**Teardown & rebuild** §3.6). After any restore: force-sync ExternalSecrets, confirm `raft list-peers` shows 3 voters, and check Semaphore can log in.

## 10. Writing values safely

`vault kv put|patch field=VALUE` treats a value starting with `@` as a file and `-` as stdin; a word-split or stdin form has stored plausible-but-wrong values. Always compare a hash after a manual write, or use the mirror script (**Secret mirroring**):

```bash
printf '%s' "$VALUE" | shasum -a 256 | cut -c1-16
vault kv get -field=<field> secret/<path> | shasum -a 256 | cut -c1-16        # must match
```

---

## See also

- **K3s node rebuild** (this section): the Vault steps for a worker rebuild.
- **Identity & secrets** (Components): the Vault model.
- **Vault & Postgres** (Troubleshooting): symptom index.
