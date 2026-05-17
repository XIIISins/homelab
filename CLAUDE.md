# Homelab — Claude Code context
*Read this before touching anything. Full design at `docs/homelab-design.md`.*

---

## What this is

A ground-up homelab rebuild on 3 physical nodes. Goals:
- Reliable services for friends/family (Teamspeak, Factorio, Immich)
- Learning environment for Kubernetes and modern infrastructure
- Portfolio/resume project at senior/principal infrastructure level

Owner has 10+ years Ansible experience, career in IT/sysadmin/platform engineering. Kubernetes is the primary learning goal.

---

## Hardware

| Host | CPU | RAM | Disk | Norse name |
|------|-----|-----|------|------------|
| MINISFORUM DeskMini JB95 | Celeron N5095 | 32 GB | 120GB SSD (~94GB LVM-thin) | **Urd** |
| Beelink MINI-S12 | N100 | 16 GB | 1TB (~950GB LVM-thin) | **Verd** |
| Beelink MINI-S12 | N100 | 16 GB | ~450GB LVM-thin | **Skuld** |
| Synology DS223J | — | — | 3.5TB RAID1 | **Munin** |

All 1 GbE. No 2.5 GbE planned.

**Hardware notes:**
- Urd (N5095) is significantly weaker than Verd/Skuld (N100). Urd should NOT run K3s control plane nodes — etcd is very sensitive to disk IO latency and the N5095 causes IO storms under load.
- Urd long-term plan: dedicated Jellyfin box with Intel QuickSync passthrough. Currently runs Einherjar-urd (K3s worker) and the Factorio LXC (1120).
- Göndul moved Urd → Verd on 2026-05-17 (was deferred since the 2026-05-14 incident; finally fixed during the asgard rebuild). Also bumped to 2vCPU/4GB at the same time to absorb load that previously OOM'd it.
- **2026-05-14 incident context:** An etcd IO storm on Göndul (Urd/N5095) cascaded into a multi-hour outage. This is the concrete event behind "never run CP on Urd" — see Known gotchas and `homelab-design.md` incident log. The 2026-05-17 rebuild confirmed the diagnosis empirically: bumping CP memory alone wasn't enough during recovery churn; moving to Verd was.

---

## Naming convention

| Thing | Name | Reason |
|-------|------|--------|
| Proxmox cluster | `niflheim` | Hidden realm |
| Proxmox node 1 | Urd | Eldest Norn, most powerful |
| Proxmox node 2 | Verd | Second Norn |
| Proxmox node 3 | Skuld | Third Norn |
| NAS | Munin | Odin's raven of memory |
| Asgard K3s CP | Göndul, Hlökk, Sigrún | Valkyries (choosers/directors) |
| Asgard K3s workers | Einherjar-urd/verd/skuld | Army of the Norns |
| Jotunheim K3s CP | Rota, Hildr, Kára | Valkyries |
| Jotunheim K3s workers | Drengr-urd/verd/skuld | Heroes of the Norns |
| AdGuard primary | Saga | Goddess of wisdom/seeing |
| AdGuard replica 1 | Mimir | Keeper of wisdom |
| AdGuard replica 2 | Kvasir | Wisest being |
| Asgard PostgreSQL | Fulla, Vör, Idunn | Frigg's handmaidens + Idunn the keeper. Meta-principle: primary defines theme, replicas expand within it. |
| Public DNS zone | `midgard.xiiisins.com` | The known world |
| Private DNS zone | `niflheim.xiiisins.com` | The hidden realm |

---

## Critical architectural decisions — never second-guess without asking

**Two K3s clusters:**
- Asgard K3s (VLAN 21) — core infrastructure + automation + production services. Cascade-failure criterion *or* recovery-blocking criterion. Resiliency > simplicity.
- Jotunheim K3s (VLAN 31) — non-cascade-critical services + genuine experiments. Failure acceptable (hours-to-days downtime). NOT "experimental only" — most jotunheim services have real users; the line is failure-domain risk, not production/experimental.
- Do NOT suggest merging them

**Asgard K3s contents:**
- *Core infrastructure:* Vault, Authentik (server ×3 + worker ×1), Redis, MetalLB, Synology CSI, ESO, Sealed Secrets, tigera-operator, Traefik, cert-manager, Cloudflared
- *Automation:* AWX (Ansible CI), Tofu Controller (Terraform GitOps via Flux)
- *Core services:* Outline, Immich, Grafana, VictoriaMetrics, VictoriaLogs, Netbox, n8n, Privatebin, Startpage

**Jotunheim K3s contents:** Arr stack, Komga, Homepage, wallpaper gallery, second instances of ESO and Synology CSI, plus ad-hoc experiments. Note: Startpage (personal browser homepage, daily-use) is in asgard; Homepage (service-grid dashboard) is in jotunheim — distinct services.

**No Docker Swarm. K3s only.**

**No Galera. PostgreSQL only.**
Zabbix migrated to PostgreSQL. Nothing requires MySQL.

**GitOps: Flux CD.**
Push to Git → exists. No ArgoCD. No manual `kubectl apply` for production.
Layered Flux Kustomizations: `infrastructure` installs operators/charts; per-component config Kustomizations (`infrastructure-config` for ESO, `metallb-config` for MetalLB, `vault-config` for the vault-unseal SealedSecret, `synology-csi-config` for the synology-csi SealedSecret) configure CRD-dependent resources with `dependsOn: infrastructure` so the CRDs exist first. Add a new config Kustomization per operator rather than bundling — shared bundles share failure domains (an ESO webhook failure blocked MetalLB config reconcile during the 2026-05-14 incident; mixing SealedSecrets into `infrastructure/` itself caused the asgard rebuild to fail on CRD timing). The naming convention is `<component>-config/`.

