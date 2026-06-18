<!-- docs/services/asgard-k3s.md -->

# Asgard K3s cluster

Production cluster — core infrastructure (Vault, MetalLB, etc.), automation (AWX, Tofu Controller), and services whose absence either cascades into other failures or blocks recovery. Resiliency > simplicity.

**Status (2026-05-22 evening):** Core infrastructure ✅ running and stable, **cluster edge stack now live**. Cluster was fully torn down and rebuilt on 2026-05-17 morning as a deliberate validation exercise — see [incident log](../incidents/README.md). Rebuild confirmed end-to-end IaC works (Terraform → Ansible → Flux) and surfaced 9+ structural gaps that have since been closed. Authentik + hand-rolled Redis deployed 2026-05-17 evening; Phase 4a (CP taints), Phase 4b (Göndul Verd → Urd), einherjar-urd worker rebuild, and Phase 5e.1 (Traefik + Gateway API + cert-manager + HTTPS cutover) all landed 2026-05-21 / 2026-05-22. AWX/Tofu Controller + core services next.

**Core infrastructure (cascade failure if down):**

| Service | Replicas | Status |
|---------|----------|--------|
| Vault | 3 (Raft HA) | ✅ Running, AWS KMS auto-unseal. K8s + AppRole auth + KV engine + test entry in Terraform (`terraform/vault/`). Re-init recovery procedure validated 2026-05-17. |
| Authentik server | 3 | ✅ Deployed 2026-05-17. Chart 2026.2.3. **Currently exposed via Traefik+Gateway at `https://authentik.niflheim.xiiisins.com`** (5e.1 cutover 2026-05-22); original LoadBalancer on `.12` released. External Postgres pointed at Fulla. Day-1 blueprints in Git (niflheim brand + personal admin user). **FQDN migration in 5e.2:** `authentik.niflheim.xiiisins.com` → `authentik.xiiisins.com` (external, via Cloudflared) + `authentik.midgard.xiiisins.com` (internal alias) — the `niflheim` zone is wrong for a publicly-reachable service. |
| Authentik worker | 1 | ✅ Migrations + blueprint reconciliation run from here. |
| Redis | 1 | ✅ Hand-rolled StatefulSet (`redis:7-alpine`, AOF persistence, 1Gi iSCSI PVC). NOT the Bitnami sub-chart — the 2026.x Authentik chart dropped its bundled Redis. |
| MetalLB | DaemonSet | ✅ L2 working end-to-end. VIP reachable from outside the cluster. Required nodeSelectors, Calico autodetection pin, rp_filter loose, route_localnet=1, and VLAN 20 source-based policy routing — all IaC'd. Pool `.10–.99` (extended `.11` → `.10` for Traefik 5e.1). |
| Synology CSI (core) | 1 | ✅ iSCSI, synology-csi-iscsi-retain. SealedSecret split into `synology-csi-config/` Kustomization. |
| External Secrets Operator | 1 | ✅ ClusterSecretStore `vault` Ready. Authentik secrets in `secret/k8s/authentik/*` synced via ESO. Cloudflare DNS-01 token in `secret/k8s/cert-manager/cloudflare`. |
| Sealed Secrets | 1 | ✅ Master keys backed up to 1Password as of 2026-05-17. |
| tigera-operator (Calico) | 1 | ✅ Fixed 2026-05-15 via MTU explicit workaround (upstream issue #7851) |
| Gateway API CRDs | — | ✅ v1.5.1 Standard channel, vendored from upstream into `infrastructure/gateway-api/` (deployed 5e.1.b, 2026-05-22). |
| cert-manager | 2 (HA pairs) | ✅ v1.19.0, `enableGatewayAPI: true`, chart-installed CRDs (`crds.enabled: true, keep: true`). ClusterIssuers `letsencrypt-staging` + `letsencrypt-prod` (Cloudflare DNS-01, zone-scoped to `xiiisins.com`). Deployed 5e.1.c–d, 2026-05-22. |
| Traefik | 3 (1/worker, required anti-affinity) | ✅ v40.2.0 chart / v3.7.1 proxy, Gateway API provider only (no IngressRoute, no Ingress; `kubernetesCRD` enabled for Middleware CRs). `NET_BIND_SERVICE` for direct 80/443 binding. MetalLB LB on `10.0.20.10`. `externalTrafficPolicy: Local` + 1 pod/worker for source IP preservation. `maxSurge: 0` mandatory rollout strategy. Deployed 5e.1.e, 2026-05-22. |
| Gateway `niflheim` | — | ✅ Single shared Gateway in `traefik` namespace, GatewayClass `traefik`, web + websecure listeners, `allowedRoutes.namespaces.from: All`. Wildcard cert ref via Secret `wildcard-niflheim-tls`. Internal-only routes only. Deployed 5e.1.f, 2026-05-22. |
| Gateway `midgard` | — | 🔲 — Second Gateway, planned 5e.2. Hosts apex (`*.xiiisins.com`) + midgard (`*.midgard.xiiisins.com`) listeners. Cloudflared backend targeting + internal AdGuard rewrites land HTTPRoutes here. Separated from `niflheim` Gateway by design to keep public-vs-internal route attachment policies distinct (decision 2026-05-22). |
| Wildcard `*.niflheim.xiiisins.com` | — | ✅ + apex `niflheim.xiiisins.com` SAN, ECDSA P-256, 90d duration / 30d renewBefore. Issued by `letsencrypt-prod` (E8 intermediate). Deployed 5e.1.f, switched staging → prod in 5e.1.h, 2026-05-22. |
| Wildcard `*.midgard.xiiisins.com` | — | 🔲 — Planned 5e.2. Same issuer + token + algo as the niflheim wildcard. Covers internal aliases of publicly-reachable services. |
| Wildcard `*.xiiisins.com` (apex) | — | 🔲 — Planned 5e.2. Origin-side cert for Cloudflared traffic (browser-visible cert is Cloudflare's universal cert; this is what Cloudflared connects to Traefik with). |
| Cloudflared | 1+ | 🔲 — Phase 5e.2. Cloudflare Tunnel for selected external exposure. Targets backend Services by ClusterIP DNS, never via MetalLB IPs. Tunnel credentials via ESO from Vault. |
| WebFinger middleware | — | 🔲 — Phase 5e.2. Traefik Middleware with static-response plugin at `xiiisins.com/.well-known/webfinger`, returning the Tailscale OIDC issuer pointer JSON. Lives on the `midgard` Gateway. |

**Automation (the git-push-and-walk-away path):**

| Service | Replicas | Status |
|---------|----------|--------|
| AWX | 1 | 🔲 — Ansible CI/CD. `ansible-awx` AppRole role already configured in Vault, SecretID generated at deploy time |
| Tofu Controller | 1 | 🔲 — Terraform/OpenTofu GitOps via Flux. Flux-native (flux-iac org, formerly Weave TF-Controller). Push to main → controller reconciles |

**Core services (production, expected to work):**

| Service | Status |
|---------|--------|
| Outline | 🔲 — wiki / knowledge base |
| Immich | 🔲 — photos/videos |
| Grafana | 🔲 — dashboards (sourced from VictoriaMetrics/VictoriaLogs) |
| VictoriaMetrics | 🔲 — metrics store (PromQL-compatible) |
| VictoriaLogs | 🔲 — log aggregation |
| Netbox | 🔲 — IPAM/DCIM |
| n8n | 🔲 — workflow automation |
| Privatebin | 🔲 — secure paste service |
| Startpage | 🔲 — personal browser homepage (most-used; promoted to asgard) |

**VMs:**

| Name | VM ID | Node | IP | Role | Spec |
|------|-------|------|----|------|------|
| Göndul | 2001 | Urd | `10.0.21.11` | K3s CP | 2vCPU/4GB/10GB |
| Hlökk | 2002 | Verd | `10.0.21.12` | K3s CP | 2vCPU/4GB/10GB |
| Sigrún | 2003 | Skuld | `10.0.21.13` | K3s CP | 2vCPU/4GB/10GB |
| Einherjar-urd | 2101 | Urd | `10.0.21.21` | K3s Worker | 2vCPU/16GB/30GB OS + 50GB `scsi1` (local-path) |
| Einherjar-verd | 2102 | Verd | `10.0.21.22` | K3s Worker | 2vCPU/16GB/30GB OS + 50GB `scsi1` (local-path) |
| Einherjar-skuld | 2103 | Skuld | `10.0.21.23` | K3s Worker | 2vCPU/16GB/30GB OS + 50GB `scsi1` (local-path) |

CP cpu/memory parameterized per-node in `locals.control_planes` map (`terraform/proxmox/asgard-k3s/main.tf`). All three CPs sized identically — failover symmetry requires it (same rule as PG nodes). Bumped 1vCPU/2GB → 2vCPU/4GB on 2026-05-17 evening when the Authentik deploy revealed hlokk and sigrun couldn't handle the migration+blueprint burst. **CPs will be tainted `node-role.kubernetes.io/control-plane:NoSchedule` in Phase 4a (scheduled 2026-05-18)** — workload pods cannot land on them under any condition once that lands. The 4 GiB sizing is correct *with* the taint: control-plane working set is ~1.5-2 GiB, the rest is bursty kernel + buff/cache headroom. Without the taint, 4 GiB would be the bare-minimum-and-things-still-break number (tonight proved that). Promote sizing into per-node overrides only when deliberate per-CP tuning is needed.

**Workers have dual NICs:**
- eth0: VLAN 21 (K3s node traffic)
- eth1: VLAN 20 (MetalLB L2, IPs 10.0.20.201/202/203)
- ⚠️ The second NIC is a known landmine — see [incident log](../incidents/README.md). Required IaC: Calico autodetection pin (`cidrs: ["10.0.21.0/24"]`), rp_filter loose mode, route_localnet=1, and VLAN 20 source-based policy routing. All four in `roles/k3s/tasks/network.yml`.

**K3s install (Ansible `k3s` role — fully IaC):**
- Role task order: `prerequisites.yml` → `network.yml` (sysctls + VLAN 20 policy routing) → `detect-state.yml` (skip-install gate) → `install.yml` → `calico.yml` (init node only).
- `prerequisites.yml` — Rancher k3s-selinux repo, `iscsi-initiator-utils` + `iscsid`, `br_netfilter`/`overlay` modules (loaded + persisted), `ip_forward=1`, bridge-nf sysctls, swap off, `firewalld` disabled.
- `network.yml` — sysctls `rp_filter=2` / `route_localnet=1`, plus the `vlan20-policy-routing.service` systemd unit on workers (OS-independent, pure ip(8) + systemd).
- `detect-state.yml` — sets `k3s_already_healthy` if `systemctl is-active k3s == 'active'` AND the node is `Ready` in the cluster. When true, `install.yml` and `calico.yml` are skipped. Required for idempotent re-runs; without it, the restart-k3s handler can fire on a healthy CP and trigger duplicate-join failure.
- `install.yml` — binary from GitHub (`k3s_version`, currently `v1.33.1+k3s1`); bootstrap order init-node (`--cluster-init`) → joining CPs → workers, gated by `wait_for`/node-count checks; token slurped from init node and distributed; kubeconfig fetched to `~/.kube/niflheim-asgard.yaml`.
- Config templates: `config-init.j2` / `config-server.j2` (CPs — disable traefik/servicelb/local-storage, `flannel-backend: none`, `disable-network-policy: true`, cluster/service CIDRs, TLS SANs, `selinux: true`) / `config-agent.j2` (workers — minimal: server + token + selinux).
- CP rebuild scenario: override `k3s_init_node` if rebuilding the default init node (`-e k3s_init_node=hlokk`), and `kubectl delete node <name>` first to remove the stale etcd member. See Known gotchas.
- No node taints or labels are set — CP taint is a manual pending task.

**Calico CNI — NOT Flux-managed.** Installed as a K3s addon via `ansible/roles/k3s/tasks/calico.yml` (runs only on the init node): Tigera operator manifest + an `Installation` CR templated from `calico-installation.yaml.j2` to `/var/lib/rancher/k3s/server/manifests/`. Key config: `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]` (pins overlay to VLAN 21), `mtu: 1450` (workaround for projectcalico/calico#7851 — operator can't read `/var/lib/calico/mtu` under SELinux), `encapsulation: VXLANCrossSubnet`, pod CIDR `10.42.0.0/16`, `calico_version` currently `v3.29.3`. The K3s addon controller *merges* the CR — removed fields can persist; verify after changes.

**Flux Kustomization structure:**
- `infrastructure` — installs HelmReleases (sealed-secrets, synology-csi, vault, external-secrets, metallb). `interval: 10m`, `prune: true`, sourceRef `GitRepository/flux-system`. No `wait`/`timeout` set.
- `infrastructure-config` — configures ESO (ClusterSecretStore), `dependsOn: [infrastructure]`.
- `metallb-config` — configures MetalLB (IPAddressPool, L2Advertisement), `dependsOn: [infrastructure]`. Split from `infrastructure-config` after the 2026-05-14 incident where an ESO webhook failure blocked MetalLB config reconcile (shared failure domain).
- `vault-config` — vault-unseal SealedSecret, `dependsOn: [infrastructure]`. Split from `infrastructure/` on 2026-05-17 — SealedSecret resources can't live alongside the sealed-secrets HelmRelease (CRD doesn't exist at dry-run time).
- `synology-csi-config` — synology-csi SealedSecret, `dependsOn: [infrastructure]`. Same reason as vault-config.

The per-component-config pattern is now the standard: every CRD-dependent resource gets its own `<component>-config/` Kustomization that `dependsOn: infrastructure`. No more bundles sharing failure domains.

**HelmRelease chart versions** are currently `version: "0.x"` placeholders across metallb / external-secrets / vault / sealed-secrets — a deliberate temporary state. Real pinning + Renovate is planned once the homelab reaches a working "2.0" state.

**Fallback documentation:** static HTML file on Munin with recovery procedures, IPs, and commands. Accessible even if both K3s clusters are down. (Not yet created — pending task.)
