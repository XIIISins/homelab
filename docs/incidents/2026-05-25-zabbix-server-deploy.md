<!-- docs/incidents/2026-05-25-zabbix-server-deploy.md -->

# 2026-05-25 — Phase 7c.2 Zabbix server LXC deploy (Hugin)

LXC 1102 provisioned, role applied, Zabbix 7.0 frontend reachable, local agent connected. Closes 7c.1 (Vault secrets) + 7c.2 (LXC + role deploy). 7c.3–7c.8 (SAML, Traefik fronting, agent rollout, etc.) still pending.

## Sequence

1. Vault secrets minted earlier in 2d7dc51 (7c.1) — four paths under `secret/ansible/{postgres,zabbix}/*`.
2. Role finalization landed in 22f3a18 (centralized Vault lookup, pipefail on schema bootstrap, parameterized frontend name) + naming/inventory in bdfccac + 2f27224 + TF host-identity rename `zabbix → hugin` in 0070a0a.
3. Operator ran the deploy sequence: `terraform apply` against `terraform/adguard/`, `terraform/proxmox/asgard-lxcs/`, `terraform/netbox/`; then `ansible-playbook postgres-host.yml` (provisioned the `zabbix` DB+role on Patroni leader); then `zabbix-host.yml --tags baseline -e 'ansible_user=root'` then full play.
4. Hit three blockers in sequence (each fixed in-flight, see Findings); after all fixes Zabbix server + frontend + local agent2 all running.

## Findings (rule-shaped)

### 1. Zabbix apt-repo URL had a bogus `/release/` segment

The role's `zabbix_repo_deb_url` was constructing `https://repo.zabbix.com/zabbix/7.0/release/debian/pool/main/z/zabbix-release/zabbix-release_latest_7.0+debian13_all.deb`. Real path is `https://repo.zabbix.com/zabbix/7.0/debian/pool/main/z/zabbix-release/…` — no `release/` segment. The wrong path 404s; the right one returns 200 and the dir lists both debian12 + debian13 variants of `zabbix-release_latest_7.0+debian<N>_all.deb`. Source of the wrong path is unclear — possibly a confusion with the upstream documentation's "Stable releases" section header. Fixed in 7ed0a19.

### 2. Schema-bootstrap failed silently under `no_log: true`

First-deploy schema load (zcat `/usr/share/zabbix-sql-scripts/postgresql/server.sql.gz` | psql against the Patroni VIP) errored out. `no_log: true` on the task (correctly — PGPASSWORD would leak under `-vvv`) blanked the failure reason from the playbook output. Couldn't determine root cause from the ansible side.

Diagnostic that bypassed `no_log` entirely: stream the SQL file via ssh + gunzip + local psql, all from the operator workstation:

```fish
set -lx PGPASSWORD (vault kv get -field=value secret/ansible/postgres/zabbix-password)
ssh ansible@hugin "cat /usr/share/zabbix-sql-scripts/postgresql/server.sql.gz" | gunzip | \
  psql -v ON_ERROR_STOP=1 \
       "host=10.0.10.210 port=5432 dbname=zabbix user=zabbix sslmode=require" \
       2>&1 | tail -50
```

The manual stream succeeded fully (47 INSERT batches + final COMMIT) — same SQL file, same credentials, same target. Whatever broke the role-driven attempt was transient. Schema landed via the manual stream, role's `users`-table idempotency probe correctly skipped the re-load on next play.

Lesson is the diagnostic pattern, not a code fix — `no_log` stays. The pattern is reusable for ANY ansible task with `no_log: true` that fails opaquely against a remote DB: stream the input file from the target host through the operator's local client.

### 3. Default admin password is the Zabbix factory default — NOT auto-applied from Vault

