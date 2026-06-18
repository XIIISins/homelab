<!-- docs/known-issues/sftpgo-factorio.md -->

# Known gotchas — SFTPGo / Factorio

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## SFTPGo / Factorio

- **SFTPGo sqlite `data_provider_name` must be absolute** (`/var/lib/sftpgo/sftpgo.db`). Unit's `WorkingDirectory=/etc/sftpgo` makes relative paths FHS-wrong. Create the var-lib dir with sftpgo ownership.
- **Factorio reconcile.timer must NOT auto-start in role.** Background timer races ahead of `initial-install.yml` → `factorio --create` hits `.lock` from already-running service. Pattern: timer `enabled` only in `reconcile.yml`; start explicitly at end of `initial-install.yml`.
- **Factorio install dir needs `chown -R factorio:factorio` after extract.** Factorio writes `.lock` at startup; tarball ownership doesn't match. The reconcile script does this — manual installs need it too.
- **SFTPGo is installed from the pinned upstream GitHub release `.deb`, NOT an apt repo.** The OSU mirror (`ftp.osuosl.org/pub/sftpgo/apt`) stopped publishing a signed `InRelease` for `trixie`, so its `sources.list.d/sftpgo.list` entry broke `apt update` (`repository '… trixie Release' no longer has a Release file`) and aborted any play doing an early cache refresh — `asgard-apply` ended `error` with factorio `failed=1, ok=2` while the rest of the fleet was clean. It only surfaced on a *real* run (`--check` skips the cache update, so the 6-hourly drift-check stayed green). **Resolved 2026-06-18** (surfaced + fixed same day): the `sftpgo` role now removes the dead apt source + keyring and installs `sftpgo_{{ version }}-{{ rev }}_{{ arch }}.deb` from `github.com/drakkan/sftpgo/releases` via `get_url` (sha256-verified, cached in `/var/cache`) + `apt: deb=`. **Version bump = update `sftpgo_version` AND `sftpgo_deb_sha256` together** (digest: `gh api repos/drakkan/sftpgo/releases/tags/v<ver> --jq '.assets[]|select(.name|endswith("amd64.deb"))|.digest'`). Migration gotcha: on a host that still has the dead `sftpgo.list`, `apt update` fails *before* the role can remove it (baseline runs first), so unstick it once with `ansible-playbook asgard-factorio.yml --limit factorio --tags sftpgo:repo,sftpgo:install` (those tags don't touch baseline or the Vault-backed config, so no deadlock + no Vault needed). Fresh builds never see the dead source.

