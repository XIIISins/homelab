<!-- docs/services/factorio.md -->

# Factorio LXC architecture (deployed 2026-05-16)

LXC 1120 hosts the Factorio headless server *and* SFTPGo on the same host. The operator (a trusted friend) manages the server via SFTP only — no shell access, no web UI, no SSH. Everything happens through JSON control files in `/factorio/control/` edited via SFTPGo's virtual filesystem.

**Filesystem layout** (under `/factorio/`):

| Path | Owner | Operator access | Purpose |
|------|-------|-----------------|---------|
| `README.txt` | `root:factorio` | read | Operator-facing guide |
| `mods/` | `sftpgo:factorio` | read/write | Mod `.zip`s, manually managed |
| `saves/` | `sftpgo:factorio` | read/write | Save files |
| `scenarios/` | `sftpgo:factorio` | read/write | Custom scenarios |
| `script-output/` | `factorio:factorio` | read-only | Factorio writes here |
| `control/factorio-control.json` | `sftpgo:factorio` | read/write | `{version, state, restart}` |
| `control/restart-now` | `sftpgo:factorio` | write (touch) | Triggers instant restart |
| `status/factorio-status.json` | `root:factorio` | read-only | Reconciler-written state |
| `logs/reconcile.log` | `factorio:factorio` | read-only | Rotating reconcile log |

RCON password lives outside `/factorio/` at `/var/lib/factorio-secrets/rconpw` (root:factorio 0750 dir, factorio:factorio 0640 file) so it's not in the operator's SFTP home tree at all.

**Reconcile loop.** `/usr/local/bin/factorio-reconcile` is a Python script (stdlib only) run by a systemd timer every 30s as root. It:
- Reads `/factorio/control/factorio-control.json` to determine desired version + state
- Resolves "stable"/"experimental" via the Factorio API (cached)
- Installs missing versions to `/opt/factorio-<version>/` (atomic symlink swap)
- Verifies SHA256 against `factorio.com/download/sha256sums/`
- Reconciles `factorio.service` running state to match control file
- Honors the one-shot `restart-now` trigger (path unit fires the handler)
- Writes `/factorio/status/factorio-status.json` after each tick
- Chowns its writes back to `factorio:factorio` so SFTPGo can serve them

**Cross-service file sharing.** The `sftpgo` system user is added to the `factorio` group via `sftpgo_extra_groups: [factorio]` in group_vars. `/factorio/*` dirs are setgid (2775) with UMask=0002, so files created by either service are mode 0664 with group=factorio. Both can read/write.

**Operator auth.** Username `operator`, password (in vault). SFTPGo's built-in defender provides brute-force protection (15 failures / 30 min ban). TOTP available as opt-in; pubkey auth deferred. Operator's SFTPGo virtual permissions are scoped per-directory — they can write to `mods/`/`saves/` but `status/` and `logs/` are read-only, and `config/` is denied entirely.

**Internet exposure.** UCG-Ultra port forwards:
- UDP 34197 → 10.0.11.220:34197 (Factorio game protocol)
- TCP 22022 → 10.0.11.220:22022 (SFTPGo SFTP — mnemonic: 22-0-22)

DNS: `factorio.xiiisins.com` (Cloudflare A → WAN IP) and `factorio.niflheim.xiiisins.com` (AdGuard → 10.0.11.220).

**LXC features.** Unprivileged, `nesting=true` (required for systemd 257 on Debian 13). 4 vCPU / 8 GB RAM / 8 GB disk on Urd. No nested containerization — `nesting` flag is for systemd's namespace ops, not Docker.
