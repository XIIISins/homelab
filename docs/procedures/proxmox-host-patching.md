<!-- docs/procedures/proxmox-host-patching.md -->
# Procedure — Proxmox PVE host patching (rolling, discovery-driven)

OS patching for the PVE hypervisor hosts (Urd / Verd / Skuld). This is the
high-blast-radius operation that [`os-updates.yml`](../../ansible/playbooks/os-updates.yml)
deliberately **excludes** — a host reboot takes every VM/LXC on it down —
so it gets its own deliberately-triggered, health-gated playbook:
[`ansible/playbooks/proxmox-host-patching.yml`](../../ansible/playbooks/proxmox-host-patching.yml).

> **PBS** (the LXC on Skuld) is patched separately by `os-updates.yml`
> (it's in the standalone-LXC group). Its *host* (Skuld) rebooting here
> takes PBS down regardless — so run the host roll **outside any backup /
> verify / GC window**. Repo posture for PBS is `pbs-repo-fix.yml`; for the
> PVE hosts it's the [`proxmox-host`](../../ansible/roles/proxmox-host/) role.

---

## Why it's safe — the symmetric-topology invariant

Every PVE host carries **exactly one member of every quorum**: one K3s
control plane, one K3s worker, one Patroni node, one etcd/HAProxy, one
AdGuard, one Tailscale router.

- **One host down = N-1 on every quorum at once → tolerated.** All trios
  survive, Patroni promotes, keepalived re-elects, K3s reschedules.
- **Two hosts down at once = quorum loss across the board → catastrophic.**
  etcd, Patroni, and K3s all go read-only/down together.

So the procedure is **strictly serial (`serial: 1`)** and will not advance
to the next host until the one just rebooted has fully reconverged to
**3/3 on every quorum**. Nothing live-migrates except Frigg (the only
`munin-nfs` / HA-managed resource); everything else is `local-lvm`,
node-pinned, and returns via `start_on_boot=true`.

## Discover, don't hardcode

The placement table is **never encoded**. Each host's work is computed
live every run:

| Discovered from | Drives |
|---|---|
| `pvesh get /cluster/resources --type vm` | guests on the host + which are HA-managed (`hastate`) → expected-back set + HA migrations |
| `kubectl get nodes` | which K3s nodes are guests on this host → cordon/uncordon |
| Patroni REST `/cluster` | where the leader is → switchover if it's here, and the off-host candidate |

The per-host **PLAN** line prints exactly what will be switched over /
migrated / cordoned **before any mutation**. Run `--check` to see the
whole plan with zero changes.

---

## Where to run it — IMPORTANT

**Run from the MacBook control node.** An orchestrator cannot live on a
host it's about to reboot:

- **Semaphore** (K3s pod) reschedules when its worker's host reboots →
  kills the running job.
- **Frigg** HA-cold-restarts (~6s) when Verd reboots → kills the job.
- **MacBook** is outside the blast radius, and this playbook needs **no**
  `community.hashi_vault` runtime lookup (switchover = Patroni REST, K3s =
  kubectl, migrate = `ha-manager` over SSH), so the Mac's Python-3.14
  fork crash doesn't apply.

Hands-off scheduling from Frigg/Semaphore is **phase 2** (see bottom).

## Prerequisites (on the controller)

- `homelab-env` loaded (gives `KUBECONFIG` + SSH key).
- LAN reach to Patroni REST (`10.0.11.x:8008`) and etcd (`:2379`).
- The cluster is **already fully healthy** — the playbook's first two
  plays refuse to start otherwise (see "Pre-flight gate" below).
- No PBS backup/verify/GC running (matters for the Skuld step).
- Optional but recommended before a big upgrade: a Vault Raft snapshot
  (`kubectl exec -n vault vault-0 -c vault -- … raft snapshot save`) —
  not automated here; a reboot isn't storage-touching, this is just
  insurance.

---

## How to run

```bash
# From the MacBook, in ansible/, with homelab-env loaded.

# 1) DRY RUN — plan only. Gathers real state, prints the per-host plan,
#    changes nothing (every mutation + the reboot gate are skipped).
ansible-playbook playbooks/proxmox-host-patching.yml --check

# 2) CANARY — one host, full orchestration. Validate the real path on a
#    single host before committing to all three.
ansible-playbook playbooks/proxmox-host-patching.yml --limit urd

# 3) FULL ROLLING PATCH — all three, serial, health-gated.
ansible-playbook playbooks/proxmox-host-patching.yml
```

### Useful overrides

| Var | Default | Effect |
|---|---|---|
| `pve_patch_reboot` | `true` | `false` = apt only (tests the apt path; skips the disruptive drain/switchover **and** the reboot gate). Not a kernel-activation path. |
| `pve_patch_force_reboot` | `false` | Reboot even when apt reported no change (activate a kernel staged by an earlier `reboot:false` run). |
| `pve_patch_drain` | `false` | Also `kubectl drain` (cordon-only by default — the reboot takes the pods anyway, and node-pinned local-path pods can't reschedule regardless). |
| `pve_patch_reboot_timeout` | `900` | Seconds to wait for SSH after reboot. |

---

## What each host's run does

```
PRE-FLIGHT (once, before any host)
  ├─ Play 1: ping all 3 PVE hosts (fail fast if any unreachable)
  └─ Play 2: assert cluster fully healthy — Patroni 3/3 + leader,
             etcd 3/3, all K3s nodes Ready. Refuses to start a rolling
             reboot against an already-degraded cluster.

PER HOST (serial: 1)
  1. Discover guests / HA state / K3s nodes / Patroni leader
  2. PLAN debug — print what will be switched/migrated/cordoned
  3. Abort if leader is here but no off-host candidate exists
  4. Switchover Patroni leader off this host (if it's the leader)
  5. ha-manager migrate HA guests (Frigg) to another host; wait gone
  6. Cordon (and optionally drain) this host's K3s nodes
  7. apt update && apt dist-upgrade
  8. Reboot (if packages changed, or --force)
  ── RECONVERGENCE GATE (blocks the next host) ──────────────────
  9. Wait: all expected guests `running` again
 10. Assert: pvecm quorate on this host
 11. Wait: Patroni 3/3 streaming + leader (this host's member streaming)
 12. Wait: etcd 3/3 healthy
 13. Wait: this host's K3s node(s) Ready  → then uncordon
 14. Next host
```

Frigg is **not** migrated back to its original host — it stays wherever
HA placed it (placement is irrelevant; it's HA-managed on shared NFS).

---

## If a gate fails

The play **halts** at the failing host (it never advances, so a second
host is never taken down). The cluster is left with that one host either
mid-reboot or back-but-not-fully-converged — i.e. **at most N-1**, which
is the tolerated state. Then:

1. Read the failing task — it names which quorum didn't reconverge.
2. Check that quorum directly (`patronictl list`, `etcdctl endpoint
   health`, `kubectl get nodes`).
3. The host's K3s node may be left **cordoned** — `kubectl uncordon
   <node>` once it's healthy.
4. Fix the underlying issue, confirm full health, then re-run
   (idempotent — already-patched hosts just no-op the apt step; a
   `--limit <next-host>` resumes from where it stopped).

### Reboot-test reminder

Per the persistence-validation rule, the gate already asserts the host
came back usable (quorate + guests running). If a host fails to return
within `pve_patch_reboot_timeout`, suspect the same class as the
hardware-refresh NIC-rename gotcha (`/etc/network/interfaces` referencing
a renamed interface) — this needs the **physical console**.

---

## The `proxmox-host` role (repo posture — separate from patching)

[`ansible/roles/proxmox-host/`](../../ansible/roles/proxmox-host/) is
**config-only**: it disables the enterprise apt repo and ensures
`pve-no-subscription`, so a host can `apt update` without a subscription.
It does **not** upgrade or reboot, so it's wired into
[`asgard-proxmox.yml`](../../ansible/playbooks/asgard-proxmox.yml) and runs
under `site.yml` / drift-check. Patching stays out of every converge loop
— it's only ever this playbook, run by hand.

---

## Phase 2 — hands-off scheduling (deferred)

To run this unattended (Semaphore/Frigg) the orchestrator-can't-reboot-
itself problem must be solved:

- **Resumable checkpoint** — persist per-host progress (a file / NetBox
  field / etcd key) so a relaunch after the orchestrator's own host
  reboots continues instead of restarting.
- **Frigg special-casing** — migrate Frigg to an already-patched host and
  reboot Frigg's host last, OR drive it from a node that's never rebooted.
- **Hermod wiring** — POST gate failures to Hermod from an allowlisted
  producer (the Caddy IP-allowlist 403s the MacBook), so an unattended run
  surfaces a halt. Until then the operator watches the run.

Until phase 2 lands, this is an **operator-launched** (MacBook), watched,
fully-automated-once-launched roll.
