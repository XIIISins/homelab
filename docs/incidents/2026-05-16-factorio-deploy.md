<!-- docs/incidents/2026-05-16-factorio-deploy.md -->

### 2026-05-16 — Factorio LXC end-to-end deploy

Initial deploy of LXC 1120 (Factorio + SFTPGo). Surfaced seven bugs in the freshly-built `factorio`/`sftpgo` roles plus general Debian-LXC issues, all fixed in this session.

**Bugs and fixes:**

1. **Factorio SHA256 lookup target string was wrong.** Script looked for `factorio_headless_x64_<v>.tar.xz`; actual filename is `factorio-headless_linux_<v>.tar.xz`. Reverse-engineered from a stale reference. Fix: corrected the target string in `fetch_sha256()`.

2. **Factorio install dir not chowned after extraction.** Factorio writes `.lock` in its install dir at startup; runtime user needs write access. Fix: `os.walk` + `os.chown` over `/opt/factorio-<v>/` after rename.

3. **Reconcile timer auto-started in role.** `state: started` on the timer caused background reconcile to race ahead of `initial-install.yml`, installing Factorio and starting the service before the role could generate the default save. Fix: timer is only `enabled` in `reconcile.yml`; `state: started` moved to end of `initial-install.yml`.

4. **Initial install race.** With control file defaulting to `state=running`, the synchronous reconcile call started `factorio.service` immediately after install, then `factorio --create` failed on the lock. Fix: `initial-install.yml` detects first-run via stat on `/opt/factorio`, writes `state=stopped`, runs sync reconcile, generates save, flips to `state=running`, then starts the service.

5. **Multiple "directory does not exist" failures.** Minimal Proxmox Debian 13 template lacks dirs the roles assumed:
   - `/etc/sudoers.d/` (no `sudo` package) → baseline now installs `sudo`
   - Ansible's `become_user` temp path (no `acl`) → baseline installs `acl`
   - `/etc/systemd/system/sftpgo.service.d/` → sftpgo role creates explicitly
   - `/var/lib/sftpgo/` → sftpgo role creates explicitly

6. **SFTPGo sqlite path was CWD-relative.** `SFTPGO_DATA_PROVIDER__NAME=sftpgo.db` resolves against the package unit's `WorkingDirectory=/etc/sftpgo`. Worked accidentally; would have broken on any override that changed WorkingDirectory. Fix: absolute path `/var/lib/sftpgo/sftpgo.db`.

7. **`ansible.builtin.user` creates locked accounts.** Default `password` value in Ansible is `!`, which PAM treats as "account locked" even for pubkey auth (since `UsePAM yes` invokes `account` stack). Symptom: SSH key offered, accepted, then rejected with "User ansible not allowed because account is locked" in auth.log. Fix: `password: '*'` (valid form, no usable password) on both ansible and recovery user tasks in baseline.

**Discovered along the way:**
- bpg/proxmox: only `nesting` can be changed via API token; other features (`keyctl`, `fuse`) require `root@pam` → destroy/recreate when changing.
- `-u root` CLI flag does *not* beat `ansible_user` in group_vars. Must use `-e ansible_user=root` for the bootstrap override.
- `group_vars/` is auto-discovered only adjacent to inventory or playbook dirs. `ansible/group_vars/` was being ignored; moved to `ansible/inventory/group_vars/`.

**Resolution:** All bugs fixed in role source, then verified with a clean destroy + apply from Terraform. End-to-end: Terraform → Ansible → operator SFTPs in from outside the LAN via tethered mobile, lists `/factorio/` contents successfully.

**Root-cause pattern:** Roles authored against assumed-richer base images (RHEL or fuller Debian). Proxmox's `debian-13-standard` template is minimal and surfaces every implicit "this dir exists" / "this binary is installed" assumption. Lesson: roles targeting minimal LXC templates need explicit `file: state: directory` and `package: state: present` tasks for every non-FHS-core path/binary they touch.

