<!-- docs/services/notifications.md -->

# Notifications (Hermod, LXC 1103)

Single notification aggregation point for the homelab. Sources POST to one HTTP endpoint; Hermod fans out to Discord webhooks (and any future delivery channel) based on tag-driven routing. Designed to scale by addition — wiring a new alert source needs zero hub-side config.

Slotted as **Phase 5h.2**, immediately after Phase 7c (Zabbix LXC). Sequence rationale: until Zabbix is live, the primary infra-alert producer doesn't exist; building the delivery layer first would be untested speculation.

## What it is

- **One LXC**, `hermod` (1103, 10.0.11.22), on Verd. Norse god of messengers — Odin's emissary who rode to Hel to retrieve Baldr. (1101 = PBS, 1102 = Hugin/Zabbix per Phase 7c.)
- **AppriseAPI** — open-source notification gateway with 80+ delivery backends. **Installed natively via pip into a venv + uvicorn + systemd unit**, matching the homelab's "all LXC services are native + systemd" pattern (Postgres, Factorio, SFTPGo, Zabbix, AdGuard). Docker deliberately not used — adds an alien moving part to one LXC and Apprise's non-Docker path is well-trodden.
- **Caddy reverse proxy on the same LXC**, AppriseAPI bound to `127.0.0.1:8000`. Caddy listens on `:80` and gates inbound POSTs via a `remote_ip` allowlist matcher — non-matches → 403. JSON access logs to `/var/log/caddy/access.log` ship to VictoriaLogs via the existing vlagent role. TLS-ready path preserved for future internal-CA wiring.
- **Stateless** — config on the LXC root disk (PBS-backed), no DB, no PVC.
- **Internal-only** — exposed on VLAN 11, no Tailscale, no Cloudflare. Sources reach it within the homelab.

## Why this shape

| Choice | Reason |
|--------|--------|
| LXC, not K8s pod | Independent failure domain from asgard K3s — alerts about K3s being down need to fire even when K3s is down |
| AppriseAPI, not raw webhooks per source | One source-side schema, N delivery destinations behind it. Adding a delivery channel (ntfy, email, Slack) doesn't touch source code. |
| Tag-based routing, not severity-based | Severity drives Discord embed *color*; tag drives *which channel*. Decoupling lets `tag: media` (future Sonarr) reuse the same hub without sharing the infra alerts channel. |
| Routine notifications NOT routed via Hermod | vlagent already ships every host's syslog/journald to VictoriaLogs. Routine event = log line. No reason to double-route through Hermod just to write to VL. |
| Config on root disk, Ansible-managed | Matches every other Ansible-managed service in the homelab (AdGuard, Postgres, Factorio). PBS backs up the LXC root disk = config covered. |

## Severity taxonomy + routing

| Tag | Response expectation | Producer examples | Discord destination |
|-----|---------------------|-------------------|---------------------|
| `critical` | Look within minutes, even at 2am | Cluster quorum lost, environment down, service hard-unavailable, disk >90% | `#infra-critical`, `@everyone` mention |
| `alert` | Look within hours, business-day OK | Single-node failure (cluster degraded but operational), drift detected, drift correction failed, sustained resource load 5–15 min, disk 70–80% | `#infra-alerts`, no mention |
| `media` (future) | Whenever | Sonarr/Radarr release notifications | `#media`, no mention |
| _(no tag, but POSTed to Hermod)_ | Producer bug — should have tagged | Any source whose code POSTs without a `tag` field | **`#hermod-untagged` quarantine channel**, no mention. Creates a natural backlog of producers to fix. |
| _(routine success, no notification)_ | n/a | Routine success, drift-check-clean, scheduled reconcile-OK | **Not routed to Hermod at all — logged to VL via vlagent, queryable post-hoc** |

Severity definitions are derived from response-time expectations rather than impact descriptors. "Service unavailable at 2am" demands `critical` because *the response time differs from `alert`*, not because the word "critical" feels heavier.

