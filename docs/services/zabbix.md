<!-- docs/services/zabbix.md -->

# Zabbix (host/LXC monitoring — Phase 8c, pre-implementation)

Zabbix 7.0 LTS on LXC 1102 (Urd, `10.0.11.21`). The host/LXC-layer half of the observability stack — counterweight to Phase 8a's in-cluster VL/VM placement. Stays on its own LXC outside K3s by design: when K3s itself is the thing on fire, Zabbix is what tells us about it.

**Status: pre-implementation.** Server-side scaffolding (TF LXC block, both Ansible roles, both playbooks) was written during Phase 5h sketch-work and pre-existed this phase's docs. Phase 8c finalises auth wiring (Authentik native SAML), ingress (Traefik fronts the LXC for `hugin.midgard.xiiisins.com` LAN + `hugin.xiiisins.com` WAN), Vault secret minting, and the post-server agent-rollout pattern.

## Why Phase 8c, not 5h

Promoted out of 5h ("remaining LXCs") during 8c pre-implementation planning. Three reasons:

1. **Failure-domain role.** Phase 8a's "VM/VL placement — asgard, not jotunheim" decision is explicitly justified by Zabbix being elsewhere (LXC, outside K3s). Treating it as miscellaneous-LXC backlog buried that load-bearing role.
2. **Stack symmetry.** Phase 8a is K8s-layer metrics + logs. Phase 8c is host/LXC-layer metrics + alerts. Putting both under Phase 8 makes the "clean split — vmagent for K8s, Zabbix for everything below" decision row legible in the build sequence.
3. **Sequencing dependencies.** Phase 5h.2 (Hermod notifications) is sequenced "after Zabbix because Zabbix is the first concrete alert producer." Phase 5h.3 (Semaphore drift-check) is sequenced "after Hermod because drift-check is the first `alert`-tag producer into Hermod." The whole chain reads cleaner if Zabbix is a named phase rather than a 5h sub-bullet.

Three corresponding decision rows in [`../operations/decisions.md`](../operations/decisions.md) (phase promotion, native-SAML auth, Traefik-fronts-LXC).

## Architecture

