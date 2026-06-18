<!-- docs/outline/procedures/vault-recovery.md -->

# Vault recovery

Bringing Vault back when it's sealed, a Raft follower won't rejoin, an init left partial state, or a token expired mid-deploy. Vault is a 3-node Raft cluster with AWS KMS auto-unseal, so most "sealed" situations resolve themselves — these are the cases where they don't.

> The Vault root token and recovery keys live in 1Password. Retrieve them dynamically when a step needs them — never paste the literal value anywhere.

---

## A follower won't rejoin Raft

Symptom: after a fresh init or a node restart, a follower logs *"stored unseal keys are supported, but none were found."* It missed the join window.

Manually join it to a healthy peer:
```
kubectl exec -n vault vault-<N> -- \
  vault operator raft join http://vault-0.vault-internal:8200
```
KMS auto-unseals it once it's a member. Confirm:
```
vault operator raft list-peers
```

---

## Vault is stuck in partial init state

When init left the StatefulSet in a half-configured state that won't recover:

1. Delete the StatefulSet, keeping the pods' PVCs orphaned:
   `kubectl delete statefulset vault -n vault --cascade=orphan`
2. Delete the data PVCs: `kubectl delete pvc -n vault data-vault-{0,1,2}`
3. Force-delete the pods: `kubectl delete pod -n vault vault-{0,1,2} --force --grace-period=0`
4. Re-reconcile: `flux reconcile helmrelease vault -n vault --force`
5. Sweep the orphaned `local-path` data dirs on the affected workers afterward. Vault's store is the node-local `local-path` tier since the storage redesign, so there are no iSCSI LUNs in DSM to clean up for Vault — a wiped node re-syncs from its Raft peers and KMS-auto-unseals.

---

## A token expired mid-deploy

Symptom: a long `terraform apply` chain starts erroring `permission denied` / `invalid token` on `vault_*` resources. The per-session `VAULT_TOKEN` in the env cache expired.

Re-mint the root token into the cache:
```
fish -c 'homelab-env; set-vault-token root; homelab-env --refresh'
```
Then `vault token lookup` to confirm. For any apply chain spanning more than a couple of hours, re-verify the token before each module's apply — expiry mid-flight is a quiet failure that costs a full plan-and-retry.

---

## Stepping down before maintenance

Before draining or deleting the worker holding Vault's Raft leadership, step down cleanly first so the cluster elects a new leader without a disruptive timeout:
```
vault operator step-down
```
Then proceed with the node operation. Costs about ten seconds; turns an abrupt leadership loss into a graceful handover.

---

## See also

- **Identity & secrets** (Components) — Vault's architecture, the KMS auto-unseal, the `tls_disable` design choice.
- **K3s node rebuild** (Procedures) — where the step-down-first rule applies during worker maintenance.
- **AppRole rotation** (Procedures) — for credential problems, which are a separate failure class from sealing/tokens.
