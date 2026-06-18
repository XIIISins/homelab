<!-- docs/outline/procedures/k3s-node-rebuild.md -->

# K3s node rebuild

Rebuilding a single asgard K3s node — a control plane or a worker — while the rest of the cluster stays up. The cluster tolerates losing one node; this runbook is how you replace one deliberately and cleanly.

---

## Control plane rebuild

The hazard is the new node trying to start a *fresh* cluster, or colliding with the old node's identity.

1. **Delete the old node object from a surviving control plane** *before* starting K3s on the replacement. This also evicts the stale etcd member:
   ```
   kubectl delete node <name>
   ```
2. **Rebuild the VM** (Terraform) and **run the K3s playbook against it**, overriding the init node so the rebuilt node *joins* rather than `--cluster-init`-ing a new cluster:
   ```
   ansible-playbook playbooks/asgard-k3s.yml --limit <name> -e 'k3s_init_node=<healthy-cp>'
   ```
3. Confirm the node is `Ready` and etcd shows a healthy three-member cluster.

> Skipping step 1 produces a "duplicate node name found" failure, or a brand-new empty cluster if the init-node override is also missed.

---

## Worker rebuild

The worker hazards are Raft-quorum workloads (Vault) and iSCSI session/attachment state.

1. **If the worker hosts a Vault pod, step down its Raft leadership first** (while the pod is still healthy), so the cluster re-elects cleanly:
   ```
   vault operator step-down
   ```
   Vault's pod anti-affinity is *required* with three replicas across three workers — expect to run at 2/3 voters during the window. That's fine; Vault stays read+write.
2. **Drain the worker** so pods reschedule to surviving nodes.
3. **Clean up iSCSI on the old worker.** Sessions die with the pods, but the node records in `/var/lib/iscsi/nodes/` persist and will try to log back in on the next reboot. Log out and delete them:
   ```
   iscsiadm -m node -T <iqn> -p 10.0.254.20 --logout
   iscsiadm -m node -T <iqn> -p 10.0.254.20 -o delete
   ```
4. **Delete any stale `VolumeAttachment`** still pointing at the dead worker, or volumes won't re-attach elsewhere (Multi-Attach errors).
5. **Rebuild the VM** (Terraform) and **run the K3s playbook** against it to rejoin. No init-node override needed for workers.

---

## Cross-cutting note

A `NoSchedule` taint (like the control-plane taint) does **not** evict existing pods — it only blocks new scheduling. Don't taint a node expecting stateful pods to move; drain them first.

## See also

- **Vault** → **Identity & secrets** (Components) — the Raft quorum and anti-affinity constraints.
- **Troubleshooting → Storage & iSCSI** — diagnosing orphan sessions and stuck VolumeAttachments.
- **Teardown & rebuild** (Procedures) — the whole-cluster version of this.
