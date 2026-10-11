<!-- docs/outline/procedures/vault-recovery.md -->

# Vault recovery

Vault is a three-node Raft cluster on `local-path` volumes, auto-unsealed with AWS KMS. Most failures are one node out of step and are cheap to fix; the expensive ones involve losing the data on more than one node, which is what the snapshots are for.

---

## Know the shape first

- Quorum is 2 of 3. One pod down is degraded but fully working.
- The chart has no automatic Raft re-join, so a node with a **wiped** data volume comes up uninitialised and sealed and must be joined by hand.
- The StatefulSet update strategy is `OnDelete`: a config or resource change merged through Flux updates the spec but does **not** touch running pods. Roll them yourself.
- The listener is TLS from the internal CA; the join command must pass the CA certificate.

## Scenarios

**A follower missed the join window on first init.** It logs that stored unseal keys are supported but none were found. Join it to the leader with the CA certificate and its auto-unseal follows.

**Rolling pods after a change.** One pod at a time, standbys first and the leader last: delete the pod, wait for Ready, check it is unsealed, a standby, and at the same Raft applied index as the others, then continue. Deleting the leader is fine; there is no need for a step-down token.

**Replacing a node's storage** (for example a storage-class migration). Remove the node from Raft, wipe its volume, bring the pod up, join it by hand, then wait until it is a voter before touching the next node. Never do two at once.

**Stuck partial init.** Delete the StatefulSet with `--cascade=orphan`, delete the three data claims and pods, then force-reconcile the HelmRelease. Clean up any old iSCSI LUNs on the NAS afterwards.

**A worker is being rebuilt.** Step leadership away before draining, accept 2 of 3 voters while the node is out (see **K3s node rebuild**).

**Token expired mid-deploy.** The cached token in the shell is per-session. If Terraform reports permission denied or invalid token against Vault resources, re-mint it and verify with a token lookup. Which token a module needs depends on the module: the AppRole used by Ansible can only read `secret/ansible/*`, so modules that write or that read `secret/k8s/*` need the root token.

**HelmRelease install exhausted its retries.** A plain reconcile will not retry; use a forced reconcile once the underlying cause is fixed.

**Lost root token.** The token lives in 1Password. If it is truly lost, `vault operator generate-root` mints a new one; rotate it afterwards.

**Everything is gone.** Restore from a Raft snapshot: a daily snapshot is uploaded to S3, and a snapshot should be taken by hand before any storage-touching maintenance. A restore drill proved a scratch single-node Vault with the production KMS key unseals a fresh store and takes the snapshot, arriving with production's data and cluster ID. A Raft snapshot is the right backup; a plaintext dump of KV is not (it covers only KV and sits unencrypted on disk).

## Rules

- Take a snapshot before touching Vault storage.
- Do not mix up the two AppRole rotation helpers (see **AppRole rotation**).
- Values written with `vault kv put/patch` should be verified by hash; a value beginning with `@` is read as a file and `-` as stdin. For Vault to 1Password copies, use the mirror script.
- After any restore, check that External Secrets resync and that Semaphore's AppRole still logs in.

---

## See also

- **Identity & secrets** (Components): the Vault model.
- **Backups & restore drills** (this section).
- **Vault & Postgres** troubleshooting (Troubleshooting): sealed, token and AppRole symptoms.