The untagged-quarantine channel exists because Apprise's default behaviour on a tag-less notification is to match *every* URL whose `tag:` is unspecified — which would silently fan a stray POST to wherever the yaml has tag-less URLs. The fix is structural: every routed URL has an explicit `tag:`, and exactly one URL (the quarantine Discord webhook) has no `tag:` clause, catching the untagged stragglers in one place.

## JSON schema (source → Hermod)

```json
POST /notify/<config-key>
Content-Type: application/json

{
  "title":  "string, required — one-line headline shown in Discord embed title",
  "body":   "string, required — full message body; markdown allowed if format=markdown",
  "type":   "info | success | warning | failure",
  "tag":    "string, comma-separated — routing key (critical, alert, media, ...)",
  "format": "text | markdown | html"
}
```

Field semantics:

| Field | Drives |
|-------|--------|
| `title` | Discord embed title |
| `body` | Discord embed body |
| `type` | Discord embed **color** (info=blue, success=green, warning=yellow, failure=red). Does NOT drive routing. |
| `tag` | **Routing.** Comma-separated tag list; Hermod fans the notification to every configured URL whose tag pattern matches. |
| `format` | Body interpretation. Default `text`; use `markdown` for any embedded formatting. |

Source-side conformance (example — Ansible failure handler):

```yaml
- name: Notify on drift correction failure
  ansible.builtin.uri:
    url: http://hermod.niflheim.xiiisins.com:8000/notify/homelab
    method: POST
    body_format: json
    body:
      title: "Drift correction failed: {{ inventory_hostname }}"
      body: |
        **Playbook:** {{ ansible_play_name }}
        **Failed task:** {{ ansible_failed_task.name }}
        **Reason:** {{ ansible_failed_result.msg | default('see logs') }}
      type: failure
      tag: alert
      format: markdown
  when: hermod_notify_on_failure | default(true)
```

## Apprise yaml (Hermod-side)