**Calico CNI is NOT Flux-managed.**
Calico is installed as a K3s auto-deploy addon: the Tigera operator manifest plus an `Installation` CR templated by Ansible to `/var/lib/rancher/k3s/server/manifests/calico-installation.yaml` (source: `ansible/roles/k3s/templates/calico-installation.yaml.j2`, applied by `ansible/roles/k3s/tasks/calico.yml`, runs only on `k3s_init_node`). The K3s addon controller applies it; the Tigera operator acts on it. To change Calico config, edit the Ansible template and re-run the playbook — `kubectl edit` on the live `Installation` gets reverted by the addon controller.

**DNS: AdGuard Home (not Pi-hole).**
Three LXCs, keepalived VIP at `10.0.10.200`. AGH Sync binary on Saga. Do not suggest Pi-hole.

**Identity: Authentik in asgard K3s.**
OIDC for web apps, LDAP for SSH via SSSD. Local admin accounts on all services as break-glass. Do not suggest Authelia.

**Secrets: two access patterns, three stores.**
- **1Password "Homelab" vault** (humans) — anything a human looks up: web admin passwords, API tokens pasted into configs, LXC root passwords for templates, homelab-hosted DB admin credentials, AppRole credentials for the MacBook control node. Also break-glass user keys and AWS KMS unseal token (already external by design).
- **HashiCorp Vault (asgard K3s, runtime)** — anything a machine pulls at runtime: K8s workload secrets via ESO, Ansible role lookups via AppRole, future runtime-retrieval targets. 3-node Raft HA, AWS KMS auto-unseal, iSCSI storage (Synology CSI, 5Gi PVC, `synology-csi-iscsi-retain`).
- **Ansible Vault (machines, bootstrap)** — secrets needed *before* HashiCorp Vault is reachable: `k3s_token`, RHEL subscription keys, SSH public keys for ansible/break-glass users. Narrow scope by design — only what's required to bootstrap a fresh node up to the point where HashiCorp Vault becomes usable.

The rule: *Human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

The bootstrap-vs-runtime split solves the circular dependency: HashiCorp Vault lives in asgard K3s, so anything K3s itself needs to come up cannot live there.

Scope of homelab secret stores: "things that exist because the homelab exists." Personal credentials, external service accounts, and infrastructure under the homelab (bare-metal node root passwords, NAS admin, UCG-Ultra, KPN router) live in 1Password but outside the Homelab vault — they're personal/external, not homelab.

Vault Kubernetes auth method, `eso` policy, `eso` role, AppRole auth method, `ansible` policy, and the `ansible-local`/`ansible-awx` AppRole roles captured in Terraform (`terraform/vault/`) on 2026-05-16. SecretIDs are NEVER in Terraform state — generated manually, stored in 1Password + local env file. See homelab-design.md AppRole bootstrap runbook.

Vault listener is plaintext (`tls_disable = 1`) — deliberate, see Known gotchas.

**Ansible Vault: bootstrap secrets only.**
Holds `k3s_token`, RHEL subscription keys, SSH pubkeys for ansible/breakglass users — the minimum needed to bring a fresh node up to the point HashiCorp Vault is reachable. All other Ansible-pulled secrets live in HashiCorp Vault under `secret/ansible/*`, retrieved via the `community.hashi_vault` lookup with AppRole auth. Proof-of-pattern: `secret/ansible/sftpgo/admin-password` (migrated 2026-05-16).

**Jellyfin: privileged LXC on Urd.**
Intel QuickSync via /dev/dri passthrough. Not in K3s.

**Storage: Synology CSI driver (christian-schlichtherle/synology-csi-chart).**
iSCSI only — NFS creates polluting shared folders on Synology. Single StorageClass: `synology-csi-iscsi-retain` (default). Two instances planned — one per K3s cluster. Not democratic-csi.
Synology CSI creates one iSCSI target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). iSCSI LUNs are single-session by default — after ungraceful node restarts, stale sessions / discovery node records can pin a LUN to the wrong node and block re-attach. Cleanup: `iscsiadm -m session` / `iscsiadm -m node -o delete` on the affected node.
NOTE: there is a vestigial iSCSI target `iqn.2000-01.com.synology:munin.k3s-core.f954439fc46` left over from an abandoned NFS-CSI attempt — it is NOT in use. The live targets are per-PVC.

**MetalLB: L2 mode.**
Workers have two NICs: eth0 (VLAN 21, K3s traffic) and eth1 (VLAN 20, MetalLB L2 advertisement).
Worker eth1 IPs: 10.0.20.201/202/203 (outside MetalLB pool 10.0.20.11-.99).
L2Advertisement restricted to eth1 interface AND restricted to non-CP nodes via `nodeSelectors` (`matchExpressions` — key `node-role.kubernetes.io/control-plane`, operator `DoesNotExist`). CP nodes have no eth1; without the nodeSelector, MetalLB's L2 election can pick a CP node and announce nowhere.
Multi-homed-worker plumbing required for VIPs to be reachable from outside the cluster, all in `roles/k3s/tasks/network.yml`:
- `rp_filter=2` (loose) on `all` and `default` — strict mode drops MetalLB traffic on eth1
- `route_localnet=1` on `all` — without this the kernel drops packets destined for the VIP because MetalLB doesn't bind the IP, only ARPs for it
- Source-based policy routing — a systemd oneshot service (`vlan20-policy-routing.service`) installs `from 10.0.20.0/24 lookup vlan20` rule + a default route via 10.0.20.1 in table `vlan20`, so replies originating from the worker's eth1 IP exit via eth1 (symmetric path) rather than the default eth0 route (asymmetric, dropped by UCG's stateful firewall)

**Monitoring: Zabbix LXC (outside K3s) + VictoriaMetrics/Logs/Grafana (jotunheim K3s).**
Zabbix stays as LXC for monitoring independence. VictoriaLogs replaces Loki. VictoriaMetrics replaces Prometheus.

