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

## What didn't get done (still pending)

- **7c.3** — Authentik SAML provider via Terraform (`terraform/authentik/zabbix.tf`).
- **7c.4** — Role's `saml.yml` task + SAML `$SSO[]` block in `zabbix.conf.php.j2`.
- **7c.5** — Traefik fronting for `hugin.midgard.xiiisins.com` + `hugin.xiiisins.com` (AGH rewrites for midgard + apex deferred; only `hugin.*` + `hugin-direct.*` niflheim rewrites landed — the latter post-rename from `zabbix-direct.*`).
- **7c.6** — SAML cutover + 3-path validation (LAN/WAN/backdoor).
- **7c.7** — Zabbix API bootstrap (admin password auto-rotate, host groups, auto-registration action).
- **7c.8** — `zabbix-agent` role cluster-wide rollout (the next operator step).

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
