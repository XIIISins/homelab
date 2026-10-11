<!-- docs/outline/services-and-purpose/zabbix.md -->

# Zabbix

Host and LXC monitoring, on its own LXC (Hugin, `10.0.11.21`, on Urd) **outside Kubernetes on purpose**: when the cluster is the thing on fire, Zabbix is what tells you. It pairs with Munin in the naming scheme: one observes, one remembers.

![Zabbix (Hugin): host and LXC monitoring outside K3s](/api/attachments.redirect?id=e5f667e2-5133-4ae6-85b7-1fa4138f2714)

---

## What it watches

Proxmox hosts, every LXC and VM, and service-level templates for Postgres, HAProxy, AdGuard, Jellyfin's QuickSync health and more, through an agent on each host. The cluster's own metrics are VictoriaMetrics' job; Zabbix covers everything beneath Kubernetes.

## How it runs

| Aspect | Detail |
|---|---|
| Server | Zabbix 7.0 LTS on LXC 1102, 2 vCPU / 2 GB. Placed on Urd, not Skuld, so losing the flaky node cannot also lose monitoring. |
| Database | The shared Patroni cluster via the HAProxy VIP. Failover is transparent. |
| Sign-in | Authentik **native SAML**, restricted to the `zabbix-admins` group, plus a local `Admin` for break-glass. |
| Ways in | `hugin.xiiisins.com` (public), `hugin.midgard.xiiisins.com` (internal, through Traefik, which SAML needs), and `hugin-direct.niflheim.xiiisins.com`, a backdoor straight to the LXC with local login only, for when Traefik is the problem. |
| Resilience | No HA: a lost LXC pauses collection until restored; stored data survives. |

## What it feeds

A webhook media type on the standard "report problems" action sends events to **Hermod**, and a second one sends context-rich events to the AIOps diagnosis agent. A read-only API identity serves the agent's tools. The Ansible role declares those webhooks and identities, so the integration is reproducible.

---

## See also

- **Observability** (Components): the monitoring split.
- **Hermod** (this section): the notification hub.
- **AIOps agent** (this section): the consumer of events.
- **Zabbix diagram** (Diagrams): what it watches, how operators reach it and what it feeds.
