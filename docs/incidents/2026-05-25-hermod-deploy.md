<!-- docs/incidents/2026-05-25-hermod-deploy.md -->

# 2026-05-25 — Phase 5h.2 Hermod notifications hub deploy

LXC 1103 provisioned, AppriseAPI + Caddy roles applied, end-to-end smoketest passed (4 positive POSTs with confirmed Discord delivery across Hrist/Mist/Ölrún/Hel channels + 1 negative POST returning HTTP 403). Closes 5h.2.a-h + 5h.2.k. 5h.2.i (Zabbix media-type integration) + 5h.2.j (other producer wiring — Patroni callbacks, PBS hook) still pending.

This is the **second** Hermod deploy attempt. The first (5aff1dd) chose Docker-in-LXC and was rolled back ~11 minutes later (a493781) for breaking the all-native-systemd LXC pattern. Current shape explicitly avoids that footgun (pip+venv+gunicorn+systemd; native; no Docker engine).

## Sequence

1. **Pre-flight (turn 1)**: doc-search of `docs/services/notifications.md` + open-questions + decisions-log; surfaced the prior rolled-back attempt from git history; locked four architectural decisions (install path = pip+systemd, access control = Caddy IP allowlist, untagged = quarantine channel, Zabbix media-type = declarative). Committed as a32985d + f3b4750.
2. **Worktree A (turn 2)**: TF across four modules. Applied from main per the apply-only-from-main rule. +10 resources total (vault 2, asgard-lxcs 2, netbox 5, adguard 1). Commit 05e1631.
3. **Worktree B (turn 3)**: Ansible — caddy-reverse-proxy role (b075ba7) + hermod-api role + playbook + inventory wiring (d87876c). Tested + iterated from the worktree. Six findings surfaced during iteration (see below); final play is fully idempotent (changed=0 on no-op re-runs).
4. **Smoketest matrix**: 4 positives (one per tag × representative type) from Verd (10.0.254.12, allowlisted) → HTTP 200, Discord delivery confirmed by operator. 1 negative (workstation 10.0.60.10, NOT allowlisted) → HTTP 403, body not proxied. AppriseAPI journal logs confirm `Delivered Notification(s) - Tags: critical/alert/media` + 1 untagged → quarantine.

## Findings (rule-shaped)

### 1. Apprise YAML schema — URL is the dict KEY, not a `url:` field

This burned an iteration of the playbook + a confused journald dive before the actual error message surfaced. Apprise YAML's load-bearing rule: URLs are **dict keys** in the `urls:` list, with options nested underneath as a list of single-key dicts. URL-as-sibling-with-fields silently fails with `Ignored entry url found under urls, entry #N` → `Unsupported URL, entry #N` → `Failed to load Apprise configuration from memory://` → `There are no service(s) to notify`. AppriseAPI then returns HTTP 424 to the producer.

**Wrong** (every URL gets dropped, every notification fails):

```yaml
urls:
  - url: discord://id/token/?format=markdown
    tag: critical
```

**Right** (tagged):

```yaml
urls:
  - discord://id/token/?format=markdown:
      - tag: critical
```

**Right** (untagged fallback):

```yaml
urls:
  - discord://id/token/?format=markdown
```

The diagnostic that surfaced the cause was `journalctl -u apprise-api` — the warnings are explicit once visible. CLAUDE.md "Apprise / AppriseAPI" section gets a new entry; the design doc (`docs/services/notifications.md`) was also updated to match.

### 2. Caddy fails to start when /var/log/caddy contains pre-existing root-owned files

