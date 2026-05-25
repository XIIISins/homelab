<!-- docs/services/notifications.md -->

# Notifications (Hermod, LXC 1103)

Single notification aggregation point for the homelab. Sources POST to one HTTP endpoint; Hermod fans out to Discord webhooks (and any future delivery channel) based on tag-driven routing. Designed to scale by addition — wiring a new alert source needs zero hub-side config.

Slotted as **Phase 5h.2**, immediately after the Zabbix LXC (Phase 5h). Sequence rationale: until Zabbix is live, the primary infra-alert producer doesn't exist; building the delivery layer first would be untested speculation.

## What it is

- **One LXC**, `hermod` (1103, 10.0.11.22), on Verd. Norse god of messengers — Odin's emissary who rode to Hel to retrieve Baldr. (1101 = PBS, 1102 reserved for Zabbix per Phase 5h.)
- **AppriseAPI** (`caronc/apprise-api`) — open-source notification gateway with 80+ delivery backends.
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
| _(no tag)_ | n/a | Routine success, drift-check-clean, scheduled reconcile-OK | **Not routed to Hermod — logged to VL via vlagent, queryable post-hoc** |

Severity definitions are derived from response-time expectations rather than impact descriptors. "Service unavailable at 2am" demands `critical` because *the response time differs from `alert`*, not because the word "critical" feels heavier.

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

Rendered by `roles/hermod` from a Jinja template, installed at `/etc/apprise/config/homelab.yml`. Secrets via Vault.

```yaml
# Tag-driven routing. Sources tag; Hermod dispatches.
urls:
  - url: discord://{{ vault_discord_critical_id }}/{{ vault_discord_critical_token }}/?format=markdown&username=CRITICAL&avatar_url={{ critical_avatar_url }}
    tag: critical

  - url: discord://{{ vault_discord_alert_id }}/{{ vault_discord_alert_token }}/?format=markdown
    tag: alert

  - url: discord://{{ vault_discord_media_id }}/{{ vault_discord_media_token }}/?format=markdown
    tag: media
```

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
secret/ansible/hermod/discord/critical    { id, token }
secret/ansible/hermod/discord/alert       { id, token }
secret/ansible/hermod/discord/media       { id, token }
```

Path under `ansible/` because Hermod's configuration is Ansible-managed at runtime — matches the consumer-domain convention (see CLAUDE.md "Vault path convention"). Created via `terraform/vault/` standing pattern; secrets minted in Discord UI, written to Vault out-of-band by the operator (no TF→Discord provider in scope).

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

Slotted **after** Phase 5h (Zabbix LXC). Until Zabbix exists, this phase has nothing to alert about.

1. **5h.2.a** — `terraform/proxmox/asgard-lxcs/` + `terraform/netbox/` LXC declaration (Hermod 1102 + interface + IP). Apply.
2. **5h.2.b** — `ansible/roles/hermod-api/` — installs AppriseAPI (Docker via Compose, or pip — TBD at implementation), renders `/etc/apprise/config/homelab.yml` from Vault, systemd unit, syslog→vlagent (already deployed by Phase 7a).
3. **5h.2.c** — `ansible/playbooks/asgard-hermod.yml` — baseline + hermod-api + hardening.
4. **5h.2.d** — Discord channels (`#infra-critical`, `#infra-alerts`, `#media`) created in Discord UI; webhooks minted; URLs stored in Vault per layout above.
5. **5h.2.e** — Source-side wiring (per producer, separate PRs):
   - Zabbix media type → Hermod HTTP POST (Phase 5h follow-up — same window as Zabbix server config).
   - Ansible failure handlers in `roles/<role>/handlers/main.yml` where role exit-state matters (drift contexts, not every role).
   - Patroni → Hermod via a `on_role_change` callback shell script (sidecar to the patroni unit on PG hosts).
6. **5h.2.f** — Smoketest: synthetic alerts at each severity tag; verify Discord embed colour, mention behaviour, VL log line for the access log.
7. **5h.2.g** — AdGuard rewrite for `hermod.niflheim.xiiisins.com` → `10.0.11.22` (via `terraform/adguard/rewrites.tf` per the standing pattern).

## Future expansion

- **ntfy phone push** for `critical` only — add one yaml line.
- **Email digest** of `alert`-tagged notifications via Mailgun/SMTP — useful if Discord-only proves insufficient.
- **VMAlert integration** (Phase 7b) — wire VMAlert's `notifier.config` at an Apprise destination URL.
- **AWX/Semaphore notification template** — single template referenced by every job, body templated from Ansible facts.

## What stays out of scope

- **HA Hermod.** Single LXC. If Hermod is down, alerts fail to deliver — but they still land in VL via vlagent (the access path also writes to syslog when sources POST and fail). Acceptable for a homelab; revisit only if Discord becomes the sole channel and downtime is felt.
- **Inbound message filtering / deduplication.** Apprise is a fanout, not a state machine. Storm prevention is the producer's responsibility (Zabbix trigger dependencies, VMAlert `for:` clauses).
- **Direct Hermod queries.** Hermod does not aggregate or replay. Audit trail = VL.

## Open items at implementation time

- AppriseAPI install path (Docker via `community.docker` Ansible collection, or pip + systemd) — pick at role-write time based on Apprise's then-current docs.
- Critical-tag avatar URL — pick a static-served image (Caddy apex-static pod can serve from `/icons/` if desired; otherwise omit).
- Confirm whether to mention `@here` vs `@everyone` for `critical` (probably `@here` — `@everyone` pings users while offline; `@here` only currently-online users — but for true-2am-pageable alerts `@everyone` may be the right thing).
