<!-- docs/known-issues/caddy.md -->

# Known gotchas — Caddy reverse-proxy role

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## Caddy reverse-proxy role

- **`/var/log/caddy/` ownership must be recursive — pre-existing root-owned files inside break startup.** Caddy runs as the `caddy` user from the apt package; if a stale root-owned file lives in the log dir (e.g. from a transient `caddy validate` run-as-root, or any prior manual invocation), Caddy fails at startup with `opening log writer ... open /var/log/caddy/<name>.log: permission denied`. Ansible's `file: state=directory` chowns the directory but NOT files inside. **Fix in the role**: `recurse: true` on the log-dir task. Generic class — applies to any role chowning a log dir as a non-root daemon user. Surfaced 2026-05-25 5h.2.
- **Cloudsmith stable repo rolls forward + drops older versions.** Pin chosen at role-write time (2.10.0 in the initial Hermod 5h.2 design) was already gone from the candidate list by deploy time (current was 2.11.3). Apt task `name: "caddy={{ ver }}*"` with trailing wildcard does NOT reliably constrain (observed: first run installed 2.11.3 anyway; second run with same role wanted "2.10.0" → "Packages were downgraded and -y was used without --allow-downgrades"). **Fix**: pin without wildcard (`name: "caddy={{ ver }}"`, exact match) + `allow_downgrade: true` on the apt task for pin-resilience. When bumping pins for any rolling apt repo, verify the target version is still in the candidate list first (`apt-cache policy <pkg>`). Same class applies to Debian backports + any third-party CI mirror.

