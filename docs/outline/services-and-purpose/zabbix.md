<!-- docs/outline/services-and-purpose/zabbix.md -->

# Zabbix (Hugin)

Host- and LXC-level monitoring — the thing that watches every physical node, every VM, and every LXC, and alerts when one of them is unhealthy. Named **Hugin**, Odin's raven of Thought, pairing with **Munin** (Memory, the NAS).

---

## Where it runs, and why it's an LXC

Zabbix 7.0 LTS runs in **LXC 1102 (Hugin) on Urd** (`10.0.11.21`) — deliberately **outside Kubernetes**.

The reason is failure-domain independence. A monitoring system that lives inside the cluster it monitors goes blind exactly when you need it most — during a cluster outage. By running in its own LXC on its own host, Zabbix keeps watching (and keeps alerting) even if asgard K3s is completely down. This is the same principle that keeps the in-cluster metrics stack and the host-monitoring stack as two separate things.

- **Database:** Postgres via the Patroni VIP.

---

## What it watches

- **Zabbix agent 2** is deployed to all 23 inventory hosts — every node, LXC, and VM.
- **Service templates** give deep monitoring of the things that matter: PostgreSQL, HAProxy, etcd, Proxmox VE, nginx, and PHP-FPM.

There's a clean split with the in-cluster observability stack: **VictoriaMetrics/Logs handle Kubernetes-internal metrics and logs; Zabbix handles host- and LXC-level health.** No overlap, no double-counting.

---

## Access and identity

- **Authentik SAML 2.0**, using Zabbix's native built-in SAML support — *not* Traefik ForwardAuth. ForwardAuth would route auth through the cluster, which contradicts the whole "observability survives a cluster outage" rationale; native SAML keeps Zabbix's auth independent.
- **Hostnames:** `hugin.xiiisins.com` is the canonical hostname (and the SAML assertion-consumer URL); `hugin.midgard.xiiisins.com` is the LAN alias; `hugin-direct.niflheim.xiiisins.com` is a direct backdoor that bypasses Traefik for emergency access.

---

## See also

- **Observability** (Components) — the full split between Zabbix and the VictoriaMetrics/Logs stack, plus how alerts flow to Hermod.
- **Identity & secrets** (Components) — Authentik's SAML provider model.
- **Hermod** (this section) — where Zabbix's alerts go.
