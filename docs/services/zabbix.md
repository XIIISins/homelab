<!-- docs/services/zabbix.md -->

# Zabbix (host/LXC monitoring — Hugin)

Zabbix 7.0 LTS on LXC 1102 (Hugin, Urd, `10.0.11.21`): the host/LXC-layer half of observability, next to the in-cluster VictoriaMetrics/Logs stack ([`observability.md`](observability.md)). It runs on its own LXC **outside K3s on purpose**: when K3s is the thing on fire, Zabbix is what tells us. Built in Phase 8c (2026-05-26); promoted from 5h so that failure-domain role is visible in the build sequence (decision rows "Zabbix promotion", "Zabbix UI auth — native SAML, not Traefik ForwardAuth", "Zabbix LAN TLS termination" in [`decisions.md`](../operations/decisions.md)). Retros: [server deploy](../incidents/2026-05-25-zabbix-server-deploy.md), [SAML](../incidents/2026-05-26-zabbix-saml-deploy.md); gotchas: [`known-issues/zabbix.md`](../known-issues/zabbix.md).

## Architecture

| Layer | Stack | Notes |
|-------|-------|-------|
| LXC | 1102 on **Urd**, Debian 13, unprivileged + nesting | 2 vCPU / 2 GB (+1 GB swap) / 8 GB, VLAN 11. Measured 2026-10-06: ~170 MB anon+shmem, ~850 MB peak. Placed on Urd, not Skuld: Skuld already carried the PG leader, a CP, a worker and AdGuard (and later proved to hard-freeze), and losing it must not also lose monitoring. TF: `hugin` in `terraform/proxmox/asgard-lxcs/lxcs.tf`. |
| Server | `zabbix-server-pgsql` 7.0 (`zabbix_major`) from `repo.zabbix.com` | `:10051` for active checks. |
| Web UI | `zabbix-frontend-php` + nginx/PHP-FPM | Plain HTTP `:80` on the LXC; TLS terminates at Traefik. |
| Local agent | `zabbix-agent2` | Monitors the Zabbix host itself. |
| Database | Patroni via the HAProxy VIP `10.0.10.210:5432`, `sslmode=require` | `zabbix` DB declared in `postgres_databases` (`group_vars/postgres.yml`). Failover is transparent: HAProxy `/master` check + `balance first`. |
| Auth | Authentik native SAML 2.0 + local `Admin` break-glass | `zabbix-admins` group gates access; JIT provisioning from the group claim; NameID = email. Declared in `terraform/authentik/zabbix.tf`. |
| LAN ingress | `hugin.midgard.xiiisins.com` | AGH rewrite to the Traefik VIP; HTTPRoute on the midgard Gateway; selector-less Service + EndpointSlice → `10.0.11.21:80` (`k8s/asgard/apps/zabbix-ingress/`). |
| WAN ingress | `hugin.xiiisins.com` | Cloudflare CNAME to the tunnel; cloudflared targets Traefik's ClusterIP service. |
| Backdoor | `hugin-direct.niflheim.xiiisins.com` | AGH rewrite straight to the LXC `:80`, bypassing Traefik; local `Admin` only. |

No HA on the server (single LXC); the database is HA via Patroni. Losing the LXC pauses collection until it returns; stored data survives.

Playbook: `asgard-zabbix.yml` (baseline → `zabbix-server` → `zabbix-agent` → `vlagent` → hardening, then `zabbix-proxmox-template-tuning.yml`; it imports `zabbix-host-groups.yml` first). The server role also declares, via the API, the Hermod and n8n webhook media types (both attached to `Admin`, which fires the default "Report problems" action) and the Toolbelt's read-only identity (`aiops-ro`, a role with no UI and an API allow-list of `*.get`): [`notifications.md`](notifications.md), [`aiops-diagnosis.md`](../procedures/aiops-diagnosis.md).

## Secrets

All under `secret/ansible/…`, because the Ansible role is the consumer (the TF→Vault SAML hand-off follows the consumer's prefix, not the minter's). TF-minted secrets store their value in field `value`.

| Path | Used by | Notes |
|------|---------|-------|
| `ansible/postgres/zabbix-password` | `postgres-common` (creates the DB user) and `zabbix-server` (wires `zabbix.conf.php`) | Same dual-purpose shape as NetBox/Authentik/Teamspeak. |
| `ansible/zabbix/admin-password` | `zabbix-server`; 1P copy for the backdoor | Break-glass local `Admin`. The factory password (`zabbix`) is **not** auto-rotated: set it via the UI after first login. |
| `ansible/zabbix/saml-sp-keypair` | `zabbix-server` | RSA-2048 self-signed, 10-year cert (`CN=hugin.midgard.xiiisins.com`); signs AuthnRequests. |
| `ansible/zabbix/saml-idp` | `zabbix-server` | IdP metadata URL and signing cert, written by `terraform/authentik/zabbix.tf`. |
| `ansible/zabbix/api-token` | `zabbix-agent` role (`community.zabbix.*` modules) | Minted once in the UI for the `ansible` user (Super admin role), then `vault kv put`; TF has `ignore_changes` so it isn't clobbered. |
| `ansible/hermod/config-key`, `ansible/aiops/n8n-ingest-token/zabbix`, `ansible/aiops/zabbix-token` | media types and the Toolbelt's read-only API token | See the role defaults. |