| Layer | Stack | Notes |
|-------|-------|-------|
| LXC | 1102 on Skuld, Debian 13, unprivileged + nesting=true | 2 vCPU / 2GB RAM / 8GB disk, VLAN 11, `10.0.11.21/24`. TF block at `terraform/proxmox/asgard-lxcs/lxcs.tf:537–605`. Pre-existing NetBox record at `terraform/netbox/vms.tf:38`. |
| Server | `zabbix-server-pgsql 7.0` LTS | apt repo from `repo.zabbix.com`, pinned `zabbix_major: "7.0"`. Listens on `:10051` for active agent checks. |
| Web UI | `zabbix-frontend-php` + `zabbix-nginx-conf` | nginx :80 serving PHP-FPM. Plain HTTP on the LXC — TLS terminates at Traefik. |
| Local agent | `zabbix-agent2` | Server runs the agent locally too — gives Zabbix visibility into its own host as a sanity check. |
| Database | PG via HAProxy VIP `10.0.10.210:5432`, `sslmode=require` | `zabbix` DB declared in `ansible/inventory/group_vars/postgres_hosts.yml:49–51` (owner `zabbix`, password at Vault `ansible/postgres/zabbix-password`). Patroni handles failover transparently — HAProxy `/master` health-check + `balance first` routes writes to the current leader. |
| Auth | Authentik native SAML 2.0 + local-Admin break-glass | Authentik SAML IdP via `authentik_provider_saml`; Zabbix's built-in SAML support handles the round-trip. NameID = email, JIT user provisioning from group claim. `zabbix-admins` Authentik group gates access. Local `Admin` user stays enabled for emergency/backdoor login only. |
| LAN ingress | Traefik fronts the LXC, midgard Gateway | AGH rewrite `hugin.midgard.xiiisins.com` → Traefik VIP `10.0.20.10`. HTTPRoute on midgard Gateway → K8s Service (no selector) + EndpointSlice → `10.0.11.21:80`. Existing `*.midgard.xiiisins.com` wildcard cert covers it. |
| WAN ingress | Cloudflared tunnel → Traefik ClusterIP DNS | `hugin.xiiisins.com` CNAME → tunnel; ingress rule targets `https://traefik.traefik.svc.cluster.local` with `httpHostHeader: hugin.xiiisins.com` + `noTLSVerify: true`. Mirrors the Authentik apex pattern. |
| Backdoor | `hugin-direct.niflheim.xiiisins.com` → LXC `:80` direct | AGH rewrite bypasses Traefik entirely. Plain HTTP, local-Admin login only (no SAML). For K3s-down emergencies — see [Recovery model](#recovery-model) below. |

**Sizing.** 2 vCPU / 2 GB is the planned floor; bump if dashboard responsiveness or scrape volume needs it. DB lives on Patroni so server-side memory is just the server + frontend + agent (~700 MB steady-state expected based on similar small-fleet deploys).

**No HA on the server.** Single LXC. Database HA is via Patroni (3-node). Loss of the server LXC stops collection until it returns; data already in PG survives. Revisit if uptime becomes critical.

## Secrets

Four Vault paths feed Zabbix:

| Vault path | Used by | Fields | Notes |
|------------|---------|--------|-------|
| `ansible/postgres/zabbix-password` | `postgres-common` role + `zabbix-server` role | `password` | Dual-purpose — postgres-common creates the user, zabbix-server reads the same password to wire `zabbix.conf.php`. Same shape as NetBox / Authentik / Teamspeak. |
| `ansible/zabbix/admin-password` | `zabbix-server` role + operator (1P copy) | `password` | Break-glass local-Admin user. Rotated from chart default `zabbix` on first deploy. 1P recovery copy for backdoor login. |
| `ansible/zabbix/saml-sp-keypair` | `zabbix-server` role | `key`, `cert` | RSA 2048 self-signed, used by Zabbix to sign SAML AuthnRequests. Generated once: `openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj '/CN=hugin.midgard.xiiisins.com'`. Long lifetime — Zabbix-side cert rotation is operationally rare. |
| `ansible/zabbix/api-token` | `zabbix-agent` role + `community.zabbix.*` modules | `token` | Populated post-deploy in 8c.7 via `zabbix-server-bootstrap.yml`. Empty placeholder pre-8c.7. |
| `ansible/zabbix/saml-idp` | `zabbix-server` role | `idp_metadata_url`, `idp_signing_cert` | Written by `terraform/authentik/zabbix.tf` outputs (TF→Vault hand-off pattern). Zabbix role reads at deploy time to wire IdP side of SAML. |

Per the Vault path convention (machine-consumer-domain prefix), all five secrets live under `ansible/` — the consumer is the Ansible role on the Zabbix LXC, including the TF→Vault SAML IdP hand-off (the TF module *writes* but the LXC role *consumes*, and the convention keys off the consumer, not the minter).

## Authentication chain

1. User browses `https://hugin.midgard.xiiisins.com` (LAN) or `https://hugin.xiiisins.com` (WAN). DNS resolves to Traefik VIP (LAN) or Cloudflare tunnel (WAN).
2. Traefik routes by hostname (HTTPRoute attaches to the midgard Gateway, listener `websecure-midgard` or `websecure-apex-wildcard`/`websecure-apex-bare`). Backend Service has no pod selector — manually-curated EndpointSlice points at `10.0.11.21:80`. Traefik terminates TLS using the existing wildcard cert.
3. Zabbix frontend renders the login page. User clicks "Sign in with SAML".
4. Zabbix builds a SAML AuthnRequest signed with its SP keypair, redirects browser to Authentik's SSO URL.
5. Authentik authenticates (if not already), checks user's `zabbix-admins` group membership against the `zabbix` Application's policy binding, returns a signed SAML Response with the user's email (NameID), username, and group claims.
6. Zabbix validates the response signature against the IdP cert (in Vault at `ansible/zabbix/saml-idp`), extracts the email/groups claims, JIT-provisions the user into the matching Zabbix usergroup if not already present, sets a session cookie, redirects to the dashboard.

**Backdoor path** (Traefik / Authentik / K3s down): browse `http://hugin-direct.niflheim.xiiisins.com` from internal LAN. AGH rewrite resolves directly to `10.0.11.21`. Zabbix nginx serves `:80` plain HTTP. Operator logs in with local `Admin` + Vault-stored password. SAML not invoked.

**Single canonical SAML hostname.** Authentik's `authentik_provider_saml` resource pins ONE `acs_url`. Zabbix's frontend bakes the current request's Host header into the AuthnRequest's `AssertionConsumerServiceURL`, so an AuthnRequest originating from `hugin.midgard.xiiisins.com` would be rejected by Authentik (ACS mismatch against the apex-pinned URL). The canonical SAML hostname is therefore `hugin.xiiisins.com` — both WAN clients (via Cloudflare tunnel) and LAN clients reach it cleanly, since the apex DNS record resolves through public Cloudflare regardless of where the client sits. The midgard alias still works for non-SAML traffic (e.g. already-authenticated browsing within a session), but each fresh SSO login lands back at the apex. Bookmark `hugin.xiiisins.com` accordingly.

**Why native SAML over Traefik ForwardAuth.** Decision row "Zabbix UI auth — native SAML, not Traefik ForwardAuth" — vmui/VL (Phase 8a) only use ForwardAuth because they have no native SSO. Zabbix 7.0 has built-in SAML 2.0; using its strongest available auth path is the right default. ForwardAuth would also add a K3s dependency for what's supposed to be a K3s-independent service's auth path. Native OIDC isn't viable in Zabbix 7.0 (OIDC is MFA-only there).

## Recovery model

Same shape as NetBox's recovery model (offline-Git copy + recovery LXC), adapted for Zabbix's "see infra when infra is on fire" mission:

1. **Backdoor URL** — `hugin-direct.niflheim.xiiisins.com` resolves directly to the LXC bypassing Traefik. When K3s is down, the SAML path is broken (Authentik is in K3s) AND the Traefik path is broken (Traefik is in K3s), but the LXC and its data are still running. Local-Admin login over plain HTTP gets the operator to the dashboard. AGH itself is on its own LXC trio (Saga/Mimir/Kvasir) outside K3s, so DNS resolution survives K3s outages.
2. **Patroni VIP** — the PG backend is on the HAProxy VIP, which is itself an outside-K3s service. Server-to-DB connectivity survives K3s outages. Zabbix server keeps writing to PG even with K3s and Authentik fully down.
3. **Alerts** — Hermod (Phase 5h.2, pending) is the notification fan-out. Hermod is LXC-based (1103, Verd), outside K3s. So Zabbix → Hermod → Discord works without K3s. Same independent-failure-domain rationale as the Zabbix server itself.

The recovery model is "Zabbix keeps observing + alerting; operator UI access falls back to plain HTTP". K3s recovery is a different incident class — Zabbix's job is to tell us when K3s broke, not to mediate the recovery.

## Agent fleet

`zabbix-agent2` on every host in inventory except the Zabbix server itself (which runs the agent locally as a sanity check via `zabbix-host.yml`). Cluster-wide rollout via `ansible/playbooks/zabbix-agents.yml` (existing — targets `all:!zabbix_hosts`).

**Auto-registration pattern.** Agents ship `HostMetadata` from `ansible/roles/zabbix-agent/defaults/main.yml`. Server-side `auto-registration action` (created in 8c.7 via `community.zabbix.zabbix_action`) regex-matches the metadata and assigns the host to the right host group + links a template. Adding a new host: ansible-play it with the agent role + the right metadata; it self-registers within ~5 min.

**HostMetadata format.** Default in the role is `{{ inventory_hostname }}`. Phase 8c.8 changes this to a group-tagged form (e.g. `k3s-worker|asgard`) so the auto-registration regex can dispatch. Per-host overrides stay available via host_vars.

**TLS off.** Agent ↔ server traffic is unencrypted — internal VLAN 11 + 21 only, no tailnet exposure. Bump to PSK or cert-based later if needed (`zabbix_agent_tls_connect` / `zabbix_agent_tls_accept` defaults).

## Source-of-truth model

| Data class | Spec lives in | NetBox role | Zabbix role |
|------------|---------------|-------------|-------------|
| Host existence + IPs + group membership | `terraform/proxmox/*` + `terraform/netbox/` (5i.3 retrofit pattern) | Authoritative for IPAM/DCIM | Auto-registers from agent metadata; group dispatch via auto-registration action |
| Ansible inventory grouping | `ansible/inventory/hosts.yml` (today) → NetBox tag projection (Phase 5h.3) | Source of `ansible:<group>` tags | Same tag flows into `HostMetadata` for Zabbix group dispatch |
| Templates + items + triggers | Zabbix DB (UI-managed today) | Not applicable | Authoritative; export to Git via Zabbix templates export is a deferred polish item |
| Notifications | Zabbix DB → Hermod (Phase 5h.2) via HTTP media type | Not applicable | Zabbix mints alerts, Hermod routes to Discord (severity→tag map in `docs/services/notifications.md`) |

The seam: NetBox tells us what hosts exist; Zabbix tells us their health. Aligning the two via shared `ansible:<group>` tags (Phase 5h.3 NetBox-as-inventory) means a new host in NetBox automatically gets an Ansible inventory entry, automatically gets agent installed, automatically registers in Zabbix with the right template. No multi-tool data entry.

## Pre-implementation sub-phase plan

Full step-by-step plan tracked in [`../operations/open-questions.md`](../operations/open-questions.md) under "Phase 8c — Zabbix LXC". Eight sub-phases:

| # | Scope | Files touched |
|---|-------|---------------|
| 8c.1 | Vault secrets minting (4 paths above) | (Vault data only, no repo changes) |
| 8c.2 | LXC provision + day-1 bootstrap | `terraform/proxmox/asgard-lxcs/lxcs.tf` (1 hunk: `lifecycle.ignore_changes`), `ansible/inventory/hosts.yml` (new `zabbix_hosts` group) |
| 8c.3 | Authentik SAML provider via TF | new `terraform/authentik/zabbix.tf` |
| 8c.4 | Server role SAML wire-up + deploy | new `roles/zabbix-server/tasks/saml.yml`, edits to `zabbix.conf.php.j2`, `meta/main.yml`, possibly `tasks/main.yml` import |
| 8c.5 | Ingress (AGH + Traefik + Cloudflared) | new `k8s/asgard/apps/zabbix-ingress/` directory (`kustomization.yaml`, `service.yaml`, `endpointslice.yaml`, `httproute.yaml`), `apps/kustomization.yaml` patch, `terraform/adguard/rewrites.tf`, `terraform/cloudflare/main.tf` |
| 8c.6 | SAML cutover + 3-path validation | (operational; no repo changes unless validation surfaces config tweaks) |
| 8c.7 | Server-side host-mgmt bootstrap | new `ansible/playbooks/zabbix-server-bootstrap.yml` |
| 8c.8 | Cluster-wide agent rollout | `ansible/roles/zabbix-agent/defaults/main.yml` (HostMetadata format) |

## Pending follow-ups (post-Phase-8c)

- **Templates-as-code.** Zabbix supports exporting templates to YAML/XML. Bringing the per-service template configuration into Git (instead of UI-managed) is a polish item — defer until template churn becomes a real cost. Mostly relevant when custom templates start landing for app-specific metrics.
- **Agent TLS.** Currently unencrypted on VLANs 11/21. Bump to PSK or cert-based when the first cross-VLAN or tailnet-exposed agent lands. Defaults are in `zabbix_agent_tls_connect` / `zabbix_agent_tls_accept`.
- **Notifications wiring (Phase 5h.2).** Zabbix is the first concrete alert producer for Hermod. Media type definition + tag mapping (`Disaster`/`High` → `critical`; `Average` → `alert`) lands as part of the 5h.2 source-side wiring step.
- **SAML group → Zabbix usergroup mapping at scale.** Day-1 mapping is single group (`zabbix-admins` → Zabbix administrators). When read-only access becomes a real need (e.g. sharing dashboards with non-operator users), extend Authentik with `zabbix_viewers` + add the mapping rule. Until then, single-group keeps the JIT provisioning trivial.
- **Templates export → Git.** Not needed for first deploy (Zabbix ships rich built-in templates that cover Linux + PostgreSQL + nginx + the bulk of what we monitor). Surface as a polish item when custom templates start to accumulate.

## See also

- [`../operations/build-sequence.md`](../operations/build-sequence.md) — Phase 8c row, sequencing in Phase 8
- [`../operations/decisions.md`](../operations/decisions.md) — three Zabbix decision rows (phase promotion, native-SAML auth, Traefik-fronts-LXC)
- [`../operations/open-questions.md`](../operations/open-questions.md) — Phase 8c sub-phases 8c.1–8c.8 checklist
- [`observability.md`](observability.md) — Phase 8a (K8s metrics+logs, complementary to this)
- CLAUDE.md "Services / placement" architectural invariant — Zabbix-as-failure-domain rationale
