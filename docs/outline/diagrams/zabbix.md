<!-- docs/outline/diagrams/zabbix.md -->

# Zabbix diagram

Host and LXC monitoring outside K3s. When Kubernetes is the thing on fire, Zabbix is what tells you. It is a single LXC; the database behind it is the highly available part.

![Zabbix (Hugin): host and LXC monitoring outside K3s](/api/attachments.redirect?id=e5f667e2-5133-4ae6-85b7-1fa4138f2714)

---

## How to read it

- **Top left: what it watches.** `zabbix-agent2` on every LXC, Proxmox host and PBS, plus templates for the Jellyfin and Proxmox iGPU checks. The agents reach the server by active checks.
- **The Hugin LXC (box, on Urd).** The web frontend (nginx and PHP-FPM, plain HTTP because TLS ends at Traefik), `zabbix-server` 7.0 LTS, an agent that watches Hugin itself, and the webhook media types for Hermod and the AIOps agent (n8n), attached to the Admin user.
- **Left: three ways for an operator.** `hugin.xiiisins.com` through the Cloudflare tunnel (the SAML name), `hugin.midgard` on the LAN through an AdGuard rewrite and Traefik's route-only shim, and `hugin-direct.niflheim` for the local Admin login.
- **Right: dependencies.** PostgreSQL through the VIP (the `zabbix` database; failover is transparent). Authentik SAML 2.0 for login, with `zabbix-admins` and just-in-time users; the local Admin is break-glass. Hermod posts critical and alert messages to Discord by severity.
- **Why a separate LXC on Urd:** Skuld already carries too much and freezes.
- **If Hugin is lost,** collection pauses until it returns; stored data survives in Patroni. SAML only works on `hugin.xiiisins.com`, because the ACS URL is pinned to one host.
- Zabbix history is long-term and low-resolution; VictoriaMetrics covers the Kubernetes layer.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[zabbix.excalidraw 44325](/api/attachments.redirect?id=c8a658dc-dac7-4cd9-8010-d53ea46b3df1)

[zabbix.svg 57996](/api/attachments.redirect?id=a9881130-7378-4f81-8cdf-aa5529e9cc30)

---

## See also

- **Zabbix** (Services): the service page.
- **Hermod** (Services): the notification hub.
- **Observability** (Components): the monitoring split.
