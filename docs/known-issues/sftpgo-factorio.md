<!-- docs/known-issues/sftpgo-factorio.md -->

# Known gotchas — SFTPGo / Factorio

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## SFTPGo / Factorio

- **SFTPGo sqlite `data_provider_name` must be absolute** (`/var/lib/sftpgo/sftpgo.db`). Unit's `WorkingDirectory=/etc/sftpgo` makes relative paths FHS-wrong. Create the var-lib dir with sftpgo ownership.
- **Factorio reconcile.timer must NOT auto-start in role.** Background timer races ahead of `initial-install.yml` → `factorio --create` hits `.lock` from already-running service. Pattern: timer `enabled` only in `reconcile.yml`; start explicitly at end of `initial-install.yml`.
- **Factorio install dir needs `chown -R factorio:factorio` after extract.** Factorio writes `.lock` at startup; tarball ownership doesn't match. The reconcile script does this — manual installs need it too.
- **The upstream SFTPGo apt repo (`ftp.osuosl.org/pub/sftpgo/apt`) stopped publishing a signed `InRelease` for `trixie`** — so `apt update` on the factorio LXC fails (`repository '… trixie Release' no longer has a Release file`) and aborts any play that does a cache refresh early (e.g. `asgard-apply` → factorio `failed=1, ok=2`, the rest of the fleet `failed=0`). It's an upstream repo breakage, NOT a config error, and it only surfaces on a real run (`--check` skips the cache update, so the 6-hourly drift-check stayed green). Surfaced 2026-06-18 during an `asgard-apply`. Fix path: pin SFTPGo to a Cloudsmith repo (the Hermod/AppriseAPI pattern) or drop the apt repo and install from the GitHub release tarball; until then factorio convergence is blocked on `apt update`.

