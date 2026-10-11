<!-- docs/outline/procedures/k3s-node-rebuild.md -->

# K3s node rebuild

Replace **one** control-plane or worker VM from scratch while the rest of the cluster keeps running. The architecture makes this routine, and it has been rehearsed for both kinds of node. Reasons to do it: a spec or template correction, failed hardware, a corrupt node.

**Use when:** one K3s VM is beyond repair or must be recreated.
**Never when:** another node, etcd member or Vault pod is already unhealthy. A second failure during the window costs quorum. **One node at a time.**

**Prerequisites**

```bash
kubectl get nodes                                                         # all 6 Ready
kubectl get --raw /readyz/etcd; echo                                      # ok
kubectl -n vault get pods -o wide                                         # vault-0..2 Running (note which worker each is on)
kubectl -n vault exec vault-0 -c vault -- vault operator raft list-peers  # 3 voters
flux get kustomizations | grep -v True                                    # nothing failing
```

Optional safety net, from the PVE hosts that own the surviving control planes: `qm snapshot 2002 before-cp-rebuild` (Hlökk) and `qm snapshot 2003 before-cp-rebuild` (Sigrún). Delete them after an hour of stability.

Node facts: control planes Göndul `2001` (Urd, `10.0.21.11`), Hlökk `2002` (Verd, `.12`), Sigrún `2003` (Skuld, `.13`); workers Einherjar-urd `2101` (`10.0.21.21`), -verd `2102` (`.22`), -skuld `2103` (`.23`). Names in Terraform and Ansible are lowercase ASCII: `gondul`, `hlokk`, `sigrun`, `einherjar-urd`.

---

## A. Replace a control plane

**Risk:** etcd runs at 2 of 3 until the new member joins.

1. **Terraform: recreate only that VM.** From the main checkout. Edit `locals.control_planes` in `terraform/proxmox/asgard-k3s/main.tf` if a spec is changing, then:

   ```bash
   cd terraform/proxmox/asgard-k3s
   terraform plan -target='proxmox_virtual_environment_vm.control_plane["<name>"]'
   # Expect exactly: 1 to add, 1 to destroy (or "1 to replace"). Anything else: STOP.
   terraform apply -target='proxmox_virtual_environment_vm.control_plane["<name>"]'
   ```

   If apply fails with `Logical Volume "vm-<id>-cloudinit" already exists`, orphan LVs remain from an earlier failed clone: on the owning host `qm list | grep <id>` (no config) and `lvs | grep vm-<id>`, then `lvremove -f /dev/pve/vm-<id>-cloudinit /dev/pve/vm-<id>-disk-0` (or `qm destroy <id> --purge` if a phantom config exists).

2. **Remove the old node object. Critical.** Without it the new VM fails with `duplicate node name found` and loops in systemd. This also evicts the stale etcd member.

   ```bash
   kubectl delete node <name>
   ssh-keygen -R <ip>; ssh-keygen -R <name>.niflheim.xiiisins.com
   ```

3. **Run the K3s play for that node only.** From Frigg. If the node you are rebuilding is the default init node (Göndul), tell the role another healthy control plane is the init node, or it will `--cluster-init` a brand-new cluster:

   ```bash
   cd ~/homelab/ansible
   # first run is as root (the hardening role locks root SSH at the end)
   ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --limit <name> -e ansible_user=root --tags baseline
   # full play as ansible; add the override ONLY when rebuilding gondul
   ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --limit <name> -e k3s_init_node=hlokk
   ```

4. **Taint (existing semantics).** The control-plane taint is applied at registration only; the role's `taint.yml` handles a rebuilt node. Verify below.

5. **Verify**

   ```bash
   kubectl get nodes                                          # 6 Ready
   kubectl describe node <name> | grep -A2 Taints             # node-role.kubernetes.io/control-plane:NoSchedule
   kubectl get --raw /readyz/etcd; echo                       # ok
   kubectl -n vault exec vault-0 -c vault -- vault operator raft list-peers
   ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --limit <name>   # changed=0 (idempotency)
   curl -s https://smoketest.niflheim.xiiisins.com/anything   # smoketest ok
   ```

6. **Reboot test.** `ssh … ansible@<ip> 'sudo reboot'`, wait for the node to return Ready and etcd to report ok.

If the new VM's `k3s` service is already looping because the playbook ran before step 2, delete the node object; the next retry succeeds on its own.

---

## B. Replace a worker (stateful)

