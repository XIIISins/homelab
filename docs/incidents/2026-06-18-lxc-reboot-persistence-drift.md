<!-- docs/incidents/2026-06-18-lxc-reboot-persistence-drift.md -->

# 2026-06-18 — LXC reboot-persistence drift (48 items, fleet-wide)

*Trigger: the day's PVE-host OS patching rebooted every LXC. The next `asgard-drift-check` reported `changed=48` across the fleet. Investigation found four latent persistence bugs that only ever manifest on a container restart — the exact failure class the CLAUDE.md "reboot-test before declaring done" rule warns about, surfaced on the first fleet-wide reboot since the roles were written.*

## Headline

48 drift items, **LXC-only** — every K3s VM, Frigg, and the three Proxmox hosts came back clean. The split was the whole diagnosis: config applied live at provision time persists fine in a VM but is reset/overwritten when an LXC's netns/veth and startup file-management re-run at container start. Four independent mechanisms:

| Drift | Hosts | Root cause | Fix |
|---|---|---|---|
| Hardened sysctls (3 net keys) | ~all LXCs | Interface bring-up re-defaults `conf.{all,default}.accept_redirects` + `conf.all.log_martians` **after** `systemd-sysctl` applied the on-disk values | Dedicated drop-in + `niflheim-sysctl-reapply.service` oneshot `After=network-online.target` (LXC-gated), re-applying only our net keys |
| `vlan10-policy-routing.service` failed | 6 (AGH + HAProxy trios) | `Type=oneshot` `ip route … via <gw>` ran before the interface was addressed (`network-online.target` too early in LXC) → status 2 → stayed failed (no `Restart=`) | `ExecStartPre` polling `ip route get <gateway>` until routable |
| `/etc/default/locale` mode/symlink | 15 | `systemd-tmpfiles` `L+` (debian.conf) force-recreates it as a symlink → `/etc/locale.conf` each boot, clobbering ansible's regular file | Manage `/etc/locale.conf` (the symlink target + canonical systemd file) |
| `/etc/resolv.conf` | 12 (9 baseline + 3 AGH) | PVE rewrites it from the container config at every start | Own DNS at the provision layer: TF `dns {}` block per container + `baseline_manage_resolv_conf: false` / drop the adguard resolv task |

## Findings

1. **The LXC-vs-VM split is the diagnosis, not a detail.** Both VMs and LXCs were rebooted; only LXCs drifted. That immediately ruled out "un-applied spec" (git showed the roles unchanged since before the 5h.3 zero-drift baseline) and pointed at container-restart re-initialisation. Always check the VM/LXC split first when drift appears fleet-wide after a reboot.
2. **`net.*` sysctls are network-namespaced — the LXC has its own, it doesn't read the host's.** Proof: ansible *successfully* sets them and they hold for weeks until a reboot. The reset is the netns being recreated at kernel defaults + interface bring-up running after `systemd-sysctl`, not the container reflecting the host value. Only keys whose container-default differs from the hardened value drift (`accept_source_route` already defaults 0 → invisible).
3. **Re-apply the dedicated drop-in, never `sysctl --system`.** In an unprivileged LXC `sysctl --system` fails on read-only `kernel.*`/`vm.*` keys → non-zero exit → the *fix* becomes a failed-service drift signal. Writing the hardened keys to `/etc/sysctl.d/60-niflheim-hardening.conf` and re-applying only that file keeps the oneshot at exit 0.
4. **`ip route show table <name>` as non-root is a red herring.** It errors `table id value is invalid` because the user can't read `/etc/iproute2/rt_tables`; the named table is valid for root. The real policy-routing failure was the boot-time gateway-unreachable race, confirmed by the unit's `status=2` at a timestamp one second after networkd started.
5. **`/etc/default/locale` showing `changed` with no content diff = a tmpfiles symlink.** `L+` deletes-then-relinks at every boot; a regular file written there always loses. Manage the target.
6. **resolv.conf belongs to PVE.** Fighting it from ansible (a per-boot re-assert oneshot was considered) is a workaround; `chattr +i` is unavailable in unprivileged LXCs. The clean fix is the provision layer — a `dns {}` block. bpg applies it **in-place** (`pct set`, 0 replacements — plan-confirmed against the documented "initialization changes force replacement" risk, which applies to `template_file_id`/`user_account`, not `dns`). It only takes effect on the next container start, so validation requires a reboot.
7. **No silent redundancy downgrade.** `baseline_nameservers` was VIP **+** UCG fallback; bare PVE host-inheritance gives only the single host nameserver. The non-AGH `dns {}` blocks carry both entries so dropping `baseline_manage_resolv_conf` doesn't quietly halve DNS redundancy. AGH keeps `127.0.0.1` + a Quad9 bootstrap fallback — valuable precisely in the all-AGH-down window this very reboot created.
8. **Separate, unrelated finding: the SFTPGo upstream apt repo broke.** `asgard-apply` ended `error` solely because `factorio`'s `apt update` failed (`ftp.osuosl.org/pub/sftpgo/apt trixie` no longer ships a signed `InRelease`) — every other host was `failed=0`. Pre-existing upstream breakage, invisible to the `--check` drift-check (which skips the cache update). Logged in [`../known-issues/sftpgo-factorio.md`](../known-issues/sftpgo-factorio.md); factorio convergence is blocked on it until SFTPGo is repinned.

## Validation

- **saga canary** (exercises all four mechanisms): applied → rebooted **twice** → check-mode drift across baseline+hardening+keepalived = `changed=0`.
- **Fleet** (`asgard-apply`): the three reboot-persistence fixes landed on every LXC except factorio (blocked by finding 8). Spot-confirmed live on hlin (HAProxy trio): policy-routing active + rule present, sysctl oneshot enabled+active, sysctls correct.
- **resolv.conf** (`terraform apply`, 12 in-place, 0 destroyed): saga reboot → PVE wrote `127.0.0.1` + `9.9.9.10`; hermod reboot → PVE wrote `10.0.10.200` + `10.0.254.1`; both resolve.

## Follow-ups

- **Repin SFTPGo** off the broken osuosl apt repo (Cloudsmith, per the Hermod/AppriseAPI pattern, or install from the GitHub release tarball) — factorio is the one host not converged.
- The reboot-persistence lens generalises: any new LXC role that sets a kernel param, a tmpfiles-managed file, or resolv.conf must be reboot-tested before "done."
