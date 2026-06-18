<!-- docs/known-issues/README.md -->

# Known issues / gotchas

Per-subject catalog of operational gotchas — rules, symptoms, diagnostics, recovery commands. Migrated out of `CLAUDE.md` (2026-06-18) so the runtime context file stays lean; read the relevant file **before** working on its subject (the `CLAUDE.md` pre-flight checklist points here).

Each entry is a hard-won fact. Incident retrospectives live in [`../incidents/`](../incidents/); architectural rationale in [`../operations/decisions.md`](../operations/decisions.md).

## Index

| Subject | File | When to read |
|---|---|---|
| Networking / multi-homed workers | [networking-multi-homed-workers.md](networking-multi-homed-workers.md) | Calico, MetalLB, rp_filter, policy routing, K3s-host→VIP reachability |
| Storage — iSCSI / Synology CSI / local-path / migration | [storage-iscsi-synology.md](storage-iscsi-synology.md) | Any PVC/LUN work, the DS223J LUN cap, fsck, rsync tier migration, NAS volume deletes |
| Garage (S3 object store) | [garage.md](garage.md) | Garage layout, admin API, jkossis/garage provider |
| K3s lifecycle / rebuilds | [k3s-lifecycle.md](k3s-lifecycle.md) | CP/worker rebuilds, taints, install-skip, Raft-quorum drains |
| Vault | [vault.md](vault.md) | Vault HA, Raft, AppRoles, token expiry, snapshots, OIDC |
| Flux / Helm / Kustomize | [flux-helm-kustomize.md](flux-helm-kustomize.md) | HelmRelease remediation, CRD timing, ConfigMap reloads, ESO refresh |
| K8s scheduling | [k8s-scheduling.md](k8s-scheduling.md) | anti-affinity rollouts, runAsNonRoot numeric UID, StatefulSet stalls |
| Traefik / Gateway API | [traefik-gateway-api.md](traefik-gateway-api.md) | Traefik chart values, Gateway listener ports |
| Authentik | [authentik.md](authentik.md) | OIDC/SAML providers, brand vs outpost host, per-app group gates |
| Cloudflare / Cloudflared | [cloudflare.md](cloudflare.md) | Tunnel origins, token scopes/rotation, ruleset imports |
| DNS / AdGuard Home | [dns-adguard.md](dns-adguard.md) | AGH rewrites, NXDOMAIN caching, coredns-custom, sync, admin user |
| Postgres | [postgres.md](postgres.md) | Patroni adoption, pg_hba, sslmode, backups from leader, DB provisioning |
| HAProxy / keepalived | [haproxy-keepalived.md](haproxy-keepalived.md) | VRID, VIP policy routing, leader-detect httpchk, reload self-heal |
| NetBox (+ TF provider) | [netbox.md](netbox.md) | netbox-chart values, OIDC, tokens, e-breuninger/netbox provider quirks |
| Frigg / control-node watchtower | [frigg-control-node.md](frigg-control-node.md) | hvac-fork crash, the Frigg shim, remote ansible, HA, claude remote-control |
| Ansible / roles | [ansible-roles.md](ansible-roles.md) | group_vars precedence, ssh_args, tasks_from/include_tasks, strict-mode bools |
| LXC / Proxmox (+ PVE host patching) | [lxc-proxmox.md](lxc-proxmox.md) | bpg provider auth, TF import drift, nesting, orphan LVs, host-patch playbook |
| Tailscale | [tailscale.md](tailscale.md) | authkey TTL, DNS resources, subnet-router LAN-cut, accept-dns hijack |
| SSH / system | [ssh-system.md](ssh-system.md) | hostkey loss, crash detection, known_hosts after rebuild |
| Shell / tooling | [shell-tooling.md](shell-tooling.md) | BSD sed `\s`, tar strip-components, fish heredocs, PATH, secret-redaction |
| Terraform / state | [terraform-state.md](terraform-state.md) | S3 backend migration, bootstrap AWS identity for `terraform/aws/` |
| Observability (VM/vmagent/vmui/vlagent) | [observability.md](observability.md) | scrape RBAC, cAdvisor labels, vmui dashboards, off-cluster log shipper |
| SFTPGo / Factorio | [sftpgo-factorio.md](sftpgo-factorio.md) | SFTPGo sqlite path, Factorio reconcile timer/ownership |
| Zabbix (server / agent-API / SAML / S4 prober) | [zabbix.md](zabbix.md) | repo URL, default creds/lockout, community.zabbix module shapes, SAML, infra-health-check |
| Caddy reverse-proxy role | [caddy.md](caddy.md) | log-dir ownership, Cloudsmith pin roll-forward |
| Semaphore (Ansible scheduler) | [semaphore.md](semaphore.md) | PG backend, collection skew/custom image, Vault config keys, inventory cache |
| Outline (wiki) | [outline.md](outline.md) | image path, Recreate strategy, OIDC callback, per-consumer Redis |
| MicroBin (pastebin / file-share) | [microbin.md](microbin.md) | hardcoded DB path, route names, partial-auth gating |

## Maintenance

New gotchas land in the matching file (create one if the subject is new, add a row above). Keep `CLAUDE.md` carrying only the **index pointer**, not the gotcha text — that's the whole point of this directory. When a gotcha generalises across subjects, put it in the most-specific file and cross-link with a one-liner from the others.
