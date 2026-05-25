<!-- ansible/roles/hermod-api/README.md -->

# hermod-api

Installs [caronc/apprise-api](https://github.com/caronc/apprise-api)
natively on a Debian LXC. AppriseAPI is the homelab's notification
fanout hub — producers POST one JSON payload, Hermod routes to per-tag
Discord webhooks (and any future delivery channel).

## What it does

1. Apt-installs build deps (`python3-venv`, `python3-dev`,
   `build-essential`, `libffi-dev`, `libssl-dev`, `git`).
2. Creates `apprise` system user + group.
3. `git clone`s caronc/apprise-api at a pinned tag to
   `/opt/apprise-api/src`.
4. Creates Python venv at `/opt/apprise-api/venv`, installs upstream
   `requirements.txt` + `gunicorn`.
5. Resolves config-key + four Discord webhook URLs from Vault
   (`secret/ansible/hermod/config-key` + `.../discord/{critical,alert,
   media,untagged}`).
6. Renders Apprise routing yaml to
   `/etc/apprise/config/<config-key>.yml`. Filename = config-key so
   AppriseAPI's `/notify/<key>` URL is gated by both the Caddy IP
   allowlist AND knowing the key (belt-and-braces).
7. Renders gunicorn config + systemd unit.
8. Enables + starts `apprise-api.service`. Binds `127.0.0.1:8000`.

The public listener is Caddy on `:80/eth0`, not AppriseAPI directly —
see the `caddy-reverse-proxy` role + `caddy_sites` group_var.

## Vault dependencies

| Path | Field | Owner | Purpose |
|------|-------|-------|---------|
| `secret/ansible/hermod/config-key` | `value` | TF (`random_password`) | Soft-auth in `/notify/<key>` URL |
| `secret/ansible/hermod/discord/critical` | `url` | operator (Discord UI → `vault kv put`) | `tag: critical` routing |
| `secret/ansible/hermod/discord/alert` | `url` | operator | `tag: alert` routing |
| `secret/ansible/hermod/discord/media` | `url` | operator | `tag: media` routing |
| `secret/ansible/hermod/discord/untagged` | `url` | operator | Quarantine for tag-less POSTs |

Operator must populate the four Discord paths **before** first role
run, or the lookup tasks fail with KeyError. Smoketest:
`vault kv get -field=url secret/ansible/hermod/discord/critical | wc -c`
should be > 100 (full webhook URL length).

## Per-tag Discord display names

The Apprise URL `?username=` param overrides the Discord-side webhook
name per-message. Defaults are the Valkyrie naming theme:

| Tag | Display name | Meaning |
|-----|--------------|---------|
| `critical` | **Hrist** | "the shaker" — wake-everyone-now alerts |
| `alert` | **Mist** | "cloud" — watchful, gathering trouble |
| `media` | **Olrun** | "ale-rune" — feast/social |
| `untagged` | **Hel** | underworld of lost messages — quarantine |

Override via the `hermod_api_usernames` dict in defaults.

## Tags

| Tag | Scope |
|-----|-------|
| `hermod-api` | All tasks |
| `hermod-api:deps` | apt build deps only |
| `hermod-api:user` | apprise system user + group |
| `hermod-api:venv` | venv + pip install (slow — only run when bumping version) |
| `hermod-api:config` | Apprise yaml + gunicorn config render (Vault lookups) |
| `hermod-api:service` | systemd unit + start/enable |

For most re-runs after Vault changes: `--tags hermod-api:config` is
sufficient (skips the slow venv tasks).

## See also

- [Notifications design](../../../docs/services/notifications.md) —
  Vault schema, JSON schema, source→tag mapping, smoketest matrix
- [`caddy-reverse-proxy`](../caddy-reverse-proxy/README.md) — sibling
  role that fronts AppriseAPI with the IP allowlist
