<!-- docs/incidents/2026-05-31-frigg-stage2-finish.md -->
# 2026-05-31 — Frigg Stage 2 finish: deferred roles + HA validation + DNS regression

**Phase:** 6 Stage 2 (Frigg control-node watchtower) — closeout
**Outcome:** Stage 2 fully LIVE. The three deferred roles (Tailscale-SSH, vlagent, zabbix-agent) deployed cleanly from Frigg-as-Linux-controller; HA validated end-to-end including a true crash test; one real regression (Tailscale hijacking DNS) caught and fixed before it could bite silently.

## Context

The Stage 2 core (HA VM 2900, `control-node` role, `claude remote-control` systemd service) went live earlier on 2026-05-31. Three roles were deferred because their controller-side `community.hashi_vault` lookups crash the ansible worker fork on the macOS/Python-3.14 MacBook. The handover called for running them from a Linux controller (Frigg itself), plus a non-destructive HA validation, an sftp-subsystem cleanup, and an optional env-cache.

## What was done

- **Three roles deployed from Frigg** (`ssh ghost@frigg` → `ansible-playbook playbooks/asgard-control.yml -i inventory/hosts.yml --limit frigg -c local --tags tailscale,vlagent,zabbix-agent`, env sourced from the on-box `homelab-env` shim). The macOS/3.14 hvac-fork crash does not apply on the Linux box — the lookups ran fine. End state: Tailscale online (`tag:server`, RunSSH on), vlagent shipping to `logs.niflheim`, zabbix-agent2 registered.
- **HA validation** of vm:2900 (no node kills): live-migrate Verd→Urd→Verd (zero downtime — RAM-only live migrate, disk on shared NFS), graceful `qm stop` + restore, and a true crash test.

## Findings

1. **Tailscale `accept-dns=true` hijacked the control node's DNS → DNS-loss-on-reboot.** `tailscale up` overwrote Frigg's baseline-owned `/etc/resolv.conf` (AdGuard VIP `10.0.10.200`) with MagicDNS. It worked initially, but **after the HA crash-test reboot the tailnet split-DNS path to AdGuard returned `server misbehaving`** → `vault.niflheim.xiiisins.com` unresolvable → `homelab-env` AppRole login failed → the entire IaC toolchain on the box was down. This is the worst kind of bug — invisible until a reboot, by which time the triggering context is lost. **Fix:** `tailscale_accept_dns: false` (control group_vars; new role flag `--accept-dns={{ tailscale_accept_dns | default(true) | lower }}`, default true so the tailnet LXCs that deliberately live on MagicDNS with `baseline_manage_resolv_conf=false` are unchanged). A LAN control node must keep DNS on the direct AdGuard path, independent of tailscaled health. **Live-fix without a Vault dependency** (chicken-egg — re-running `tailscale up` needs the authkey from the now-unresolvable Vault): `sudo tailscale set --accept-dns=false` flips the pref on the connected node, but does NOT restore resolv.conf, so also rewrite `/etc/resolv.conf` to the baseline content. Reboot-tested: `tailscale debug prefs` → `CorpDNS:false`, resolv.conf intact, Vault resolves, homelab-env loads. **The HA crash-test reboot is what exposed it — without that test it would have surfaced weeks later on the first unplanned reboot.**

2. **`qm stop <vmid>` on an HA resource is a graceful HA stop, NOT a crash.** The CLI prints "Requesting HA stop"; HA sets desired-state → `stopped` and will NOT auto-restart it (`ha-manager status` shows `(node, stopped)` steady). Recovery requires `ha-manager set vm:<id> --state started`. To actually test HA crash-recovery without a node kill: **`kill -9 $(cat /var/run/qemu-server/<vmid>.pid)`** on the hosting node — qemu dies while desired-state stays `started`, so the LRM auto-restarts it (validated ~6s to running, ~18s to SSH-reachable; all services incl. `claude remote-control`, tailscaled, vlagent, zabbix-agent2 auto-start on boot). Live migration of an HA VM uses `ha-manager migrate vm:<id> <node>` (not `qm migrate`).

3. **NetBox dynamic inventory on Frigg needed `python3-pytz` + `python3-pynetbox`.** Without them `nb_inventory` fails to parse (`pytz must be installed`) and Frigg silently falls back to the static `-i inventory/hosts.yml` (where `frigg` lives under group `control` — fine for a `--limit` run, and the documented DR fallback). Added to the `control-node` toolchain apt list (same pattern as `python3-hvac`; Debian 13 is PEP668 → apt, not pip). Same class as the Semaphore pod-start `pip install pytz`.

4. **A brand-new Zabbix host class needs its host group created before agent registration.** Frigg's `Asgard/Control` group didn't exist, so the `zabbix_host` register step failed `Hostgroup not found: Asgard/Control`. Fixed by `ansible-playbook playbooks/zabbix-host-groups.yml --limit frigg` (aggregates `zabbix_agent_host_groups` across the limited inventory and creates any missing group, idempotent) before re-running `--tags zabbix-agent`.

5. **sftp subsystem missing on hardened hosts (cosmetic).** The `hardening` role's `sshd_config.j2` fully replaces sshd_config and dropped the distro-default `Subsystem sftp` line → Ansible logs "sftp transfer mechanism failed, falling back to scp" on every file push. Fixed by re-stating `Subsystem sftp internal-sftp` (path-independent, Debian + RHEL). Deploys on next converge.

## Commits

- `fix(6): control-node NetBox-inventory deps + sftp subsystem on hardened hosts`
- `fix(6): tailscale --accept-dns=false on Frigg (control-node DNS regression)`
- docs: this retro + CLAUDE.md status/gotchas + open-questions + build-sequence

## What's next

Stage 2 is complete. The committed role fixes (tailscale accept-dns, inventory deps, sftp) deploy on the next converge once pushed. Optional env-cache of the homelab-env shim was deliberately skipped (avoids caching secrets to disk; 436 ms login is acceptable). Remaining Phase 6 item: the Vault `tls_disable=1` listener decision (deferred).
