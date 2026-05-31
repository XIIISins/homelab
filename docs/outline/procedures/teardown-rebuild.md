<!-- docs/outline/procedures/teardown-rebuild.md -->

# Teardown & rebuild

The flagship disaster-recovery procedure: destroy the entire asgard K3s cluster and rebuild it from Terraform state up through Flux-managed workloads. Also the test that proves the infrastructure-as-code actually reproduces the cluster — running it occasionally on purpose is how you know it works when you need it.

> **This is break-glass.** It destroys VMs and cluster state. Read the whole runbook before starting, and confirm the pre-flight backups exist — one of them, if missing, makes the rebuild impossible.

---

## Pre-flight — before destroying anything

1. **Back up the sealed-secrets master key to 1Password.** This is the one that, if lost, makes every SealedSecret in Git permanently undecryptable.
   ```
   kubectl get secret -n sealed-secrets \
     -l sealedsecrets.bitnami.com/sealed-secrets-key=active -o yaml > backup.yaml
   ```
2. **Confirm the Vault root token and recovery keys are in 1Password.** The rebuild re-unseals Vault via AWS KMS, but the root token is needed to verify and operate.
3. **Note any stateful PVC data that must survive.** Cluster rebuild does not preserve iSCSI LUN data unless you've captured it.

---

## Teardown

4. **Drain workloads cleanly.** Suspend Flux and scale down workloads holding iSCSI LUNs, so sessions release gracefully rather than orphaning.
5. **Clean up iSCSI on the Synology.** Remove the released LUNs/targets in DSM SAN Manager and clear any stale initiator connections. Skipping this leaves orphan sessions that consume the NAS's session cap and bite the rebuild.
6. **`terraform destroy`** in `terraform/proxmox/asgard-k3s/` to remove the VMs. **This is the point of no return for VM state.**

---

## Rebuild

7. **`terraform apply`** the same module to recreate the VMs from templates.
8. **Bootstrap K3s with Ansible.** Run the asgard K3s playbook. The **init node initialises first** (`--cluster-init`), then the other control planes join, then the workers. If you're rebuilding *only* the default init node, override the init node so the playbook joins an existing cluster rather than starting a fresh one:
   ```
   ansible-playbook playbooks/asgard-k3s.yml -e 'k3s_init_node=<healthy-cp>'
   ```
9. **Restore the sealed-secrets master key** *before* the sealed-secrets controller generates a new one:
   ```
   kubectl apply -f backup.yaml
   ```
10. **Bootstrap Flux.** `flux bootstrap github` recreates the deploy key idempotently and starts reconciliation. Flux processes Kustomizations in dependency order: infrastructure → `*-config` → apps.
11. **Confirm Vault unsealed and Raft healthy.** AWS KMS auto-unseals; verify the peers rejoined:
    ```
    vault operator raft list-peers
    ```

---

## Validation

12. Every node `Ready`; every HelmRelease reconciled; the ESO `ClusterSecretStore` `Ready`; services reachable on their hostnames. The cluster is rebuilt when all four hold.

---

## Composes

- **K3s node rebuild** — the per-node mechanics (delete-node-first, init-node override).
- **Vault recovery** — if Raft followers don't rejoin cleanly.

## See also

- **Storage & data** (Components) — why clean iSCSI release matters.
- **Troubleshooting → Storage & iSCSI** — recovering orphan sessions if the cleanup was missed.
- **Troubleshooting → Kubernetes & Flux** — duplicate-node-name and failed-reconcile recovery.