Rendered by `roles/hermod-api` from a Jinja template, installed at `/etc/apprise/config/<config-key>.yml` where `<config-key>` is a long random string (acting as soft-auth in the URL — Caddy's IP allowlist is the primary gate, this is belt-and-braces). Secrets via Vault.

Vault stores each Discord webhook as a **single `url` field** containing the full webhook URL (`https://discord.com/api/webhooks/<id>/<token>`) — the form Discord copy-pastes natively. The Jinja template strips the `https://discord.com/api/webhooks/` prefix and prepends Apprise's `discord://` scheme at render time, keeping the operator workflow to one paste per webhook (no manual ID/token splitting).

**Apprise YAML schema — URL is the dict KEY, not a `url:` field.** Subtle but load-bearing — see [2026-05-25 deploy retro](../incidents/2026-05-25-hermod-deploy.md). Two valid forms:

- **Tagged routing**: URL is a dict key, options (incl. `tag`) under it as a list of single-key dicts.
- **Untagged fallback**: URL as a bare string list entry, no nested options — matches notifications with no `tag` field.

```jinja2
{%- macro discord_apprise(url) -%}
discord://{{ url | regex_replace('^https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/', '') }}
{%- endmacro -%}

# Tag-driven routing. Sources tag; Hermod dispatches.
urls:
  - {{ discord_apprise(vault_discord_critical_url) }}/?format=markdown&username=Hrist:
      - tag: critical

  - {{ discord_apprise(vault_discord_alert_url) }}/?format=markdown&username=Mist:
      - tag: alert

  - {{ discord_apprise(vault_discord_media_url) }}/?format=markdown&username=Olrun:
      - tag: media

  # Quarantine: bare URL (no nested options) catches POSTs with no `tag`
  # field. Apprise YAML semantics: a URL with no tag-options matches
  # notifications with empty/unset tag. Producers fanning to tagged URLs
  # (`critical`, `alert`, `media`) DO NOT also land here.
  - {{ discord_apprise(vault_discord_untagged_url) }}/?format=markdown&username=Hel
```

**What does NOT work** (the wrong shape — silently fails with "Ignored entry url found under urls, entry #N" + "Unsupported URL, entry #N" → "no service(s) to notify"):

```yaml
# WRONG — Apprise rejects this format.
urls:
  - url: discord://...     # ❌ url-as-sibling-field
    tag: critical          # ❌ tag-as-sibling-field
```

Webhook display names per tag — **Hrist** (critical, "the shaker" — canonical Valkyrie from Grímnismál), **Mist** (alert, "cloud" — watchful), **Ölrún** (media, "ale-rune" — feast/social), **Hel** (untagged, the underworld of lost messages). Set via Apprise `?username=` override so the Discord-side display name is consistent regardless of how the webhook itself was named in the Discord UI.

Adding a delivery channel later (e.g. ntfy for phone push on `critical`) is one yaml line — the source-side code does not change.

## Source-side severity mapping

The policy artifact. Every alert producer in the homelab maps its native severity → an Apprise tag. Committing this table makes "what should I tag this as?" a documented lookup, not a per-author judgment call.

| Producer | Native level | Apprise tag |
|----------|-------------|-------------|
| **Zabbix** | Disaster, High | `critical` |
| **Zabbix** | Average | `alert` |
| **Zabbix** | Information, Warning | _(none — only logs)_ |
| **VMAlert** (future Phase 7b) | severity: critical | `critical` |
| **VMAlert** (future Phase 7b) | severity: warning | `alert` |
| **Semaphore** (or AWX) | Task failure: apply | `critical` |
| **Semaphore** (or AWX) | Task failure: drift-check | `alert` |
| **Semaphore** (or AWX) | Task success | _(none — only logs)_ |
| **Patroni** | Leader change | `alert` (degraded; cluster still up) |
| **Patroni** | All replicas lost | `critical` |
| **Ansible** (custom playbook) | `failed_when` trigger | author picks; documented in role README |
| **Sonarr / Radarr** (future) | All releases | `media` |

Changes to this table are policy decisions worth a PR. The Apprise yaml is just the mechanical realization.

## Vault layout

```
secret/ansible/hermod/discord/critical    { url }
secret/ansible/hermod/discord/alert       { url }
secret/ansible/hermod/discord/media       { url }
secret/ansible/hermod/discord/untagged    { url }
secret/ansible/hermod/config-key          { value }
```

Path under `ansible/` because Hermod's configuration is Ansible-managed at runtime — matches the consumer-domain convention (see CLAUDE.md "Vault path convention"). Discord webhook entries minted in Discord UI, written to Vault out-of-band by the operator (no TF→Discord provider in scope, no TF resource for these paths either — operator-managed end-to-end). `config-key` is TF-minted in `terraform/vault/` via `random_password` (length 32, special=false) — gates the AppriseAPI `/notify/<key>` URL as soft-auth behind Caddy.

Each Discord path's `url` field stores the **full webhook URL** (`https://discord.com/api/webhooks/<id>/<token>`) as a single paste-friendly value — the Jinja template handles the conversion to Apprise's `discord://<id>/<token>` URL scheme at config-render time (see Apprise yaml section). Avoids the operator having to split ID + token by hand; Discord's UI exposes the URL, not the parts.

The Caddy IP-allowlist is the *primary* access gate; the config-key is *additional* depth. Producers receive the full URL `http://hermod.niflheim.xiiisins.com/notify/<config-key>` via their respective integration mechanisms (Zabbix media-type macro, Ansible group_vars, etc.).

## Access control — Caddy IP allowlist

Caddy is the only thing bound to `:80` on the Hermod LXC. AppriseAPI listens only on `127.0.0.1:8000` (uvicorn `--host 127.0.0.1`). Caddy's `remote_ip` matcher gates accepted producers; everything else gets 403 with a JSON access-log line for VL.

Caddyfile shape (rendered by `roles/caddy-reverse-proxy` — generic, reusable):

```caddy
:80 {
    @allowed remote_ip {{ caddy_allowed_cidrs | join(' ') }}

    handle @allowed {
        reverse_proxy 127.0.0.1:8000
    }

    handle {
        respond "forbidden" 403
    }

    log {
        output file /var/log/caddy/access.log {
            roll_size  10mb
            roll_keep  5
        }
        format json
    }
}
```

Starting allowlist (concrete, derived from the source→tag mapping table):

| CIDR | Producer |
|------|----------|
| `10.0.11.21/32` | Hugin (Zabbix server) |
| `10.0.11.230/32`, `10.0.11.231/32`, `10.0.11.232/32` | PG trio (Patroni callbacks) |
| `10.0.11.233/32`, `10.0.11.234/32`, `10.0.11.235/32` | HAProxy/etcd trio (future: failover hooks) |
| `10.0.21.0/24` | Asgard K3s nodes (future VMAlert, Semaphore, any K8s-side producer) |
| `10.0.20.0/24` | Asgard K3s MetalLB pool (catches LB-mode producers if any land) |
| `10.0.254.11/32`, `10.0.254.12/32`, `10.0.254.13/32` | Proxmox hosts (PBS notification hook, optional) |

Allowlist lives in `ansible/inventory/group_vars/hermod_hosts.yml` as `caddy_allowed_cidrs`. Additions are a group_vars edit + re-run.

## LXC spec

| Field | Value |
|-------|-------|
| Hostname | `hermod` |
| VMID | 1103 |
| Host | Verd |
| OS | Debian 13 |
| vCPU | 1 |
| RAM | 512 MB |
| Disk | 4 GB |
| Network | VLAN 11, `10.0.11.22/24` |
| Privileged | No |
| Nesting | Yes (Debian 13 systemd) |

Sized small — AppriseAPI is stateless Python/uvicorn, single-digit RSS. Bumps later if needed.

## Implementation outline (Phase 5h.2)

Slotted **after** Phase 7c (Zabbix LXC). Until Zabbix exists, this phase has nothing to alert about.

1. **5h.2.a** — `terraform/proxmox/asgard-lxcs/lxcs.tf` Hermod resource (Debian 13, 1 vCPU / 512 MB / 4 GB, VLAN 11 / 10.0.11.22, unprivileged + nesting). Standard LXC pattern — no `device_passthrough` / `keyctl` / `fuse`, so the main API-token module not the root-pam variant. `lifecycle.ignore_changes` for `operating_system[0].template_file_id` + `initialization[0].user_account` per the import-drift gotcha (defensive even on fresh creates). Apply from main per the `terraform apply` rule.
2. **5h.2.b** — `terraform/netbox/vms.tf` Hermod records: `netbox_virtual_machine` (vmid=1103, role=`notifications` [new role in `roles.tf` — semantically distinct from `monitoring`/Zabbix], cluster=niflheim, device=verd) + `netbox_interface` (eth0) + `netbox_ip_address` (10.0.11.22/24) + primary_ip binding. Per the TF→NetBox standing-pattern rule. Apply from main.
3. **5h.2.c** — `terraform/vault/main.tf` (additions): `random_password.hermod_config_key` (length=32, special=false) + `vault_kv_secret_v2` at `secret/ansible/hermod/config-key`. **Discord webhook paths are NOT TF-managed** — they're operator-minted (Discord UI), operator-rotated, operator-written. Keeping them out of TF avoids the create-time placeholder overwriting real values. Apply from main.
4. **5h.2.d** — `ansible/roles/caddy-reverse-proxy/` — generic role: install `caddy` apt package, render Caddyfile from `caddy_sites` list-of-dicts (each: name, listen, allowlist_cidrs, upstream, log_path), systemd-managed, log dir owned by `caddy` user, vlagent picks up `/var/log/caddy/access.log` via group_vars override. Reusable for any future LXC needing a thin reverse proxy.
5. **5h.2.e** — `ansible/roles/hermod-api/` — installs AppriseAPI:
   - `apt install python3-venv python3-pip` (idempotent)
   - `python3 -m venv /opt/apprise-api`
   - `pip install apprise-api[all]==<pin>` (concrete-pin per IaC policy; capture latest stable at role-write time)
   - Render `/etc/apprise/config/{{ hermod_config_key }}.yml` from Jinja template; Discord webhook `url` field per tag via `community.hashi_vault.vault_kv2_get` (template strips the `https://discord.com/api/webhooks/` prefix + prepends `discord://`); config-key via the same Vault lookup
   - systemd unit `apprise-api.service` invoking `/opt/apprise-api/bin/uvicorn AppriseAPI.asgi:application --host 127.0.0.1 --port 8000`
   - `APPRISE_CONFIG_DIR=/etc/apprise/config` env var so the API auto-loads on startup
   - Config render task notifies a `reload apprise-api` handler (`systemctl reload apprise-api` — uvicorn handles SIGHUP for config reload; if not, fall back to restart)
6. **5h.2.f** — `ansible/playbooks/asgard-hermod.yml` — baseline + caddy-reverse-proxy + hermod-api + hardening. Add `hermod_hosts` group to `ansible/inventory/hosts.yml` with `hermod` host entry; group_vars in `group_vars/hermod_hosts.yml` for `caddy_allowed_cidrs` + `caddy_sites` config. vlagent log-input override for `/var/log/caddy/access.log` (JSON-structured field hints).
7. **5h.2.g** — Operator: Discord UI channel creation (`#infra-critical`, `#infra-alerts`, `#media`, `#hermod-untagged`); mint four webhooks (named Hrist / Mist / Ölrún / Hel respectively, though display names are also enforced via Apprise `?username=` override — the webhook name is informational); `vault kv put secret/ansible/hermod/discord/<tag> url=https://discord.com/api/webhooks/...` for each of `critical,alert,media,untagged`. 1P "Homelab" recovery copy per webhook (`Hermod - Discord webhook - <tag>`, fields = `url`). Then re-run `asgard-hermod.yml` to re-render config now that Vault is populated.
8. **5h.2.h** — `terraform/adguard/rewrites.tf` entry for `hermod.niflheim.xiiisins.com → 10.0.11.22`. Apply from main.
9. **5h.2.i** — Zabbix media-type integration in `ansible/roles/zabbix-server/tasks/hermod-mediatype.yml` (or a separate task file):
   - `community.zabbix.zabbix_mediatype` declaratively registers a Webhook media-type (JavaScript) that maps Zabbix trigger severity → Apprise tag (`Disaster`/`High` → `critical`, `Average` → `alert`, lower severities → suppressed) and POSTs JSON `{title, body, type, tag}` to `http://hermod.../notify/<config-key>`
   - `community.zabbix.zabbix_action` binding the trigger-recovery actions to the new media-type for the Local-Admin user
   - Reuses the httpapi-connection + Vault-token pattern from 7c.8 host registration
10. **5h.2.j** — Source-side wiring for non-Zabbix producers (separate PRs as the producers exist):
    - Patroni `on_role_change` callback shell script (sidecar to patroni unit on PG hosts) — leader changes → `alert`, all-replicas-lost → `critical`
    - Ansible failure handlers in `roles/<role>/handlers/main.yml` where role exit-state matters — folded into Semaphore drift-check (Phase 5h.3)
    - PBS notification hook → `critical` on backup failure (optional, Proxmox host integration)
11. **5h.2.k** — Smoketest matrix:
    - Positive: 3 tags × 2 representative types each (critical/failure, critical/warning, alert/warning, alert/success, media/info, media/success) = 6 curls from an allowlisted source; verify Discord embed colour matches `type` (info=blue, success=green, warning=yellow, failure=red), mention behaviour (`@everyone` on critical only), VL access-log JSON line per request
    - Negative — untagged: 1 POST with no `tag` field → lands in `#hermod-untagged`, no fanout to the three real channels
    - Negative — disallowed source: 1 POST from a non-allowlisted IP (e.g. tailnet) → 403, no Discord traffic, VL line with `status=403`
    - End-to-end Zabbix: temporarily lower a trigger severity to High on a no-op item (e.g. `agent.ping` on a host you're about to reboot), trigger fires, Discord receives `critical`, recovery clears

## Future expansion

- **ntfy phone push** for `critical` only — add one yaml line.
- **Email digest** of `alert`-tagged notifications via Mailgun/SMTP — useful if Discord-only proves insufficient.
- **VMAlert integration** (Phase 7b) — wire VMAlert's `notifier.config` at an Apprise destination URL.
- **AWX/Semaphore notification template** — single template referenced by every job, body templated from Ansible facts.

## What stays out of scope

- **HA Hermod.** Single LXC. If Hermod is down, alerts fail to deliver — but they still land in VL via vlagent (the access path also writes to syslog when sources POST and fail). Acceptable for a homelab; revisit only if Discord becomes the sole channel and downtime is felt.
- **Inbound message filtering / deduplication.** Apprise is a fanout, not a state machine. Storm prevention is the producer's responsibility (Zabbix trigger dependencies, VMAlert `for:` clauses).
- **Direct Hermod queries.** Hermod does not aggregate or replay. Audit trail = VL.

## Decisions locked (2026-05-25)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| AppriseAPI install path | **pip into venv + uvicorn + systemd** (Option A) | Matches every other LXC role's "native + systemd" pattern. Docker rejected — alien moving part for one LXC, and Apprise's non-Docker path is well-trodden. Detailed install steps in 5h.2.e above. |
| Access control | **Caddy reverse proxy on same LXC with `remote_ip` allowlist matcher** (Option A) | Producers are a small concrete set (Zabbix, PG, K3s, etc.) — explicit allowlist audits well in git. Caddy gives JSON-structured access logs to VL for free + preserves a trivial TLS path for later. Generic `caddy-reverse-proxy` role is reusable. |
| Untagged-notification policy | **Quarantine channel `#hermod-untagged`** via Apprise yaml URL with `tag:` omitted | Producer bugs surface in a dedicated Discord channel rather than silent fanout or `@everyone` pings. Natural backlog of producers to fix. Four Discord webhooks total (critical/alert/media/untagged). |
| Zabbix media-type management | **`community.zabbix.zabbix_mediatype` + `zabbix_action` declarative** | Reuses the httpapi-connection pattern from 7c.8 host registration. No click-ops in Zabbix UI. Lives in `roles/zabbix-server/`. |
| Config-key as soft-auth | **TF-minted via `random_password` length 32**, stored at `secret/ansible/hermod/config-key` | Belt-and-braces behind the Caddy IP allowlist. Producers receive the full `/notify/<key>` URL via Vault lookups, not literal HCL. |

## Items still deferred to role-write time

- Concrete `apprise-api` pip version pin — capture latest stable at role-write time (5h.2.e).
- Concrete Caddy version pin (apt `caddy` package version, or pin to a release) — capture at role-write time (5h.2.d).
- Critical-tag avatar URL — pick a static-served image at smoketest time (Caddy apex-static pod can serve from `/icons/` if desired; otherwise omit).
- `@here` vs `@everyone` for `critical` — confirm at smoketest time. `@everyone` pings users while offline (correct for true-2am-pageable alerts); `@here` only currently-online users. Decision can be deferred safely — it's a one-character Apprise URL parameter (`&mentions=@everyone` vs `&mentions=@here`) to flip later.