The role's `defaults/main.yml` had a contradictory comment claiming "The role rotates this immediately to the Vault-stored value." The README (correctly) says it doesn't — initial Zabbix admin is `Admin` / `zabbix` (the factory default seeded by `server.sql.gz`); the Vault path is where the operator stashes the POST-rotation password after changing via the UI. API-driven rotation is the 7c.7 plan; not deployed in 7c.2.

Operator initially tried `Admin` / `<Vault value>` from `vault kv get … secret/ansible/zabbix/admin-password`, failed → triggered the 5-attempt lockout. Confusion was reasonable given the wrong comment.

**Comment fixed in f55d1f4.** Now matches README + documents the lockout-recovery SQL inline.

### 4. Zabbix 5-failed-attempt lockout → SQL unlock pattern

Zabbix locks the `Admin` user (and any other) after 5 failed login attempts. The lock is stored in the `users` table; clear via direct SQL from a workstation psql session against the Patroni VIP:

```sql
UPDATE users SET attempt_failed=0, attempt_clock=0, attempt_ip=''
WHERE username='Admin';
```

Pattern is generic (any locked user, same UPDATE), not Zabbix-version-specific — applies to 6.0/7.0 LTS as of writing.

### 5. Default "Zabbix server" host needs renaming to match the agent's `Hostname=`

`server.sql.gz` creates a default monitored host named literally `Zabbix server` with `Agent interface: 127.0.0.1:10050`. The `zabbix-agent` role configures the local agent2 with `Hostname={{ inventory_hostname }}` (i.e. `hugin`). Zabbix matches incoming agent data by Hostname → mismatch → agent shows as "not connecting" / "no data" in the UI.

Fix: rename the schema-default host. Data collection → Hosts → click `Zabbix server` → set *Host name* to match the agent (e.g. `hugin`) → Update. Auto-registration action (7c.7 plan) would supersede this — until then, manual rename on first deploy is the operator step.

### 6. Debian's nginx default site shadows the Zabbix server block on Host-header mismatch

`zabbix-nginx-conf` package wires our server block via `/etc/nginx/conf.d/zabbix.conf` → `/etc/zabbix/nginx.conf`, but does NOT touch `/etc/nginx/sites-enabled/default`. Debian's default site listens on `*:80 default_server`. Result: any Host header that doesn't match one of `zabbix_web_server_names` falls through to the default → "Welcome to nginx" page instead of Zabbix.

