<!-- docs/outline/procedures/k3s-node-rebuild.md -->

# K3s node rebuild

Replacing a single control-plane or worker VM from scratch. The whole point of the architecture is that this is routine, and it has been rehearsed for both kinds of node.

---

## Control plane

1. **Delete the old node object first**, from a surviving control plane: `kubectl delete node <name>`. Without it the rebuilt VM fails with "duplicate node name". The delete handler also evicts the stale etcd member.
2. **Recreate the VM** with Terraform (same name, same IP, same VMID).
3. **Run the K3s play with `--limit <name>`.** If the node being rebuilt is the default init node (Göndul), override the init node with any healthy control plane (`-e k3s_init_node=hlokk`); otherwise the role starts a brand new cluster with `--cluster-init`.
4. **Verify**: node Ready, three etcd members healthy, Flux and the CNI happy.

The control-plane taint is applied at registration only. Restarting K3s on an existing node will not re-apply it; use `kubectl taint` for existing nodes.

## Worker

Workers hold state, so the order matters:

1. **Step Raft leadership away first.** If a Vault pod runs on this worker, step down leadership while the pod is healthy so the cluster elects cleanly (about ten seconds), then the pod's departure is a clean follower leaving.
2. **Cordon and drain.** Vault uses required anti-affinity (three replicas, three workers), so the displaced Vault pod will sit Pending. Run on 2 of 3 voters while the node is gone; Vault stays fully readable and writable. Budget 20–30 minutes.
3. **Delete the node object, recreate the VM, run the K3s play.** The worker gets a fresh `/data` disk, so its `local-path` volumes are empty: VictoriaMetrics and VictoriaLogs start empty (refillable data), and the Vault pod must rejoin Raft and resync.
4. **If the old worker held iSCSI volumes**, clean up stale iSCSI node records on the source worker and orphan sessions on the NAS, or a later reboot can log into stale targets and cause Multi-Attach errors elsewhere.
5. **Uncordon** and verify Vault shows three voters again.

## General rules

- Do **one** node at a time and wait for 3 of 3 before the next.
- Do not run a K3s config change across all nodes at once; a restart handler fires everywhere. Stage with `--limit` on one node first.
- Persistent changes are verified by rebooting the node.

---

## See also

- **K3s & Calico upgrade** (this section).
- **Vault recovery** (this section): the Raft rejoin steps.
- **Teardown & rebuild** (this section): the partial-rebuild appendices in full.
- **Compute & hypervisors** (Components): node specs and roles.