Workers are not etcd members but carry **stateful pods**: one Vault Raft voter each (`local-path`, on the worker's `/data` disk), the VictoriaMetrics/VictoriaLogs volumes, and whatever else is pinned. Vault uses **required** anti-affinity (three replicas, three workers), so a displaced Vault pod cannot reschedule anywhere: **accept 2 of 3 voters for the 20 to 30 minute window.** Vault stays readable and writable at 2 of 3.

1. **Identify the Vault pod on the doomed worker, and step leadership off it if it leads.**

   ```bash
   kubectl -n vault get pods -o wide                                     # which vault-N is on <worker>?
   for i in 0 1 2; do echo -n "vault-$i: "; kubectl -n vault exec vault-$i -c vault -- vault status 2>/dev/null | grep "HA Mode"; done
   ```

   If the pod on the doomed worker is `active`, step down (needs a token with `sys/step-down`: the root token, operator only). If it is already `standby`, skip.

   ```bash
   kubectl -n vault exec vault-<N> -c vault -- env VAULT_TOKEN="$(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password')" vault operator step-down
   sleep 10; kubectl -n vault exec vault-<N> -c vault -- vault status | grep "HA Mode"      # now standby
   ```

2. **Drain.**

   ```bash
   kubectl drain einherjar-<w> --ignore-daemonsets --delete-emptydir-data --force --timeout=300s
   kubectl -n vault get pods -o wide        # the displaced vault pod is Pending: expected
   ```

3. **Confirm no iSCSI is left on that worker** (only if it held iSCSI volumes, e.g. Garage metadata):

   ```bash
   ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@<worker-ip> 'sudo iscsiadm -m session'    # expect: No active sessions
   kubectl get volumeattachment | grep einherjar-<w>                                                              # expect: empty
   # if not empty:
   ssh … ansible@<worker-ip> 'sudo iscsiadm -m node -u && sudo iscsiadm -m node -o delete'
   kubectl delete volumeattachment <name>
   ```

4. **Terraform: recreate only that VM** (main checkout):

   ```bash
   cd terraform/proxmox/asgard-k3s
   terraform plan -target='proxmox_virtual_environment_vm.worker["einherjar-<w>"]'       # expect 1 to add, 1 to destroy; nothing touching other nodes
   terraform apply -target='proxmox_virtual_environment_vm.worker["einherjar-<w>"]'
   ```

   The worker resource sets `reboot_after_update = false` on purpose (an apply must never power-cycle a node holding Raft state); a pending disk-flag change needs a deliberate `qm reboot <vmid>` after draining.

5. **Cluster cleanup and host keys**

   ```bash
   kubectl delete node einherjar-<w>
   ssh-keygen -R <worker-ip>; ssh-keygen -R einherjar-<w>
   ```

6. **Run the play** (no init-node override; workers join via the server URL):

   ```bash
   cd ~/homelab/ansible
   ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --limit einherjar-<w> -e ansible_user=root --tags baseline
   ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --limit einherjar-<w>
   ```

   About 10 to 15 minutes. The role builds the 50 GB `/data` xfs disk (`local-path-disk`) and the multi-homed network fixes. The worker rejoins and DaemonSets (Calico, MetalLB speaker, CSI node plugin, log collector) roll onto it.

7. **Vault rejoin.** The Pending Vault pod now schedules on the new worker, but its Raft data (`/data`) was on the destroyed disk, so it starts **uninitialised and sealed** and does not auto-join:

   ```bash
   kubectl -n vault get pod vault-<N> -o wide -w                      # Ctrl-C when Running on the new worker
   # Remove the dead peer entry if the cluster still lists it (root token):
   kubectl -n vault exec vault-0 -c vault -- env VAULT_TOKEN="$(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password')" vault operator raft list-peers
   kubectl -n vault exec vault-0 -c vault -- env VAULT_TOKEN="$(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password')" vault operator raft remove-peer <node-id>   # only if listed as a ghost/unreachable
   # Join the new pod (TLS: pass the internal CA)
   kubectl -n vault exec vault-<N> -c vault -- vault operator raft join -leader-ca-cert=@/vault/userconfig/vault-tls/ca.crt https://vault-0.vault-internal:8200
   sleep 30
   kubectl -n vault exec vault-<N> -c vault -- vault status | grep -E "Initialized|Sealed|HA Mode"    # true / false / standby
   kubectl -n vault exec vault-0 -c vault -- vault operator raft list-peers                            # 3 voters
   # A freshly joined node is a NON-voter until autopilot promotes it; wait for Voter true and tolerance 1 before touching anything else
   kubectl -n vault exec vault-0 -c vault -- env VAULT_TOKEN="$(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password')" vault operator raft autopilot state | grep -E "Healthy|Failure Tolerance"
   ```

   (If you restore the worker's `/data` disk from PBS instead of recreating it, the pod keeps its Raft identity and rejoins on its own.)

8. **Verify everything came back**

   ```bash
   kubectl get nodes; kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
   kubectl get pvc -A | grep -v Bound
   flux get kustomizations | grep -v True
   kubectl -n monitoring get pods          # VictoriaMetrics/Logs start empty if their volume was on the destroyed disk; refillable
   ```

   Uncordon is not needed after a node delete and rejoin; if you only drained, run `kubectl uncordon einherjar-<w>`.

---

## Rules that apply to every rebuild

- **One node at a time**, and wait for 3 of 3 before the next.
- Never run a K3s config change over all nodes at once: the restart handler fires everywhere. Stage with `--limit <one node>` first. (`asgard-k3s.yml` is `serial: 1` for workers.)
- A runtime-only fix is not done: **reboot the node** and re-verify.
- If the failure is the host (Skuld hard-freeze), rebuilding the VM will not help; see *Hosts & Proxmox* troubleshooting.

---

## See also

- **K3s & Calico upgrade**, **Vault recovery** and **Teardown & rebuild** (this section).
- **Compute & hypervisors** (Components): node specs.
- **Kubernetes & Flux** (Troubleshooting).
