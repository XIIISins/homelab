<!-- docs/known-issues/ssh-system.md -->

# Known gotchas — SSH / system

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## SSH / system

- **Hostkey files can be left zero-byte after hard crash mid-write.** ext4 journal restores inode metadata, not page-cache data. sshd serves from in-memory hostkeys until next restart → `sshd: no hostkeys available -- exiting`. Diagnostic: `ls -la /etc/ssh/ssh_host_*_key` (zero-byte, old mtime); cross-reference `find /etc /var -size 0 -newermt '<window>'`. **Recovery:** `rm` empties first (`ssh-keygen -A` skips existing), then `ssh-keygen -A`, then `systemctl start sshd`. Then `ssh-keygen -R <host>` + `-R <ip>` on control node + operator workstations. Defense pending: baseline asserts hostkey files non-empty.
- **`last reboot` showing multiple "still running" entries = hard crashes.** Only current boot can actually be running — others are boot-without-shutdown records. Useful first-pass diagnostic for "broken since some time ago." Pair with `find -newermt` to localize the crash window. wtmp rolls off via logrotate.
- **Rebuilding an LXC/VM at the same IP triggers SSH hostkey mismatch on operator workstations.** `REMOTE HOST IDENTIFICATION HAS CHANGED!` from `~/.ssh/known_hosts`. Ansible itself is unaffected (`host_key_checking = False` in `ansible.cfg`). **Fix on workstation**: `ssh-keygen -R <ip>` (+ `-R <hostname>` if AdGuard rewrite is in `known_hosts`). Add to the rebuild runbook for any LXC that's being recreated at the same IP. Surfaced 2026-05-25 5h.2 (Hermod replaced the 5aff1dd rolled-back attempt at 10.0.11.22).

