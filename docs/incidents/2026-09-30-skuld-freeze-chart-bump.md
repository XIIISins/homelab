<!-- docs/incidents/2026-09-30-skuld-freeze-chart-bump.md -->

# 2026-09-30 — Skuld hard freeze during chart-bump rollout

**Impact:** Skuld (PVE host, carrying `sigrun` CP + `einherjar-skuld` worker) froze ~03:46; control plane at 2/3 etcd, Vault at 2/3 Raft for the duration. Helm upgrades from the same-day chart bump (`1d0e0f1`) timed out and looked like bump failures.

## Findings

1. **Not the bump.** `csi-driver-nfs` (and `victoria-logs-collector`, `immich`) timed out because DaemonSet/Deployment pods sat `Terminating` on the two dead Skuld nodes. Skuld recovered after a manual power-cycle; `csi-nfs-node` rolled to 6/6 on its own.
2. **HelmReleases stayed `Stalled`** (`RetriesExceeded`) after recovery → needed `flux reconcile --reset`.
3. **Immich: rollback poisoned by DB migration.** 0.13.2 migrated the DB; the timed-out upgrade was rolled back to the 0.13.1 image, which crash-looped on the newer schema. Rolled forward via `--reset`.
4. **Root cause of the freeze: unknown** — full system freeze, no kernel panic/log. Recurring (pods restarted ~8 days earlier).
5. **HA fencing ran on `softdog`** fleet-wide — cannot fire on a kernel freeze. Hardware `iTCO_wdt` is available but blacklisted by Proxmox.

## Changes

- `proxmox-host` role: `proxmox_host_watchdog_module` (`WATCHDOG_MODULE` in `/etc/default/pve-ha-manager`); `host_vars/skuld.yml` sets `iTCO_wdt`. Applied + rebooted via `proxmox-host-patching.yml --limit skuld`; verified `watchdog0` = `iTCO_wdt`, `state=active`, cluster 6/6 Ready, all HelmReleases Ready.
- Gotchas: [`lxc-proxmox.md`](../known-issues/lxc-proxmox.md) (PVE HA watchdog), [`flux-helm-kustomize.md`](../known-issues/flux-helm-kustomize.md) (`--reset`, rollback vs DB migration).

## Follow-ups

- Promote the watchdog setting to Urd/Verd once a real Skuld freeze is auto-recovered (see open-questions).
- Optional fallback: smart plug with BIOS power-on-after-AC-loss.
