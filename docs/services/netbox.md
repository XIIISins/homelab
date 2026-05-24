<!-- docs/services/netbox.md -->

# NetBox (IPAM/DCIM, deployed 2026-05-24)

NetBox 4.6.1 in asgard K3s, internal-only via Traefik at `netbox.niflheim.xiiisins.com`. Source of truth for: devices, virtual machines/LXCs, IPs, VLANs, prefixes, sites, clusters. Git remains the IaC spec (Terraform / Ansible / Flux manifests); NetBox is the queryable view, written by TF going forward via the `e-breuninger/netbox` provider (Phase 5i.3 implementation pending — initial inventory was hand-imported via web UI per [`docs/procedures/netbox-initial-data-import.md`](../procedures/netbox-initial-data-import.md)).

## Architecture

| Layer | Stack | Notes |
|-------|-------|-------|
| Chart | `oci://ghcr.io/netbox-community/netbox-chart` v`8.2.17` | OCI, NOT the legacy HTTPS index. Pin concrete. |
| App | NetBox v`4.6.1` | community edition, includes CVE-2026-29514 fix |
| Web server | granian (Rust ASGI) | NOT gunicorn — changed in 4.6 |
| Cache + RQ | bundled Valkey (chart sub-chart), `architecture: standalone` | one pod, single replica. Reload-on-restart fine for cache |
| Database | external PG via HAProxy VIP `10.0.10.210:5432` | `sslmode=require`, `target_session_attrs=read-write` |
| Auth | Authentik OIDC + local-superuser break-glass | python-social-auth backend, NOT django-allauth |
| Storage (media) | `emptyDir` for first deploy | Chart shares ONE RWO PVC across server+worker — Synology iSCSI is RWO-only, deadlocks anti-affinity. emptyDir fine while no device photos. |
| Edge | HTTPRoute on niflheim Gateway → ClusterIP Service | internal-only, no midgard listener, no Cloudflare tunnel |
| DNS (in-cluster ↔ Authentik) | CoreDNS `coredns-custom` rewrite of `authentik.midgard.xiiisins.com` → `traefik.traefik.svc.cluster.local` | bypasses MetalLB-VIP tromboning for pod→Authentik OIDC backchannel |

**Sizing.** `resourcesPreset: medium` (1.5Gi memory limit) for both server + worker. The chart's `small` preset (768Mi) OOMs during initial migrations — `medium` is the realistic floor for a fresh deploy / version upgrade. Steady-state usage is ~600-800Mi (peak observed: 830Mi).

**Why internal-only.** IPAM/DCIM is operationally sensitive — knowing the full topology + IP allocations + device descriptions is reconnaissance gold. Even SSO-gated, the smaller attack surface (no public ingress at all) is worth the trade-off of needing tailnet to access from off-LAN. Tailnet + AGH split-DNS (Phase 5e.4) gives remote access without external exposure.

## Secrets

Five Vault paths feed NetBox via ESO ExternalSecrets:

| Vault path | Secret name | Used by |
|------------|-------------|---------|
| `ansible/postgres/netbox-password` | (Ansible-only) | postgres-common role for DB user creation |
| `k8s/netbox/postgres-password` | `netbox-db` | chart's `externalDatabase.existingSecretName` |
| `k8s/netbox/app` (key `secret_key`) | `netbox-app` | chart's `existingSecret` (Django SECRET_KEY) |
| `k8s/netbox/superuser` (keys `password`, `api_token`) | `netbox-superuser` | chart's `superuser.existingSecret` |
| `k8s/netbox/oidc-client-secret` (keys `client_id`, `client_secret`) | `netbox-oidc` (rendered as `99-oidc.py` file) | mounted via `extraVolumes`+`subPath` to `/etc/netbox/config/99-oidc.py` |

PG password is dual-pathed (`ansible/postgres/...` + `k8s/netbox/...`) because Ansible's AppRole policy can't read `k8s/...` paths — single mint via `random_password` writes to both. App/superuser/OIDC secrets are K8s-only, single path.

## Authentication chain

1. User browses `https://netbox.niflheim.xiiisins.com` → AGH resolves to MetalLB VIP `10.0.20.10` (Traefik) → HTTPRoute matches → NetBox login page.
2. User clicks "Log in via OIDC" → NetBox redirects to `https://authentik.midgard.xiiisins.com/application/o/authorize/?client_id=netbox&...`.
3. Authentik authenticates (if not already) → checks user's group membership against the `netbox-admins` + `netbox-viewers` policy bindings on the NetBox Application (OR semantics via `policy_engine_mode: any`) → consent flow (or implicit-consent skip) → redirects back to `https://netbox.niflheim.xiiisins.com/oauth/complete/oidc/?code=...`.
4. **NetBox pod's OIDC backchannel** (discovery on startup + token exchange + userinfo per login): pod resolves `authentik.midgard.xiiisins.com` via CoreDNS rewrite → Traefik ClusterIP → Traefik routes by SNI to authentik backend. NOT through the MetalLB VIP (that path tromboning is broken for in-cluster pod traffic — see CLAUDE.md "Cloudflared / Cloudflared" rule generalization).
5. NetBox auto-creates user record (`autoCreateUser: true`) on first OIDC login, sets session cookie, redirects to `/`.

