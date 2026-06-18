<!-- docs/outline/procedures/proxmox-host-patching.md -->

# Proxmox host patching

Rolling OS patching for the three PVE hypervisor hosts (Urd / Verd / Skuld). This is the highest-blast-radius routine operation — a host reboot takes every VM and LXC on it down — so it is deliberately excluded from the normal `os-updates` cadence and run by hand through its own health-gated playbook.

> The reboot of a host takes its guests down regardless of how they're patched — so run the roll **outside any PBS backup / verify / GC window** (Skuld hosts PBS).

---

## Why it's safe — the symmetric-topology invariant

Every PVE host carries **exactly one member of every quorum**: one K3s control plane, one K3s worker, one Patroni node, one etcd/HAProxy, one AdGuard, one Tailscale router.

- **One host down → N-1 on every quorum at once → tolerated.** All trios survive: Patroni promotes, keepalived re-elects, K3s reschedules.
- **Two hosts down at once → quorum loss across the board → catastrophic.**

So the roll is **strictly serial** and will not advance to the next host until the one just rebooted has fully reconverged to **3/3 on every quorum**. Only Frigg live-migrates (it's the one HA-managed, NFS-backed guest); everything else is node-pinned local storage and returns on boot.

## Discover, don't hardcode

The placement table is never written down — each host's work is computed live every run from `pvesh` (guests + HA state), `kubectl get nodes` (which K3s nodes live here), and the Patroni REST API (where the leader is). A **PLAN** line prints exactly what will be switched over / migrated / cordoned before any mutation; `--check` shows the whole plan with zero changes.

---

## Where to run it

**From the MacBook control node — never from inside the cluster.** An orchestrator can't reboot the host it runs on: Semaphore (a K3s pod) and Frigg (an HA guest) both get killed mid-job when their host reboots. The MacBook is outside the blast radius, and this playbook needs no Vault runtime lookup (so the Mac's fork-crash constraint doesn't apply).

## How to run

```bash
# from the MacBook, in ansible/, with homelab-env loaded
ansible-playbook playbooks/proxmox-host-patching.yml --check        # plan only, no changes
ansible-playbook playbooks/proxmox-host-patching.yml --limit urd    # canary one host
ansible-playbook playbooks/proxmox-host-patching.yml                # full rolling roll
```

Two pre-flight plays gate the start: all three hosts reachable, and the cluster already fully healthy (Patroni 3/3 + leader, etcd 3/3, all K3s nodes Ready). It refuses to start a rolling reboot against an already-degraded cluster.

## Per-host sequence

For each host, serially: discover state → print the PLAN → switch the Patroni leader off this host (if it's here) → migrate HA guests (Frigg) away → cordon its K3s nodes → `apt dist-upgrade` → reboot. Then the **reconvergence gate** blocks the next host until all guests are running again, the host is quorate, and Patroni / etcd / K3s are all back to 3/3 — only then uncordon and move on.

## If a gate fails

The play **halts at the failing host** and never advances, so a second host is never taken down — the cluster is left at most N-1 (the tolerated state). Read the failing task (it names the quorum that didn't reconverge), check that quorum directly, uncordon the host's K3s node once healthy, fix the cause, and re-run (idempotent; `--limit <next-host>` resumes). If a host doesn't return at all, suspect the NIC-rename-after-hardware-change class and reach for the physical console.

> Hands-off scheduling (from Semaphore/Frigg) is deferred — it needs a resumable checkpoint and special-casing for the orchestrator-can't-reboot-itself problem. Until then this is operator-launched and watched, fully automated once started.

---

## See also

- **Compute & hypervisors** (Components) — the three-host topology and the one-member-per-quorum placement this relies on.
- **Troubleshooting → Kubernetes & Flux** — diagnosing a node that doesn't come Ready after reboot.
- **K3s node rebuild** (Procedures) — when a node needs replacing rather than just patching.
