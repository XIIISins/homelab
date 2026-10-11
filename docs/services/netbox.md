<!-- docs/services/netbox.md -->

# NetBox (IPAM/DCIM)

NetBox in asgard K3s, internal-only via Traefik at `netbox.niflheim.xiiisins.com` (deployed 2026-05-24). The queryable view of devices, VMs/LXCs, IPs, VLANs, prefixes, sites and clusters, and the source of the Ansible dynamic inventory. **Git stays the IaC spec**: Terraform writes NetBox (`terraform/netbox/`), people don't, apart from per-entry notes and tags. Retros: [5i](../incidents/2026-05-24-5i-netbox.md), [TF retrofit](../incidents/2026-05-24-5i3-tf-netbox-retrofit.md); gotchas (including provider quirks): [`known-issues/netbox.md`](../known-issues/netbox.md).

## Architecture

| Layer | Stack | Notes |
|-------|-------|-------|
| Chart | `oci://ghcr.io/netbox-community/netbox-chart` | OCI, not the legacy HTTPS index. Chart and image tag are pinned in `k8s/asgard/apps/netbox/helmrelease.yaml`. |
| App | NetBox community edition, **held on the 4.6 line** | No released `e-breuninger/netbox` provider supports 4.7 and 4.7's migration is irreversible. Move once the provider does, after adding the `ltree` extension to `postgres_databases[netbox].extensions` ([`chart-bumps-2026-09.md`](../plans/active/chart-bumps-2026-09.md) §8). |
| Web server | granian (Rust ASGI) | Not gunicorn (changed in 4.6). |
| Cache and RQ | Bundled Valkey (sub-chart), `standalone`, `emptyDir` | Cache-class, rebuilds on restart. One pod. |
| Database | External PG via HAProxy VIP `10.0.10.210:5432` | `sslmode=require`, `target_session_attrs=read-write`, `connMaxAge: 300`. |
| Auth | Authentik OIDC + local superuser break-glass | python-social-auth backend, not django-allauth. |
| Media | `emptyDir` | The chart shares one RWO PVC between server and worker; Synology iSCSI is RWO-only, which deadlocks anti-affinity. Fine until device photos land; options are in the HelmRelease. Reports and scripts persistence are off. |
| Edge | HTTPRoute on the niflheim Gateway → ClusterIP Service | Internal-only: no midgard listener, no tunnel. |
| In-cluster ↔ Authentik | `coredns-custom` rewrites `authentik.midgard.xiiisins.com` to `traefik.traefik.svc.cluster.local` | Avoids MetalLB-VIP tromboning for the pod's OIDC backchannel; Traefik routes by SNI. |

**Sizing.** `resourcesPreset: "none"` with explicit values. Server requests 360m / 1968 Mi, limits 1500m / 2560 Mi; worker requests 50m / 384 Mi, limits 750m / 1536 Mi. The memory floor follows measurement (the server ran at 1521 Mi of a 1536 Mi limit on 2026-10-05, and was OOMKilled during a `terraform apply` on 2026-10-01). The 1500m CPU limit is for bursts: a `terraform apply` state refresh against `/api/*` can peg granian for tens of seconds, which trips the 1 s readiness probe and drops the pod from endpoints while it stays "Running". Keep the CPU *request* low (workers are CPU-requested at 73–83%, which deadlocks surge rollouts: [`k8s-scheduling.md`](../known-issues/k8s-scheduling.md)). Run `terraform` here with `-parallelism=2`.

**Why internal-only.** Full topology, IP allocations and device descriptions are reconnaissance gold; even SSO-gated, no public ingress is a worthwhile reduction. Remote access is via the tailnet plus AdGuard split DNS.

## Secrets

| Vault path | K8s Secret | Used by |
|------------|------------|---------|
| `ansible/postgres/netbox-password` | (Ansible only) | `postgres-common` creates the DB user |
| `k8s/netbox/postgres-password` | `netbox-db` | chart `externalDatabase.existingSecretName` |
| `k8s/netbox/app` (`secret_key`) | `netbox-app` | chart `existingSecret` (Django SECRET_KEY) |
| `k8s/netbox/api-token-peppers` (`peppers_json`) | `netbox-app` | Pepper for v2 API tokens; without it every token is rejected. Losing it invalidates all existing tokens (1P copy: "Asgard - NetBox - API token peppers") |
| `k8s/netbox/superuser` (`password`, `api_token`) | `netbox-superuser` | chart `superuser.existingSecret` |
| `k8s/netbox/oidc-client-secret` (`client_id`, `client_secret`) | `netbox-oidc`, rendered as `99-oidc.py` | mounted via `extraVolumes` + `subPath` at `/etc/netbox/config/99-oidc.py` |
| `ansible/netbox/inventory-token` (`value`) | n/a | the dynamic inventory plugin |