**Permissions caveat.** OIDC-created users get ZERO NetBox permissions by default — they can authenticate but can't do anything. Manual elevation via local-superuser (`admin` + Vault password) → Admin → Users → check Active/Staff/Superuser. Automating this requires a custom `SOCIAL_AUTH_PIPELINE` override mapping Authentik group claims → NetBox permission assignments, deferred until manual elevation per user becomes recurring toil (>3 users).

## Source-of-truth model

The IaC vs NetBox-as-view split:

| Data class | Spec lives in | NetBox role |
|------------|---------------|-------------|
| LXC/VM existence + sizing + placement | `terraform/proxmox/*` | Receives `netbox_virtual_machine` writes from TF (5i.3 pending) |
| IP allocations | `terraform/proxmox/*` locals maps | Receives `netbox_ip_address` writes from TF |
| K8s workloads | `k8s/asgard/**` Flux manifests | Not in NetBox (NetBox is for IPAM/DCIM, not K8s state) |
| Ansible inventory | `ansible/inventory/hosts.yml` | Could be a future NetBox consumer (Phase 5i.4 — dynamic inventory via `netbox.netbox.nb_inventory`, deferred until AWX lands) |
| Device descriptions / tags / per-entry notes | NetBox UI | Hand-entered; preserved across `terraform import` retrofit |

The seam: when a new LXC/VM is added to TF, both `proxmox_virtual_environment_container` AND `netbox_virtual_machine` resources land in the same module — single `terraform apply` creates both. If NetBox is down, the apply errors on the NetBox resources but the LXC is created; re-running picks up where it left off (acceptable failure mode for a single-operator homelab).

## Recovery model

Primary NetBox runs in asgard K3s — same failure domain as Patroni, Authentik, Vault. When K3s itself is down (rare but possible), the IPAM lookup is needed MOST during recovery. Two answers:

1. **Offline copy in Git**: [`docs/architecture/network.md`](../architecture/network.md) maintains a human-readable IP table that's the authoritative copy when NetBox is unreachable. Kept in sync with NetBox manually (low churn — IPs rarely change without a corresponding doc update).
2. **Recovery LXC** (Phase 5i.2, deferred): fully isolated tier — local PG restored from `pg_dump`-via-PBS, local Redis, local admin auth (no OIDC), `MAINTENANCE_MODE=True`. Zero dependencies on K3s, Patroni, HAProxy, Authentik, Vault. TF + Ansible recipe ready but LXC not pre-provisioned. Operator runs a documented runbook (to be written at `docs/procedures/netbox-recovery.md`) when needed.

The recovery LXC + offline-Git pair satisfies the "IPAM available during cluster outage" goal without making the primary cross-cluster (which would just shift the dependency, not eliminate it).

## Operational notes

- **Chart `install.remediation.retries: -1`** during initial deploy (because mid-migration uninstall-on-failure leaves orphan PVCs + partially-migrated DB rows). Restored to standard `3` post-deploy. Re-enable `-1` again on any major NetBox version upgrade.
- **Migrations run in main container entrypoint**, NOT a separate pre-install Job. First deploy takes 5-10min before pod reaches Ready. `timeout: 15m` on the HelmRelease.
- **`/etc/netbox/config/`** is NetBox's config dir — lexical-order `.py` load. Custom configs (via chart's `extraConfig.values:`) land at index 0; our OIDC secret goes via `extraVolumes`+`subPath` at `99-oidc.py` so it loads last + overrides defaults.
- **`bytes` operator can be ignored.** The Bitnami valkey sub-chart uses `bitnami/valkey:latest` (public registry, public-restricted catalog not relevant); if Bitnami's image policy changes in the future, repin to a stable tag or migrate to a different Valkey chart.

## Pending follow-ups

- **Phase 5i.3** — TF→NetBox standing pattern via `e-breuninger/netbox` provider. Next phase, NOT deferred indefinitely.
- **Phase 5i.2** — Recovery LXC. Deferred until 5i.3 is stable.
- **Phase 5i.4** — NetBox → Ansible dynamic inventory. Deferred until AWX is on jotunheim K3s.
- **SOCIAL_AUTH_PIPELINE override** for OIDC group→permission sync. Trigger: >3 OIDC users.
- **Media PVC** if device photos start landing — options documented inline in helmrelease.yaml.