## Authentication chain

1. A user browses `hugin.xiiisins.com` (WAN, or LAN via public DNS) or `hugin.midgard.xiiisins.com`; Traefik routes by hostname and terminates TLS with the wildcard cert.
2. Zabbix shows its login page; "Sign in with SAML" sends a signed AuthnRequest to Authentik.
3. Authentik authenticates, checks the `zabbix-admins` policy binding, and returns a signed Response (email as NameID, username, group claims).
4. Zabbix validates it against the IdP cert, JIT-provisions the user into the mapped usergroup, sets the session cookie.

**Canonical SAML hostname is `hugin.xiiisins.com`.** Authentik's SAML provider pins one `acs_url`, and Zabbix bakes the request's Host into the AuthnRequest, so a login started on the midgard name is rejected for ACS mismatch (SP entityID is also the apex URL). The midgard name works for non-SAML browsing; bookmark the apex one.

**Why SAML, not ForwardAuth:** Zabbix 7.0 has built-in SAML, ForwardAuth would add a K3s dependency to a K3s-independent service, and native OIDC isn't viable (OIDC is MFA-only in 7.0).

## Recovery model

"Zabbix keeps observing and alerting; operator UI access falls back to plain HTTP." K3s recovery itself is a different incident class.

1. **Backdoor URL.** With K3s down, SAML (Authentik) and the Traefik path are both gone, but the LXC and its data are up: `http://hugin-direct.niflheim.xiiisins.com` + local `Admin`. DNS survives because AdGuard is its own LXC trio.
2. **Patroni VIP.** The DB path (HAProxy VIP) is outside K3s, so Zabbix keeps writing with K3s and Authentik fully down.
3. **Alerts.** Zabbix → Hermod (LXC 1103, outside K3s) → Discord works without K3s.

## Agent fleet

`zabbix-agent2` on every inventory host except the Zabbix LXC (which gets it from `asgard-zabbix.yml`) and the hosts handled elsewhere (offsite by hand, canaries via `site-nonprod.yml`): `playbooks/zabbix-agent.yml` (which first imports `zabbix-host-groups.yml`, aggregating each host's `zabbix_agent_host_groups` and creating missing groups).

- **Registration is declarative, not auto-registration.** After install, `register-host.yml` calls the Zabbix API (`community.zabbix.zabbix_host`, `httpapi` connection delegated to `hugin`) to create or update the host with its groups, linked templates and macros (`zabbix_agent_host_groups`, `zabbix_agent_link_templates`, `zabbix_agent_host_macros`/class macros; per-group overrides in `group_vars`). Groups and templates must already exist; the API call fails otherwise. `HostMetadata` is still sent (default: inventory hostname) but nothing dispatches on it.
- **Plain HTTP to the API** on the internal LAN (`zabbix_agent_api_use_ssl: false`). Agent ↔ server traffic is unencrypted too (VLANs 11/21 only); move to PSK/cert (`zabbix_agent_tls_connect`/`_accept`) when the first cross-VLAN or tailnet-exposed agent lands.
- Fleet-wide monitoring choices (QEMU discovery disabled, LXC load-average muted, canary smoke template) are decision rows in `decisions.md` ("VM/LXC monitoring source", "LXC load-average alerting", "Canaries carry their own Zabbix smoke-test template").

## Source of truth

| Data | Spec lives in | Zabbix role |
|------|---------------|-------------|
| Host existence, IPs | `terraform/proxmox/*` + `terraform/netbox/` | Hosts are registered by the agent role from the Ansible inventory (NetBox dynamic inventory) |
| Templates, items, triggers | Zabbix DB (UI-managed) | Authoritative; export to Git is a deferred polish item |
| Notifications | Zabbix media types (declared by the server role) | Zabbix mints alerts, Hermod routes to Discord by severity→tag map ([`notifications.md`](notifications.md)) |

NetBox says what exists, Zabbix says how it is: a new host in NetBox gets an inventory entry, then the agent, then a Zabbix record with the right groups, with no multi-tool data entry.

## Not done

- **Templates as code:** export templates to Git once custom templates start to accumulate (the built-ins cover Linux, PostgreSQL and nginx today).
- **Viewer access:** SAML maps one group (`zabbix-admins` → administrators). For read-only sharing, add `zabbix_viewers` in Authentik and a mapping rule.
- **Trim 3× cluster-wide Proxmox-template discovery redundancy:** tracked in [`open-questions.md`](../operations/open-questions.md).
