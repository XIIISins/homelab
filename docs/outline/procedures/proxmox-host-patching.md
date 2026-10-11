<!-- docs/outline/procedures/proxmox-host-patching.md -->

# Proxmox host patching

Patching the three hypervisors is the highest-blast-radius routine operation: a host reboot takes down every VM and LXC on it. It therefore has its own deliberately-triggered, health-gated playbook, separate from the fleet OS-update template, which excludes the hypervisors.

---

## Why it is safe

**Every host carries exactly one member of every quorum**: one K3s control plane, one worker, one Patroni node, one etcd/HAProxy member, one AdGuard node and one Tailscale router. One host down means N-1 on every quorum at once, which all of them tolerate. Two hosts down at once is catastrophic: etcd, Patroni and K3s lose quorum together.

So the playbook is **strictly serial** and does not advance until the host it just rebooted is back to 3 of 3 on everything.

## Discover, don't hardcode

For each host, the playbook computes its work live: which guests run there and which are HA-managed (from the Proxmox API), which K3s nodes are guests on that host (from `kubectl`), and where the Patroni leader is (from its REST API). A PLAN line prints what will be switched over, migrated and cordoned before anything changes.

## Run it from outside the blast radius

Run from the operator's workstation. An orchestrator cannot live on a host it is about to reboot: Semaphore is a pod that reschedules when its worker's host reboots, and Frigg restarts when its host does.

## What happens per host

1. Pre-flight (once): all hosts reachable, and the cluster fully healthy (Patroni 3 of 3 with a leader, etcd 3 of 3, K3s nodes Ready). It refuses to start against a degraded cluster.
2. Switch the Patroni leader off this host if it is the leader.
3. Migrate HA-managed guests (Frigg) elsewhere.
4. Cordon this host's K3s nodes.
5. `apt dist-upgrade`, then reboot if packages changed.
6. **Reconvergence gate before the next host**: guests running, Proxmox quorate, Patroni and etcd back to 3 of 3, K3s nodes Ready, then uncordon.

## Running it

- **Dry run first**: `--check` prints the plan with no changes.
- **Canary**: limit to one host and validate the real path.
- **Full rolling patch**: all three, serially.

Do not run during a PBS backup, verify or garbage-collection window: Urd hosts PBS, so its reboot takes the backup server down regardless.

---

## See also

- **Hypervisors** (Hardware): the nodes involved.
- **Storage & data** (Components): Patroni, PBS and the quorum members.
- **K3s node rebuild** (this section).