**Ansible: AWX in jotunheim K3s.**
30-minute scheduled reconciliation. Vault-backed credentials.

**VM disks: local LVM-thin.**
Faster than NFS at 1 GbE.

**PBS: privileged LXC on Skuld.**
NFS bind-mounted via Proxmox host.

**Repo: private** (GitHub). Secrets never in Git regardless; SealedSecrets used for bootstrap secrets.

**Internet exposure: KPN Experia Box → UCG-Ultra DMZ.**
KPN configured as "exposed host" / DMZ pointing all unsolicited inbound (IPv4 *and* IPv6) at the UCG-Ultra's WAN IP. UCG-Ultra is the sole firewall policy boundary; KPN does outbound NAT for `192.168.2.0/24` devices (settop box, family devices) only. UCG firewall posture: `Internal → Any: Allow`, `External → Internal: Allow Return`, `Any → Any: Deny` (last). Port-forwards configured on UCG only. KPN is the one piece of infra that is NOT and will NEVER be in IaC — any change to it lives here in the docs or it doesn't exist.

**LXC management is via Terraform `asgard-lxcs` module.**
Same provider (`bpg/proxmox ~> 0.77`), same `terraform.tfvars` as `asgard-k3s/` (API token + SSH pubkey), parallel structure. To add a new asgard LXC: append to `lxcs.tf`, `terraform apply`, add the host to `inventory/hosts.yml`, write the role + playbook.

**LXC bootstrap flow.**
Day 1:
1. `terraform apply` (LXC has only root SSH, with the key from `var.ssh_public_key`)
2. `ansible-playbook -i inventory/hosts.yml playbooks/<service>-host.yml -e 'ansible_user=root' --tags baseline` (smallest root surface)
3. `ansible-playbook -i inventory/hosts.yml playbooks/<service>-host.yml` (full deploy as `ansible`, hardening locks root SSH out at the end)

Day N: just step 3. Root SSH is locked out via `AllowUsers ansible recovery`. If something breaks the ansible user, recovery is your way in (key in 1Password).

**Factorio LXC: operator self-service via SFTPGo + reconcile loop.**
LXC 1120 hosts Factorio + SFTPGo on the same host. The operator never gets shell access — they SFTP into `/factorio/` and edit JSON control files. A root-owned Python reconcile script runs as a systemd timer every 30s, reading desired state from `/factorio/control/factorio-control.json` and converging actual state (version installed, service running/stopped, restarts). The operator declares intent; the reconciler owns mechanism. This is the template pattern for future operator-managed game/voice services.

---

## Network

> **MGMT subnet is `10.0.254.0/24`.** Earlier drafts of this doc said `10.0.1.0/24` — that was wrong and nearly caused a correct iSCSI/portal address to be "fixed". VLAN 1 = `10.0.254.0/24`.

```
KPN Experia Box (192.168.2.0/24, untouched) — DMZ → UCG-Ultra WAN
  └── UCG-Ultra WAN
        ├── LAN 1 → Dumb switch (your room)
        │             ├── MacBook dock (VLAN 60, 10.0.60.10 static)
        │             ├── Game PC (VLAN 60)
        │             └── Hue bridge (VLAN 60)
        └── LAN 2 → Dumb switch (spare bedroom)
                      ├── Urd   (10.0.254.11)
                      ├── Verd  (10.0.254.12)
                      ├── Skuld (10.0.254.13)
                      └── Munin (10.0.254.20)
```

### VLANs

| VLAN | Subnet | Name | Purpose |
|------|--------|------|---------|
| 1 | `10.0.254.0/24` | HL-MGMT | Management |
| 10 | `10.0.10.0/24` | HL-ASG-VIP | Asgard VIPs |
| 11 | `10.0.11.0/24` | HL-ASG-SVC | Asgard LXCs |
| 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP | Asgard K3s MetalLB |
| 21 | `10.0.21.0/24` | HL-ASG-K3S-WRK | Asgard K3s nodes |
| 30 | `10.0.30.0/24` | HL-JOT-K3S-VIP | Jotunheim K3s MetalLB |
| 31 | `10.0.31.0/24` | HL-JOT-K3S-WRK | Jotunheim K3s nodes |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage/NFS (stable) |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

### Key IPs
- `10.0.254.1` — UCG-Ultra
- `10.0.254.11/12/13` — Urd/Verd/Skuld
- `10.0.254.20` — Munin (Synology)
- `10.0.10.200` — AdGuard VIP ✅
- `10.0.11.20` — PBS (LXC 1101) ✅
- `10.0.11.201/202/203` — Saga/Mimir/Kvasir ✅
- `10.0.21.11/12/13` — Asgard K3s CP (Göndul/Hlökk/Sigrún)
- `10.0.21.21/22/23` — Asgard K3s workers (Einherjar-urd/verd/skuld) — eth0
- `10.0.20.11–.99` — Asgard K3s MetalLB pool
- `10.0.20.201/202/203` — Worker eth1 IPs (Einherjar-urd/verd/skuld)
- `10.0.31.11/12/13` — Jotunheim K3s CP (Rota/Hildr/Kára)
- `10.0.31.21/22/23` — Jotunheim K3s workers
- `10.0.30.11–.99` — Jotunheim K3s MetalLB pool

### Cluster CIDRs
- Pod CIDR: `10.42.0.0/16` (`k3s_pod_cidr` — used for K3s `cluster-cidr` AND the Calico ipPool)
- Service CIDR: `10.43.0.0/16` (`k3s_service_cidr`)

### Resource IDs
- `1101–1199` — Asgard LXCs (sub-grouped by function)
- `2001–2999` — Asgard K3s VMs
- `3001–3999` — Jotunheim K3s VMs
- `10001+` — Templates

---

## Current build status