The PG password is written to both `ansible/postgres/…` and `k8s/netbox/…` by one `random_password` mint, because the Ansible AppRole can't read `k8s/` paths. The OIDC file is mounted with `subPath` (mounting the volume over the directory would clobber `configuration.py` and `extra.py`) and named `99-` so it loads last in NetBox's lexical `.py` order. The Terraform provider authenticates with the admin API token from 1Password ("Asgard - NetBox - admin API token"; no dedicated TF user).

## Authentication chain

1. `netbox.niflheim.xiiisins.com` resolves via AGH to the Traefik VIP `10.0.20.10`; the HTTPRoute serves the login page.
2. "Log in via OIDC" redirects to `https://authentik.midgard.xiiisins.com/application/o/authorize/?client_id=netbox…`. The OIDC endpoint must equal the discovery `issuer` (`…/application/o/netbox`) or python-social-auth rejects the JWT `iss`.
3. Authentik checks the `netbox-admins` / `netbox-viewers` policy bindings (`policy_engine_mode: any`) and redirects back to `/oauth/complete/oidc/`.
4. The pod's backchannel (discovery, token exchange, userinfo) goes through the CoreDNS rewrite to Traefik's ClusterIP.
5. NetBox auto-creates the user (`autoCreateUser: true`) and sets the session.

**Permissions caveat.** OIDC-created users get **zero** NetBox permissions. Elevate manually from the local superuser (Admin → Users → Active/Staff/Superuser). Automating group-claim → permission mapping needs a `SOCIAL_AUTH_PIPELINE` override; deferred until elevation becomes recurring toil (more than ~3 users).

## Source of truth

| Data | Spec lives in | NetBox role |
|------|---------------|-------------|
| LXC/VM existence, sizing, placement | `terraform/proxmox/*` | `terraform/netbox/vms.tf` mirrors each guest. Every new LXC/VM needs `netbox_virtual_machine` + `netbox_interface` + `netbox_ip_address`; physical devices go in `devices.tf`. |
| IP allocations | Terraform locals | Same module; the VIPs are in `vips.tf` |
| K8s workloads | `k8s/asgard/**` | Not in NetBox (IPAM/DCIM, not K8s state) |
| Ansible inventory | NetBox via `ansible/inventory/netbox.yml` (`nb_inventory`, cached) plus the static `hosts.yml` as DR fallback | `ansible<group>` tags become inventory groups; Semaphore and `drift-check` consume it |
| Descriptions, tags, per-entry notes | NetBox UI | Hand-entered; preserved by the import-based retrofit |

When a guest is added, the Proxmox and NetBox resources land in one apply. If NetBox is down the apply errors on the NetBox resources but the guest exists, and a re-run resumes (acceptable for a single operator).

## Recovery model

NetBox shares K3s' failure domain with Patroni, Authentik and Vault, yet IPAM is needed most during recovery. So:

1. **Offline copy in Git:** [`network.md`](../architecture/network.md) holds the human-readable IP table, authoritative when NetBox is unreachable (low churn, kept in sync by hand). The static `hosts.yml` backs the inventory.
2. **Recovery LXC (Phase 5i.2, deferred, tracked in [`open-questions.md`](../operations/open-questions.md)):** a fully isolated tier with local PG restored from `pg_dump` via PBS, local Redis, local admin auth, `MAINTENANCE_MODE=True`, and no dependency on K3s, Patroni, HAProxy, Authentik or Vault. The runbook is unwritten.

## Operational notes

- First deploys take 5–10 min: migrations run in the main container's entrypoint, not a pre-install Job (`timeout: 15m` on the HelmRelease). During a first deploy or a major-version upgrade set `install.remediation.retries: -1` (mid-migration rollback leaves orphan PVCs and half-migrated rows); the steady state is `3`.
- The chart's `extraConfig.values` land at index 0, so non-secret OIDC settings (endpoint, scopes, logout URL) go there and the secret goes through the `99-` file.
- The bundled Valkey sub-chart pulls a Bitnami image; if Bitnami's image policy changes, repin a stable tag or switch Valkey charts.

## Not done

- **OIDC group → permission sync** (`SOCIAL_AUTH_PIPELINE` override), trigger above.
- **Media PVC** if device photos start landing.
- **Pod memory limit / `terraform` parallelism** hygiene: see [`open-questions.md`](../operations/open-questions.md).
