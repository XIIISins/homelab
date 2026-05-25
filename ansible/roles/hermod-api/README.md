<!-- ansible/roles/hermod-api/README.md -->

# hermod-api

Installs + configures [AppriseAPI](https://github.com/caronc/apprise-api) on the Hermod LXC (1103) as the homelab's notification fan-out hub. Sources POST to one HTTP endpoint; the role routes to Discord channels by tag. Full design in [`docs/services/notifications.md`](../../../docs/services/notifications.md).

## What this role does

1. Installs Docker engine + compose plugin from Docker's official Debian repo.
2. Creates the `apprise` system user (uid 1500) + directory layout.
3. Renders a docker-compose stack (single `caronc/apprise` container, bound to `:8000`).
4. Renders the Apprise config at `/etc/apprise/{{ hermod_api_config_key }}` — one URL per tag, Discord webhook id/token pulled from Vault inline.
5. Installs a systemd unit (`apprise.service`) that wraps `docker compose up -d`.

## Variables

See [`defaults/main.yml`](defaults/main.yml). The interesting ones:

- `hermod_api_image` — pinned tag, no `:latest` floats.
- `hermod_api_config_key` (default `homelab`) — URL path component (`POST /notify/<key>`).
- `hermod_api_discord_webhooks` — list of `{ tag, vault_path, mention }`. Default covers `critical` / `alert` / `media` per the design.

## Secrets

| Vault path | Fields | Notes |
|------------|--------|-------|
| `ansible/hermod/discord/critical` | `id`, `token` | Discord webhook for `#infra-critical`. `@everyone` mention configured at the Apprise URL level. |
| `ansible/hermod/discord/alert` | `id`, `token` | Discord webhook for `#infra-alerts`. No mention. |
| `ansible/hermod/discord/media` | `id`, `token` | Discord webhook for `#media` (future Sonarr/Radarr). No mention. |

**Operator bootstrap (one-time):**

1. In Discord, create three channels: `#infra-critical`, `#infra-alerts`, `#media`.
2. Per channel: Channel Settings → Integrations → Webhooks → New Webhook → copy URL.
3. Webhook URL format: `https://discord.com/api/webhooks/<id>/<token>`. Extract id + token segments.
4. Stash in Vault:
   ```fish
   vault kv put secret/ansible/hermod/discord/critical id=<id> token=<token>
   vault kv put secret/ansible/hermod/discord/alert    id=<id> token=<token>
   vault kv put secret/ansible/hermod/discord/media    id=<id> token=<token>
   ```
5. Re-run the asgard-hermod.yml playbook to re-render config with the real values.

The TF-managed Vault resources at `terraform/vault/main.tf` carry placeholders + `lifecycle.ignore_changes` on `data_json`, so `vault kv put` doesn't get clobbered.

## Operational notes

- **AppriseAPI listens on `:8000`** — internal-only (VLAN 11). Sources POST to `http://hermod.niflheim.xiiisins.com:8000/notify/{{ hermod_api_config_key }}`. AGH rewrite resolves the hostname.
- **No HA.** Single LXC; if Hermod is down, alerts fail to deliver, but vlagent on every source host still ships logs to VictoriaLogs as a fallback audit trail. Acceptable for homelab scale.
- **Docker logs go to journald** via the `journald` log driver in compose. vlagent's syslog input on Hermod picks them up automatically (no extra wiring).
- **Sending a test notification:**
  ```fish
  curl -X POST http://hermod.niflheim.xiiisins.com:8000/notify/homelab \
    -H 'Content-Type: application/json' \
    -d '{"title":"test","body":"hello","type":"info","tag":"alert"}'
  ```

## See also

- [`docs/services/notifications.md`](../../../docs/services/notifications.md) — full design (tag taxonomy, JSON schema, source-side mapping)
- Phase 5h.2 in [`docs/operations/build-sequence.md`](../../../docs/operations/build-sequence.md)
