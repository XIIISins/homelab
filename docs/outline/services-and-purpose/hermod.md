<!-- docs/outline/services-and-purpose/hermod.md -->

# Hermod

The notification hub — the single place every alert-producing system sends to, which then fans those alerts out to the right Discord channel. Named for Hermóðr, the messenger of the gods. When Postgres fails over or Zabbix trips a trigger, Hermod is how the operator finds out.

---

## Where it runs

Hermod runs in **LXC 1103 on Verd** (`10.0.11.22`). It's built from two pieces:

- **AppriseAPI** (installed natively — pip + venv + gunicorn + systemd, no Docker) does the actual notification dispatch. It's bound to localhost only.
- **Caddy** sits in front as a reverse proxy on port 80, gating inbound requests.

---

## How producers reach it

Producers POST to `/notify/<config-key>`. Two layers guard that endpoint:

- **Caddy's `remote_ip` allowlist** is the real access gate — only known homelab sources can POST at all.
- The config-key in the path is a soft second factor (it ends up in access logs, so it's not a real secret — the IP allowlist is what matters).

---

## The tag taxonomy

A notification carries a `tag`, and the tag decides which Discord channel it lands in. Each channel has a Valkyrie name:

| Tag | Channel | For |
|---|---|---|
| `critical` | **Hrist** | Things that need attention now — quorum loss, a service down. |
| `alert` | **Mist** | Things worth knowing — drift detected, a warning-level trigger. |
| `media` | **Ölrún** | Media-stack notifications. |
| *(untagged)* | **Hel** | Quarantine for anything that arrives without a recognised tag — so nothing silently vanishes. |

---

## Who sends to it

- **Zabbix** — via a webhook media-type wired to its standard "report problems" action, so triggers deliver end-to-end without bespoke per-alert configuration.
- **Patroni** — via an `on_role_change` callback on every Postgres node, so a failover (promotion or demotion) posts to Discord within seconds.

Both feed the same pipeline; different sources, identical fan-out.

---

## See also

- **Observability** (Components) — the full alerting picture and how Hermod fits the monitoring split.
- **Storage & data** (Components) — the Patroni failover events Hermod reports.
- **Semaphore** (this section) — drift-check alerts that flow through Hermod.