Caddy runs as the `caddy` user (apt package default). A stale root-owned, 0600, empty `hermod.log` file in `/var/log/caddy/` (origin unclear — possibly a transient `caddy validate` run-as-root from the template's validate clause, possibly something else) made Caddy fail at startup with:

```
loading initial config: setting up custom log 'log0': opening log writer using
&logging.FileWriter{...}: open /var/log/caddy/hermod.log: permission denied
```

The role's `file: state=directory` chowns the **directory** but not the **files inside**. Fix: `recurse: true` on the log-dir task. Once added, the role is idempotent against this class of pre-existing-wrong-ownership.

Generic class — applies to any role that chowns a log dir as a non-root daemon user but doesn't recurse. Worth a CLAUDE.md "Ansible / roles" entry.

### 3. Caddy version pin + Cloudsmith stable repo's roll-forward behaviour

Initial role default was `caddy_version: "2.10.0"` (captured from public docs at design time). Apt task used `name: "caddy={{ caddy_version }}*"` with a trailing wildcard.

By the time we deployed, Cloudsmith stable had rolled forward to **2.11.3** + dropped 2.10.0 entirely from the candidate list. Behaviour observed:

- First role run: apt installed 2.11.3 anyway (the wildcard appears not to constrain when there's no exact-prefix match — or the upgrade-all-packages task in baseline pulled it in unconstrained earlier in the play, hard to tell from logs).
- Second run: apt saw 2.11.3 installed + `caddy=2.10.0` requested → `Packages were downgraded and -y was used without --allow-downgrades` → role failed.

**Fix:** pin without the wildcard (`name: "caddy={{ caddy_version }}"`, exact match) + `allow_downgrade: true` on the apt task for pin-resilience (lets a future downgrade pin do its job without manual intervention). Bumped the default to `2.11.3`.

**Class lesson**: Cloudsmith stable rolls forward + drops older versions periodically. When bumping pins for Cloudsmith-hosted packages, verify the target version is still in the candidate list first (`apt-cache policy <pkg>`). Same applies to any rolling apt repo (Debian backports, third-party CI mirrors).

### 4. Config-key leaks via /notify/<key> URI in Caddy + gunicorn access logs

The AppriseAPI config-key is intended as "soft-auth depth" behind the primary Caddy IP allowlist — a producer with allowlisted source IP additionally needs to know the key to POST. The key is stored in Vault at `secret/ansible/hermod/config-key` + read into the Apprise yaml filename at role-render time.

**The leak**: both Caddy's JSON access log + gunicorn's access log echo the full request URI verbatim, including `/notify/<config-key>`. Caddy ships its access log to VictoriaLogs via vlagent (via `caddy-hermod` service tag); gunicorn's access log goes to journald → vlagent → VL. So **the config-key value is queryable in VL** for any operator with VL access.

Practical impact for the homelab: VL is gated by Authentik (any user with VL access is already inside the trusted boundary), so the additional defence is moot. But the "soft-auth depth" framing is misleading — the design doc should reflect that the IP allowlist is the **only** real gate. Documenting; not fixing for now. Mitigations exist (Caddy log redaction filter via a `format` directive transform, gunicorn `--access-logformat` override stripping the path) but they're non-trivial + the threat model doesn't justify the complexity.

If config-key starts mattering more — e.g. if Hermod ever gets fronted publicly via Cloudflared, or VL access scope changes — revisit with proper auth (HTTP Basic via Caddy `basicauth` matcher, or HMAC-signed payloads).

### 5. vlagent role's input schema is `{path, service}`, not `{glob, extra_fields}`

When wiring vlagent log inputs in `group_vars/hermod_hosts.yml`, I guessed the schema from the role README without reading `defaults/main.yml`. Wrong shape (`glob:`/`extra_fields:`) — playbook errored with `object of type 'dict' has no attribute 'path'` inside the systemd unit template. Real schema:

```yaml
vlagent_log_inputs:
  - path: /var/log/syslog
    service: syslog
```

The role template iterates: `--fileCollector.glob='{{ input.path }}' --fileCollector.extraFields='{"host":"{{ vlagent_host_label }}","service":"{{ input.service }}"}'`. So `glob` is the *internal* vlagent CLI flag name; `path` is the *role-input* field name. Predictable now that I've seen it; not predictable from reading the README alone.

Lesson: when consuming an existing role's input schema, read its `defaults/main.yml` first, not just its README — the README often documents intent, the defaults document the actual data shape.

### 6. NetBox transient OperationalError on apply state-refresh

First `terraform apply` against `terraform/netbox/` (from main, after the worktree merge) errored mid-refresh:

```
Error: [GET /dcim/sites/{id}/][500] dcim_sites_read default
  {"error":"the connection is closed","exception":"OperationalError",
   "netbox_version":"4.6.1-Docker-5.0.1","python_version":"3.14.4"}
```

Django's PG connection dropped mid-request — single GET against `/api/dcim/sites/{id}/`. Retry immediately succeeded (5 resources added). No further investigation done; likely a Patroni connection-pool blip or a Valkey hiccup. TF state-refresh hits many sequential GETs and any single one can blip.

If this recurs, candidate mitigation: `provider "netbox" { request_timeout = N }` (currently default) or a Django settings override increasing connection retries. Not actionable on a one-off. Worth a CLAUDE.md NetBox gotcha entry naming the class.

### 7. Webhook URL leak event — operator-side rotation triggered

During the design conversation, three Discord webhook URLs (Hrist/Mist/Ölrún) were pasted full-form into the chat. URLs persist in the transcript on disk (`~/.claude/projects/...`) + in scrollback; Discord webhook URLs are unauthenticated, so anyone with the URL can POST until rotation. Flagged immediately; operator rotated each via Discord Server Settings → Integrations → Webhooks → Reset Token + wrote the new URLs into Vault.

**Rule confirmed**: even in design conversation, never paste credential URLs in chat. Discord's UI exposes the **assembled** URL (not separate ID/token), so the only safe operator workflow is to write the URL straight into Vault from the operator's shell — `vault kv put secret/ansible/hermod/discord/<tag> url=<URL>` — and confirm to me with "Vault populated" (paths only, no values). This was documented as a feedback memory earlier; the leak event reinforced it.

### 8. Stale SSH hostkey from the rolled-back 5aff1dd attempt

After the new Hermod LXC came up at 10.0.11.22, `ssh ansible@10.0.11.22` failed with `REMOTE HOST IDENTIFICATION HAS CHANGED` — known_hosts had a stale entry from the previous (rolled-back) Hermod LXC at the same IP. `ssh-keygen -R 10.0.11.22` cleared it. Ansible itself was unaffected (`host_key_checking = False` in ansible.cfg).

Generic: rolling-back-and-redeploying any LXC at the same IP triggers this on the operator's workstation. CLAUDE.md "SSH / system" section could mention this as a recovery aside; deferred — it's a one-line `ssh-keygen -R` fix.

### 9. NetBox API token leak — operator-side rotation triggered (separately, during this deploy)

Mid-deploy, while planning the TF modules, I ran `grep -nE 'NETBOX|TF_VAR' ~/.cache/homelab/env.sh` to verify env var presence — which printed the matching line verbatim, exposing `NETBOX_API_TOKEN`. Same class as the Caddy webhook leak earlier. Operator rotated via Django shell on the netbox pod + `homelab-env --refresh`.

**Rule reinforced**: never grep secret-bearing files in a way that prints matching values. Use `grep -c` for presence (count-only), `wc -c` for length, `awk '{print length}'` for length-without-line-content. The shell / tooling section in CLAUDE.md already documents this class as a fish-line-miss gotcha; this incident extends it: even when targeting bash-style `VAR=value` lines, the redaction technique that's safe is "don't echo the line at all."

## What landed in IaC vs operator-side

| Artefact | Owner | Where |
|---|---|---|
| LXC 1103 spec | TF | `terraform/proxmox/asgard-lxcs/lxcs.tf` |
| NetBox VM record + `notifications` role | TF | `terraform/netbox/{roles,vms}.tf` |
| Vault config-key (32-char random_password) | TF | `terraform/vault/main.tf` |
| AGH rewrite `hermod.niflheim.xiiisins.com` | TF | `terraform/adguard/rewrites.tf` |
| Discord channels (`#infra-critical`, `#infra-alerts`, `#media`, `#hermod-untagged`) | operator | Discord UI |
| Discord webhooks (Hrist/Mist/Ölrún/Hel; URLs in Vault) | operator | Discord UI → `vault kv put secret/ansible/hermod/discord/<tag> url=...` |
| Caddy reverse-proxy role | Ansible | `ansible/roles/caddy-reverse-proxy/` (generic, reusable) |
| Hermod-api role | Ansible | `ansible/roles/hermod-api/` (pip+venv+gunicorn+systemd) |
| Playbook + inventory wiring | Ansible | `ansible/playbooks/asgard-hermod.yml` + `inventory/hosts.yml` + `inventory/group_vars/hermod_hosts.yml` |

## Still pending

- **5h.2.i** — Zabbix media-type integration via `community.zabbix.zabbix_mediatype` + `zabbix_action`. JavaScript webhook script mapping trigger severity → Apprise tag; reuses the httpapi-connection pattern from 7c.8 host registration.
- **5h.2.j** — Patroni `on_role_change` callback + PBS notification hook + any other non-Zabbix producers, separate PRs as the producers exist.
- **Phase 5h.3** — Ansible orchestration (Semaphore + drift-check). Drift-check loop is the first concrete `alert`-tag producer; sequenced after 5h.2 closes.