Operator hit this trying `hugin.niflheim.xiiisins.com` (intentionally NOT in the role's `zabbix_web_server_names` — host-identity FQDN is for SSH only, per the design intent "internal serves Zabbix via the direct-backdoor FQDN only"); fell through to welcome page. Worked around by hitting `zabbix-direct.niflheim.xiiisins.com` (which WAS in `zabbix_web_server_names` at deploy time — post-deploy rename to `hugin-direct.niflheim.xiiisins.com` landed for identity-theme consistency across all three zones).

**Fix landed in f55d1f4:** `tasks/web-config.yml` now does `state=absent` on `/etc/nginx/sites-enabled/default` + notifies `restart nginx`. Idempotent; `/etc/nginx/sites-available/default` left intact so re-enabling is a one-symlink revert. Re-apply via `ansible-playbook zabbix-host.yml --tags=zabbix:web-config` (validated in this session).

### 7. Naming convention: Hugin pairs with Munin

Zabbix LXC's Norse identity = **Hugin** (Odin's raven of Thought — flies daily across the world and returns with what he's seen). Pairs naturally with **Munin** (Memory, the existing Synology NAS): one observes, one remembers. Function tag stays `zabbix` (matches AGH→`adguard`, PG→`postgres` convention; identity is in the resource name, function is in tags). Naming convention table in CLAUDE.md updated.

---

# Phase 7c.7 + 7c.8 — cluster-wide agent rollout + monitoring depth

Continued same-day after 7c.2 close. Closes 7c.7 (Zabbix API bootstrap, recast as declarative-per-host rather than the originally-planned auto-registration action) + 7c.8 (cluster-wide agent rollout) + Round 2 monitoring-depth work (PG + HAProxy + Etcd + Proxmox VE + Nginx + PHP-FPM templates with per-service config).

## Sequence

1. **OS-aware agent role refactor.** Initial rollout failed on 6 K3s VMs because the role assumed apt across all hosts (CLAUDE.md was clear that K3s nodes are RHEL 9, missed in role write). Split `tasks/main.yml` into OS-routed import_tasks: `install-debian.yml` (apt), `install-rhel.yml` (dnf + `ansible.posix.seboolean: zabbix_can_network`), `configure.yml` (common config + start). Pattern mirrors the vlagent role's os_family split (2afe86b).
2. **Inventory cleanup.** `proxmox_hosts` + `backup_hosts` (pbs) are root-SSH-only — no `ansible` user provisioned. Codified `ansible_user: root` overrides in their group_vars rather than per-playbook `-e ansible_user=root` (869c258).
3. **PBS apt repo fix.** PBS shipped with `pbs-enterprise.sources` (deb822 format, NOT .list) pointing at the subscription-gated enterprise repo. Every apt update 401'd; subsequent playbook steps failed cascadingly. New one-off playbook `pbs-repo-fix.yml` removes the enterprise file; pbs-no-subscription is already in `/etc/apt/sources.list` from the installer (bb4bb50, 86f5846 — second iteration after first attempt added a redundant `.list` file).
4. **Declarative host registration.** Pivoted away from the original 7c.7 plan (server-side auto-registration action) to declarative-per-host: each agent's playbook run uses `community.zabbix.zabbix_host` to ensure a host record exists with the right Agent interface + groups + linked templates. Inventory is the source of truth; no Zabbix UI configuration of auto-reg required (ec9e1e1 + four iteration fixes 404bea6 / 4dac316 / f373258 / 118181a).
5. **Identity rename** `zabbix-*` FQDNs → `hugin-*` across all three zones (b75643e). Operator-driven mid-flight.
6. **Hierarchical host groups.** New `zabbix-host-groups.yml` playbook aggregates `zabbix_agent_host_groups` across all inventory hosts and pre-creates each named group via `community.zabbix.zabbix_group`. Hierarchy: `Asgard/K3s/CP`, `Asgard/LXCs/Postgres`, `Hypervisors/Proxmox`, etc. (318141c + c20a60e to move API connection vars to `group_vars/all/` for pre-task access).
7. **Round 2 monitoring depth.** Five new templates wired with per-service config:
   - **PostgreSQL** — `zbx_monitor` PG role with `pg_monitor` predefined-role membership minted on Patroni leader, agent2 PG plugin Debian package installed, `{$PG.PASSWORD}` macro injected per host (fe3155d + 5f67bc7 Vault mint).
   - **HAProxy + Etcd** — `HAProxy by Zabbix agent` (agent2 web.page.get fetching localhost:9000) + `Etcd by HTTP` (server-side scrape of each node's :2379/metrics) (d1670a4).
   - **Proxmox VE** — new TF module `terraform/proxmox/zabbix-access/` mints `zabbix-monitor@pve` user + API token with PVEAuditor role, writes token to Vault (112ae9b + e992e58 fix for `value` attribute returning the full prefixed string).
   - **Nginx + PHP-FPM** on hugin — stub_status + status/ping endpoints exposed via `allow 127.0.0.1; deny all;` in nginx.conf, FPM pool config sets `pm.status_path` + `ping.path` (8eba6f4).
8. **Verification.** 3478/3521 (98.8%) items OK across all Round 2 hosts. 43 unsupported items are upstream/cosmetic (PG 17 split `pg_stat_bgwriter`, PVE template-VM data shape edge cases, unused Zabbix-server-internal items).

## Findings (rule-shaped)

### 1. `community.zabbix` 4.x requires the httpapi connection plugin, NOT env vars

1.x/2.x used `ZABBIX_API_URL` + `ZABBIX_API_TOKEN` env vars. 4.x dropped that. Trying the old pattern errors with `Module failed: socket_path must be a value` (httpapi connection wasn't initialized → no socket). Right shape: `delegate_to: <inventory-host-representing-zabbix-server>` with task-scoped vars setting `ansible_connection: httpapi`, `ansible_network_os: community.zabbix.zabbix`, and `ansible_zabbix_auth_key: <token>`.

### 2. `delegate_to` inherits play-level `become: true` → sudo prompt on the workstation

Play-level `become: true` (which most ansible playbooks have) propagates to tasks that `delegate_to: localhost`. Result: ansible tries sudo on the operator's workstation. Workstation user has no passwordless sudo; the task hangs/fails with `sudo: a password is required`. **Fix:** explicit `become: false` on delegated tasks. The control-node-side API call has no need for root anyway.

`no_log: true` on the same tasks (correctly there for token-bearing tasks) hides the sudo prompt from playbook output — symptom presents as "every host fails with zero diagnostic info." **Debug recipe:** `ANSIBLE_NO_LOG=False ansible-playbook ... --limit <one-host>` to see the real error.

### 3. Zabbix API doesn't handle parallel host.create / hostgroup.create cleanly

Default ansible forks=5 means up to 5 concurrent API calls per task across hosts. Zabbix's host.create + hostgroup.create APIs race somewhere internally; ~1/5 succeed, the rest fail with the underlying error masked by `no_log: true`. **Fix:** `throttle: 1` on the offending tasks. Per-host total cost is ~1s; 23-host fleet serializes in under a minute. Not a real cost.

### 4. Zabbix host.create requires Super admin, NOT just Admin

Operator initially created the `ansible` Zabbix user with the "Admin role" assuming it was sufficient for global access. It's not — Admin grants admin-level permissions only WITHIN host groups the user has been explicitly granted access to (via user-group → host-group mappings). With `usrgrps: []` and Admin role, the user has zero host visibility. **Fix:** assign "Super admin role" for system-automation users that need to create/update arbitrary hosts.

### 5. `community.zabbix.zabbix_group`, not `zabbix_hostgroup`

The module that creates Zabbix host groups is named `zabbix_group` despite the parameter being `host_groups`. ansible-doc disambiguates; the bare guess from the Zabbix term gets you "couldn't resolve module/action."

### 6. `set_fact` with `run_once + delegate_to: localhost` scopes the fact to localhost only

Target hosts don't see the fact. Subsequent tasks that interpolate `{{ <fact> }}` from target hostvars get "is undefined." **Fix:** inline lookups in the task's `vars:` block instead. Lookups always run on the controller regardless of delegation, so there's no scoping issue + no per-host fact-pre-population needed. 22 Vault lookups against a local Vault cost nothing.

### 7. Pre-tasks need group_vars/all/, not role defaults, for cross-role-loaded values

The host-groups bootstrap playbook runs BEFORE the zabbix-agent role is loaded, so role-default vars aren't in hostvars at pre-task time. `'zabbix_agent_api_delegate' is undefined` errors. **Fix:** API connection vars (delegate host, port, ssl, user, token vault path) live in `group_vars/all/zabbix.yml`; the role still declares the same keys as documentation + standalone-use fallback (group_vars/all wins per Ansible's variable precedence).

### 8. agent2 plugin packages ship separately on Debian

`zabbix-agent2` base package doesn't include the PG/Redis/MySQL/etc. plugin binaries. `zabbix-agent2-plugin-postgresql` is a separate Debian package; without it, PG items return `Unknown metric pgsql.<…>` in unsupported state. Same is likely true for Redis/Memcached/MySQL/Oracle plugins. Override `zabbix_agent_packages` per group_vars (e.g. PG hosts add the plugin package). RHEL bundles plugins differently (different package shape).

### 9. `community.postgresql.postgresql_user` has NO `groups` arg

Despite the parameter name suggesting it. To grant predefined-role membership (e.g. `pg_monitor` for the Zabbix monitoring user), use a separate `community.postgresql.postgresql_membership` task. `subelements('groups', skip_missing=True)` lets the membership task iterate (user, group) pairs while users that don't declare `groups` pass through unchanged.

### 10. `include_tasks` doesn't propagate `--tags` to inner tasks

Already documented in CLAUDE.md (this session reinforced it). When using `--tags=<some-tag>` to scope a re-run, the include statement gets the tag + runs, loading the inner file — but the inner tasks don't inherit the tag and silently no-op. **Fix:** `apply: tags:` on the include:

```yaml
- ansible.builtin.include_tasks:
    file: users.yml
    apply:
      tags: [postgres-common-users]
  when: ...
  tags: [postgres-common-users]
```

Applied to postgres-common/tasks/main.yml during the zbx_monitor provisioning.

### 11. `bpg/proxmox_virtual_environment_user_token.value` returns the FULL `USER@REALM!TOKENNAME=UUID` string

Designed for direct use as `Authorization: PVEAPIToken=<value>`. Passing the full string into the Zabbix `{$PVE.TOKEN.SECRET}` macro made every scrape send `Authorization: PVEAPIToken=USER@REALM!TOKENNAME=USER@REALM!TOKENNAME=UUID` → 401 on every item. **Fix:** `element(reverse(split("=", value)), 0)` extracts just the UUID (safe because the token_id portion contains no `=` and the UUID is hex+dashes only).

### 12. Shell-variable interpolation through `ansible -a` echoes the value in adhoc output

`PW=$(vault kv get ...); ansible host -a "cmd --secret \"$PW\""` looks safe (inline fetch into local shell var) but ansible's adhoc module ECHOES THE COMMAND back in its output with the substituted value, leaking the secret to the transcript. Verified twice this session — `zbx_monitor` PG password leaked via `zabbix_agent2 -t pgsql.ping[...,"$PW",...]`. Rotated via TF `-replace=random_password.<resource>`. **Rule:** avoid local shell variable interpolation entirely when the value is sensitive and the wrapper might echo. Use a remote-side Vault lookup inside an `ansible.builtin.uri` task or similar where `no_log: true` actually suppresses the value.

### 13. PG 17 split `pg_stat_bgwriter` into bgwriter + checkpointer views

Zabbix `PostgreSQL by Zabbix agent 2` template targets the older single-view shape; on PG 17 the `pgsql.bgwriter` master item returns `Unknown metric` from the plugin. Upstream issue. 6 items unsupported per PG cluster (master + dependents × 3 hosts). Cosmetic; the rest of the template still works.

## Architecture decisions made

- **Declarative host registration over server-side auto-registration action.** Originally 7c.7 planned a server-side `host.create` auto-registration action triggered by agent HostMetadata. We went the other direction: each agent's playbook run does `community.zabbix.zabbix_host: state=present` against the API, with host_groups + link_templates + macros sourced from inventory group_vars. Inventory becomes the source of truth for "what hosts should be monitored + how." Hosts disappear from inventory by re-running with `zabbix_agent_register_host: false` (or future `state: absent` extension). No Zabbix UI configuration needed.
- **Hierarchical host groups via `/` separator.** `Asgard/K3s/CP`, `Asgard/LXCs/Postgres`, `Hypervisors/Proxmox`, etc. Zabbix UI renders the hierarchy as an expandable tree. Sets up cleanly for Jotunheim K3s later (`Jotunheim/K3s/*` alongside Asgard).
- **Host-level macros for per-host config** (e.g. `{$PG.PASSWORD}`, `{$PVE.URL.HOST}`, `{$ETCD.HOST}`). The role's new `zabbix_agent_host_macros` list-of-dicts supports `type: secret` for sensitive values + inline Vault lookups. Per-group declarations in group_vars.
- **Round 1 → Round 2 split for monitoring depth.** Round 1 attached only stock templates that work without per-host config (Linux baseline, Zabbix server health). Round 2 added per-service depth (PG, HAProxy, Etcd, PVE, Nginx, PHP-FPM) — each bundled with the agent-side / target-side enablement (PG monitoring user, HAProxy stats endpoint already enabled, etcd metrics endpoint, PVE token, nginx stub_status + FPM status endpoints). Avoided "template attached but unsupported because macro missing" intermediate states.
- **TF module `terraform/proxmox/zabbix-access/` for PVE user/token** rather than polluting either `proxmox/asgard-lxcs-root/` or `vault/`. New tiny module = dedicated S3 state key + clear scope. Uses root@pam auth (PVE user management requires Administrator-level perms; API tokens minted via UI don't carry that).
- **PVE API token's secret is `element(reverse(split("=", value)), 0)`**, not the raw `value`. Documented inline in `terraform/proxmox/zabbix-access/main.tf` because the gotcha is non-obvious and tooling-specific to bpg/proxmox.

## What's still pending after 7c.8

- **7c.3 + 7c.4 + 7c.5 + 7c.6** — SAML SSO via Authentik. Local-Admin login still the only path. Internal `hugin-direct.niflheim.xiiisins.com` is the canonical access URL; Traefik fronting at `hugin.midgard.*` + `hugin.xiiisins.com` deferred.
- **PG 17 bgwriter template gap** (upstream issue, not actionable here).
- **AdGuard / Tailscale / PBS / Factorio** — no Zabbix templates ship for these. Linux baseline is the only monitoring depth they get. Could write custom templates later if specific metrics matter.
- **Phase 5h.2 — Hermod notifications.** Zabbix can produce alerts but has no routing target until Hermod (AppriseAPI on LXC 1103) lands. Until then, alerts fire to UI only.

## Related commits (7c.8 chapter)

- ee08fd4 — agent role: drop `/release/` from repo URL
- 2afe86b — agent role: OS-aware install (Debian apt + RHEL 9 dnf)
- 869c258 — inventory: `ansible_user: root` for proxmox + backup_hosts
- bb4bb50, 86f5846 — pbs-repo-fix playbook (two iterations)
- ec9e1e1 — declarative host registration (initial)
- 404bea6 — `become: false` on delegated tasks
- 4dac316 — httpapi connection pattern (community.zabbix 4.x)
- f373258 — inline Vault lookup (drop run_once-on-localhost)
- 118181a — `throttle: 1` on host.create (parallel-forks race)
- b75643e — `zabbix-*` → `hugin-*` FQDN rename
- 318141c — declarative hierarchical host groups
- c20a60e — API vars to group_vars/all/

### Round 2 monitoring depth

- 5f67bc7 — Vault mint: zbx_monitor PG password
- fe3155d — PG monitoring (template + plugin package + user + macros)
- d1670a4 — HAProxy + Etcd templates + macros
- 112ae9b — TF `terraform/proxmox/zabbix-access/` + group_vars for PVE
- e992e58 — fix: strip token_id prefix from PVE secret
- 8eba6f4 — Nginx + PHP-FPM (stub_status + FPM status/ping endpoints)

## Related commits

- 2d7dc51 — Vault secrets (7c.1)
- 978b544 — TF lifecycle `ignore_changes` (7c.2 prep)
- 4000c96 — LXC 1102 Skuld→Urd + 2G→4G
- 741975d — role pre-deploy fixes (Vault field, PHP pin, multi-Host nginx)
- 22f3a18 — role finalization (Vault lookup DRY + pipefail + frontend name)
- bdfccac — defaults match theme (Hugin)
- 2f27224 — inventory rename (zabbix_hosts → hugin)
- 0070a0a — TF host-identity rename across modules
- 7ed0a19 — repo URL `/release/` segment fix
- f55d1f4 — nginx default site + admin password comment fix