- ✅ UCG-Ultra — all VLANs, zones, firewall
- ✅ KPN DMZ → UCG-Ultra (IPv4 + IPv6)
- ✅ Synology (Munin) — factory reset, volumes, NFS, kubernetes user
- ✅ Proxmox cluster — Urd/Verd/Skuld on PVE 9.x, cluster niflheim
- ✅ PBS — LXC 1101 on Skuld, NFS datastore, connected to cluster
- ✅ AdGuard Home — Saga/Mimir/Kvasir, keepalived VIP 10.0.10.200, AGH Sync
- ✅ Asgard K3s — fully IaC end-to-end. **Validated via full teardown+rebuild on 2026-05-17.** Surfaced and fixed: route_localnet sysctl, VLAN 20 policy routing, k3s role install-idempotency, vault-unseal + synology-csi SealedSecret CRD-timing split, Vault test KV entry as IaC, CP cpu/memory parameterization. Göndul moved to Verd at the same time.
- ✅ Sealed Secrets — deployed via Flux. **Master keys backed up to 1Password as of 2026-05-17.** Loss of these keys makes every SealedSecret in Git undecryptable.
- ✅ Synology CSI — iSCSI only, StorageClass synology-csi-iscsi-retain (default)
- ✅ Vault — 3 node Raft HA, AWS KMS auto-unseal, iSCSI storage, initialized; K8s auth method + KV engine + ESO policy/role + AppRole + 2 AppRole roles in Terraform (`terraform/vault/`). Root token + recovery keys in 1Password (post-rebuild, tagged `asgard-rebuild-2026-05-17`).
- ✅ External Secrets Operator — deployed; ClusterSecretStore `vault` Ready
- ✅ MetalLB — L2 working end-to-end. VIP `10.0.20.11` reachable from outside the cluster. Required nodeSelectors, Calico autodetection pin, rp_filter loose mode, route_localnet, and VLAN 20 policy routing — all in IaC as of 2026-05-17.
- ✅ tigera-operator — fixed 2026-05-15 via MTU explicit (workaround for upstream projectcalico/calico#7851). No longer Degraded.
- ✅ Factorio LXC (1120) — Deployed 2026-05-16. Terraform + Ansible end-to-end. Factorio headless + SFTPGo running. Operator self-service via SFTP on TCP 22022. Game on UDP 34197. Reachable at `factorio.xiiisins.com` (external) and `factorio.niflheim.xiiisins.com` (internal).
- ✅ Fulla / PostgreSQL 1 (LXC 1130) — Deployed 2026-05-17. PG 17 from PGDG on Skuld at 10.0.11.230. TLS (self-signed), scram-sha-256 only. Two SUPERUSER management roles (admin, ansible) with passwords in HashiCorp Vault. Per-service DB provisioning machinery ready (empty until Authentik). Standalone; cluster expansion to Vör (1131, Urd) + Idunn (1132, Verd) + HAProxy VIP (10.0.10.210) deferred until post-Authentik.
- 🔲 Authentik + Redis (in asgard K3s — the next forward step on the K8s side)
- 🔲 Remaining asgard LXCs (Tailscale, Teamspeak, PostgreSQL cluster expansion, HAProxy, Zabbix, Jellyfin)
- 🔲 Jotunheim K3s
- 🔲 Services

---

## Repo structure

```
homelab/
├── CLAUDE.md
├── renovate.json
├── terraform/
│   ├── proxmox/
│   │   ├── asgard-k3s/      # VM definitions (bpg/proxmox provider) — CP cpu/memory per-node via locals map
│   │   └── asgard-lxcs/     # LXC definitions — 1120 Factorio, 1130 Fulla (PG primary); cluster nodes for_each via locals.postgres_nodes
│   ├── vault/               # Vault config — KV engine, K8s auth, eso policy + role, AppRole + 2 roles, test KV entry
│   ├── dns/                 # scaffolding (empty)
│   ├── aws/                 # scaffolding (empty)
│   ├── k3s/                 # scaffolding (empty)
├── ansible/
│   ├── ansible.cfg
│   ├── inventory/
│   │   ├── hosts.yml        # groups: asgard_k3s_cp, asgard_k3s_workers, factorio_host, postgres_hosts
│   │   ├── group_vars/all/  # vars.yml + vault.yml (Ansible Vault encrypted) — adjacent to inventory for auto-discovery
│   │   └── group_vars/{factorio_host,postgres_hosts}.yml  # per-group service config
│   ├── playbooks/
│   │   ├── asgard-k3s.yml         # roles: baseline → k3s → hardening
│   │   ├── factorio-host.yml      # roles: baseline → factorio → sftpgo → hardening
│   │   ├── postgres-host.yml      # roles: baseline → postgres → hardening
│   │   └── test-vault-lookup.yml  # smoke test for AppRole + Vault KV chain
│   └── roles/
│       ├── baseline/        # OS prereqs (sudo, acl, tzdata, gnupg, ca-certificates), timezone, locale, pkg update, ansible + recovery users
│       ├── postgres/        # PG 17 from PGDG, TLS, scram-sha-256, management users (admin, ansible), per-service DB provisioning
│       ├── k3s/             # prereqs, network.yml (sysctls + VLAN 20 policy routing), detect-state.yml (skip install on healthy), install.yml, calico.yml
│       └── hardening/       # SELinux, SSH config, sysctl (security only — K3s+MetalLB sysctls are in k3s role), module blocklist, banner
├── k8s/
│   ├── asgard/
│   │   ├── flux-system/
│   │   │   ├── flux-system/                  # Flux bootstrap manifests
│   │   │   ├── infrastructure.yaml           # Flux Kustomization → infrastructure/
│   │   │   ├── infrastructure-config.yaml    # Flux Kustomization → infrastructure-config/ (ESO config), dependsOn infrastructure
│   │   │   ├── metallb-config.yaml           # Flux Kustomization → metallb-config/, dependsOn infrastructure
│   │   │   ├── vault-config.yaml             # Flux Kustomization → vault-config/ (vault-unseal SealedSecret), dependsOn infrastructure
│   │   │   └── synology-csi-config.yaml      # Flux Kustomization → synology-csi-config/ (synology-csi SealedSecret), dependsOn infrastructure
│   │   ├── infrastructure/
│   │   │   ├── sealed-secrets/
│   │   │   ├── synology-csi/                 # helmrelease + namespace (SealedSecret moved to synology-csi-config/)
│   │   │   ├── vault/                        # helmrelease + namespace (SealedSecret moved to vault-config/)
│   │   │   ├── external-secrets/
│   │   │   ├── metallb/
│   │   │   ├── authentik/                    # scaffolded, not yet populated
│   │   │   └── kustomization.yaml
│   │   ├── infrastructure-config/
│   │   │   ├── clustersecretstore.yaml       # ESO ClusterSecretStore
│   │   │   └── kustomization.yaml
│   │   ├── metallb-config/
│   │   │   ├── metallb-config.yaml           # IPAddressPool + L2Advertisement
│   │   │   └── kustomization.yaml
│   │   ├── vault-config/
│   │   │   ├── vault-unseal-secret.yaml      # SealedSecret with AWS KMS creds
│   │   │   └── kustomization.yaml
│   │   ├── synology-csi-config/
│   │   │   ├── synology-secret.yaml          # SealedSecret with Synology API creds
│   │   │   └── kustomization.yaml
│   │   └── apps/
│   └── jotunheim/
│       ├── flux-system/
│       ├── infrastructure/
│       └── apps/
└── docs/
    ├── homelab-design.md
    └── teardown-rebuild.md                   # rebuild runbook
```

---

## K3s install — how it actually works

K3s itself is fully IaC'd via the Ansible `k3s` role. It is NOT a source of config drift — the drift concern is the in-cluster *workload* layer and the Calico addon merge behavior, not the K3s install.

- **Version:** `k3s_version` in `roles/k3s/defaults/main.yml` (currently `v1.33.1+k3s1`), binary downloaded from GitHub releases.
- **Role task order:** `prerequisites.yml` → `network.yml` (sysctls + VLAN 20 policy routing) → `detect-state.yml` (skip-install gate) → `install.yml` → `calico.yml` (init node only).
- **Idempotency:** `detect-state.yml` sets `k3s_already_healthy` if `systemctl is-active k3s` returns `active` AND the node is `Ready` in the cluster. When true, `install.yml` and `calico.yml` are skipped. Re-running the playbook against a healthy cluster produces `changed=0` for K3s tasks — critical to avoid duplicate-join failures.
- **Bootstrap order:** `k3s_init_node` (default `gondul`) initialises with `--cluster-init` → other CP nodes join → workers join last. Enforced by `when` conditions + `wait_for` on :6443 + node-count gates.
- **CP rebuild scenario:** if you destroy and recreate the default init node, override with `-e k3s_init_node=hlokk` (or another healthy CP) so the rebuilt node *joins* the existing cluster instead of `--cluster-init`ing a new one. Also run `kubectl delete node <name>` first to remove the stale member entry (see Known gotchas).
- **Config templates:** `config-init.yaml.j2` (first CP), `config-server.yaml.j2` (joining CPs), `config-agent.yaml.j2` (workers). CP configs disable `traefik`, `servicelb`, `local-storage`; set `flannel-backend: none` + `disable-network-policy: true` (Calico handles both); set `cluster-cidr`/`service-cidr`, TLS SANs, `selinux: true`. Worker config is minimal: `server` + `token` + `selinux: true`.
- **Token:** generated by K3s on the init node, slurped and distributed as the `k3s_token` fact. (`k3s_token` default in defaults/main.yml is empty — real value in `group_vars/all/vault.yml`.)
- **kubeconfig:** fetched to `~/.kube/niflheim-asgard.yaml`, server address rewritten from `127.0.0.1` to the init node IP.
- **Prerequisites** (`prerequisites.yml`): Rancher k3s-selinux repo, `iscsi-initiator-utils` + `iscsid` enabled, `br_netfilter` + `overlay` modules loaded & persisted, `ip_forward=1`, `bridge-nf-call-iptables`/`ip6tables=1`, swap disabled, `firewalld` disabled.
- **Network plumbing** (`network.yml`): sysctls `rp_filter=2` (loose), `route_localnet=1`, plus the `vlan20-policy-routing.service` systemd unit on workers. See MetalLB section for why.
- **No node taints or labels** are set anywhere in the K3s config — this is why the CP-taint work is a manual addition (see pending tasks).

---

## K3s VM specs

CP cpu/memory parameterized per-node in the `locals.control_planes` map in `terraform/proxmox/asgard-k3s/main.tf`.

**Control planes (Göndul/Hlökk/Sigrún):**
- Göndul: **2 vCPU, 4GB RAM**, 10GB disk, on **Verd** — bumped 2026-05-17 after OOM/CPU exhaustion during rebuild reconciliation churn
- Hlökk: 1 vCPU, 2GB RAM, 10GB disk, on Verd
- Sigrún: 1 vCPU, 2GB RAM, 10GB disk, on Skuld
- Single NIC on VLAN 21
- RHEL 9, K3s, Calico CNI
- ⚠️ Hlökk and Sigrún at 1vCPU/2GB are still unvalidated under sustained load. If they exhibit gondul's pre-2026-05-17 symptoms, bump them via the same locals map.

**Workers (Einherjar-urd/verd/skuld):**
- 2 vCPU, 4GB RAM, 15GB disk
- eth0: VLAN 21 (K3s traffic)
- eth1: VLAN 20 (MetalLB L2, IPs 10.0.20.201/202/203)
- RHEL 9, K3s, Calico CNI, iscsiadm
- ⚠️ Multi-homed. The second NIC (eth1/VLAN 20) is a known landmine: it requires Calico autodetection pin, rp_filter loose mode, route_localnet=1, AND VLAN 20 source-based policy routing. All four are in IaC. See Known gotchas + MetalLB decision.

---

## Conventions

- Terraform provider: `bpg/proxmox`
- All IPs static
- Versions: intended to be pinned, with Renovate managing minor/patch via PR once the homelab reaches a working "2.0" state. The Ansible role versions ARE pinned (`k3s_version`, `calico_version`). HelmRelease charts are currently `version: "0.x"` placeholders — real pinning is a deliberate deferred step gated on the 2.0 state, not an oversight.
- Conventional commits
- Norse mythology naming
- Never commit secrets
- Shell: fish (owner uses fish shell — heredocs `<<EOF` don't work, use pipes or temp files)

---

## Known gotchas

- **etcd on Urd**: The N5095 is too slow for etcd. Never put K3s CP nodes on Urd. On 2026-05-14 an etcd IO storm here cascaded into a multi-hour, multi-symptom outage (overlay, webhook, iSCSI, Vault all affected).
- **Multi-homed workers — Calico autodetection**: Workers have eth0 (VLAN 21) and eth1 (VLAN 20). Calico's default `firstFound` autodetection bound the overlay to eth1 on the workers, breaking cross-node vxlan. Fix (in the Ansible Calico template): `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]`. Do NOT revert to firstFound.
- **Multi-homed workers — rp_filter**: Strict reverse-path filtering (`rp_filter=1`) silently drops MetalLB LoadBalancer traffic arriving on eth1. K3s role's `network.yml` sets `rp_filter=2` (loose mode) on `all` and `default` — required on these multi-homed nodes, still drops genuinely unroutable sources. Do NOT "harden" it back to 1. (Per-interface `eth0`/`eth1` values derive from `all`; setting them explicitly would be belt-and-suspenders.)
- **Multi-homed workers — route_localnet**: MetalLB L2 mode does NOT bind VIPs to any interface — it only ARPs for them. Without `route_localnet=1`, the kernel drops packets destined for the VIP because the IP isn't local. K3s role's `network.yml` sets it on `all`. Discovered 2026-05-17 during asgard rebuild — VIP was completely unreachable from outside the VLAN until this landed.
- **Multi-homed workers — VLAN 20 policy routing**: Replies from MetalLB VIPs originate from the worker's eth1 IP. Without source-based policy routing, the reply goes out via the default route (eth0/VLAN 21), creating asymmetric routing that UCG-Ultra's stateful firewall drops. Fix: the `vlan20-policy-routing.service` systemd oneshot unit installed by `roles/k3s/tasks/network.yml` adds `from 10.0.20.0/24 lookup vlan20` + `default via 10.0.20.1 dev eth1 table vlan20`. OS-independent (pure ip(8) + systemd, no NetworkManager coupling).
- **MetalLB L2 election**: L2Advertisement needs `nodeSelectors` excluding CP nodes (they have no eth1). Without it, election can land on a CP node and announce nowhere.
- **Calico Installation CR merge behavior**: The K3s addon controller MERGES the `Installation` CR rather than replacing it — a removed field can persist in the live object (observed 2026-05-14: a stale `firstFound` survived alongside the new `cidrs`). After changing autodetection, verify with `kubectl get installation default -o jsonpath='{.spec.calicoNetwork.nodeAddressAutodetectionV4}'` and `kubectl patch ... --type=json` to strip stale fields if needed.
- **tigera-operator `/var/lib/calico/mtu` denied (upstream bug #7851)**: The Tigera operator runs as `container_t` with MCS categories (e.g. `s0:c322,c902`); `/var/lib/calico/mtu` is written by privileged calico-node as `container_var_lib_t:s0` with NO categories. MCS dominance fails on read. The denial is `dontaudit`'d by default policy, so `ausearch` returns nothing until `semodule -DB`. **Fix (shipped 2026-05-15):** set `mtu: 1450` explicitly in the Calico `Installation` CR (`ansible/roles/k3s/templates/calico-installation.yaml.j2`). When MTU is set explicitly the operator never reads the file. This is the upstream-maintainer-recommended workaround. 1450 = 1500 (host) - 50 (VXLAN overhead). Do NOT remove without a replacement fix — the operator will go Degraded again. Revisit if/when upstream actually fixes the operator.
- **Vault SKIP_CHOWN**: Vault Helm chart sets SKIP_CHOWN=true when running as non-root. With iSCSI storage, fsGroup doesn't apply automatically. Fix (in the Vault HelmRelease values): pod-level `securityContext` with `runAsUser: 100`, `runAsGroup: 1000`, `fsGroup: 1000`, `runAsNonRoot: false`; plus an `extraInitContainer` `vault-data-chown` (busybox, `runAsUser: 0`) running `chown -R 100:1000 /vault/data && chmod 750 /vault/data`.
- **Vault TLS disabled — deliberate**: The Vault raft config sets `tls_disable = 1` on the listener; there is no TLS on the listener OR cluster traffic (`cluster_address` is under the same listener). This is a conscious homelab tradeoff (simplicity vs defense-in-depth), not an oversight. Do NOT "fix" it without understanding the cluster-traffic implications. Revisit only as part of deliberate Vault hardening. The `vault-ui` LoadBalancer (VIP `10.0.20.11`) therefore serves plaintext HTTP on :8200 — it is internal/VLAN-only.
- **iSCSI single-session LUNs**: Synology CSI LUNs are single-session. After ungraceful node restarts, a stale session or discovery node record can pin a LUN to a dead/wrong node and block re-attach (`non-retryable iSCSI login failure`, or `iscsi_limit_max_session_count` on the NAS side). Also: the Synology CSI node plugin is a DaemonSet and runs on CP nodes too (no CP taint yet) — a CP node grabbed a worker's LUN during a reschedule. Cleanup: `iscsiadm -m session` / `-m node -o delete` on affected nodes; clear stale sessions NAS-side if needed.
- **Flux CRD timing — SealedSecrets**: SealedSecret resources can NOT live in the same Kustomization that installs the sealed-secrets HelmRelease. The dry-run runs before the chart installs → SealedSecret CRD doesn't exist yet → reconcile fails with `no matches for kind "SealedSecret" in version "bitnami.com/v1alpha1"`. Same rule applies to any CRD-dependent resource: it goes in a per-component `<component>-config/` Kustomization that `dependsOn: infrastructure`. Current `<component>-config` Kustomizations: `infrastructure-config` (ESO ClusterSecretStore), `metallb-config` (IPAddressPool + L2Advertisement), `vault-config` (vault-unseal SealedSecret), `synology-csi-config` (synology-csi SealedSecret).
- **Sealed-secrets master keys must be backed up**: sealed-secrets controller generates a fresh keypair on first start. SealedSecrets in Git are encrypted against that pair's public cert. If the cluster is rebuilt without restoring the keypair, every existing SealedSecret becomes undecryptable and must be re-sealed from plaintext sources. Backup procedure: `kubectl get secret -n sealed-secrets -l sealedsecrets.bitnami.com/sealed-secrets-key=active -o yaml > backup.yaml`, store contents in 1Password. Restore: `kubectl apply -f backup.yaml` *before* the sealed-secrets controller starts on the rebuilt cluster. Discovered 2026-05-17 — both SealedSecrets (vault-unseal, synology-csi) had to be re-sealed from plaintext during the asgard rebuild because the original keys were never backed up.
- **CP rebuild → "duplicate node name"**: destroying and recreating a CP VM and re-running the playbook fails with `etcd cluster join failed: duplicate node name found`. K3s tries to join with the same name the cluster already considers a member. Fix: `kubectl delete node <name>` from a surviving CP *before* starting K3s on the new VM. The K3s native node-delete handler also evicts the stale etcd member. If the failing k3s.service is already in a systemd restart loop, the next retry will succeed automatically once the node entry is removed.
- **CP rebuild of the default init node**: if the destroyed CP is `k3s_init_node` (default `gondul`), the role would `--cluster-init` it as a fresh cluster instead of joining the existing one. Override: `ansible-playbook playbooks/asgard-k3s.yml --limit <name> -e 'k3s_init_node=hlokk'` (any healthy CP works as the temporary init reference). The new node then joins via that CP.
- **K3s role install-skip on healthy nodes**: `roles/k3s/tasks/detect-state.yml` sets `k3s_already_healthy` if `systemctl is-active k3s == 'active'` AND `kubectl get node <name>` returns Ready. `install.yml` and `calico.yml` skip when true. Without this guard, re-running the play against a healthy CP can fire the restart-k3s handler, K3s restarts and re-attempts join, and the cluster rejects with "duplicate node name." Deeper bug: a genuine config template change still triggers the handler and the same failure — tracked separately, not yet fixed.
- **Vault Raft follower auto-join can fail**: on fresh Vault init via the Helm chart's StatefulSet, vault-1 and vault-2 are supposed to auto-discover vault-0 and join via `retry_join`. They often miss the join window if they started before vault-0 was initialized, then sit logging "stored unseal keys are supported, but none were found." Fix: `kubectl exec -n vault vault-<N> -- vault operator raft join http://vault-0.vault-internal:8200`. They auto-unseal via KMS after joining.
- **Vault init can leave a stuck partial state**: if `vault operator init` exits non-zero (e.g. running it before all pods are stable), the Raft data dir can end up in a half-initialized state where the next init attempt also fails with "stored unseal keys supported, but none were found." Recovery: `kubectl delete statefulset vault -n vault --cascade=orphan`, `kubectl delete pvc -n vault data-vault-0 data-vault-1 data-vault-2`, `kubectl delete pod -n vault vault-0 vault-1 vault-2 --force --grace-period=0`, then `flux reconcile helmrelease vault -n vault --force` to recreate. Old iSCSI LUNs on Synology retain-policy survive — clean them up on DSM after.
- **Flux deploy key not in IaC**: re-bootstrapping Flux on a fresh cluster needs the GitHub deploy key Secret. `flux bootstrap github` re-creates it idempotently if the deploy key still exists in the GitHub repo settings. If not, generates a new keypair. NOT a SealedSecret. Tracked: should be backed up or rotated post-bootstrap.
- **ESO API version**: ClusterSecretStore uses `external-secrets.io/v1` not `v1beta1`.
- **MetalLB speaker labels**: `app.kubernetes.io/component=speaker` not `component=speaker`.
- **fish shell heredocs**: Don't use `<<EOF` syntax. Use `echo '...' | command` or temp files.
- **Ansible vault loading**: `group_vars/all/` directory with `vars.yml` + `vault.yml`. `k3s_token` is defined in `vault.yml` (default in role is empty).
- **Rancher SELinux repo path**: `prerequisites.yml` pulls `k3s-selinux` from the Rancher `centos/8/noarch` repo even on RHEL 9 nodes. This is the documented Rancher path and the RPM is noarch — works fine, intentional, not a bug.
- **baseline role updates all packages**: The `baseline` role runs `dnf update "*" → latest` + reboot on every run. Re-running the playbook is therefore not pure config-idempotency — it can pull OS updates. Known/intended behavior.
- **bpg/proxmox reboots**: Provider may reboot VMs on apply even without meaningful changes due to IP address drift in state. Expect this — cluster should survive rolling restarts.
- **SELinux dontaudit hides denials**: A "permission denied" with nothing in `ausearch` does NOT mean SELinux is innocent. The default policy `dontaudit`s many denials. Diagnose with `semodule -DB` (disable dontaudit), reproduce, capture AVC, then `semodule -B` to re-enable. Never leave dontaudit disabled — it floods the audit log.
- **Proxmox minimal Debian template is *minimal***: `debian-13-standard_*.tar.zst` lacks `sudo`, `acl`, and other things roles often assume. The `baseline` role installs `sudo` and `acl` OS-agnostically (in `main.yml`, via `ansible.builtin.package` — no-op on RHEL which ships both in `@core`). When writing a new role that uses `become_user: <non-root>`, ensure `acl` is installed via baseline first (Ansible's preferred privilege-drop mechanism uses POSIX ACLs).
- **Minimal-template package gaps keep surfacing**: PG deploy 2026-05-17 added `tzdata`, `gnupg`, `ca-certificates`, and locale generation (`locales` package + `community.general.locale_gen`) to baseline. The list will grow as new roles surface new gaps — that's expected. Baseline owns the class; alternative is every role rediscovering the same gaps. Particularly: any role adding a third-party apt repo needs `gnupg` (for `apt-key`-style dearmor) and `ca-certificates` (for HTTPS to upstream). Any role using locale-sensitive tools (PG's `pg_createcluster` is one) needs the locale generated first.
- **`--check` doesn't validate file ownership**: `--check` reports "would create" for files that don't yet exist, but doesn't validate `owner`/`group`/`mode` against actual users. Bugs in this dimension survive `--check` and only surface at real-run + post-hoc inspection. The auth path is unforgiving — sshd silently rejects keys with wrong directory perms or ownership. **Always set `owner`/`group`/`mode` explicitly on every `file`/`template`/`copy` task; never rely on defaults**. Discovered 2026-05-17 when fulla's `authorized_keys` ended up owned by `recovery` instead of `ansible` and the fix took longer than the bug.
- **`ansible.posix.authorized_key` in `--check` needs explicit `path:`**: The module resolves `~/.ssh/authorized_keys` from the user's home dir via getent. In `--check`, the previous create-user task is a no-op, getent finds no user, module errors with "Either user must exist or you must provide full path to key file in check mode". Fix: pass `path: "/home/{{ user }}/.ssh/authorized_keys"` + `manage_dir: true` explicitly. Real-run is functionally identical; check-mode unblocks.
- **`ansible.builtin.user` creates accounts with `password: '!'` by default**: PAM's `account` stack interprets this as "account locked" even though pubkey auth via sshd never actually checks a password. Symptom after hardening enables full sshd PAM evaluation: `User <name> not allowed because account is locked` in auth.log; SSH key offered and rejected. Fix: pass `password: '*'` explicitly. `*` is "valid account, no usable password, key auth permitted." Required for any key-auth-only user.
- **Service-specific systemd drop-in dirs (`/etc/systemd/system/<unit>.service.d/`) don't pre-exist**: Roles that drop override.conf files must `file: state: directory` first. SFTPGo bit us; assume any service-specific override needs the dir created.
- **bpg/proxmox API tokens can change `nesting` but no other features**: `keyctl`, `fuse`, and other LXC features require `root@pam` to change once the container exists. Workaround: destroy + recreate. Practical implication: don't put `keyctl: false`/`fuse: false` in the resource block — they're defaults, omitting them avoids future "tried to change keyctl, 403" plans even when values aren't actually changing.
- **Systemd 257 in unprivileged LXC requires `nesting=true`**: Debian 13 ships systemd 257; it uses namespace operations that need CAP_SYS_ADMIN inside the user namespace, which `nesting=true` provides. This flag does NOT enable nested containerization — its name is misleading. The "no nested containerization" rule is preserved by `unprivileged=true`, not by `nesting=false`.
- **`-u root` CLI flag does NOT override `ansible_user` from group_vars**: Group_vars are inventory-level data and outrank CLI `-u` in Ansible's variable precedence. To override during bootstrap, use `-e 'ansible_user=root'` — `-e` is the only level above inventory. Same issue affects the `asgard-k3s` flow if you ever need to re-bootstrap.
- **`group_vars/` is auto-discovered only adjacent to inventory or playbook directories**: `ansible/group_vars/` is invisible to Ansible; must be `ansible/inventory/group_vars/`. If a play seems to be ignoring vault variables, this is usually why.
- **SFTPGo sqlite `data_provider_name` must be an absolute path**: The Debian package unit sets `WorkingDirectory=/etc/sftpgo`, so a relative `sftpgo.db` lands in `/etc/sftpgo/sftpgo.db` — works but FHS-wrong (config dir holding state). Set explicitly to `/var/lib/sftpgo/sftpgo.db` and ensure that dir exists with sftpgo ownership.
- **Factorio reconcile.timer must NOT auto-start in the role**: Background timer firing reconcile before `initial-install.yml` runs causes a race: factorio installs and the service starts (control file defaults to `state=running`) before Ansible can generate the default save → `factorio --create` fails on the .lock file held by the running service. Pattern: enable timer only in `reconcile.yml`, start it explicitly at the end of `initial-install.yml`.
- **Factorio install dir needs `chown -R factorio:factorio` after extract**: Factorio writes `.lock` in its install dir at startup. The tarball's extracted ownership won't match. The reconcile script does this; if you ever sidestep reconcile and install manually, you need to chown manually too.

---

## What the owner wants to learn

Kubernetes is the primary goal. Explain the *why* behind K8s design choices, not just manifests. Owner knows Linux, Ansible, networking, enterprise infrastructure patterns. K8s-specific concepts are the knowledge gap. Deep K3s/K8s experimentation is intended for the future jotunheim ("can implode") cluster — asgard is built carefully, not used as a learning sandbox.